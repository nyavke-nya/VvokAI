"""Health-bar reading.

Two things are being defended here, and the second is the important one.

Accuracy is easy: draw a bar at a known fraction and check what comes back.

Not hallucinating is hard. Map grass is green, saturated and bright, which is
also a description of an ally's health bar - and with colour thresholds alone
this reader called 25 of 40 patches of ordinary grass a full health bar, at
full confidence. A bot that believes it is always at 100% is worse than one
with no health reading at all, so the false-positive count is asserted at zero.
"""

import sys
import time

from _harness import Failures

import cv2
import numpy as np

from dodge.config import DodgeConfig
from dodge.vitals import HealthReader
from utils import load_toml_as_dict

W, H = 1920, 1080


def make_map(rng):
    frame = np.full((H, W, 3), (196, 168, 120), dtype=np.uint8)   # desert sand, RGB
    noise = rng.integers(-20, 20, (H, W, 1), dtype=np.int16)
    frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    for _ in range(70):
        centre = (int(rng.integers(0, W)), int(rng.integers(0, H)))
        cv2.circle(frame, centre, int(rng.integers(20, 70)), (72, 150, 66), -1)
    for _ in range(40):
        centre = (int(rng.integers(0, W)), int(rng.integers(0, H)))
        cv2.circle(frame, centre, int(rng.integers(10, 30)), (140, 120, 96), -1)
    return frame


def draw_brawler(frame, cx, cy, fraction, hostile, box_w=90, box_h=120):
    """A sprite with a health bar above it, the way the game draws them."""
    x1, y1 = cx - box_w // 2, cy - box_h // 2
    x2, y2 = cx + box_w // 2, cy + box_h // 2
    cv2.rectangle(frame, (x1, y1), (x2, y2), (60, 60, 70), -1)
    cv2.circle(frame, (cx, y1 + 30), 22, (210, 190, 170), -1)

    bar_w = int(box_w * 0.92)
    bar_h = max(5, int(box_h * 0.09))
    bx1 = cx - bar_w // 2
    by1 = y1 - int(box_h * 0.30)
    bx2, by2 = bx1 + bar_w, by1 + bar_h

    cv2.rectangle(frame, (bx1 - 2, by1 - 2), (bx2 + 2, by2 + 2), (18, 18, 22), -1)
    bright = (235, 60, 55) if hostile else (70, 220, 90)
    spent = (86, 26, 24) if hostile else (28, 80, 36)
    cv2.rectangle(frame, (bx1, by1), (bx2, by2), spent, -1)
    filled_end = bx1 + int(round(bar_w * fraction))
    if filled_end > bx1:
        cv2.rectangle(frame, (bx1, by1), (filled_end, by2), bright, -1)
    return [x1, y1, x2, y2]


def main():
    report = Failures("health reader")
    rng = np.random.default_rng(11)
    config = DodgeConfig(load_toml_as_dict("cfg/dodge_config.toml"), 1.0, 54.0)
    reader = HealthReader(config)

    report.section("accuracy across the range, both teams")
    for hostile in (False, True):
        side = "enemy" if hostile else "ally"
        for truth in (1.0, 0.9, 0.75, 0.5, 0.35, 0.2, 0.08):
            frame = make_map(rng)
            box = draw_brawler(frame, 700, 500, truth, hostile)
            reading = reader.read(frame, box, hostile)
            report.near(f"{side} at {truth:.0%}",
                        reading.fraction, truth, 0.05)

    report.section("no bar drawn at all - grass must not be read as health")
    false_positives = 0
    for _ in range(40):
        frame = make_map(rng)
        cx = int(rng.integers(300, W - 300))
        cy = int(rng.integers(300, H - 300))
        box = [cx - 45, cy - 60, cx + 45, cy + 60]
        cv2.rectangle(frame, (box[0], box[1]), (box[2], box[3]), (60, 60, 70), -1)
        if reader.read(frame, box, False).known:
            false_positives += 1
    report.at_most("false positives on plain map", false_positives, 0)

    report.section("distant brawlers, whose boxes are much smaller")
    for box_w, box_h in ((90, 120), (64, 86), (48, 64), (34, 46)):
        frame = make_map(rng)
        box = draw_brawler(frame, 700, 500, 0.6, True, box_w, box_h)
        report.near(f"box {box_w}x{box_h}",
                    reader.read(frame, box, True).fraction, 0.6, 0.06)

    report.section("bars that touch scenery of the same colour")
    for truth in (0.85, 0.4, 0.08):
        frame = make_map(rng)
        box = draw_brawler(frame, 700, 500, truth, False)
        # Bushes growing right up against both ends of an ally's green bar.
        cv2.circle(frame, (700 - 88, 500 - 34), 44, (72, 150, 66), -1)
        cv2.circle(frame, (700 + 90, 500 - 34), 44, (72, 150, 66), -1)
        report.near(f"ally at {truth:.0%} between two bushes",
                    reader.read(frame, box, False).fraction, truth, 0.06)

    report.section("two brawlers stacked, the near one must be measured")
    frame = make_map(rng)
    draw_brawler(frame, 700, 380, 0.25, True)
    box = draw_brawler(frame, 700, 560, 0.85, True)
    report.near("front brawler", reader.read(frame, box, True).fraction, 0.85, 0.06)

    report.section("cost")
    frame = make_map(rng)
    box = draw_brawler(frame, 700, 500, 0.7, True)
    started = time.perf_counter()
    for _ in range(300):
        reader.read(frame, box, True)
    per_call = (time.perf_counter() - started) / 300 * 1000
    print(f"  {per_call:.3f} ms per brawler")
    # Read once per bot iteration for a handful of brawlers; anything under a
    # millisecond each is free at the rates this runs at.
    report.at_most("ms per brawler", round(per_call, 2), 2.0)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
