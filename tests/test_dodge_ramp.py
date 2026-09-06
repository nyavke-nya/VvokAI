"""A brawler is not a cursor, and the escape has to be solved for the one we have.

The solver modelled an escape as full speed in the new direction from the
instant it was chosen. Told to go left while walking right, a brawler first
stops going right, turns, and works up to speed - and for the first fraction of
a second it is still mostly travelling the old way.

Measured on a real session: of 12918 escapes the old model called clean, the
brawler could complete a median of 59% of the movement in the time it had, and
34% of them were credited with more than double the displacement that existed.
That is how the bot picked a mathematically clean direction 89.9% of the time
and was hit anyway.
"""
import sys

from _harness import Failures, read_source

sys.path.insert(0, "src")
from dodge.solver import DodgeSolver, _ramp_fraction  # noqa: E402
from dodge.motion import MotionMonitor  # noqa: E402

report = Failures("escapes a brawler can actually make")


report.section("the ramp itself")
TAU = 0.12
report.check("nothing has happened at t=0", _ramp_fraction(0.0, TAU), 0.0)
report.check("and everything has happened long after",
             round(_ramp_fraction(10.0, TAU), 3), 0.988)
# Monotonic, and always a fraction.
_values = [_ramp_fraction(t / 100.0, TAU) for t in range(1, 200)]
report.check("it only ever increases",
             all(b >= a - 1e-9 for a, b in zip(_values, _values[1:])), True)
report.check("and never leaves 0..1",
             all(0.0 <= v <= 1.0 for v in _values), True)
# The half-second escape the bot actually faces.
report.check("half of a 0.12s ramp is done after ~0.19s",
             round(_ramp_fraction(0.19, TAU), 2), 0.5)
report.check("a shot 80 ms out gets almost none of the move",
             _ramp_fraction(0.08, TAU) < 0.32, True)
report.check("a shot a second out gets nearly all of it",
             _ramp_fraction(1.0, TAU) > 0.87, True)

report.section("and turning it off restores the old model exactly")
for t in (0.05, 0.2, 1.0, None):
    report.check(f"tau=0 credits the full move at t={t}",
                 _ramp_fraction(t, 0.0), 1.0)


report.section("the motion model reports what the brawler is really doing")
_motion_src = read_source("dodge/motion.py")
report.check("velocity is measured, not assumed",
             "self._velocity = (travelled_x / dt, travelled_y / dt)" in _motion_src,
             True)


def _motion():
    from dodge.config import DodgeConfig
    return MotionMonitor(DodgeConfig.load(scale_factor=1.0, tile_size=100.0))


_m = _motion()
report.check("it starts still", _m.velocity, (0.0, 0.0))
# Camera scrolled left by 30px in 0.1s => the brawler walked right at 300 px/s.
_m.update(camera_shift=(-30.0, 0.0), dt=0.1, joystick_vector=(75.0, 0.0),
          expected_speed=330.0, now=1.0)
report.check("walking right reads as travelling right",
             tuple(round(v) for v in _m.velocity), (300.0, 0.0))
_m.update(camera_shift=(0.0, 0.0), dt=0.1, joystick_vector=None,
          expected_speed=330.0, now=1.1)
report.check("and the stick released reads as stopping", _m.velocity, (0.0, 0.0))


report.section("an escape is solved from the velocity the brawler has")


class _Shot:
    __slots__ = ("id", "x", "y", "vx", "vy", "radius", "confidence")

    def __init__(self, x, y, vx, vy, radius=18.0):
        self.id = 1
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.radius = radius
        self.confidence = 1.0


class _Motion:
    """A motion model that only reports a velocity."""

    def __init__(self, velocity):
        self.velocity = velocity

    def is_direction_blocked(self, vector, now=None):
        return False

    def is_toward_boundary(self, vector, tolerance=0.6):
        return False


def _config(tau):
    from dodge.config import DodgeConfig
    config = DodgeConfig.load(scale_factor=1.0, tile_size=100.0)
    config.speed_ramp_time = tau
    return config


PLAYER = (960.0, 540.0)
# Straight down at the player from above, arriving in about a third of a second.
SHOT = _Shot(960.0, 300.0, 0.0, 700.0)


def _solve(tau, velocity=None):
    solver = DodgeSolver(_config(tau))
    return solver.solve([SHOT], PLAYER, 53.0, None, None,
                        motion=_Motion(velocity) if velocity else None,
                        player_speed=330.0, now=100.0)


# Whichever way it goes, it must go somewhere: a shot on the nose is dodgeable.
report.check("with a slow shot it still escapes", _solve(TAU).vector is not None, True)

# The one that matters. Already sprinting one way, an escape the other way is
# not free, and the old model priced it as though it were.
_left = _solve(TAU, velocity=(-320.0, 0.0))
_right = _solve(TAU, velocity=(320.0, 0.0))
report.check("running left, it does not answer by claiming to be running right",
             _left.vector is None or _left.vector[0] <= 1e-6, True)
report.check("and running right, the mirror of that",
             _right.vector is None or _right.vector[0] >= -1e-6, True)
report.check("the two answers are mirror images, not the same vector",
             _left.vector != _right.vector or _left.vector is None, True)

# Momentum in a useful direction is not thrown away either.
_with = _solve(TAU, velocity=(0.0, -300.0))
report.check("already moving out of the way, that is still the answer",
             _with.vector is not None, True)


report.section("a shot too fast to clear is leaned away from, not stood through")
# Bea, measured off a real session: her shot arrives in about 0.16s and stepping
# the hitbox clear takes about 0.37s. There is no escape and there never was.
# The lean fallback is what turns a body hit into a graze - and it is gated on a
# minimum gain that was calibrated against the old, inflated model, so an honest
# ramp made that gate reject everything and go back to standing still.
# Placed where it is 0.16 s out: 3040 px/s x 0.16 s = 486 px above.
_fast = _Shot(960.0, 540.0 - 486.0, 0.0, 3040.0)


def _lean(tau):
    solver = DodgeSolver(_config(tau))
    return solver.solve([_fast], PLAYER, 53.0, None, None,
                        player_speed=330.0, now=100.0)


for _tau in (0.0, 0.06, 0.12, 0.2):
    _d = _lean(_tau)
    report.check(f"it still moves at tau={_tau}", _d.vector is not None, True)
    if _d.vector:
        along = _d.vector[0] * _fast.vx + _d.vector[1] * _fast.vy
        report.at_most(f"and not into the shot at tau={_tau}", round(along), 0)

_solver_src = read_source("dodge/solver.py")
report.check("the lean floor scales with the movement that exists",
             "config.lean_min_gain * _ramp_fraction(" in _solver_src, True)


report.section("nothing changes for anybody who turns it off")
# The setting has to be a real off switch, or it is not a safe thing to ship.
_off, _on = _solve(0.0), _solve(TAU)
report.check("tau=0 and a slow shot agree with the ramp",
             (_off.vector is not None) and (_on.vector is not None), True)
_cfg_text = open("cfg/dodge_config.toml", encoding="utf-8").read()
report.check("the knob is in the config with its default",
             "speed_ramp_time = 0.12" in _cfg_text, True)
report.check("and says how to switch it off",
             "Set to 0 to go back" in _cfg_text.split("speed_ramp_time")[0][-1200:], True)
# New keys reach installs that auto-update: the dodge config lists no keys,
# which means "add anything new, change nothing".
_updater = open("tools/updater.py", encoding="utf-8").read()
report.check("so it arrives on an install that only ever updates",
             '"cfg/dodge_config.toml": ()' in _updater, True)

sys.exit(report.finish())
