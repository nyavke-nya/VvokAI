"""Save training frames from inside the playing bot, projectiles included.

Everything else in a Brawl Stars frame can be labelled afterwards from a
screenshot, because brawlers and walls sit still long enough to be seen. A
projectile cannot. What makes it a projectile is that it is moving in a
straight line while the world behind it is not, and that is only visible in
the gap between two frames taken milliseconds apart.

Two attempts at collecting them from outside failed, and both failed the same
way. A screenshot every couple of seconds never catches the same shot twice.
Android's screenrecord gives twenty frames a second, and at that spacing the
bot's own tracker returns thirteen thousand motion blobs over twenty seconds -
almost all of them on animated interface text, because at 50 ms apart the
scoreboard has changed as much as a fireball has.

The tracker is not at fault; it was built for eighty frames a second and it
works at eighty. So rather than rebuild it or fake its input, this asks the
one process that already has frames at that rate and already runs the tracker
over every one of them to write down what it found. No second capture, no
second tracker, no extra inference: the work is already being done and the
answer is already in memory. This only spends a JPEG.

Off unless switched on in the config, and when off it costs one comparison per
frame. When on it saves at most one frame every few seconds, and only frames
that actually had a shot in flight - a picture with no projectile teaches a
projectile detector nothing, and there are already six thousand of those.
"""

import json
import threading
import time
from pathlib import Path

import cv2

# Class ids shared with the offline collector, so the two sets merge.
PLAYER, TEAMMATE, ENEMY, WALL, BUSH, CLOSE_BUSH, PROJECTILE = range(7)
CLASS_NAMES = ["player", "teammate", "enemy", "wall", "bush", "close_bush",
               "projectile", "map_border"]
BOX_KEYS = {"player": PLAYER, "teammate": TEAMMATE, "enemy": ENEMY}

DEFAULTS = {
    "dataset_capture": False,
    "dataset_capture_dir": "dataset_live",
    # A shot stays on screen for well under a second, so frames closer than
    # this show the same one barely moved.
    "dataset_capture_interval": 3.0,
    "dataset_capture_max_frames": 4000,
    "dataset_capture_quality": 92,
    # Below this world speed, in pixels a second, it is not a shot. The bot
    # tolerates the difference because a spurious dodge costs it almost
    # nothing, so it never had to mask the interface out - and the interface
    # is what the tracker latches onto between matches. A kill feed slides in,
    # a banner wipes across, a name tag follows a brawler, and all of it is
    # movement. None of it travels at eight hundred pixels a second.
    "dataset_capture_min_speed": 400.0,
    # And only while a match is actually being played. The intro banner and
    # the results screen are nothing but moving interface.
    "dataset_capture_states": "match",
}

_settings = None
_settings_read = 0.0
_lock = threading.Lock()
_state = {"last": 0.0, "kept": 0, "counted": False}


def settings():
    """Config, re-read occasionally so it can be switched on without a restart."""
    global _settings, _settings_read
    now = time.time()
    if _settings is None or now - _settings_read > 30:
        values = dict(DEFAULTS)
        try:
            from utils import config_bool, load_toml_as_dict
            config = load_toml_as_dict("cfg/bot_config.toml")
            for key, fallback in DEFAULTS.items():
                if key not in config:
                    continue
                if isinstance(fallback, bool):
                    values[key] = config_bool(config[key], fallback)
                elif isinstance(fallback, str):
                    values[key] = str(config[key])
                else:
                    values[key] = type(fallback)(config[key])
        except Exception:
            pass
        _settings, _settings_read = values, now
    return _settings


def _folders(config):
    root = Path(config["dataset_capture_dir"])
    (root / "images").mkdir(parents=True, exist_ok=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    marker = root / "data.yaml"
    if not marker.exists():
        names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASS_NAMES))
        marker.write_text(
            "# Frames saved by the bot while playing. Projectiles here are\n"
            "# labelled by the same tracker the bot dodges with, at the frame\n"
            "# rate it actually runs at.\n"
            f"path: {root.resolve().as_posix()}\n"
            f"train: images\nval: images\nnc: {len(CLASS_NAMES)}\n"
            f"names:\n{names}\n", encoding="utf-8")
    return root


def _rows(debug_data, projectiles, width, height, min_speed=0.0):
    """Every box worth writing, as YOLO lines."""
    lines = []

    def add(class_id, x1, y1, x2, y2):
        x1, x2 = max(0.0, min(x1, x2)), min(float(width), max(x1, x2))
        y1, y2 = max(0.0, min(y1, y2)), min(float(height), max(y1, y2))
        if x2 - x1 < 3 or y2 - y1 < 3:
            return
        lines.append(f"{class_id} "
                     f"{(x1 + x2) / 2 / width:.6f} {(y1 + y2) / 2 / height:.6f} "
                     f"{(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}")

    for shot in projectiles:
        speed = (float(getattr(shot, "vx", 0.0)) ** 2 +
                 float(getattr(shot, "vy", 0.0)) ** 2) ** 0.5
        if speed < min_speed:
            continue
        radius = max(6.0, float(getattr(shot, "radius", 0.0)))
        x, y = float(shot.x), float(shot.y)
        add(PROJECTILE, x - radius, y - radius, x + radius, y + radius)

    # The brawlers too, from the detections this frame already produced, so
    # the label is complete rather than saying a crowded screen holds one
    # fireball and nothing else.
    for key, class_id in BOX_KEYS.items():
        for box in (debug_data or {}).get(key) or []:
            if len(box) >= 4:
                add(class_id, float(box[0]), float(box[1]),
                    float(box[2]), float(box[3]))
    return lines


def capture(frame, debug_data, projectiles):
    """Called from the loop with the frame it just worked on.

    Returns immediately unless capture is on, a shot is in flight, and enough
    time has passed since the last one. Nothing here blocks the loop: the
    write is a JPEG of a frame already in memory, a few milliseconds, and it
    happens at most once every few seconds.
    """
    config = settings()
    if not config["dataset_capture"] or frame is None or not projectiles:
        return

    wanted = str(config["dataset_capture_states"]).split(",")
    if str((debug_data or {}).get("state", "")).strip() not in wanted:
        return

    now = time.time()
    with _lock:
        if now - _state["last"] < float(config["dataset_capture_interval"]):
            return
        if _state["kept"] >= int(config["dataset_capture_max_frames"]):
            return
        _state["last"] = now

    try:
        root = _folders(config)
        if not _state["counted"]:
            # Count what a previous run left, so a restart adds rather than
            # overwrites. Done once, here, because doing it at import time
            # would touch the disk on every bot start whether on or not.
            _state["kept"] = len(list((root / "images").glob("*.jpg")))
            _state["counted"] = True

        height, width = frame.shape[:2]
        lines = _rows(debug_data, projectiles, width, height,
                      float(config["dataset_capture_min_speed"]))
        if not any(line.startswith(f"{PROJECTILE} ") for line in lines):
            return

        name = f"live_{_state['kept']:06d}"
        cv2.imwrite(str(root / "images" / f"{name}.jpg"),
                    cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                    [int(cv2.IMWRITE_JPEG_QUALITY),
                     int(config["dataset_capture_quality"])])
        (root / "labels" / f"{name}.txt").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        with open(root / "log.jsonl", "a", encoding="utf-8") as journal:
            journal.write(json.dumps({
                "frame": name, "at": round(now, 1),
                "projectiles": sum(1 for line in lines
                                   if line.startswith(f"{PROJECTILE} ")),
                "boxes": len(lines),
            }) + "\n")
        _state["kept"] += 1
    except Exception:
        # Collecting training data is never worth a dropped match.
        pass


def kept():
    return _state["kept"]
