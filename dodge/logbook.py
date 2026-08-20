"""Forensic logging for dodge decisions.

When the bot takes a hit there are only a handful of possible reasons, and they
call for completely different fixes. This log exists to tell them apart:

  NOT_DETECTED      the shot never became a track. Tune vision.motion_threshold
                    or the origin gate - the bot was blind.
  DETECTED_TOO_LATE confirmed with less time remaining than it physically needs
                    to clear its own hitbox. Reduce tracking.min_confirm_hits or
                    raise the device framerate.
  IMPOSSIBLE        seen in time, but no direction escapes: the shot is too fast
                    or too wide. Nothing to fix; a human would also be hit.
  BLOCKED           an escape existed but every one ran into a wall or the map
                    edge. Positioning problem, not a detection problem.
  OVERRULED         an escape existed and scored well, but tactical_weight or
                    the confidence floor vetoed it. Config problem.
  DODGED            the escape was issued.

Written as JSON Lines so a run can be replayed and counted afterwards with
tools/dodge_report.py, rather than eyeballed in a console.
"""

import json
import math
import os
import threading
import time


class DodgeLog:
    def __init__(self, config, path="debug_frames/dodge_log.jsonl", enabled=False,
                 max_bytes=32 * 1024 * 1024):
        self.config = config
        self.enabled = enabled
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._handle = None
        self._session_start = time.time()
        self._last_motion_log = 0.0
        self._last_vision_log = 0.0
        self._seen_shots = {}
        self.counters = {
            "shots_seen": 0,
            "decisions": 0,
            "dodged": 0,
            "impossible": 0,
            "blocked": 0,
            "overruled": 0,
            "too_late": 0,
        }

    # ------------------------------------------------------------------

    def open(self):
        if not self.enabled or self._handle is not None:
            return
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Rotate rather than grow without bound across long sessions.
        if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
            try:
                os.replace(self.path, self.path + ".1")
            except OSError:
                pass
        self._handle = open(self.path, "a", encoding="utf-8")
        self._write({
            "event": "session_start",
            "config": {
                "motion_threshold": self.config.motion_threshold,
                "min_confirm_hits": self.config.min_confirm_hits,
                "horizon": self.config.horizon,
                "critical_time": self.config.critical_time,
                "safety_margin": round(self.config.safety_margin, 1),
                "tactical_weight": self.config.tactical_weight,
                "downscale": self.config.downscale,
            },
        })

    def close(self):
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None

    def _write(self, record):
        if self._handle is None:
            return
        record["t"] = round(time.time() - self._session_start, 4)
        try:
            self._handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._handle.flush()
        except (OSError, TypeError, ValueError):
            pass

    # ------------------------------------------------------------------

    def log_projectiles(self, projectiles, player_center, player_speed):
        """Record each shot the first time it is confirmed.

        The important number here is time_to_impact at the moment of
        confirmation, compared against how long the bot needs to physically
        clear its own hitbox. If the former is smaller, the shot was seen too
        late and no solver could have helped.
        """
        if not self.enabled or self._handle is None:
            return

        for projectile in projectiles:
            if projectile.id in self._seen_shots:
                continue
            self._seen_shots[projectile.id] = True
            self.counters["shots_seen"] += 1

            speed = projectile.speed
            distance = math.hypot(projectile.x - player_center[0], projectile.y - player_center[1])
            clearance = self.config.safety_margin + projectile.radius + 53.0 * self.config.scale
            time_to_reach = (distance - clearance) / speed if speed > 1e-6 else None
            # How long it takes to walk our own hitbox out of the shot's line.
            time_to_clear = clearance / player_speed if player_speed > 1e-6 else None
            too_late = (
                time_to_reach is not None
                and time_to_clear is not None
                and time_to_reach < time_to_clear
            )
            if too_late:
                self.counters["too_late"] += 1

            self._write({
                "event": "shot_confirmed",
                "id": projectile.id,
                "pos": [round(projectile.x, 1), round(projectile.y, 1)],
                "vel": [round(projectile.vx, 1), round(projectile.vy, 1)],
                "speed": round(speed, 1),
                "radius": round(projectile.radius, 1),
                "confidence": round(projectile.confidence, 3),
                "hits": projectile.hits,
                "age_when_confirmed": round(projectile.age, 3),
                "distance": round(distance, 1),
                "time_to_reach": round(time_to_reach, 4) if time_to_reach is not None else None,
                "time_to_clear_hitbox": round(time_to_clear, 4) if time_to_clear is not None else None,
                "verdict": "DETECTED_TOO_LATE" if too_late else "in_time",
                "from_enemy": projectile.origin is not None,
                "origin_reason": getattr(projectile, "origin_reason", "unknown"),
                # Taken early because waiting would have cost the dodge. Worth
                # separating in the report: if these are mostly still too late,
                # the problem is capture rate rather than the confirmation gate.
                "urgent": bool(getattr(projectile, "urgent", False)),
            })

    def log_decision(self, decision, player_center, player_speed, tactical, applied):
        """Record why the bot did or did not move."""
        if not self.enabled or self._handle is None or decision is None:
            return

        incoming = [t for t in decision.threats if t.would_hit]
        if not incoming:
            return

        self.counters["decisions"] += 1
        analysis = getattr(decision, "analysis", None)

        if decision.active:
            verdict = "DODGED"
            self.counters["dodged"] += 1
        elif analysis and analysis.get("all_blocked"):
            verdict = "BLOCKED"
            self.counters["blocked"] += 1
        elif analysis and analysis.get("best_hits_free"):
            verdict = "OVERRULED"
            self.counters["overruled"] += 1
        else:
            verdict = "IMPOSSIBLE"
            self.counters["impossible"] += 1

        record = {
            "event": "decision",
            "verdict": verdict,
            "urgency": decision.urgency,
            "time_to_impact": round(decision.time_to_impact, 4) if decision.time_to_impact else None,
            "threats": [
                {
                    "id": t.projectile.id,
                    "tti": round(t.time_to_impact, 4),
                    "speed": round(t.projectile.speed, 1),
                    "conf": round(t.projectile.confidence, 2),
                }
                for t in incoming
            ],
            "player_speed": round(player_speed, 1),
            "tactical": [round(tactical[0], 1), round(tactical[1], 1)] if tactical else None,
            "chosen": [round(decision.vector[0], 1), round(decision.vector[1], 1)]
                      if decision.vector else None,
            "applied": [round(applied[0], 1), round(applied[1], 1)] if applied else None,
            "hits_after_move": decision.hit_count,
            "score": round(decision.score, 3),
        }
        if analysis:
            record["candidates"] = analysis.get("candidates")
            record["stay_score"] = analysis.get("stay_score")
        self._write(record)

    def log_vision(self, stats, stamp, every=1.0):
        """What the detector looked at, and why most of it was thrown away.

        The counters existed but only ever reached the console, and without the
        per-reason breakdown - so "the bot picks up too much rubbish" could be
        argued about but not measured. Written here at the same one-per-second
        rate as the motion record, which is far cheaper than the vision work it
        describes.

        What the reasons mean, in the order they are applied:

            few_hits   too little history yet. High is normal and healthy.
            speed      too slow for a shot, or too fast to be real. A large
                       count here with no shooting is the noise floor.
            bent       moved, but not in a straight line. Grass and animation.
            origin     straight and fast, but not traceable to an enemy.
            weak_seed  two samples that do not agree with where it came from.
        """
        if not self.enabled or self._handle is None:
            return
        if stamp - self._last_vision_log < every:
            return
        self._last_vision_log = stamp

        rejects = dict(stats.get("rejects") or {})
        blobs = int(stats.get("blobs", 0))
        confirmed = int(stats.get("confirmed", 0))
        self._write({
            "event": "vision",
            "blobs": blobs,
            "tracks": int(stats.get("tracks", 0)),
            "confirmed": confirmed,
            "rejected_blobs": int(stats.get("rejected", 0)),
            "rejects": rejects,
            "ms": round(float(stats.get("ms", 0.0)), 2),
        })

    def log_motion(self, motion, player_speed, tactical, stamp, every=1.0, context=None):
        """Periodic movement health check.

        Cheap, and it catches the failure mode where the bot walks nowhere: if
        `stuck` is true most of the time while `efficiency` hovers just under
        the threshold, the speed estimate is wrong rather than the map being
        cramped.
        """
        if not self.enabled or self._handle is None:
            return
        if stamp - self._last_motion_log < every:
            return
        self._last_motion_log = stamp

        self.counters["motion_samples"] = self.counters.get("motion_samples", 0) + 1
        if motion.stuck:
            self.counters["stuck_samples"] = self.counters.get("stuck_samples", 0) + 1

        record = {
            "event": "motion",
            "stuck": bool(motion.stuck),
            "stuck_for": round(motion.stuck_for, 2),
            "efficiency": round(motion.efficiency, 3),
            "player_speed": round(player_speed, 1),
            "boundary": list(motion.boundary),
            "drift": [int(motion.drift[0]), int(motion.drift[1])],
            "blocked_dirs": len(motion.blocked_directions()),
            "tactical": [round(tactical[0], 1), round(tactical[1], 1)] if tactical else None,
        }
        if context is not None:
            record["enemies"] = len(context.enemies)
            record["teammates"] = len(context.teammates)
            record["walls"] = len(context.walls)
        self._write(record)

    def log_note(self, message, **fields):
        if not self.enabled or self._handle is None:
            return
        record = {"event": "note", "message": message}
        record.update(fields)
        self._write(record)

    def summary(self):
        counters = dict(self.counters)
        decisions = max(counters["decisions"], 1)
        counters["dodge_rate_pct"] = round(counters["dodged"] / decisions * 100, 1)
        return counters
