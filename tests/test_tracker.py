"""Track continuity.

The failure this guards against is the one that made the bot stop dodging: a
brand-new track has no measured velocity, so the size of its search gate is
guesswork, and when that guess was too small a fast shot could never acquire a
second sample. It stayed on screen as an unconfirmed candidate forever and the
solver never saw it.

The symptom in the log was subtle - shots WERE being confirmed, just constantly
re-confirmed under new ids, with the median confirmed track surviving two
frames. So this measures the thing that actually matters: how many distinct
ids one shot gets chopped into on its way to the player.
"""

import math
import sys

from _harness import Failures

import numpy as np

from dodge.config import DodgeConfig
from dodge.tracker import FrameContext, ProjectileTracker
from utils import load_toml_as_dict

W, H = 1920, 1080


def fly(speed, fps, noise_blobs, seed=3, seed_speed=None):
    """One shot flying from an enemy at the player, plus drifting noise.

    Drives the association and confirmation stages directly with synthetic
    blobs, so a change in the vision front-end cannot mask a regression here.
    """
    rng = np.random.default_rng(seed)
    config = DodgeConfig(load_toml_as_dict("cfg/dodge_config.toml"), 1.0, 54.0)
    if seed_speed is not None:
        config.seed_speed = seed_speed
    tracker = ProjectileTracker(config)

    dt = 1.0 / fps
    enemy = np.array([1500.0, 300.0])
    player = np.array([W / 2, H / 2])
    heading = player - enemy
    heading = heading / np.linalg.norm(heading)
    shot = enemy + heading * 70.0

    context = FrameContext(
        player_box=[player[0] - 40, player[1] - 55, player[0] + 40, player[1] + 55],
        enemies=[[enemy[0] - 45, enemy[1] - 60, enemy[0] + 45, enemy[1] + 60]],
        stamp=0.0,
    )

    stamp = 1000.0
    ids, longest, run, last, frames = [], 0, 0, None, 0

    while np.linalg.norm(shot - player) > 90 and frames < 60:
        blobs = [(shot[0], shot[1], 16.0, 0.9)]
        for _ in range(noise_blobs):
            blobs.append((
                float(rng.uniform(200, W - 200)),
                float(rng.uniform(150, H - 150)),
                float(rng.uniform(12, 24)),
                float(rng.uniform(0.3, 0.9)),
            ))
        rng.shuffle(blobs)

        tracker._associate(blobs, stamp, context)
        found = None
        for projectile in tracker._collect(stamp, context):
            if math.hypot(projectile.x - shot[0], projectile.y - shot[1]) < 70:
                found = projectile
                break

        if found is not None:
            ids.append(found.id)
            run = run + 1 if found.id == last else 1
            last = found.id
            longest = max(longest, run)
        else:
            run, last = 0, None

        shot = shot + heading * speed * dt
        stamp += dt
        frames += 1

    return {
        "seen": len(ids),
        "total": frames,
        "ids": len(set(ids)),
        "longest": longest,
    }


def main():
    report = Failures("tracker association")

    report.section("a shot must be followed as ONE track, at every speed and rate")
    for speed in (700, 1500, 2600):
        for fps in (12, 30, 60):
            result = fly(speed, fps, noise_blobs=6)
            label = f"{speed} px/s at {fps} fps"
            # Seen for most of its flight, and under a single identity.
            report.at_least(f"{label}: frames seen", result["seen"],
                            max(1, int(result["total"] * 0.7)))
            report.at_most(f"{label}: distinct ids", result["ids"], 1)

    report.section("heavy noise must not steal the shot's blob")
    for speed in (700, 2600):
        result = fly(speed, 30, noise_blobs=20)
        report.at_most(f"{speed} px/s with 20 noise blobs: ids", result["ids"], 2)
        report.at_least(f"{speed} px/s with 20 noise blobs: seen", result["seen"], 1)

    report.section("the seed gate is what makes fast shots visible at all")
    # seed_speed = 0 reproduces the old behaviour, where a one-sample track
    # could only search gate_base px. This asserts the regression stays fixed.
    old = fly(1500, 30, noise_blobs=0, seed_speed=0.0)
    new = fly(1500, 30, noise_blobs=0)
    report.check("1500 px/s at 30 fps was invisible before the fix",
                 old["seen"], 0)
    report.at_least("...and is tracked now", new["seen"], 6)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
