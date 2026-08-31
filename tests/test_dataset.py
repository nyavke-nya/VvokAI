"""A training label claims more than a dodge does, so it is filtered harder.

The bot's projectile tracker answers "is something dangerous coming at me".
It answers it well, and it is deliberately generous: a muzzle flash, an
explosion or a kill feed sliding across the screen all produce motion that
behaves like a shot for a moment, and dodging one of those costs the bot
almost nothing. That is why it never needed to mask the interface out.

A label says something stronger - "this region IS a projectile" - and every
one of those generous cases becomes a lie in the training set. Left alone
they teach a detector that a portrait with a skull on it is a fireball.

So the same tracker output goes through four gates on its way to a label, and
what is tested here is that each gate actually shuts. Measured on real frames
the bot saved while playing, they threw away twelve of twenty-nine boxes, and
every one thrown away was on the kill feed or on the name plate above a
brawler.

Also tested, and more important than any of it: that a fork which has not
switched this on collects nothing at all. It ships off, and off has to mean
no folder, no file and no measurable cost.
"""
import pathlib
import sys
import time

import numpy as np

from _harness import Failures

sys.path.insert(0, "src")
import dataset_capture as dc  # noqa: E402

report = Failures("dataset capture")


class Shot:
    """Stands in for a tracked projectile."""

    def __init__(self, x, y, vx=900.0, vy=100.0, radius=20.0,
                 confidence=0.9, hits=8):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.radius = radius
        self.confidence = confidence
        self.hits = hits


FRAME_W, FRAME_H = 1920, 1080
# A brawler in the middle right of the screen, with a name and trophy count
# floating above it the way the game draws them.
CONTEXT = {"state": "match", "player": [[900, 500, 1000, 600]],
           "teammate": [], "enemy": []}
ZONES = dc._parse_zones(dc.DEFAULTS["dataset_capture_ui_zones"])


def labelled(shot):
    lines = dc._rows(CONTEXT, [shot], FRAME_W, FRAME_H, 400.0, ZONES, 0.75, 5)
    return any(line.startswith("6 ") for line in lines)


report.section("what belongs in the set")

report.check("a tracked shot crossing open ground",
             labelled(Shot(600, 540)), True)
report.check("a fast one, at nearly twice the speed",
             labelled(Shot(700, 700, vx=1900, vy=600, confidence=0.85, hits=6)),
             True)


report.section("what does not, and why each one would poison it")

# Flashes and blasts look like a shot for two or three frames and then stop.
# A shot keeps going, so the number of frames it was actually followed for is
# what tells them apart.
report.check("an explosion, seen for two frames and gone",
             labelled(Shot(600, 540, hits=2)), False)
report.check("a track the tracker itself half believed",
             labelled(Shot(600, 540, confidence=0.4)), False)

# Interface animates, and it animates slowly.
report.check("something drifting at a tenth of shot speed",
             labelled(Shot(600, 540, vx=100, vy=0)), False)

# The kill feed is the one that actually appeared in the data: it slides in
# fast enough to pass a speed test and it is drawn over the play area.
report.check("the kill feed in the top left",
             labelled(Shot(150, 180)), False)
report.check("the buttons in the bottom right",
             labelled(Shot(1500, 900)), False)

# And the name plate rides with the brawler, so no fixed rectangle catches it.
report.check("the name and trophies above a brawler",
             labelled(Shot(950, 460)), False)


report.section("brawlers are still labelled, wherever they stand")

# The masks drop projectile boxes only. A brawler standing under the buttons
# is still a brawler, and dropping it would quietly teach the opposite.
_lines = dc._rows({"state": "match", "player": [[1500, 880, 1600, 980]],
                   "teammate": [], "enemy": []},
                  [], FRAME_W, FRAME_H, 400.0, ZONES, 0.75, 5)
report.check("a player standing in the button corner is kept",
             any(line.startswith("0 ") for line in _lines), True)


report.section("off means off, for every fork that did not ask for this")

# A folder name nothing else could have created, so "it exists" can only
# mean this module made it. The default name is a real folder on the machine
# this was written on, because the bot is collecting into it right now.
_never = pathlib.Path("dataset_should_not_appear")
dc._settings = dict(dc.DEFAULTS, dataset_capture_dir=str(_never))
dc._settings_read = time.time()
dc._state = {"last": 0.0, "kept": 0, "counted": False}

frame = (np.random.default_rng(5).random((360, 640, 3)) * 255).astype("uint8")
for _ in range(200):
    dc.capture(frame, CONTEXT, [Shot(600, 540)])

report.check("nothing was written", dc.kept(), 0)
report.check("and the output folder was never created", _never.exists(), False)

started = time.perf_counter()
for _ in range(20000):
    dc.capture(frame, CONTEXT, [Shot(600, 540)])
per_call_us = (time.perf_counter() - started) / 20000 * 1_000_000
report.at_most(f"microseconds per frame while off ({per_call_us:.2f})",
               per_call_us, 10.0)

raise SystemExit(report.finish())
