"""Leading targets.

The bot's attack button is a stick, not a button: touch down on it and drag,
and the shot goes where you dragged. Tapping it uses the game's auto-aim, which
fires at where the enemy *is*. Against anything that moves, that is a miss.

This module answers "where will they be when the shot arrives", which is the
interception problem:

    | rel_pos + rel_vel * t | = projectile_speed * t

Expanded, that is a quadratic in t:

    (|rel_vel|^2 - s^2) t^2 + 2 (rel_pos . rel_vel) t + |rel_pos|^2 = 0

and the aim point is rel_pos + rel_vel * t for the smallest positive root. This
is the same V*T=S idea as a fixed 0.3 s lead, except t falls out of the geometry
instead of being guessed - which matters because the correct lead at point
blank is near zero and at maximum range is several times 0.3 s.

Enemy velocity has to be measured in WORLD space, not screen space. On screen
the enemy's apparent motion is contaminated by the camera panning with the
player, and leading against that aims at empty ground whenever the bot walks.
The camera shift from the projectile tracker is what removes it.
"""

import math
import time
from collections import deque


class TrackedEnemy:
    """An enemy with a measured world-space velocity."""

    __slots__ = ("id", "x", "y", "vx", "vy", "box", "hits", "confidence")

    def __init__(self, enemy_id, x, y, vx, vy, box, hits, confidence):
        self.id = enemy_id
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.box = box
        self.hits = hits
        self.confidence = confidence

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)

    @property
    def position(self):
        return (self.x, self.y)


class _EnemyTrack:
    __slots__ = ("id", "samples", "missed", "box", "screen")

    def __init__(self, track_id, stamp, world, screen, box):
        self.id = track_id
        self.samples = deque(maxlen=8)
        self.samples.append((stamp, world[0], world[1]))
        self.missed = 0
        self.box = box
        self.screen = screen


class EnemyTracker:
    """Follows enemy boxes across frames and measures how fast they move."""

    def __init__(self, config):
        self.config = config
        self._tracks = []
        self._next_id = 1
        # Running total of the camera pan, used to convert screen positions
        # into a frame of reference that does not move with the player.
        self._camera_offset = (0.0, 0.0)

    def reset(self):
        self._tracks = []
        self._camera_offset = (0.0, 0.0)

    def update(self, enemy_boxes, camera_shift, stamp=None):
        stamp = time.time() if stamp is None else stamp
        config = self.config

        self._camera_offset = (
            self._camera_offset[0] + camera_shift[0],
            self._camera_offset[1] + camera_shift[1],
        )
        offset = self._camera_offset

        observations = []
        for box in enemy_boxes or []:
            if len(box) < 4:
                continue
            screen = ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)
            world = (screen[0] - offset[0], screen[1] - offset[1])
            observations.append((world, screen, box))

        unmatched = list(range(len(observations)))
        gate = config.aim_match_gate

        for track in self._tracks:
            last = track.samples[-1]
            velocity = self._fit_velocity(track)
            dt = max(stamp - last[0], 1e-3)
            predicted = (last[1] + velocity[0] * dt, last[2] + velocity[1] * dt)

            best_position = -1
            best_distance = gate + math.hypot(velocity[0], velocity[1]) * dt
            for position, index in enumerate(unmatched):
                world = observations[index][0]
                distance = math.hypot(world[0] - predicted[0], world[1] - predicted[1])
                if distance < best_distance:
                    best_distance = distance
                    best_position = position

            if best_position >= 0:
                world, screen, box = observations[unmatched.pop(best_position)]
                track.samples.append((stamp, world[0], world[1]))
                track.screen = screen
                track.box = box
                track.missed = 0
            else:
                track.missed += 1

        self._tracks = [t for t in self._tracks if t.missed <= config.aim_max_missed]

        for index in unmatched:
            world, screen, box = observations[index]
            self._tracks.append(_EnemyTrack(self._next_id, stamp, world, screen, box))
            self._next_id += 1

        return self.tracked()

    def tracked(self):
        config = self.config
        result = []
        for track in self._tracks:
            vx, vy = self._fit_velocity(track)
            speed = math.hypot(vx, vy)
            if speed > config.aim_max_enemy_speed:
                # Almost certainly a mismatch between two different enemies
                # rather than someone genuinely moving that fast.
                vx = vy = 0.0
                speed = 0.0
            hits = len(track.samples)
            confidence = min(hits / max(config.aim_min_samples, 1), 1.0)
            if hits < config.aim_min_samples:
                vx = vy = 0.0
            result.append(TrackedEnemy(
                track.id, track.screen[0], track.screen[1], vx, vy, track.box, hits, confidence
            ))
        return result

    def find(self, position, tolerance=None):
        """The tracked enemy nearest to a screen position, or None."""
        tolerance = tolerance if tolerance is not None else self.config.aim_match_gate
        best = None
        best_distance = tolerance
        for enemy in self.tracked():
            distance = math.hypot(enemy.x - position[0], enemy.y - position[1])
            if distance < best_distance:
                best_distance = distance
                best = enemy
        return best

    @staticmethod
    def _fit_velocity(track):
        samples = list(track.samples)
        if len(samples) < 2:
            return (0.0, 0.0)

        base = samples[0][0]
        times = [s[0] - base for s in samples]
        mean_t = sum(times) / len(times)
        variance = sum((t - mean_t) ** 2 for t in times)
        if variance < 1e-9:
            return (0.0, 0.0)

        mean_x = sum(s[1] for s in samples) / len(samples)
        mean_y = sum(s[2] for s in samples) / len(samples)
        vx = sum((t - mean_t) * (s[1] - mean_x) for t, s in zip(times, samples)) / variance
        vy = sum((t - mean_t) * (s[2] - mean_y) for t, s in zip(times, samples)) / variance
        return (vx, vy)


class AimSolution:
    __slots__ = ("point", "direction", "flight_time", "lead_distance", "confidence")

    def __init__(self, point, direction, flight_time, lead_distance, confidence):
        self.point = point
        self.direction = direction
        self.flight_time = flight_time
        self.lead_distance = lead_distance
        self.confidence = confidence


class AimSolver:
    def __init__(self, config):
        self.config = config

    def solve(self, shooter_pos, target_pos, target_velocity=(0.0, 0.0),
              projectile_speed=None, confidence=1.0):
        """Where to aim so the shot and the target arrive together.

        Returns an AimSolution, or None if the target is unreachable - it is
        running away faster than the projectile flies.
        """
        config = self.config
        speed = projectile_speed or config.aim_projectile_speed

        rel_x = target_pos[0] - shooter_pos[0]
        rel_y = target_pos[1] - shooter_pos[1]
        distance = math.hypot(rel_x, rel_y)
        if distance < 1e-6:
            return None

        vx, vy = target_velocity
        solved = self._intercept_time(rel_x, rel_y, vx, vy, speed)
        if solved is None:
            return None

        flight = min(max(solved, config.aim_min_lead), config.aim_max_lead)
        # Trust the lead only as far as the velocity estimate is trustworthy.
        # A half-confident track gets half the lead, which is much better than
        # either ignoring the motion or over-committing to a noisy estimate.
        scale = max(0.0, min(confidence, 1.0))
        point = (target_pos[0] + vx * flight * scale, target_pos[1] + vy * flight * scale)

        # Clipping the flight time keeps the bot from extrapolating half a
        # second into a target's future, but it also means the aim point is
        # knowingly short. Report that as reduced confidence so the playstyle
        # can hold fire rather than take a shot we already know misses.
        clip_quality = flight / solved if solved > flight else 1.0

        aim_x = point[0] - shooter_pos[0]
        aim_y = point[1] - shooter_pos[1]
        length = math.hypot(aim_x, aim_y)
        if length < 1e-6:
            return None

        return AimSolution(
            point=point,
            direction=(aim_x / length, aim_y / length),
            flight_time=flight,
            lead_distance=math.hypot(point[0] - target_pos[0], point[1] - target_pos[1]),
            confidence=scale * clip_quality,
        )

    @staticmethod
    def _intercept_time(rel_x, rel_y, vx, vy, speed):
        a = vx * vx + vy * vy - speed * speed
        b = 2.0 * (rel_x * vx + rel_y * vy)
        c = rel_x * rel_x + rel_y * rel_y

        if abs(a) < 1e-6:
            # Target closing or fleeing at exactly projectile speed.
            if abs(b) < 1e-9:
                return None
            t = -c / b
            return t if t > 0 else None

        discriminant = b * b - 4.0 * a * c
        if discriminant < 0.0:
            return None

        root = math.sqrt(discriminant)
        t1 = (-b + root) / (2.0 * a)
        t2 = (-b - root) / (2.0 * a)
        candidates = [t for t in (t1, t2) if t > 1e-6]
        if not candidates:
            return None
        return min(candidates)
