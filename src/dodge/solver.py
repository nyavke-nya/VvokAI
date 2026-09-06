"""Dodge geometry.

Given the confirmed projectiles and where the playstyle *wanted* to go, pick the
joystick direction that gets hit least. Two properties matter and both come
straight from how the game actually works:

  * Hitboxes are circles, not points. Stepping one pixel aside does nothing;
    the bot has to clear (player radius + projectile radius + margin).
  * Several shots arrive at once. Each candidate direction is scored against
    *every* threat, so the answer is the direction that minimises total damage,
    not the one that dodges the nearest shot into the path of another.
"""

import math
import time

from utils import JOYSTICK_RADIUS


class Threat:
    """One projectile, evaluated against the player."""

    __slots__ = ("projectile", "time_to_impact", "would_hit")

    def __init__(self, projectile, time_to_impact, would_hit):
        self.projectile = projectile
        self.time_to_impact = time_to_impact
        self.would_hit = would_hit


class DodgeDecision:
    __slots__ = ("vector", "urgency", "time_to_impact", "threats", "hit_count",
                 "score", "analysis")

    def __init__(self, vector, urgency, time_to_impact, threats, hit_count, score,
                 analysis=None):
        self.vector = vector
        self.urgency = urgency
        self.time_to_impact = time_to_impact
        self.threats = threats
        self.hit_count = hit_count
        self.score = score
        # Per-candidate breakdown, populated only when logging is on. This is
        # what makes "why did it not dodge" answerable after the fact.
        self.analysis = analysis

    @property
    def active(self):
        return self.urgency != "none"

    @property
    def critical(self):
        return self.urgency == "critical"


def _impact_time(rel_x, rel_y, rel_vx, rel_vy, radius, horizon):
    """When does a circle of `radius` centred on the origin get touched?

    Solves |rel_p + rel_v * t| = radius for the smallest t in [0, horizon].
    Returns None if the paths never close inside the horizon.
    """
    c = rel_x * rel_x + rel_y * rel_y - radius * radius
    if c <= 0.0:
        return 0.0

    a = rel_vx * rel_vx + rel_vy * rel_vy
    if a < 1e-9:
        return None

    b = 2.0 * (rel_x * rel_vx + rel_y * rel_vy)
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return None

    root = math.sqrt(discriminant)
    t = (-b - root) / (2.0 * a)
    if t < 0.0:
        t = (-b + root) / (2.0 * a)
    if t < 0.0 or t > horizon:
        return None
    return t


def _closest_approach(rel_x, rel_y, rel_vx, rel_vy, horizon):
    """Smallest distance between the two paths within the horizon.

    Used to rank directions that all get hit anyway. Without it every losing
    option scores identically and the solver has no reason to move at all,
    which is how the bot came to stand perfectly still while being shot by
    something it could never have fully escaped.
    """
    speed_sq = rel_vx * rel_vx + rel_vy * rel_vy
    if speed_sq < 1e-9:
        return math.hypot(rel_x, rel_y)

    # Time of closest approach, clamped into the window we care about.
    t = -(rel_x * rel_vx + rel_y * rel_vy) / speed_sq
    t = min(max(t, 0.0), horizon)
    return math.hypot(rel_x + rel_vx * t, rel_y + rel_vy * t)


def _ramp_fraction(t, tau):
    """How much of a change of direction has actually happened by time t.

    A brawler is not a cursor. Told to go left it does not go left: it stops
    going wherever it was going, turns, and works up to speed, and for the
    first fraction of a second it is still mostly travelling the old way. The
    solver used to model the escape as full speed in the new direction from the
    instant it was chosen, which is why it could pick a mathematically clean
    direction nine times out of ten and still be hit - the geometry was solved
    for a brawler that does not exist.

    Velocity is taken to approach the commanded one exponentially with time
    constant `tau`. What matters for whether a shot connects is not the speed at
    the moment of impact but the DISPLACEMENT before it, so this returns the
    mean of that approach over [0, t] rather than its endpoint:

        1 - (tau / t) * (1 - exp(-t / tau))

    0 as t -> 0 (nothing has happened yet, the brawler is still doing what it
    was doing) and 1 for t >> tau (the old instant-full-speed model, which is
    correct once there has been time for it to be).
    """
    if tau <= 0.0 or t is None:
        return 1.0
    if t <= 0.0:
        return 0.0
    ratio = tau / t
    return max(0.0, min(1.0, 1.0 - ratio * (1.0 - math.exp(-t / tau))))


class DodgeSolver:
    def __init__(self, config):
        self.config = config
        self._committed_vector = None
        self._committed_until = 0.0
        # An escape outlives the moment it was chosen: see _hold_escape.
        self._escape_vector = None
        self._escape_ids = set()
        self._escape_expires = 0.0
        self._directions = self._build_directions(config.candidate_directions)

    @staticmethod
    def _build_directions(count):
        directions = [(0.0, 0.0)]
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            directions.append((math.cos(angle), math.sin(angle)))
        return directions

    def evaluate(self, projectiles, player_center, player_radius):
        """Threats assuming the player keeps standing where they are."""
        config = self.config
        threats = []
        radius = player_radius + config.safety_margin

        for projectile in projectiles:
            total_radius = radius + projectile.radius
            t = _impact_time(
                projectile.x - player_center[0],
                projectile.y - player_center[1],
                projectile.vx,
                projectile.vy,
                total_radius,
                config.horizon,
            )
            threats.append(Threat(projectile, t, t is not None))

        return threats

    def solve(self, projectiles, player_center, player_radius, tactical_vector,
              is_blocked, player_speed=None, now=None, motion=None, collect_analysis=False,
              hazard_veto=None):
        """Pick an escape direction.

        `tactical_vector` is whatever the playstyle decided to do this frame, in
        joystick units. `is_blocked` takes a joystick-unit vector and reports
        whether a wall is in the way. `hazard_veto` does the same for poison and
        for whatever a thrower left on the ground. Returns a DodgeDecision; when
        nothing is incoming, `urgency` is "none" and `vector` is None.

        Hazards are an input here rather than a check applied to the answer.
        Refusing the finished escape afterwards threw the whole dodge away - 92
        of 669 critical dodges in one session were cancelled that way, with the
        bot then standing still and taking the shot. The solver has fifteen
        other directions; it should be told which ones are poisoned and pick
        among the rest.
        """
        config = self.config
        now = time.time() if now is None else now
        speed = player_speed or config.default_player_speed

        # Everything we know is already out of date. The frame was encoded on
        # the device, streamed, decoded and only then analysed; the joystick
        # event then has to travel back. Solving against the on-screen position
        # aims the escape at where the shot *was*, which is why the bot could
        # pick a mathematically clean direction 89% of the time and still be
        # hit by every single one.
        #
        # Advance the shots to where they will be when the command lands, and
        # solve from there.
        if config.reaction_latency > 0:
            projectiles = [_advance(p, config.reaction_latency) for p in projectiles]

        # Shots already past us are not threats, and letting them linger makes
        # the bot flinch at things flying away from it.
        projectiles = [p for p in projectiles if _is_approaching(p, player_center)]

        threats = self.evaluate(projectiles, player_center, player_radius)
        incoming = [t for t in threats if t.would_hit]

        if not incoming:
            # Nothing hits us *right now* - but that may be precisely because we
            # are already stepping aside. Releasing here is what made the bot
            # dodge, immediately turn back into the shot's path, and dodge
            # again. Keep the escape until the shot that caused it is actually
            # gone from the tracker.
            held = self._hold_escape(projectiles, now)
            if held is not None:
                return DodgeDecision(held, "soft", None, threats, 0, 0.0)
            self._committed_vector = None
            return DodgeDecision(None, "none", None, threats, 0, 0.0)

        soonest = min(t.time_to_impact for t in incoming)

        tactical_unit = _unit(tactical_vector)
        radius = player_radius + config.safety_margin
        # Dodges always use full stick deflection: a half-committed dodge is a hit.
        joystick_scale = float(JOYSTICK_RADIUS)

        best_vector = None
        best_score = float("inf")
        best_hits = 0
        stay_score = None
        # Best clearance among moving directions, and the clearance of doing
        # nothing, for the lean fallback.
        stay_clearance = None
        lean_vector = None
        lean_clearance = None
        breakdown = [] if collect_analysis else None

        # What the brawler is doing right now, which is where any escape has to
        # start from. Unknown means "from a standstill", which is still nearer
        # the truth than "already at full speed the other way".
        current_vx, current_vy = 0.0, 0.0
        if motion is not None:
            measured = getattr(motion, "velocity", None)
            if measured:
                current_vx, current_vy = float(measured[0]), float(measured[1])

        # One ramp figure per threat, not per direction: it depends only on how
        # long there is before that shot arrives.
        ramp = {}
        for threat in threats:
            ramp[id(threat)] = _ramp_fraction(
                threat.time_to_impact if threat.time_to_impact is not None
                else config.horizon,
                config.speed_ramp_time)

        for direction in self._directions:
            target_vx = direction[0] * speed
            target_vy = direction[1] * speed
            hit_wall = False
            hit_edge = False
            poisoned = False

            score = 0.0
            hits = 0
            # Worst clearance across all threats: how close the nearest shot
            # comes to the hitbox edge. Negative means it lands.
            clearance = float("inf")
            for threat in threats:
                projectile = threat.projectile
                total_radius = radius + projectile.radius
                rel_x = projectile.x - player_center[0]
                rel_y = projectile.y - player_center[1]
                # The average velocity the brawler will actually have managed by
                # the time this particular shot arrives - not the one it was
                # told to have. Against a shot 150 ms out that is barely any of
                # it; against one a second away it is all of it.
                blend = ramp[id(threat)]
                move_vx = current_vx + (target_vx - current_vx) * blend
                move_vy = current_vy + (target_vy - current_vy) * blend
                rel_vx = projectile.vx - move_vx
                rel_vy = projectile.vy - move_vy
                t = _impact_time(rel_x, rel_y, rel_vx, rel_vy, total_radius, config.horizon)
                if t is None:
                    continue
                hits += 1
                # Sooner impacts hurt more: there is less time to do anything
                # else about them.
                urgency_weight = 1.0 + 2.0 * (1.0 - t / config.horizon)
                score += projectile.confidence * urgency_weight

                # How close this direction comes to missing. Used only to rank
                # directions that all get hit anyway - see the lean fallback
                # below - so it does not disturb the ordinary scoring.
                miss = _closest_approach(rel_x, rel_y, rel_vx, rel_vy, config.horizon)
                clearance = min(clearance, miss - total_radius)

            is_stay = direction == (0.0, 0.0)
            if is_stay:
                stay_score = score
                stay_clearance = clearance
            else:
                candidate = (direction[0] * joystick_scale, direction[1] * joystick_scale)
                walled = bool(is_blocked is not None and is_blocked(candidate))
                poisoned = False
                if motion is not None and motion.is_direction_blocked(candidate, now):
                    # Measured obstruction, as opposed to the wall model's
                    # opinion: a direction we already pushed and did not move in.
                    walled = True

                poisoned = bool(hazard_veto is not None and hazard_veto(candidate))
                if poisoned:
                    # Strictly worse than standing still, unlike a wall. A wall
                    # means the bot does not move and takes the shot it would
                    # have taken anyway; gas and puddles mean it takes the shot
                    # AND stands in continuous damage it walked into on purpose.
                    score += config.hazard_penalty
                    hit_edge = True

                if not walled and motion is not None and motion.is_toward_boundary(candidate):
                    # The map edge is not a hard stop yet, but there is no room
                    # to keep going that way, so it is worth avoiding without
                    # being treated as a wall.
                    score += config.wall_penalty
                    hit_edge = True

                if tactical_unit is not None and config.tactical_weight:
                    alignment = direction[0] * tactical_unit[0] + direction[1] * tactical_unit[1]
                    score += config.tactical_weight * (1.0 - alignment)
                elif config.tactical_weight:
                    # No tactical preference, so mildly prefer not moving at all
                    # over thrashing for no reason.
                    score += config.tactical_weight * 0.5

                if walled:
                    hit_wall = True
                    # Applied LAST, and as an assignment rather than a penalty.
                    #
                    # A blocked direction is not expensive, it is inert: the
                    # stick goes over, the brawler does not move, and every shot
                    # arrives exactly as if it had stayed put. So its score IS
                    # the score of standing still, and nothing may be added
                    # after that - a tactical nudge stacked on top would make a
                    # wall look worse than doing nothing, which is not true.
                    #
                    # Pricing it as a penalty was wrong at both settings tried.
                    # Small (2.5) and the solver picked walls over clean
                    # escapes; large (12.0) and a walled direction cost more
                    # than four simultaneous hits - so once the wall model
                    # started seeing the whole screen, 22-36 boxes instead of
                    # 11, most of the circle looked catastrophic and the solver
                    # concluded there was no escape at all. "Impossible"
                    # verdicts went 27% -> 32%, and the hit rate after dodging
                    # rose with them.
                    #
                    # The epsilon breaks the tie toward genuinely standing
                    # still, which at least does not fight the stick.
                    score = (stay_score if stay_score is not None else score) + 1e-3

            if breakdown is not None:
                breakdown.append({
                    "dir": [round(direction[0], 2), round(direction[1], 2)],
                    "hits": hits,
                    "score": round(score, 3),
                    "wall": hit_wall,
                    "edge": hit_edge,
                })

            if not is_stay and not hit_wall and not hit_edge and not poisoned:
                if lean_clearance is None or clearance > lean_clearance:
                    lean_clearance = clearance
                    lean_vector = (direction[0] * joystick_scale,
                                   direction[1] * joystick_scale)

            if score < best_score:
                best_score = score
                best_hits = hits
                best_vector = None if is_stay else (
                    direction[0] * joystick_scale,
                    direction[1] * joystick_scale,
                )

        analysis = self._build_analysis(breakdown, stay_score)

        if stay_score is not None and best_score >= stay_score - 1e-6:
            # No direction escapes. That is the common case against a fast
            # shot - measured on a session, shots above 2000 px/s were too late
            # to escape 82% of the time - and answering it by standing
            # perfectly still is how the bot ate two Bea shots without moving.
            #
            # It genuinely cannot clear: her shot leaves 0.16 s and stepping
            # the hitbox out of the way needs 0.37 s. Nothing tunes that away
            # and a human does not manage it either. But "cannot fully clear"
            # is not "may as well stand there" - leaning turns a body hit into
            # a graze, and it puts the bot somewhere other than where the next
            # shot is already aimed.
            #
            # So: take whichever free direction the shot passes furthest from,
            # but only if it is a real improvement over doing nothing.
            # The floor scales with what the brawler can actually manage in
            # the time available. lean_min_gain was calibrated when an
            # escape was modelled as full speed from the first instant: at
            # 330 px/s a 0.16 s window was 53 px of movement, so asking for
            # 4 px of extra clearance filtered out only noise. Measured
            # honestly that window is nearer 21 px, and the same 4 px turns
            # into a fifth of everything achievable - which is how a fixed
            # floor came to reject the lean entirely and go back to standing
            # still through the shot. Held against the movement that exists
            # rather than the movement that does not, it filters what it was
            # written to filter. At speed_ramp_time = 0 this is exactly the
            # old test.
            min_gain = config.lean_min_gain * _ramp_fraction(
                soonest, config.speed_ramp_time)
            if (config.near_miss_weight > 0 and lean_vector is not None
                    and stay_clearance is not None and lean_clearance is not None
                    and lean_clearance > stay_clearance + min_gain):
                self._escape_vector = lean_vector
                self._escape_ids = {t.projectile.id for t in incoming}
                self._escape_expires = now + config.horizon + config.escape_hold_extra
                urgency = "critical" if soonest <= config.critical_time else "soft"
                return DodgeDecision(lean_vector, urgency, soonest, threats,
                                     best_hits, best_score, analysis)

            self._committed_vector = None
            return DodgeDecision(None, "none", soonest, threats, best_hits, best_score, analysis)

        urgency = "critical" if soonest <= config.critical_time else "soft"
        vector = self._apply_commitment(best_vector, urgency, now)
        # Record what this escape is for; _hold_escape keeps it alive while any
        # of these shots is still inbound.
        self._escape_vector = vector
        self._escape_ids = {t.projectile.id for t in incoming}
        self._escape_expires = now + config.horizon + config.escape_hold_extra
        return DodgeDecision(vector, urgency, soonest, threats, best_hits, best_score, analysis)

    def _hold_escape(self, projectiles, now):
        """Keep an escape running until the shots it was taken for have passed.

        Returns the held vector, or None when it may be released.
        """
        if self._escape_vector is None or now > self._escape_expires:
            self._escape_vector = None
            self._escape_ids = set()
            return None

        # Still inbound? The tracker drops shots once they stop approaching, so
        # simply being absent from the list means the danger is over.
        live = {p.id for p in projectiles}
        if not (self._escape_ids & live):
            self._escape_vector = None
            self._escape_ids = set()
            return None

        return self._escape_vector

    @staticmethod
    def _build_analysis(breakdown, stay_score):
        """Classify why the outcome was what it was, for the forensic log."""
        if breakdown is None:
            return None

        moves = [c for c in breakdown if c["dir"] != [0.0, 0.0] and c["dir"] != [0, 0]]
        # An escape that takes no hits and is not walled off did exist.
        clean = [c for c in moves if c["hits"] == 0 and not c["wall"]]
        return {
            "candidates": breakdown,
            "stay_score": round(stay_score, 3) if stay_score is not None else None,
            "best_hits_free": bool(clean),
            "all_blocked": bool(moves) and all(c["wall"] or c["edge"] for c in moves),
        }

    def _apply_commitment(self, vector, urgency, now):
        """Hold a chosen escape briefly so the bot commits instead of vibrating.

        Without this, two escapes that score within noise of each other swap
        every frame and the brawler stands still shaking.
        """
        if vector is None or self.config.commit_time <= 0:
            return vector

        if self._committed_vector is not None and now < self._committed_until:
            previous = _unit(self._committed_vector)
            current = _unit(vector)
            if previous and current:
                alignment = previous[0] * current[0] + previous[1] * current[1]
                # Still roughly the same way: keep the old vector so the stick
                # does not micro-adjust. A genuine reversal overrides it.
                if alignment > 0.5:
                    return self._committed_vector

        self._committed_vector = vector
        self._committed_until = now + self.config.commit_time
        return vector


class _Advanced:
    """A projectile shifted forward in time to compensate for pipeline lag."""

    __slots__ = ("id", "x", "y", "vx", "vy", "radius", "confidence")

    def __init__(self, source, dt):
        self.id = source.id
        self.x = source.x + source.vx * dt
        self.y = source.y + source.vy * dt
        self.vx = source.vx
        self.vy = source.vy
        self.radius = source.radius
        self.confidence = source.confidence

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)


def _advance(projectile, dt):
    return _Advanced(projectile, dt)


def _is_approaching(projectile, player_center):
    """True while the shot is still closing on the player."""
    to_player_x = player_center[0] - projectile.x
    to_player_y = player_center[1] - projectile.y
    return to_player_x * projectile.vx + to_player_y * projectile.vy > 0.0


def _unit(vector):
    if not vector:
        return None
    length = math.hypot(vector[0], vector[1])
    if length < 1e-6:
        return None
    return (vector[0] / length, vector[1] / length)
