"""Movement feel.

Two modes, and the contrast between them is the point:

  glide  - the default. The joystick vector is eased toward the target and its
           rotation rate is capped, so direction changes come out as arcs. A
           bot that snaps between eight compass directions is obvious to any
           human watching; one that curves is not.

  snap   - used only while dodging. Easing is bypassed entirely and the stick
           jumps to the escape vector in a single frame, because a projectile
           does not wait for a smooth interpolation.
"""

import math
import time


class MovementShaper:
    def __init__(self, config):
        self.config = config
        self._current = (0.0, 0.0)
        self._last_update = None
        self._recover_until = 0.0
        self._was_sharp = False

    def reset(self):
        self._current = (0.0, 0.0)
        self._last_update = None
        self._recover_until = 0.0
        self._was_sharp = False

    @property
    def current(self):
        return self._current

    def shape(self, target, sharp=False, now=None):
        """Return the joystick vector to actually send this frame.

        `target` is what the playstyle asked for, or None to stop. `sharp`
        bypasses all easing.
        """
        config = self.config
        now = time.time() if now is None else now

        if self._last_update is None:
            self._last_update = now
            dt = 0.0
        else:
            dt = max(now - self._last_update, 0.0)
            self._last_update = now

        goal = (0.0, 0.0) if target is None else (float(target[0]), float(target[1]))

        if sharp and config.sharp_on_dodge:
            self._current = goal
            self._was_sharp = True
            self._recover_until = now + config.recover_time
            return goal if target is not None else None

        if not config.smoothing_enabled or dt <= 0.0:
            self._current = goal
            return goal if target is not None else None

        if self._was_sharp:
            self._was_sharp = False

        # Coming out of a dodge, close the gap somewhat faster than usual so the
        # bot does not coast on the escape vector after the danger has passed.
        # Only somewhat: at 0.4 the return was itself a snap, which defeats the
        # point of the contrast - the dodge should be the only sharp motion.
        tau = config.smooth_tau
        if now < self._recover_until:
            tau *= 0.7

        alpha = 1.0 - math.exp(-dt / tau)
        eased = (
            self._current[0] + (goal[0] - self._current[0]) * alpha,
            self._current[1] + (goal[1] - self._current[1]) * alpha,
        )
        eased = self._limit_turn(self._current, eased, dt)
        self._current = eased

        if target is None and math.hypot(eased[0], eased[1]) < 1.5:
            # Close enough to centre that the stick may as well be released.
            self._current = (0.0, 0.0)
            return None

        return eased

    def _limit_turn(self, previous, proposed, dt):
        max_delta = self.config.max_turn_rate * dt
        if max_delta <= 0.0:
            return proposed

        previous_length = math.hypot(previous[0], previous[1])
        proposed_length = math.hypot(proposed[0], proposed[1])
        # Nothing to rotate away from, or the vector is collapsing to centre.
        if previous_length < 1e-3 or proposed_length < 1e-3:
            return proposed

        previous_angle = math.atan2(previous[1], previous[0])
        proposed_angle = math.atan2(proposed[1], proposed[0])
        delta = (proposed_angle - previous_angle + math.pi) % (2.0 * math.pi) - math.pi
        if abs(delta) <= max_delta:
            return proposed

        angle = previous_angle + math.copysign(max_delta, delta)
        return (
            math.cos(angle) * proposed_length,
            math.sin(angle) * proposed_length,
        )
