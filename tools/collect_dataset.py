"""Collect training frames overnight while the bot plays, and pre-label them.

The models the bot ships with were trained on somebody else's frames. Better
ones need frames from the machines and the game version people actually run,
and the only time those exist in quantity is while a bot is playing - which is
exactly when nothing may be allowed to slow it down.

So this is a separate process that touches the running bot in no way at all.
It takes its own screenshots over ADB, which the emulator serves without the
bot's involvement, and it never imports, patches or restarts anything the bot
owns. If this tool dies the bot does not notice; if the bot restarts the game
or the emulator - and its watchdogs will, overnight - this reconnects and
carries on.

What it produces is a YOLO dataset: images/, labels/, data.yaml. The labels
are written by the models the bot already has, so brawlers, walls and bushes
arrive already boxed and the human work becomes correcting rather than
drawing. That is the whole point: a night of this is a few thousand frames
that are ninety percent done.

What it CANNOT label, and this matters before anybody trains on the output:

Projectiles. The bot finds them by comparing consecutive frames a few
milliseconds apart, and a screenshot every few seconds is far too slow to see
a shot that crosses the screen in half of one. The frames will contain
projectiles; nothing here will have boxed them.

Map borders. Nothing in the bot detects them, so there is nothing to copy.

Both are in the class list so that boxes drawn later get the right numbers,
but every image needs a manual pass for them. Training on this dataset as it
stands would teach a model that projectiles do not exist - which is worse than
what we have now, not better.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Where the classes the bot's own models produce sit in the new numbering.
# These six come out pre-labelled; the rest are for the manual pass, and are
# listed here so a person drawing boxes later gets consistent ids.
CLASS_NAMES = [
    "player", "teammate", "enemy",      # mainInGameModel
    "wall", "bush", "close_bush",       # tileDetector
    "projectile", "map_border",         # by hand - see the module docstring
]
CLASS_ID = {name: index for index, name in enumerate(CLASS_NAMES)}

# How different two frames must be, on a 64x36 grey thumbnail, to count as a
# new one. A bot standing still would otherwise fill a disk with one picture.
CHANGE_THRESHOLD = 6.0

# The bot's own capture is 1920x1080; anything else means the emulator is set
# up differently and the frame is worth keeping anyway, just noted.
EXPECTED = (1920, 1080)


def adb_path():
    """Whichever adb is on this machine. The emulator ships one."""
    candidates = [
        Path(r"C:/Program Files/Netease/MuMuPlayer/nx_main/adb.exe"),
        Path(r"C:/Program Files/BlueStacks_nxt/HD-Adb.exe"),
        Path(r"C:/LDPlayer/LDPlayer9/adb.exe"),
        ROOT / "platform-tools" / "adb.exe",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return "adb"  # on PATH, or we find out on the first call


def devices(adb):
    """Every attached device, newest listing each time.

    Re-read rather than remembered: the bot's watchdogs restart the emulator
    overnight, and it can come back on a different port.
    """
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                             timeout=20).stdout
    except Exception:
        return []
    found = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            found.append(parts[0])
    return found


def grab(adb, serial):
    """One screenshot, as BGR, or None.

    exec-out rather than a file on the device and a pull: one round trip, no
    litter left in the emulator's storage.
    """
    try:
        raw = subprocess.run([adb, "-s", serial, "exec-out", "screencap", "-p"],
                             capture_output=True, timeout=30).stdout
    except Exception:
        return None
    if not raw or len(raw) < 1024:
        return None
    try:
        image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None
    return image


def signature(image):
    """A thumbnail to compare frames by, cheap enough to do on every one."""
    return cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (64, 36),
                      interpolation=cv2.INTER_AREA).astype(np.float32)


def to_yolo(box, width, height):
    """A pixel box to YOLO's normalised centre-and-size, clamped to the frame."""
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    x1, x2 = max(0.0, min(x1, x2)), min(float(width), max(x1, x2))
    y1, y2 = max(0.0, min(y1, y2)), min(float(height), max(y1, y2))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None
    return ((x1 + x2) / 2 / width, (y1 + y2) / 2 / height,
            (x2 - x1) / width, (y2 - y1) / height)


COLOURS = {
    0: (0, 255, 0), 1: (255, 200, 0), 2: (0, 0, 255),
    3: (128, 128, 128), 4: (0, 200, 0), 5: (0, 140, 0),
}


def draw_preview(image, lines, path):
    """The frame with its labels drawn, so quality can be judged at a glance.

    Numbers in a text file say nothing about whether the boxes are on the
    brawlers. One picture every so often answers that in a second, which is
    what somebody wants before spending a day correcting three thousand
    frames - or before deciding the whole night was wasted.
    """
    canvas = image.copy()
    height, width = canvas.shape[:2]
    for line in lines:
        parts = line.split()
        if len(parts) != 5:
            continue
        cls = int(parts[0])
        cx, cy, w, h = (float(v) for v in parts[1:])
        x1, y1 = int((cx - w / 2) * width), int((cy - h / 2) * height)
        x2, y2 = int((cx + w / 2) * width), int((cy + h / 2) * height)
        colour = COLOURS.get(cls, (255, 255, 255))
        thickness = 3 if cls < 3 else 1
        cv2.rectangle(canvas, (x1, y1), (x2, y2), colour, thickness)
        if cls < 3:
            cv2.putText(canvas, CLASS_NAMES[cls], (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, colour, 2)
    cv2.imwrite(str(path), canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 80])


def build_detectors(confidence, device="cpu"):
    """The bot's own models, loaded read-only in this process.

    Loading them here rather than reaching into the running bot is the whole
    safety argument: two processes each with their own session, sharing only
    model files that neither writes.

    On CPU by default, and that is deliberate. The bot is playing on the GPU
    right now, and a second process running TensorRT on the same card would be
    competing for it - and worse, writing to the same engine cache the bot is
    reading. Labelling one frame every few seconds does not need a GPU; the
    bot does. A slow label costs nothing, a dropped dodge costs a match.

    The switch is made by intercepting the config read inside THIS process
    only. Editing cfg/general_config.toml would change it for the running bot
    too, which is precisely what must not happen.
    """
    import detect as detect_module
    from utils import load_toml_as_dict

    config = load_toml_as_dict("cfg/bot_config.toml")
    wall_classes = config.get("wall_model_classes") or ["wall", "bush", "close_bush"]

    original = detect_module.load_toml_as_dict

    def patched(path, *args, **kwargs):
        values = original(path, *args, **kwargs)
        if str(path).endswith("general_config.toml"):
            values = dict(values)
            # Two threads, not the bot's six. The bot is playing on this same
            # machine and its loop is latency-bound; a background labeller
            # taking a third of the cores would show up as a worse frame rate
            # in the game, which is the one thing this must not cost.
            values["used_threads"] = 2
            if device == "cpu":
                values["cpu_or_gpu"] = "cpu"
                values["execution_provider"] = "cpu"
        return values

    detect_module.load_toml_as_dict = patched
    try:
        main = detect_module.Detect("models/mainInGameModel.onnx",
                                    classes=["enemy", "teammate", "player"])
        tiles = detect_module.Detect("models/tileDetector.onnx",
                                     classes=list(wall_classes))
    finally:
        detect_module.load_toml_as_dict = original
    return main, tiles, float(confidence)


def label(main, tiles, confidence, image):
    """Every box the bot's models see, as YOLO lines. Empty means not in a match."""
    height, width = image.shape[:2]
    # The models were trained on RGB frames; ADB hands us BGR.
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    lines = []
    found = {}
    for detector, threshold in ((main, confidence), (tiles, confidence)):
        try:
            results = detector.detect_objects(rgb, conf_tresh=threshold) or {}
        except Exception:
            continue
        for name, boxes in results.items():
            if name not in CLASS_ID:
                continue
            for box in boxes or []:
                converted = to_yolo(box, width, height)
                if converted is None:
                    continue
                lines.append(f"{CLASS_ID[name]} " +
                             " ".join(f"{v:.6f}" for v in converted))
                found[name] = found.get(name, 0) + 1
    return lines, found


def write_dataset_files(out):
    """data.yaml and a README, so the folder explains itself in the morning."""
    names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
    (out / "data.yaml").write_text(
        "# VvokAI collected dataset.\n"
        "#\n"
        "# READ THIS BEFORE TRAINING. Classes 0-5 were labelled automatically\n"
        "# by the bot's current models. Classes 6 and 7 - projectile and\n"
        "# map_border - are NOT labelled in any of these files. Training as-is\n"
        "# teaches a model that projectiles do not exist.\n"
        "#\n"
        "# The automatic labels also inherit the current models' mistakes, so\n"
        "# they are a starting point for correction, not ground truth.\n"
        f"path: {out.resolve().as_posix()}\n"
        "train: images\n"
        "val: images\n"
        f"nc: {len(CLASS_NAMES)}\n"
        f"names:\n{names}\n", encoding="utf-8")

    (out / "README.txt").write_text(
        "Frames collected while the bot played, with boxes the bot's own\n"
        "models drew on them.\n"
        "\n"
        "  images/   the frames, full resolution\n"
        "  labels/   one YOLO .txt per frame, same name\n"
        "  data.yaml classes and paths\n"
        "  log.jsonl one line per kept frame, for checking what happened\n"
        "\n"
        "Already labelled: player, teammate, enemy, wall, bush, close_bush.\n"
        "NOT labelled, must be drawn by hand: projectile, map_border.\n"
        "\n"
        "Only frames where a player was detected are kept, so menus and\n"
        "loading screens are not in here. Near-identical frames are dropped.\n",
        encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dataset",
                        help="where to write the dataset")
    parser.add_argument("--interval", type=float, default=2.5,
                        help="seconds between screenshots")
    parser.add_argument("--max-frames", type=int, default=6000,
                        help="stop after this many kept frames")
    parser.add_argument("--max-gb", type=float, default=8.0,
                        help="stop once the dataset reaches this size")
    parser.add_argument("--hours", type=float, default=10.0,
                        help="stop after this long")
    parser.add_argument("--confidence", type=float, default=0.55,
                        help="detection threshold for the pre-labels")
    parser.add_argument("--quality", type=int, default=92,
                        help="JPEG quality for saved frames")
    parser.add_argument("--no-label", action="store_true",
                        help="only collect frames, run no models")
    parser.add_argument("--device", choices=("cpu", "gpu"), default="cpu",
                        help="where to run the labelling models. cpu by "
                             "default so the playing bot keeps the GPU")
    parser.add_argument("--preview-every", type=int, default=40,
                        help="save a boxes-drawn copy this often, 0 for none")
    args = parser.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    (out / "preview").mkdir(parents=True, exist_ok=True)
    write_dataset_files(out)
    journal = open(out / "log.jsonl", "a", encoding="utf-8")

    adb = adb_path()
    print(f"adb: {adb}")

    main_model = tile_model = None
    confidence = args.confidence
    if not args.no_label:
        print(f"loading the bot's models on {args.device.upper()} "
              "(a second session, its own)...")
        main_model, tile_model, confidence = build_detectors(
            args.confidence, args.device)
        print("models ready")

    serial = None
    previous = None
    kept = skipped_same = skipped_menu = failed = 0
    started = time.time()
    deadline = started + args.hours * 3600
    # Existing frames are counted so a second night adds to the first rather
    # than overwriting it.
    index = len(list((out / "images").glob("*.jpg")))
    if index:
        print(f"{index} frames already here, continuing from there")

    print(f"collecting every {args.interval}s into {out.resolve()}")
    print("stop with Ctrl+C; everything written so far stays.\n")

    try:
        while time.time() < deadline and kept < args.max_frames:
            loop_started = time.time()

            if serial is None:
                available = devices(adb)
                if not available:
                    # The bot restarts the emulator when it freezes. That takes
                    # a while and there is nothing to do but wait for it.
                    failed += 1
                    time.sleep(15)
                    continue
                serial = available[0]
                print(f"device: {serial}")

            image = grab(adb, serial)
            if image is None:
                failed += 1
                serial = None  # re-discover; the port may have changed
                time.sleep(5)
                continue

            current = signature(image)
            if previous is not None:
                if float(np.mean(np.abs(current - previous))) < CHANGE_THRESHOLD:
                    skipped_same += 1
                    time.sleep(max(0.0, args.interval - (time.time() - loop_started)))
                    continue
            previous = current

            lines, found = ([], {})
            if main_model is not None:
                lines, found = label(main_model, tile_model, confidence, image)
                # No player on screen means a menu, a loading screen or a
                # results card. Those are not what a model needs to learn, and
                # keeping them would bury the useful frames.
                if "player" not in found:
                    skipped_menu += 1
                    time.sleep(max(0.0, args.interval - (time.time() - loop_started)))
                    continue

            name = f"frame_{index:06d}"
            cv2.imwrite(str(out / "images" / f"{name}.jpg"), image,
                        [int(cv2.IMWRITE_JPEG_QUALITY), args.quality])
            (out / "labels" / f"{name}.txt").write_text(
                "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            journal.write(json.dumps({
                "frame": name, "at": round(time.time(), 1),
                "size": [image.shape[1], image.shape[0]], "found": found,
            }, ensure_ascii=False) + "\n")
            journal.flush()

            if args.preview_every and kept % args.preview_every == 0 and lines:
                draw_preview(image, lines, out / "preview" / f"{name}.jpg")

            index += 1
            kept += 1

            if kept % 25 == 0:
                size_gb = sum(f.stat().st_size for f in
                              (out / "images").glob("*.jpg")) / 1024 ** 3
                hours = (time.time() - started) / 3600
                print(f"[{hours:5.2f}h] kept {kept}, same {skipped_same}, "
                      f"menu {skipped_menu}, failed {failed}, {size_gb:.2f} GB")
                if size_gb >= args.max_gb:
                    print(f"reached {args.max_gb} GB, stopping")
                    break

            time.sleep(max(0.0, args.interval - (time.time() - loop_started)))
    except KeyboardInterrupt:
        print("\nstopped by hand")
    finally:
        journal.close()
        hours = (time.time() - started) / 3600
        print(f"\n{kept} frames kept in {hours:.2f}h "
              f"({skipped_same} identical, {skipped_menu} menus, {failed} failures)")
        print(f"dataset: {out.resolve()}")
        print("projectiles and map borders still need a manual pass - "
              "see README.txt")


if __name__ == "__main__":
    main()
