"""Map-boundary detection from the camera itself.

The wall detector already handles walls well. What it cannot see at all is the
edge of the map - there is no wall sprite there to detect, just the point past
which the arena stops. That is the gap this module fills.

The trick is that the camera is locked to the player only while the player can
still move. At the edge of the map the camera stops dead while the joystick is
still held, and two things become measurable:

  * the camera stalls - the stick is pushed but the world is not scrolling;
  * the brawler slides off the centre of the screen, and which way it slides
    says which boundary was hit. Drifted right means the camera cannot pan any
    further right, so the right-hand edge of the map is there.

Both are ground truth rather than model output, and both work for anything that
stops the camera: the arena boundary above all, but also water, rope fences and
map furniture the wall model does not know about.

Two consumers:
  * the dodge solver, which must not pick an escape into the map edge;
  * the unstuck logic, which currently guesses on a timer.
"""

import math
import time


DIRECTION_TTL = 0.6

# Cap on remembered blocked directions.
#
# Without it a bot wedged in a corner records a new one every frame as it
# flails, and within a second nine of the sixteen candidate directions are
# marked impassable - including the ones that would actually free it. Observed
# live: ten seconds pinned against the map edge with blocked=9. Keeping only
# the most recent few means the memory decays as fast as the situation does.
MAX_BLOCKED_DIRECTIONS = 5

# Player offset from screen centre, as a fraction of the frame, past which the
# camera counts as pinned against a map boundary.
#
# Set deliberately high. Simply walking up to a wall nudges the player off
# centre a little, and so does an imprecise detection box - neither means the
# map has run out. Only a large, sustained offset does, because that requires
# the camera to have stopped panning entirely. At 1920x1080 the enter threshold
# is ~326 px horizontally: roughly a third of the way to the screen edge.
BOUNDARY_ENTER_FRACTION = 0.17
# Released at a lower offset than it engages, so the flag does not chatter
# while sliding along the edge.
BOUNDARY_EXIT_FRACTION = 0.12
# And it has to hold for this many consecutive frames before it counts.
BOUNDARY_FRAMES_REQUIRED = 4


class MotionMonitor:
    def __init__(self, config):
        self.config = config
        # Fraction of the expected speed below which we call it a hard stop.
        self.stuck_ratio = 0.30
        # Cosine between where we pushed and where we actually went, below
        # which we are sliding along something rather than going where we meant.
        self.slide_cosine = 0.55
        # A single stalled frame is noise (a lag spike, a dropped scrcpy frame).
        self.stuck_frames_required = 3

        self._stalled_frames = 0
        self._blocked = []          # [(unit_x, unit_y, stamp, hard)]
        self._stuck_since = None
        self._drift = (0.0, 0.0)
        self._efficiency = 1.0
        self._velocity = (0.0, 0.0)
        self._boundary = (0, 0)     # sign per axis: +1 = edge to the right/down
        self._boundary_frames = 0
        self._odometer = (0.0, 0.0)

    # ------------------------------------------------------------------

    @property
    def stuck(self):
        return self._stuck_since is not None

    @property
    def odometer(self):
        """Total distance the player has travelled through the world, in px.

        Every on-screen coordinate the bot has is relative to a camera that
        follows it, so "did that teammate actually move" cannot be answered from
        screen positions alone - a stationary teammate slides across the screen
        the moment the bot walks. Accumulating the camera pan gives a world
        frame: world = screen + odometer, and differences in that frame are
        real movement.
        """
        return self._odometer

    @property
    def stuck_for(self):
        if self._stuck_since is None:
            return 0.0
        return time.time() - self._stuck_since

    @property
    def velocity(self):
        """How fast the brawler is ACTUALLY travelling, and which way.

        Measured off the camera, in full-frame px/s, and (0, 0) until the bot
        has moved. The solver needs it because a brawler is not a cursor: an
        escape to the left, chosen while walking right, spends its first
        moments undoing the walk rather than clearing the shot.
        """
        return self._velocity

    @property
    def efficiency(self):
        """How much of the commanded movement is actually happening, 0-1."""
        return self._efficiency

    @property
    def drift(self):
        """Player offset from the centre of the screen, in full-frame pixels.

        Near zero while the camera is free to follow. Grows only when the
        camera is pinned, which in practice means the edge of the map.
        """
        return self._drift

    @property
    def boundary(self):
        """Which map edges we are currently against, as (sx, sy) signs.

        (1, 0) means the right-hand edge of the map is right there; (0, -1)
        the top edge; (1, -1) the top-right corner; (0, 0) means free.
        """
        return self._boundary

    def boundary_vector(self):
        """Unit vector pointing at the nearest map edge, or None when free."""
        sx, sy = self._boundary
        if not sx and not sy:
            return None
        length = math.hypot(sx, sy)
        return (sx / length, sy / length)

    def is_toward_boundary(self, vector, tolerance=0.6):
        """True if this movement heads into a map edge we are already against."""
        edge = self.boundary_vector()
        if edge is None:
            return False
        length = math.hypot(vector[0], vector[1])
        if length < 1e-6:
            return False
        return (vector[0] / length) * edge[0] + (vector[1] / length) * edge[1] > tolerance

    def blocked_directions(self, now=None):
        now = time.time() if now is None else now
        return [(x, y, hard) for x, y, stamp, hard in self._blocked if now - stamp < DIRECTION_TTL]

    def is_direction_blocked(self, vector, now=None):
        """True if we have recently measured a stop while pushing this way."""
        length = math.hypot(vector[0], vector[1])
        if length < 1e-6:
            return False
        ux, uy = vector[0] / length, vector[1] / length

        for bx, by, hard in self.blocked_directions(now):
            if not hard:
                continue
            # Within ~40 degrees of a direction that provably did not work.
            if bx * ux + by * uy > 0.77:
                return True
        return False

    def reset(self):
        self._stalled_frames = 0
        self._blocked = []
        self._stuck_since = None
        self._drift = (0.0, 0.0)
        self._efficiency = 1.0
        self._velocity = (0.0, 0.0)
        self._boundary = (0, 0)
        self._boundary_frames = 0
        self._odometer = (0.0, 0.0)

    # ------------------------------------------------------------------

    def update(self, camera_shift, dt, joystick_vector, expected_speed,
               player_center=None, screen_size=None, now=None):
        now = time.time() if now is None else now

        # Before any early return: the odometer must keep counting whether or
        # not the stick is held, because the camera keeps settling for a moment
        # after the bot stops and that motion is still real displacement.
        self._odometer = (
            self._odometer[0] - camera_shift[0],
            self._odometer[1] - camera_shift[1],
        )

        if player_center and screen_size:
            self._drift = (
                player_center[0] - screen_size[0] / 2.0,
                player_center[1] - screen_size[1] / 2.0,
            )
            self._boundary = self._classify_boundary(self._drift, screen_size)

        self._blocked = [b for b in self._blocked if now - b[2] < DIRECTION_TTL]

        # No stick, no push: whatever the brawler was doing it is coasting
        # to a stop, and reporting the last measured velocity would have
        # the solver believe it is still travelling.
        if not joystick_vector or dt <= 0 or expected_speed <= 0:
            self._stalled_frames = 0
            self._stuck_since = None
            self._efficiency = 1.0
            self._velocity = (0.0, 0.0)
            return

        commanded = math.hypot(joystick_vector[0], joystick_vector[1])
        if commanded < 1e-3:
            self._stalled_frames = 0
            self._stuck_since = None
            self._efficiency = 1.0
            self._velocity = (0.0, 0.0)
            return

        push_x = joystick_vector[0] / commanded
        push_y = joystick_vector[1] / commanded

        # The world scrolls opposite to the way the player walks, so negating
        # the camera shift gives the direction the player actually travelled.
        travelled_x = -camera_shift[0]
        travelled_y = -camera_shift[1]
        travelled = math.hypot(travelled_x, travelled_y)
        speed = travelled / dt
        self._velocity = (travelled_x / dt, travelled_y / dt)
        self._efficiency = min(speed / expected_speed, 1.5)

        if self._efficiency < self.stuck_ratio:
            self._stalled_frames += 1
            if self._stalled_frames >= self.stuck_frames_required:
                if self._stuck_since is None:
                    self._stuck_since = now
                self._remember_blocked(push_x, push_y, now, hard=True)
            return

        self._stalled_frames = 0
        self._stuck_since = None

        if travelled < 1e-6:
            return

        alignment = (travelled_x / travelled) * push_x + (travelled_y / travelled) * push_y
        if alignment < self.slide_cosine:
            # Moving, but not where we asked: we are sliding along a surface.
            # The blocked axis is the component of the push that got cancelled.
            residual_x = push_x - alignment * (travelled_x / travelled)
            residual_y = push_y - alignment * (travelled_y / travelled)
            residual = math.hypot(residual_x, residual_y)
            if residual > 1e-6:
                self._remember_blocked(residual_x / residual, residual_y / residual, now, hard=False)

    def _classify_boundary(self, drift, screen_size):
        """Turn the player's screen offset into which map edges are adjacent.

        The camera pans to keep the player centred, so a large sustained offset
        can only mean the camera ran out of map in that direction. Uses a wide
        hysteresis band plus a frame count, because brushing a wall produces a
        small offset that must not be mistaken for the edge of the arena.
        """
        current_x, current_y = self._boundary

        def axis(value, size, current):
            enter = size * BOUNDARY_ENTER_FRACTION
            exit_at = size * BOUNDARY_EXIT_FRACTION
            # Already latched: hold until the offset drops well back.
            if current:
                return current if abs(value) > exit_at and value * current > 0 else 0
            if value > enter:
                return 1
            if value < -enter:
                return -1
            return 0

        candidate = (
            axis(drift[0], screen_size[0], current_x),
            axis(drift[1], screen_size[1], current_y),
        )

        if candidate == (0, 0):
            self._boundary_frames = 0
            return (0, 0)

        # Latched axes are already confirmed; a new one has to persist first.
        if candidate == self._boundary:
            return candidate

        self._boundary_frames += 1
        if self._boundary_frames < BOUNDARY_FRAMES_REQUIRED:
            return self._boundary

        self._boundary_frames = 0
        return candidate

    def _remember_blocked(self, ux, uy, stamp, hard):
        for index, (bx, by, _, existing_hard) in enumerate(self._blocked):
            if bx * ux + by * uy > 0.9:
                # Same direction as an existing entry: refresh it, and let a
                # hard stop upgrade a previous soft slide.
                self._blocked[index] = (bx, by, stamp, existing_hard or hard)
                return

        self._blocked.append((ux, uy, stamp, hard))
        if len(self._blocked) > MAX_BLOCKED_DIRECTIONS:
            # Drop the oldest: the newest evidence describes where we are now.
            self._blocked.pop(0)
