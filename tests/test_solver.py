"""Dodge geometry, and how it treats walls.

The regression this guards against: walls used to be priced as a penalty added
to a direction's own score. Both settings of that penalty were wrong. Small, and
the solver picked a wall over a clean escape. Large, and a walled direction cost
more than several simultaneous hits - so once the wall model started seeing the
whole screen instead of a box around the player, most of the circle looked
catastrophic and the solver reported no escape at all.

A blocked direction is not expensive. It is exactly as good as not moving,
because that is what happens: the stick goes over and the brawler stays put.
"""

import math
import sys

from _harness import Failures

from dodge.config import DodgeConfig
from dodge.solver import DodgeSolver
from utils import JOYSTICK_RADIUS, load_toml_as_dict


class Shot:
    """A projectile heading at the player from `angle`, arriving in `eta`."""

    def __init__(self, angle, speed=1200.0, eta=0.55, radius=18.0, track_id=1):
        self.id = track_id
        self.vx = -math.cos(angle) * speed
        self.vy = -math.sin(angle) * speed
        distance = speed * eta
        self.x = 960.0 + math.cos(angle) * distance
        self.y = 540.0 + math.sin(angle) * distance
        self.radius = radius
        self.confidence = 0.9

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)


PLAYER = (960.0, 540.0)


def config():
    return DodgeConfig(load_toml_as_dict("cfg/dodge_config.toml"), 1.0, 54.0)


def blocker(*blocked_angles, spread=0.9):
    """is_blocked() that refuses headings near the given angles."""
    def is_blocked(vector):
        length = math.hypot(vector[0], vector[1])
        if length < 1e-6:
            return False
        for angle in blocked_angles:
            dot = (vector[0] / length) * math.cos(angle) + (vector[1] / length) * math.sin(angle)
            if dot > spread:
                return True
        return False
    return is_blocked


def solve(is_blocked=None, shots=None, now=1000.0):
    solver = DodgeSolver(config())
    return solver.solve(
        shots if shots is not None else [Shot(0.0)],
        PLAYER, 53.0, None, is_blocked, player_speed=330.0, now=now,
        collect_analysis=True,
    )


def heading(decision):
    if not decision.vector:
        return None
    return math.atan2(decision.vector[1], decision.vector[0])


def main():
    report = Failures("dodge solver")

    report.section("an unobstructed shot must produce an escape")
    decision = solve()
    report.check("a dodge is issued", decision.active, True)
    report.check("and it expects to be clear", decision.hit_count, 0)

    report.section("a wall must not be chosen over open ground")
    # Everything free; the natural sidestep is perpendicular to the shot.
    free = solve().vector
    # Now block the direction that was chosen and check it moves elsewhere.
    chosen = math.atan2(free[1], free[0])
    decision = solve(is_blocked=blocker(chosen))
    report.check("still dodges", decision.active, True)
    if decision.vector:
        moved = math.atan2(decision.vector[1], decision.vector[0])
        apart = abs((moved - chosen + math.pi) % (2 * math.pi) - math.pi)
        report.at_least("picks a different heading (radians apart)",
                        round(apart, 2), 0.3)

    report.section("most of the circle walled - it must still find the gap")
    # Sixteen candidate directions; block all but a narrow window. This is the
    # shape that made the old penalty model give up: with the wall model seeing
    # the whole screen, 22-36 boxes at once, a large share of the circle is
    # blocked in ordinary play.
    # Free window centred on +90 degrees, which is a real sidestep for a shot
    # arriving along the x axis. Everything else is walled.
    free = math.pi / 2
    blocked = [free + math.pi / 8 * i for i in range(2, 15)]
    decision = solve(is_blocked=blocker(*blocked, spread=0.97))
    report.check("does not report the escape impossible", decision.active, True)

    report.section("boxed in on every side")
    decision = solve(is_blocked=lambda v: math.hypot(v[0], v[1]) > 1e-6)
    # Nothing can help, so it must not pretend: no vector, and the log should
    # record it rather than claiming a dodge happened.
    report.check("no dodge is faked", decision.active, False)

    report.section("a walled direction scores as standing still, not worse")
    solver = DodgeSolver(config())
    decision = solver.solve([Shot(0.0)], PLAYER, 53.0, None,
                            blocker(math.pi), player_speed=330.0, now=1000.0,
                            collect_analysis=True)
    candidates = decision.analysis["candidates"]
    stay = decision.analysis["stay_score"]
    walled = [c for c in candidates if c["wall"]]
    report.at_least("the test actually blocked something", len(walled), 1)
    if walled and stay is not None:
        worst = max(c["score"] for c in walled)
        # Equal to standing still, give or take the tie-breaking epsilon.
        report.at_most("a walled direction never costs more than staying",
                       round(worst - stay, 2), 0.01)

    report.section("a shot that cannot be escaped must still be leaned away from")
    # Bea, measured off a real session: her shot arrives in about 0.16 s, and
    # stepping the hitbox clear takes about 0.37 s. There is no escape and
    # there never was - but the solver used to answer that by standing
    # perfectly still, which is how the bot ate two of them without moving.
    hopeless = Shot(0.0, speed=3040.0, eta=0.16)
    decision = solve(shots=[hopeless])
    report.check("it still moves", decision.active, True)
    report.at_least("and admits it expects to be hit", decision.hit_count, 1)

    if decision.vector:
        # Leaning across the shot beats leaning into it.
        along = (decision.vector[0] * hopeless.vx + decision.vector[1] * hopeless.vy)
        report.at_most("it does not step into the shot", round(along), 0)

    report.section("near-miss ranking must not override a real escape")
    # A shot with room to spare: the clean direction must still win outright.
    decision = solve(shots=[Shot(0.0, eta=0.7)])
    report.check("a clean escape is still preferred", decision.hit_count, 0)

    check_gas_veto(report)
    return report.finish()


def check_gas_veto(report):
    """Poison must outrank a projectile, even on the emergency path.

    That path exists to skip the playstyle when a shot is milliseconds away,
    which also skips the playstyle's own gas veto. A shot costs a chunk of
    health once; standing in gas costs a chunk every tick, and the bot would
    have walked in on purpose.
    """
    import play as play_module

    report.section("an escape into poison must be refused")

    reader = play_module.Play.escape_leads_into_gas

    class Stub:
        pass

    stub = Stub()
    stub.last_gas_reading = {"up": 9000, "down": 0, "left": 0, "right": 0}
    report.check("north, with gas to the north", reader(stub, (0.0, -100.0)), True)
    report.check("south, away from it", reader(stub, (0.0, 100.0)), False)
    report.check("east, unaffected", reader(stub, (100.0, 0.0)), False)
    report.check("north-east still touches it", reader(stub, (70.0, -70.0)), True)

    stub.last_gas_reading = {"up": 0, "down": 0, "left": 0, "right": 0}
    report.check("no gas anywhere, nothing refused", reader(stub, (0.0, -100.0)), False)

    stub.last_gas_reading = None
    report.check("no reading yet, nothing refused", reader(stub, (0.0, -100.0)), False)

    # And the service must consult it rather than firing regardless.
    from dodge.config import DodgeConfig
    from dodge.service import DodgeService

    class Controller:
        scale_factor = 1.0

        def __init__(self):
            self.moves = []

        def move_with_priority(self, x, y, hold=None):
            self.moves.append((x, y))

        def get_latest_frame(self):
            return None, 0.0

    cfg = config()
    cfg.threaded = False
    cfg.log_enabled = False
    controller = Controller()
    service = DodgeService(controller, config=cfg)
    service._apply_emergency((0.0, -100.0))
    report.check("the emergency path can move the stick at all",
                 len(controller.moves), 1)


if __name__ == "__main__":
    sys.exit(main())
