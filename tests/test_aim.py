"""Leading a moving target.

The bot's attack control is a stick: drag it and the shot goes where you
dragged, tap it and the game's own auto-aim fires at where the enemy IS. Against
anything that moves, that is a miss - so the whole point is to work out where
they will be when the shot arrives.

That needs a projectile speed, and the configured constant was wrong by more
than a factor of two. It said 2600 px/s while the tracker was measuring real
shots at 411-3040 with a median of 1152, so the lead came out at 0.186 s where
the honest answer was 0.432, and every shot at a moving enemy landed behind
them. No clamp was involved; the arithmetic was simply fed a bad number.
"""

import math
import sys

from _harness import Failures

from dodge.aim import AimSolver, EnemyTracker
from dodge.config import DodgeConfig
from dodge.tracker import FrameContext, ProjectileTracker
from utils import load_toml_as_dict


def config():
    return DodgeConfig(load_toml_as_dict("cfg/dodge_config.toml"), 1.0, 54.0)


def main():
    report = Failures("aim")
    cfg = config()
    solver = AimSolver(cfg)

    report.section("the lead must match the flight time, not a fixed guess")
    # Enemy running straight across at 300 px/s, at ordinary combat range.
    for distance, speed, expected in ((480, 1150, 0.43), (900, 1150, 0.7), (200, 2600, 0.08)):
        solution = solver.solve((0, 0), (distance, 0), (0.0, 300.0),
                                projectile_speed=speed, confidence=1.0)
        report.near(f"{distance}px at {speed}px/s", solution.flight_time, expected, 0.06)

    report.section("the ceiling must not clip an honest answer at normal range")
    solution = solver.solve((0, 0), (480, 0), (0.0, 300.0),
                            projectile_speed=cfg.aim_projectile_speed, confidence=1.0)
    report.check("a mid-range lead is reported at full confidence",
                 round(solution.confidence, 2), 1.0)
    report.at_least("and it is a real lead, not a token one",
                    round(solution.lead_distance), 100)

    report.section("a still target needs no lead")
    solution = solver.solve((0, 0), (480, 0), (0.0, 0.0),
                            projectile_speed=1150.0, confidence=1.0)
    report.check("no sideways offset", round(solution.lead_distance), 0)

    report.section("a shaky velocity estimate is only half trusted")
    full = solver.solve((0, 0), (480, 0), (0.0, 300.0), projectile_speed=1150.0,
                        confidence=1.0)
    half = solver.solve((0, 0), (480, 0), (0.0, 300.0), projectile_speed=1150.0,
                        confidence=0.5)
    report.near("half confidence leads half as far",
                half.lead_distance, full.lead_distance * 0.5, 5.0)

    report.section("an enemy fleeing faster than the shot is unreachable")
    report.check("no solution is invented",
                 solver.solve((0, 0), (480, 0), (2000.0, 0.0),
                              projectile_speed=1150.0, confidence=1.0), None)

    report.section("the speed is measured from our own shots")
    tracker = ProjectileTracker(cfg)
    report.check("nothing claimed before any are seen",
                 tracker.own_projectile_speed, None)

    # A shot leaving the player and travelling away, over several frames.
    player = [920.0, 485.0, 1000.0, 595.0]
    ctx = FrameContext(player_box=player, enemies=[], stamp=0.0)
    stamp = 1000.0
    truth = 1400.0
    for shot in range(8):
        x, y = 990.0, 520.0
        for _ in range(6):
            tracker._associate([(x, y, 16.0, 0.9)], stamp, ctx)
            tracker._collect(stamp, ctx)
            x += truth / 30.0
            stamp += 1 / 30.0
        stamp += 0.2
    measured = tracker.own_projectile_speed
    report.check("it has an answer now", measured is not None, True)
    if measured is not None:
        report.near("and it is the speed the shots actually flew at",
                    measured, truth, truth * 0.15)

    report.section("shots coming AT us must not be counted as ours")
    tracker = ProjectileTracker(cfg)
    ctx = FrameContext(player_box=player, enemies=[[1455, 240, 1545, 360]], stamp=0.0)
    stamp = 1000.0
    for shot in range(8):
        x, y = 1450.0, 300.0
        for _ in range(6):
            tracker._associate([(x, y, 16.0, 0.9)], stamp, ctx)
            tracker._collect(stamp, ctx)
            x -= 3000.0 / 30.0
            y += 600.0 / 30.0
            stamp += 1 / 30.0
        stamp += 0.2
    incoming = tracker.own_projectile_speed
    if incoming is not None:
        report.at_most("an enemy's 3000 px/s shot did not become our estimate",
                       round(incoming), 2000)
    else:
        report.check("nothing was measured from incoming fire", incoming, None)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())
