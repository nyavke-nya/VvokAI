"""Ground hazards: things a thrower leaves lying on the map.

Tick's mines, Barley's and Grom's puddles, Sprout's hedge, Amber's fire. They
arrive as a lobbed shot and then sit there doing damage, at which point the
projectile tracker stops recognising them - which is why the bot used to walk
straight through them.

The promotion rule is the whole design, and it is deliberately not colour:

    a hazard must ALREADY have been a confirmed projectile, and then stop.

Colour is the obvious approach and that is precisely why it is avoided here.
Tick's mines are pink and so is plenty of map art; fire is orange and so is
desert sand. The health reader made the case: with colour gating alone it
called 25 of 40 patches of ordinary grass a full health bar, at full
confidence. Requiring a history - born beside an enemy, flew away in a straight
line, passed confirmation, then stopped - is something no scenery can fake,
because scenery never arrives.

So most of what follows is about what must NOT become a hazard.
"""

import math
import sys

from _harness import Failures

import numpy as np

from dodge.config import DodgeConfig
from dodge.tracker import FrameContext, ProjectileTracker
from utils import load_toml_as_dict

W, H = 1920, 1080
PLAYER = [W / 2 - 40, H / 2 - 55, W / 2 + 40, H / 2 + 55]
ENEMY = [1455.0, 240.0, 1545.0, 360.0]


def tracker():
    config = DodgeConfig(load_toml_as_dict("cfg/dodge_config.toml"), 1.0, 54.0)
    return ProjectileTracker(config), config


def context():
    return FrameContext(player_box=PLAYER, enemies=[ENEMY], stamp=0.0)


def feed(track, blobs_by_frame, fps=30.0):
    """Drive association and collection over a scripted blob sequence."""
    stamp = 1000.0
    ctx = context()
    for blobs in blobs_by_frame:
        track._associate(blobs, stamp, ctx)
        track._collect(stamp, ctx)
        track._age_hazards((0.0, 0.0), 1, stamp)
        stamp += 1.0 / fps
    return stamp


def thrown_then_landed(speed=900.0, fly_frames=8, rest_frames=8, fps=30.0):
    """A shot leaving the enemy, flying at the player, then stopping dead."""
    dt = 1.0 / fps
    start = np.array([1500.0, 300.0])
    target = np.array([W / 2, H / 2])
    heading = (target - start) / np.linalg.norm(target - start)
    position = start + heading * 70.0

    frames = []
    for _ in range(fly_frames):
        frames.append([(position[0], position[1], 18.0, 0.9)])
        position = position + heading * speed * dt
    # Landed: same place every frame, blinking, so it keeps showing up.
    for _ in range(rest_frames):
        frames.append([(position[0], position[1], 18.0, 0.9)])
    return frames, position


def main():
    report = Failures("ground hazards")

    report.section("a thrown shot that lands becomes a hazard")
    track, config = tracker()
    frames, resting = thrown_then_landed()
    feed(track, frames)
    hazards = track.hazards()
    report.at_least("one is recorded", len(hazards), 1)
    if hazards:
        found = hazards[0]
        report.at_most("it sits where the shot stopped",
                       round(math.hypot(found.x - resting[0], found.y - resting[1])), 40)
        report.at_least("its radius covers more than the blob",
                        round(found.radius), round(config.hazard_min_radius) - 1)

    report.section("things that never flew must NOT become hazards")
    # Scenery that animates in place: a bush swaying, a decoration blinking.
    track, _ = tracker()
    feed(track, [[(800.0, 400.0, 18.0, 0.9)] for _ in range(30)])
    report.check("a blob that only ever sat still", len(track.hazards()), 0)

    # Jitter without travel - the same thing one or two pixels apart.
    track, _ = tracker()
    frames = []
    for index in range(30):
        wobble = 2.0 if index % 2 else -2.0
        frames.append([(800.0 + wobble, 400.0, 18.0, 0.9)])
    feed(track, frames)
    report.check("a blob that only jittered", len(track.hazards()), 0)

    # Flew, but never came from an enemy and never confirmed as a shot.
    track, _ = tracker()
    ctx = FrameContext(player_box=PLAYER, enemies=[], stamp=0.0)
    stamp = 1000.0
    position = np.array([300.0, 900.0])
    for _ in range(10):
        track._associate([(position[0], position[1], 18.0, 0.9)], stamp, ctx)
        track._collect(stamp, ctx)
        position = position + np.array([25.0, -8.0])
        stamp += 1 / 30.0
    for _ in range(10):
        track._associate([(position[0], position[1], 18.0, 0.9)], stamp, ctx)
        track._collect(stamp, ctx)
        stamp += 1 / 30.0
    report.check("a shot with no enemy behind it", len(track.hazards()), 0)

    report.section("one object, one hazard")
    track, _ = tracker()
    long_frames, _ = thrown_then_landed(rest_frames=25)
    feed(track, long_frames)
    report.at_most("re-detecting it every frame does not pile them up",
                   len(track.hazards()), 1)

    report.section("it expires")
    track, config = tracker()
    frames, _ = thrown_then_landed()
    stamp = feed(track, frames)
    report.at_least("present right after landing", len(track.hazards()), 1)
    track._age_hazards((0.0, 0.0), 1, stamp + config.hazard_ttl + 0.5)
    report.check("gone once it stops being seen", len(track.hazards()), 0)

    report.section("it stays put while the camera pans")
    track, _ = tracker()
    frames, resting = thrown_then_landed()
    stamp = feed(track, frames)
    before = track.hazards()[0]
    start = (before.x, before.y)
    # Camera pans right by 10 px a frame for ten frames; a world-fixed object
    # must slide the same way on screen, or the marker drifts off the mine.
    for index in range(10):
        track._age_hazards((10.0, 4.0), 1, stamp + index * 0.01)
    after = track.hazards()[0]
    report.check("moved with the world, not against it",
                 (round(after.x - start[0]), round(after.y - start[1])), (100, 40))

    report.section("the swept veto")
    from dodge.service import _segment_hits_circle
    report.check("a mine straight ahead is caught",
                 _segment_hits_circle((0, 0), (100, 0), (50, 0), 20), True)
    report.check("a mine halfway along is caught too",
                 _segment_hits_circle((0, 0), (100, 0), (40, 15), 20), True)
    report.check("one well off the path is not",
                 _segment_hits_circle((0, 0), (100, 0), (50, 80), 20), False)
    report.check("one behind the bot is not",
                 _segment_hits_circle((0, 0), (100, 0), (-60, 0), 20), False)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
