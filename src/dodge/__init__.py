"""Projectile tracking and dodge solving for PylaAI.

The package is deliberately split so that vision and tactics never mix:

    palette.py   terrain colour model used to separate projectiles from the map
    tracker.py   camera-compensated motion detection -> confirmed projectiles
    solver.py    geometry: will this hit me, and where do I go instead
    smoothing.py joystick easing, plus the sharp bypass used while dodging
    service.py   optional background thread running the tracker at full FPS

Only `service.DodgeService` is meant to be used from the rest of the bot.
"""

from dodge.config import DodgeConfig
from dodge.solver import DodgeSolver, Threat
from dodge.tracker import Projectile, ProjectileTracker
from dodge.service import DodgeService

__all__ = [
    "DodgeConfig",
    "DodgeService",
    "DodgeSolver",
    "Projectile",
    "ProjectileTracker",
    "Threat",
]
