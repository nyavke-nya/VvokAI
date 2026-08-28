"""Offline tuning harness for the projectile tracker.

Run the detector over a recording and watch what it does, without touching a
live match. This is the tool to reach for before trusting the dodge in a game,
and the one to reach for when it misbehaves - the whole question is whether the
grey noise tier stays empty while the magenta confirmed tier lights up on real
shots.

    python tools/dodge_tune.py recording.mp4
    python tools/dodge_tune.py recording.mp4 --out annotated.mp4
    python tools/dodge_tune.py frames/ --set vision.motion_threshold=16
    python tools/dodge_tune.py recording.mp4 --sweep vision.motion_threshold=8,12,16,20

Overlay legend:
    grey cross     raw blob that passed the size/shape filter (the noise floor)
    amber circle   track with history, not confirmed yet
    magenta + line confirmed projectile and its predicted path
    red boxes      enemies, green box the player (from the YOLO model)

Without --no-model the real in-game model runs so that enemy-origin gating is
exercised. That is slow but honest; --no-model is much faster but disables the
single strongest noise filter, so its numbers are a worst case rather than a
preview.
"""

import argparse
import os
import sys
import time

import cv2
import numpy as np

# utils.PROJECT_ROOT is derived from the working directory, and cfg/ and models/
# are looked up relative to it - so running this from tools/ would look for
# tools/cfg/general_config.toml. Switch to the project root before importing
# anything from the project, and remember where the user actually was so their
# own file arguments still resolve.
INVOCATION_DIR = os.getcwd()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))
os.chdir(PROJECT_ROOT)

from dodge.config import DodgeConfig
from dodge.tracker import FrameContext, ProjectileTracker


def user_path(path):
    """Resolve a path the user typed, relative to where they ran the command."""
    if path is None or os.path.isabs(path):
        return path
    return os.path.join(INVOCATION_DIR, path)


def parse_overrides(pairs):
    overrides = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"--set expects section.key=value, got {pair!r}")
        path, value = pair.split("=", 1)
        if "." not in path:
            raise SystemExit(f"--set expects section.key=value, got {pair!r}")
        section, key = path.split(".", 1)
        try:
            parsed = float(value) if "." in value else int(value)
        except ValueError:
            parsed = {"true": True, "false": False}.get(value.lower(), value)
        overrides.setdefault(section, {})[key] = parsed
    return overrides


def load_config(overrides):
    from utils import load_toml_as_dict

    raw = load_toml_as_dict("cfg/dodge_config.toml")
    for section, values in overrides.items():
        raw.setdefault(section, {}).update(values)
    return DodgeConfig(raw, scale_factor=1.0, tile_size=54.0)


def frame_source(path):
    """Yield RGB frames from an mp4 or a directory of images."""
    if os.path.isdir(path):
        names = sorted(
            name for name in os.listdir(path)
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
        )
        if not names:
            raise SystemExit(f"No images found in {path}")
        for index, name in enumerate(names):
            image = cv2.imread(os.path.join(path, name))
            if image is None:
                continue
            yield index, cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        return

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        raise SystemExit(f"Could not open {path}")
    index = 0
    while True:
        ok, image = capture.read()
        if not ok:
            break
        yield index, cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        index += 1
    capture.release()


def warn_if_outside_venv():
    """The project's venv is where the CUDA libraries live.

    Running this with a bare `python`/`py` uses the system interpreter, which
    has onnxruntime but none of the nvidia-* CUDA packages. It still finds a
    GPU through DirectML, so nothing looks broken - it is just markedly slower
    than the CUDA path the venv is set up for.
    """
    venv_python = os.path.join(PROJECT_ROOT, "venv", "Scripts", "python.exe")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv or not os.path.exists(venv_python):
        return
    print(
        "NOTE: running on the system Python, which has no CUDA packages, so this\n"
        "      falls back to DirectML or CPU. For the CUDA path use:\n"
        f"      {venv_python} tools\\dodge_tune.py ...\n"
    )


def build_detector(disabled):
    if disabled:
        return None
    warn_if_outside_venv()
    from detect import Detect

    print("Loading mainInGameModel.onnx (use --no-model to skip)...")
    return Detect("./models/mainInGameModel.onnx", classes=["enemy", "teammate", "player"])


def annotate(frame_bgr, snapshot, projectiles, context, stats, index, fps):
    for box in context.enemies:
        cv2.rectangle(frame_bgr, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (0, 0, 255), 2)
    if context.player_box:
        box = context.player_box
        cv2.rectangle(frame_bgr, (int(box[0]), int(box[1])), (int(box[2]), int(box[3])), (0, 255, 0), 2)

    for blob in snapshot["blobs"]:
        cv2.drawMarker(frame_bgr, (blob["x"], blob["y"]), (150, 150, 150), cv2.MARKER_CROSS, 10, 1)

    for track in snapshot["pending"]:
        cv2.circle(frame_bgr, (track["x"], track["y"]), max(track["r"], 5), (0, 190, 255), 2)
        cv2.putText(frame_bgr, str(track["hits"]), (track["x"] + 10, track["y"] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 190, 255), 1, cv2.LINE_AA)

    for projectile in projectiles:
        start = (int(projectile.x), int(projectile.y))
        end = (int(projectile.x + projectile.vx * 0.5), int(projectile.y + projectile.vy * 0.5))
        cv2.line(frame_bgr, start, end, (255, 0, 255), 3, cv2.LINE_AA)
        cv2.circle(frame_bgr, start, int(projectile.radius), (255, 0, 255), 2, cv2.LINE_AA)
        cv2.putText(frame_bgr, f"{projectile.speed:.0f}px/s c{projectile.confidence:.2f}",
                    (start[0] + 12, start[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 0, 255), 1, cv2.LINE_AA)

    banner = (f"f{index}  blobs {stats['blobs']}  rejected {stats['rejected']}  "
              f"tracks {stats['tracks']}  confirmed {stats['confirmed']}  {stats['ms']:.1f}ms")
    cv2.putText(frame_bgr, banner, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(frame_bgr, banner, (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return frame_bgr


def run(path, config, detector, output=None, limit=None, fps=30.0, quiet=False):
    from utils import load_toml_as_dict

    tracker = ProjectileTracker(config)
    writer = None
    # Match the live bot's detection threshold so the boxes here are the same
    # boxes the origin gate would see in a real match.
    entity_confidence = load_toml_as_dict("cfg/bot_config.toml").get("entity_detection_confidence", 0.65)

    totals = {"frames": 0, "blobs": 0, "confirmed_frames": 0, "shots": 0, "ms": 0.0}
    seen_ids = set()
    detector_every = 2  # the model is the slow part; boxes barely move in one frame
    context = FrameContext()
    assumed_player = None  # filled in on the first frame when running --no-model

    for index, frame in frame_source(path):
        if limit and index >= limit:
            break

        if detector is not None and index % detector_every == 0:
            data = detector.detect_objects(frame, conf_tresh=entity_confidence)
            context = FrameContext(
                player_box=(data.get("player") or [None])[0],
                enemies=data.get("enemy") or [],
                teammates=data.get("teammate") or [],
                walls=[],
                joystick_active=True,
            )
        elif detector is None and assumed_player is None:
            # No model, so no player box - but the camera keeps the player at
            # the centre of the screen, which is close enough for the
            # unknown-origin gate to still mean something.
            height, width = frame.shape[:2]
            assumed_player = [width / 2 - 55, height / 2 - 70, width / 2 + 55, height / 2 + 70]
            context = FrameContext(player_box=assumed_player, joystick_active=True)

        stamp = index / fps
        projectiles, _ = tracker.update(frame, context, stamp)
        snapshot = tracker.debug_snapshot()
        stats = tracker.stats

        totals["frames"] += 1
        totals["blobs"] += stats["blobs"]
        totals["ms"] += stats["ms"]
        if projectiles:
            totals["confirmed_frames"] += 1
            for projectile in projectiles:
                if projectile.id not in seen_ids:
                    seen_ids.add(projectile.id)
                    totals["shots"] += 1

        if output:
            annotated = annotate(
                cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                snapshot, projectiles, context, stats, index, fps,
            )
            if writer is None:
                height, width = annotated.shape[:2]
                writer = cv2.VideoWriter(output, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
                if not writer.isOpened():
                    print(f"Could not open {output} for writing")
                    writer = None
                    output = None
            if writer is not None:
                writer.write(annotated)

        if not quiet and index % 60 == 0:
            print(f"  frame {index:5d}  blobs {stats['blobs']:3d}  "
                  f"tracks {stats['tracks']:2d}  confirmed {stats['confirmed']}")

    if writer is not None:
        writer.release()

    frames = max(totals["frames"], 1)
    return {
        "frames": totals["frames"],
        "blobs_per_frame": totals["blobs"] / frames,
        "confirmed_frames": totals["confirmed_frames"],
        "confirmed_pct": totals["confirmed_frames"] / frames * 100.0,
        "distinct_shots": totals["shots"],
        "ms": totals["ms"] / frames,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="mp4 file or directory of frames")
    parser.add_argument("--out", help="write an annotated mp4 here")
    parser.add_argument("--set", action="append", dest="overrides",
                        help="override a config value, e.g. vision.motion_threshold=16")
    parser.add_argument("--sweep", help="try several values, e.g. vision.motion_threshold=8,12,16")
    parser.add_argument("--no-model", action="store_true",
                        help="skip YOLO; much faster, but disables enemy-origin gating")
    parser.add_argument("--limit", type=int, help="stop after N frames")
    parser.add_argument("--fps", type=float, default=30.0, help="source framerate (default 30)")
    args = parser.parse_args()
    args.input = user_path(args.input)
    args.out = user_path(args.out)

    if not os.path.exists(args.input):
        raise SystemExit(f"Input not found: {args.input}")

    detector = build_detector(args.no_model)
    if args.no_model:
        print("WARNING: running without the model. Enemy-origin gating is off, so the "
              "false-positive numbers below are a worst case, not what the bot would see.")

    if args.sweep:
        path, values = args.sweep.split("=", 1)
        print(f"\nSweeping {path} over {values}\n")
        header = f"{path:>34} {'blobs/frame':>12} {'shots':>7} {'frames w/ shot':>15} {'cost':>8}"
        print(header)
        print("-" * len(header))
        for value in values.split(","):
            config = load_config(parse_overrides((args.overrides or []) + [f"{path}={value}"]))
            result = run(args.input, config, detector, limit=args.limit, fps=args.fps, quiet=True)
            print(f"{value:>34} {result['blobs_per_frame']:12.1f} {result['distinct_shots']:7d} "
                  f"{result['confirmed_pct']:14.1f}% {result['ms']:6.2f}ms")
        print("\nPick the row with the fewest blobs/frame that still finds every real shot.")
        return

    config = load_config(parse_overrides(args.overrides))
    started = time.time()
    result = run(args.input, config, detector, output=args.out, limit=args.limit, fps=args.fps)

    print()
    print("=" * 58)
    print(f"frames processed        : {result['frames']}")
    print(f"raw blobs per frame     : {result['blobs_per_frame']:.2f}   (the noise floor)")
    print(f"distinct shots tracked  : {result['distinct_shots']}")
    print(f"frames with a shot      : {result['confirmed_frames']} ({result['confirmed_pct']:.1f}%)")
    print(f"tracker cost per frame  : {result['ms']:.2f} ms")
    print(f"wall clock              : {time.time() - started:.1f} s")
    print("=" * 58)
    if args.out:
        print(f"\nAnnotated video written to {args.out}")


if __name__ == "__main__":
    main()
