"""Runs the tracker, optionally on its own thread.

Why a thread: YOLO caps the bot loop at roughly 10-20 iterations per second,
but scrcpy delivers 30-60 frames per second and projectile velocity estimation
lives or dies on temporal resolution. The tracker therefore consumes frames
directly, at full rate, and the bot loop just reads the latest result.

The thread also owns an emergency path. When an impact is under
`dodge.critical_time` away, waiting for the next YOLO pass would cost 50-100 ms
- longer than the threat itself - so the thread pushes the escape vector to the
joystick immediately and hands control back a moment later.
"""

import math
import threading
import time

from dodge.aim import AimSolver, EnemyTracker
from dodge.config import DodgeConfig
from dodge.logbook import DodgeLog
from dodge.solver import DodgeSolver
from dodge.tracker import FrameContext, ProjectileTracker


def _segment_hits_circle(start, end, centre, radius):
    """Does the segment start->end pass within `radius` of `centre`?"""
    dx, dy = end[0] - start[0], end[1] - start[1]
    fx, fy = start[0] - centre[0], start[1] - centre[1]
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-9:
        return math.hypot(fx, fy) <= radius
    # Closest approach, clamped to the segment.
    t = -(fx * dx + fy * dy) / length_sq
    t = min(max(t, 0.0), 1.0)
    cx = start[0] + dx * t - centre[0]
    cy = start[1] + dy * t - centre[1]
    return math.hypot(cx, cy) <= radius


class DodgeService:
    def __init__(self, window_controller, config=None, tile_size=54.0):
        scale = getattr(window_controller, "scale_factor", None) or 1.0
        self.config = config or DodgeConfig.load(scale_factor=scale, tile_size=tile_size)
        self.window_controller = window_controller
        self.tracker = ProjectileTracker(self.config)
        self.solver = DodgeSolver(self.config)
        self.enemy_tracker = EnemyTracker(self.config)
        self.aim_solver = AimSolver(self.config)
        self.log = DodgeLog(
            self.config,
            path=self.config.log_path,
            enabled=self.config.log_enabled,
            max_bytes=self.config.log_max_bytes,
        )
        self.log.open()

        self._lock = threading.Lock()
        self._context = FrameContext()
        self._accumulated_shift = (0.0, 0.0)
        self._player_center = None
        self._player_radius = 53.0 * scale
        self._tactical_vector = None
        self._is_blocked = None
        self._gas_veto = None

        self._projectiles = []
        self._decision = None
        self._decision_stamp = 0.0

        self._thread = None
        self._stop_event = threading.Event()
        self._last_frame_time = 0.0
        self._last_stats_log = 0.0
        self._emergency_hold = max(self.config.commit_time, 0.10)
        self._last_emergency = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @property
    def enabled(self):
        return self.config.enabled

    def start(self):
        if not self.config.enabled or not self.config.threaded:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="pyla-dodge")
        self._thread.start()
        print("Dodge tracker started (threaded).")

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
        if self.config.log_enabled:
            summary = self.log.summary()
            self.log.log_note("session_end", **summary)
            print(
                f"[dodge] shots seen {summary['shots_seen']}, decisions {summary['decisions']}, "
                f"dodged {summary['dodged']} ({summary['dodge_rate_pct']}%), "
                f"impossible {summary['impossible']}, blocked {summary['blocked']}, "
                f"overruled {summary['overruled']}, too late {summary['too_late']}"
            )
        self.log.close()

    def reset(self):
        self.tracker.reset()
        self.tracker.motion.reset()
        self.enemy_tracker.reset()
        with self._lock:
            self._projectiles = []
            self._decision = None
            self._accumulated_shift = (0.0, 0.0)

    # ------------------------------------------------------------------
    # called from the bot loop
    # ------------------------------------------------------------------

    def update_context(self, player_box, enemies, teammates, walls,
                       player_center=None, player_radius=None,
                       joystick_active=False, joystick_vector=None):
        """Publish the latest YOLO detections for the tracker to use."""
        now = time.time()
        context = FrameContext(
            player_box=player_box,
            enemies=list(enemies or []),
            teammates=list(teammates or []),
            walls=list(walls or []),
            joystick_active=joystick_active,
            joystick_vector=joystick_vector,
            stamp=now,
        )
        with self._lock:
            self._context = context
            # Fresh boxes, so the drift correction accumulated since the last
            # update is no longer needed. The pan since the previous update is
            # handed to the enemy tracker instead, which needs it to convert
            # screen motion into world motion.
            pan = self._accumulated_shift
            self._accumulated_shift = (0.0, 0.0)
            if player_center is not None:
                self._player_center = player_center
            if player_radius:
                self._player_radius = player_radius

        if self.config.aim_enabled:
            self.enemy_tracker.update(context.enemies, pan, now)

    # ------------------------------------------------------------------
    # aiming
    # ------------------------------------------------------------------

    def tracked_enemies(self):
        if not self.config.aim_enabled:
            return []
        return self.enemy_tracker.tracked()

    def aim_at(self, shooter_pos, target_pos, projectile_speed=None):
        """Lead a target. Returns an AimSolution, or None to just tap."""
        if not self.config.aim_enabled:
            return None

        enemy = self.enemy_tracker.find(target_pos)
        velocity = (enemy.vx, enemy.vy) if enemy else (0.0, 0.0)
        confidence = enemy.confidence if enemy else 0.0
        return self.aim_solver.solve(
            shooter_pos,
            target_pos,
            velocity,
            projectile_speed=projectile_speed,
            confidence=confidence,
        )

    @property
    def motion(self):
        """Map-boundary and stall state measured from the camera."""
        return self.tracker.motion

    def set_tactical_intent(self, vector, is_blocked=None, gas_veto=None):
        """Tell the emergency path what the playstyle currently wants to do.

        `gas_veto` takes a joystick vector and returns True if it leads into
        poison. The emergency path bypasses the playstyle entirely - that is
        the point of it - so without this it could sidestep a shot straight
        into the gas, which costs far more health than the shot would have.
        """
        with self._lock:
            self._tactical_vector = vector
            if is_blocked is not None:
                self._is_blocked = is_blocked
            self._gas_veto = gas_veto

    def get_projectiles(self):
        with self._lock:
            return list(self._projectiles)

    def hazards(self):
        """Ground hazards left by throwers: mines, puddles, fire."""
        if not self.config.enabled or not self.config.hazard_enabled:
            return []
        return self.tracker.hazards()

    def leads_into_hazard(self, origin, vector, reach=None):
        """Would stepping this way from `origin` enter a hazard?

        Swept against the segment rather than the end point: a mine sitting
        halfway along a short sidestep still gets walked over.
        """
        hazards = self.hazards()
        if not hazards or not vector or origin is None:
            return False

        length = math.hypot(vector[0], vector[1])
        if length < 1e-6:
            return False
        travel = reach if reach is not None else self.config.hazard_clearance * 4.0
        ux, uy = vector[0] / length, vector[1] / length
        end = (origin[0] + ux * travel, origin[1] + uy * travel)

        clearance = self.config.hazard_clearance + self._player_radius
        for hazard in hazards:
            if _segment_hits_circle(origin, end, (hazard.x, hazard.y),
                                    hazard.radius + clearance):
                return True
        return False

    def get_decision(self):
        with self._lock:
            return self._decision

    @property
    def player_speed(self):
        return self.tracker.player_speed

    def process_frame(self, frame, stamp=None):
        """Inline (non-threaded) tracking, driven by the bot loop."""
        if not self.config.enabled or self.config.threaded:
            return
        self._process(frame, stamp or time.time(), emergency=False)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _run(self):
        # Above ~60 Hz the extra frames buy almost nothing: a projectile moves
        # a third of its own radius in 8 ms, so the velocity fit barely
        # improves, while the tracker's ~3.3 ms per frame turns into a third of
        # a core that the emulator would rather spend rendering the game.
        min_interval = 1.0 / self.config.tracker_max_fps if self.config.tracker_max_fps else 0.0
        last_processed = 0.0

        while not self._stop_event.is_set():
            frame, frame_time = self.window_controller.get_latest_frame()
            if frame is None or frame_time <= self._last_frame_time:
                # Poll tightly: sleeping a whole frame here would throw away
                # exactly the temporal resolution this thread exists for.
                self._stop_event.wait(0.002)
                continue

            if min_interval and frame_time - last_processed < min_interval:
                self._stop_event.wait(0.002)
                continue
            last_processed = frame_time

            self._last_frame_time = frame_time
            try:
                self._process(frame, frame_time, emergency=True)
            except Exception as exc:
                print(f"Dodge tracker error: {exc}")
                self._stop_event.wait(0.05)

    def _process(self, frame, stamp, emergency):
        with self._lock:
            context = self._context
            shift = self._accumulated_shift
            player_center = self._player_center
            player_radius = self._player_radius
            tactical = self._tactical_vector
            is_blocked = self._is_blocked
            gas_veto = self._gas_veto

        aged_context = context.shift_by(shift[0], shift[1]) if any(shift) else context
        projectiles, frame_shift = self.tracker.update(frame, aged_context, stamp)

        self.log.log_motion(self.tracker.motion, self.tracker.player_speed, tactical, stamp,
                            context=context)

        decision = None
        if player_center is not None:
            if projectiles:
                self.log.log_projectiles(projectiles, player_center, self.tracker.player_speed)
            decision = self.solver.solve(
                projectiles,
                player_center,
                player_radius,
                tactical,
                is_blocked,
                hazard_veto=gas_veto,
                player_speed=self.tracker.player_speed,
                now=stamp,
                motion=self.tracker.motion,
                collect_analysis=self.config.log_enabled and self.config.log_candidates,
            )

        with self._lock:
            self._projectiles = projectiles
            self._decision = decision
            self._decision_stamp = stamp
            self._accumulated_shift = (
                shift[0] + frame_shift[0],
                shift[1] + frame_shift[1],
            )

        applied = None
        if emergency and decision is not None and decision.critical and decision.vector:
            # Critical impacts only.
            #
            # Firing this on every active dodge was a mistake: this thread runs
            # at 60 Hz, the escape vector changes slightly each frame, so it
            # pushed a touch event per frame. That flooded ADB and the emulator
            # (halving the game's framerate) and kept the joystick under
            # priority almost continuously, which starved ordinary movement -
            # the bot stopped walking to its teammate entirely.
            # Backstop only. The solver was given the same veto and has
            # already scored poisoned directions out of contention, so this
            # should now fire almost never - if the count in the log climbs
            # again, the two are disagreeing and that is the bug to chase.
            if gas_veto is not None and gas_veto(decision.vector):
                self.log.log_note("dodge_refused_hazard_late",
                                  vector=[round(decision.vector[0], 1),
                                          round(decision.vector[1], 1)])
            else:
                self._apply_emergency(decision.vector)
                applied = decision.vector

        if decision is not None and player_center is not None:
            self.log.log_decision(
                decision, player_center, self.tracker.player_speed, tactical, applied
            )

        if self.config.log_stats:
            self._log_stats(stamp, projectiles)

    def _apply_emergency(self, vector, hold=None):
        controller = self.window_controller
        mover = getattr(controller, "move_with_priority", None)
        if mover is None:
            return

        # The solver re-derives the escape every frame and it wobbles by a
        # degree or two, so the raw vector is "new" each time even when the
        # intent has not changed. Re-sending it 60 times a second is pure load
        # on ADB and on the emulator's input handling, for no behavioural gain.
        length = math.hypot(vector[0], vector[1])
        if length > 1e-6 and self._last_emergency is not None:
            previous = self._last_emergency
            alignment = (vector[0] * previous[0] + vector[1] * previous[1]) / length
            if alignment > 0.97:
                return

        self._last_emergency = (vector[0] / length, vector[1] / length) if length > 1e-6 else None
        try:
            mover(vector[0], vector[1], hold=hold or self._emergency_hold)
        except Exception as exc:
            print(f"Dodge emergency move failed: {exc}")

    def _log_stats(self, stamp, projectiles):
        if stamp - self._last_stats_log < 1.0:
            return
        self._last_stats_log = stamp
        stats = self.tracker.stats
        speeds = ", ".join(f"{math.hypot(p.vx, p.vy):.0f}" for p in projectiles) or "-"
        print(
            f"[dodge] blobs={stats['blobs']} rejected={stats['rejected']} "
            f"tracks={stats['tracks']} confirmed={stats['confirmed']} "
            f"speeds=[{speeds}] player_speed={self.tracker.player_speed:.0f} "
            f"cost={stats['ms']:.1f}ms"
        )
