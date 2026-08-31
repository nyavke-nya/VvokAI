"""Camera-compensated projectile tracking.

Pipeline, in the order noise is removed:

  1. downscale                 - 8x less work, projectiles survive at 640x360
  2. phase correlation         - estimate how far the camera panned this frame
  3. three-frame difference    - motion, with the previous position cancelled
  4. terrain palette           - drop pixels that look like map, not projectile
  5. static + entity masking   - HUD, brawlers, healthbars, walls
  6. connected components      - candidate blobs, filtered by size and shape
  7. track association         - nearest neighbour with a predicted-position gate
  8. confirmation              - straight flight, plausible speed, enemy origin

Steps 2 and 8 are the ones that matter. Naive detectors fail because the camera
follows the player, so the entire background registers as motion the moment the
bot walks; and because muzzle flashes, grass animation and healthbars all move.
Requiring a straight line away from an enemy box removes essentially all of it.
"""

import math
import time
from collections import deque

import cv2
import numpy as np

from dodge.motion import MotionMonitor
from dodge.palette import TerrainPalette


# Screen-fixed HUD regions as fractions of (width, height). These never move
# with the camera, so they would otherwise light up the difference image every
# time they animate: the joystick, the attack/super/gadget buttons, the top
# scoreboard and timer.
HUD_REGIONS = (
    (0.00, 0.00, 1.00, 0.10),   # top bar: team icons, score, timer
    (0.00, 0.60, 0.24, 1.00),   # bottom left: movement joystick
    (0.72, 0.52, 1.00, 1.00),   # bottom right: attack / super / gadget
    (0.82, 0.24, 1.00, 0.50),   # right: teammate portraits and chat bubbles
)

# Patches used to measure the camera pan, as fractions of (width, height).
#
# They deliberately avoid the middle of the screen. The camera is locked to the
# player, so the player sprite is the one large object that does NOT move with
# the world - measuring over the centre drags the estimate toward zero and
# leaves several pixels of uncompensated background motion, which is exactly
# the noise this whole stage exists to remove. They also avoid the HUD, for the
# same reason.
CAMERA_PATCHES = (
    (0.06, 0.13, 0.32, 0.40),   # upper left
    (0.60, 0.13, 0.86, 0.40),   # upper right
    (0.06, 0.45, 0.32, 0.74),   # lower left
    (0.54, 0.45, 0.80, 0.74),   # lower right
)


# Ceiling on live tracks. Association and merging are both quadratic in this
# number, and the tracker runs on its own thread at capture rate, so a frame
# that produces hundreds of blobs (an explosion, a scene wipe) must not be able
# to stall it. Comfortably above what a real fight produces.
MAX_TRACKS = 96


def _median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


class Projectile:
    """A confirmed incoming shot, in full-frame pixel coordinates."""

    __slots__ = ("id", "x", "y", "vx", "vy", "radius", "hits", "age", "origin",
                 "confidence", "origin_reason", "urgent")

    def __init__(self, track_id, x, y, vx, vy, radius, hits, age, origin, confidence,
                 origin_reason="unknown", urgent=False):
        self.id = track_id
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.radius = radius
        self.hits = hits
        self.age = age
        self.origin = origin
        self.confidence = confidence
        # Why this shot was or was not credited to an enemy box; drives the
        # 3-frame vs 5-frame confirmation path.
        self.origin_reason = origin_reason
        # Taken before it had the usual evidence, because waiting for the rest
        # would have cost more time than the dodge had left. Logged so the
        # report can show how much of the dodging this is responsible for.
        self.urgent = urgent

    @property
    def speed(self):
        return math.hypot(self.vx, self.vy)

    def position_at(self, dt):
        return self.x + self.vx * dt, self.y + self.vy * dt

    def as_dict(self):
        return {
            "id": self.id,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "vx": round(self.vx, 1),
            "vy": round(self.vy, 1),
            "radius": round(self.radius, 1),
            "confidence": round(self.confidence, 2),
        }


class FrameContext:
    """Everything the tracker needs to know about the current game state.

    Populated by the main loop from YOLO output. The tracker runs faster than
    YOLO does, so these boxes are usually a frame or two stale; `shift_by`
    slides the world-anchored ones along with the camera to compensate.
    """

    __slots__ = ("player_box", "enemies", "teammates", "walls", "joystick_active",
                 "joystick_vector", "stamp")

    def __init__(self, player_box=None, enemies=None, teammates=None, walls=None,
                 joystick_active=False, joystick_vector=None, stamp=0.0):
        self.player_box = player_box
        self.enemies = enemies or []
        self.teammates = teammates or []
        self.walls = walls or []
        self.joystick_active = joystick_active
        self.joystick_vector = joystick_vector
        self.stamp = stamp

    def shift_by(self, dx, dy):
        if not dx and not dy:
            return self

        def move(boxes):
            return [[b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy] for b in boxes if len(b) >= 4]

        # The player box stays put: the camera is locked to the player, so on
        # screen they barely move even while the world scrolls underneath.
        return FrameContext(
            player_box=self.player_box,
            enemies=move(self.enemies),
            teammates=move(self.teammates),
            walls=move(self.walls),
            joystick_active=self.joystick_active,
            joystick_vector=self.joystick_vector,
            stamp=self.stamp,
        )


class _Track:
    __slots__ = ("id", "samples", "missed", "radius", "colour_score",
                 "origin_enemies", "origin_pos", "confirmed", "born_at",
                 "origin_reason", "velocity", "seed_dir", "matched", "peak_speed",
                 "urgent_confirm")

    def __init__(self, track_id, stamp, x, y, radius, colour_score, enemies, seed_dir=None):
        self.id = track_id
        self.samples = deque(maxlen=12)
        self.samples.append((stamp, x, y))
        self.missed = 0
        self.radius = radius
        self.colour_score = colour_score
        # Snapshot of enemy centres at birth. Kept rather than re-read later,
        # because by the time the track is confirmed the enemy has moved and
        # the "did this come out of them" test would be against the wrong spot.
        self.origin_enemies = enemies
        self.origin_pos = (x, y)
        self.confirmed = False
        # Set when the track was taken before it had the hits normally wanted,
        # because the shot would have landed first. Recorded so the log can
        # separate these from ordinary confirmations.
        self.urgent_confirm = False
        self.born_at = stamp
        self.origin_reason = "pending"
        self.velocity = (0.0, 0.0)
        # Unit vector away from the enemy this blob appeared next to. A shot
        # leaving a brawler can only travel outward, so on the very first frame
        # - when there is no measured velocity yet - this is the only clue about
        # where to look for the blob next, and it is a good one.
        self.seed_dir = seed_dir
        # Frames actually matched, which is not len(samples): the deque caps at
        # 12, so a long-lived track would otherwise look permanently young.
        self.matched = 1
        # Fastest this track has ever been measured at. A landed hazard must
        # have genuinely flown at some point; something that merely twitched in
        # place never qualifies.
        self.peak_speed = 0.0

    @property
    def hits(self):
        return len(self.samples)

    @property
    def last(self):
        return self.samples[-1]

    def predict(self, stamp, velocity):
        last_t, last_x, last_y = self.samples[-1]
        dt = stamp - last_t
        return last_x + velocity[0] * dt, last_y + velocity[1] * dt

    def absorb(self, other):
        """Fold a duplicate track into this one, keeping this identity.

        Only the samples the older track is missing are taken, so the velocity
        fit stays monotonic in time.
        """
        newest = self.samples[-1][0]
        for sample in other.samples:
            if sample[0] > newest:
                self.samples.append(sample)
        self.matched = max(self.matched, other.matched)
        self.peak_speed = max(self.peak_speed, other.peak_speed)
        self.missed = min(self.missed, other.missed)
        if self.seed_dir is None:
            self.seed_dir = other.seed_dir
        if not self.origin_enemies:
            self.origin_enemies = other.origin_enemies


class Hazard:
    """Something a thrower left on the ground, in full-frame pixels.

    Tick's mines, Barley's and Grom's puddles, Sprout's hedge, Amber's fire -
    all of them arrive as a lobbed shot and then stay put doing damage. To the
    projectile tracker they simply stop being projectiles, which is why the bot
    used to walk straight through them.
    """

    __slots__ = ("id", "x", "y", "radius", "born_at", "seen_at", "source")

    def __init__(self, hazard_id, x, y, radius, born_at, source):
        self.id = hazard_id
        self.x = x
        self.y = y
        self.radius = radius
        self.born_at = born_at
        self.seen_at = born_at
        # Track id of the shot that became this, kept for the log.
        self.source = source

    def age(self, now):
        return now - self.born_at

    def as_dict(self):
        return {
            "id": self.id,
            "x": round(self.x, 1),
            "y": round(self.y, 1),
            "r": round(self.radius, 1),
        }


class ProjectileTracker:
    def __init__(self, config):
        self.config = config
        self.palette = TerrainPalette(config)
        # Fed from the same camera-shift estimate the difference stage uses,
        # so map-boundary detection comes essentially for free.
        self.motion = MotionMonitor(config)

        # Frame size, filled in on the first update. Until then the interface
        # test does not fire, which is the safe direction: it can only ever
        # reject a track, so not knowing means rejecting nothing.
        self._frame_width = 0
        self._frame_height = 0

        self._prev_gray = None
        self._prev2_gray = None
        self._prev_gray_half = None
        self._prev_stamp = 0.0
        self._prev_shift = (0.0, 0.0)
        # Smoothed gap between frames, i.e. what waiting for one more sample
        # costs. Zero until two frames have been seen.
        self._frame_interval = 0.0
        self._patches = None
        self._patch_shape = None
        self._static_mask = None
        self._static_mask_shape = None
        self._kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

        self._tracks = []
        self._next_id = 1
        self._player_speed = config.default_player_speed
        # ~1.5 s of samples at 30 FPS: long enough to be steady, short enough
        # to follow a hypercharge or a speed gadget.
        self._speed_samples = deque(maxlen=45)

        # Kept purely so the debug overlay can show what the detector saw
        # before filtering, which is the only honest way to judge how much
        # noise is getting through.
        self.debug_blobs = []

        # Ground hazards: shots that landed and stayed. Held in screen
        # coordinates and slid along with the camera each frame, the same way
        # the YOLO boxes are.
        self._hazards = []
        self._next_hazard_id = 1

        # Speeds of shots seen leaving the player. Feeds the aim solver, which
        # otherwise has to guess at a single constant for every brawler.
        self._own_shot_speeds = deque(maxlen=60)

        self.stats = {
            "blobs": 0, "tracks": 0, "confirmed": 0, "ms": 0.0, "rejected": 0,
            # Why tracks die and why they fail to confirm. Without these the
            # only visible symptom is "the overlay is full of amber circles",
            # which has half a dozen possible causes.
            "lost": 0, "merged": 0, "born": 0,
            "rejects": {"few_hits": 0, "speed": 0, "bent": 0, "origin": 0,
                        "weak_seed": 0, "interface": 0},
        }

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    @property
    def player_speed(self):
        return self._player_speed

    def debug_snapshot(self, max_items=60):
        """Raw candidates and unconfirmed tracks, for the debug overlay.

        Three tiers are reported so the noise floor is visible rather than
        hidden: every blob that passed the size/shape filter, every track that
        has history but has not been confirmed, and (separately, via update's
        return value) the confirmed projectiles. If the grey tier is crowded,
        raise motion_threshold; if the amber tier never promotes, the origin
        or straightness gates are too strict.
        """
        blobs = [
            {"x": int(b[0]), "y": int(b[1]), "r": int(b[2]), "c": round(b[3], 2)}
            for b in self.debug_blobs[:max_items]
        ]

        pending = []
        trails = []
        for track in self._tracks[:max_items]:
            _, x, y = track.last
            # The whole history, not the last four points. A shot's trail is the
            # one thing that makes it obvious at a glance whether the tracker is
            # following one object or re-acquiring a new one every frame.
            trail = [[int(s[1]), int(s[2])] for s in track.samples]
            if track.confirmed:
                trails.append({"id": track.id, "trail": trail})
                continue
            pending.append({
                "x": int(x),
                "y": int(y),
                "r": int(track.radius),
                "hits": track.hits,
                "age": round(self._age_of(track), 2),
                "trail": trail,
            })

        return {
            "blobs": blobs,
            "pending": pending,
            "trails": trails,
            "stats": dict(self.stats),
        }

    @staticmethod
    def _age_of(track):
        return track.samples[-1][0] - track.born_at

    def reset(self):
        self._prev_gray = None
        self._prev2_gray = None
        self._prev_gray_half = None
        self._tracks = []
        self._speed_samples.clear()
        self._own_shot_speeds.clear()
        self._hazards = []
        self._player_speed = self.config.default_player_speed

    def update(self, frame, context, stamp=None):
        """Process one frame. Returns (projectiles, camera_shift)."""
        started = time.perf_counter()
        stamp = time.time() if stamp is None else stamp
        config = self.config
        step = config.downscale

        height, width = frame.shape[:2]
        self._frame_width, self._frame_height = width, height
        if config.downscale <= 0:
            # Auto: aim for a fixed working width regardless of what the capture
            # resolution happens to be. A fixed divisor would silently halve the
            # detector's effective resolution the moment capture_max_width is
            # lowered to give the emulator's encoder some breathing room.
            step = max(1, int(round(width / max(config.target_width, 1))))
        # INTER_LINEAR rather than stride slicing: 3x faster here, and the
        # averaging keeps small bright projectiles alive instead of letting
        # them fall between sample points.
        small = cv2.resize(frame, (width // step, height // step), interpolation=cv2.INTER_LINEAR)
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)

        gray_half = self._reduce_for_estimate(gray)

        dt = stamp - self._prev_stamp if self._prev_stamp else 0.0
        if self._prev_gray is None or self._prev_gray_half is None or dt <= 0 or dt > 0.5:
            self._roll(gray, gray_half, stamp, (0.0, 0.0))
            return [], (0.0, 0.0)

        # What one more sample costs, measured rather than assumed - the rate
        # swings with the emulator and with what is on screen. Smoothed, because
        # a single long frame should not convince the confirmation gate that
        # every shot is now urgent. Kept here so it does not depend on whether
        # _collect happens to run before or after _roll.
        self._frame_interval = (dt if not self._frame_interval
                                else self._frame_interval * 0.7 + dt * 0.3)

        shift, reliable = self._estimate_camera_shift(self._prev_gray_half, gray_half)
        if not reliable:
            # A scene cut, a death animation or a screen-filling effect. Trust
            # nothing this frame and start the difference chain over.
            self._tracks = []
            self._roll(gray, gray_half, stamp, (0.0, 0.0))
            return [], (0.0, 0.0)

        self._age_hazards(shift, step, stamp)
        self._update_player_speed(shift, dt, context.joystick_active, step)
        player_center = None
        if context.player_box and len(context.player_box) >= 4:
            box = context.player_box
            player_center = ((box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5)
        self.motion.update(
            camera_shift=(shift[0] * step, shift[1] * step),
            dt=dt,
            joystick_vector=context.joystick_vector if context.joystick_active else None,
            expected_speed=self._player_speed,
            player_center=player_center,
            screen_size=(width, height),
            now=stamp,
        )

        mask = self._motion_mask(gray, shift)
        if mask is None:
            self._roll(gray, gray_half, stamp, shift)
            return [], (shift[0] * step, shift[1] * step)

        hsv = None
        cached_terrain = [None]
        if self.palette.enabled:
            hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
            self.palette.update_adaptive(hsv)
            if config.palette_mode == "hard":
                cached_terrain[0] = self.palette.not_terrain_mask(hsv)
                cv2.bitwise_and(mask, cached_terrain[0], dst=mask)

        def not_terrain():
            # In soft mode the mask is only needed to score blobs, and most
            # frames have none. Building it lazily saves ~0.2 ms every frame
            # where nothing is flying.
            if cached_terrain[0] is None and hsv is not None:
                cached_terrain[0] = self.palette.not_terrain_mask(hsv)
            return cached_terrain[0]

        self._apply_static_mask(mask)
        self._apply_entity_mask(mask, context, step)

        if config.morph_open:
            cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel, dst=mask)

        blobs = self._find_blobs(mask, not_terrain, step)
        self.stats["blobs"] = len(blobs)
        self.debug_blobs = blobs

        self._associate(blobs, stamp, context)
        projectiles = self._collect(stamp, context)

        self._roll(gray, gray_half, stamp, shift)
        self.stats["tracks"] = len(self._tracks)
        self.stats["confirmed"] = len(projectiles)
        self.stats["ms"] = (time.perf_counter() - started) * 1000.0
        return projectiles, (shift[0] * step, shift[1] * step)

    # ------------------------------------------------------------------
    # vision
    # ------------------------------------------------------------------

    def _roll(self, gray, gray_half, stamp, shift):
        self._prev2_gray = self._prev_gray
        self._prev_gray = gray
        self._prev_gray_half = gray_half
        self._prev_stamp = stamp
        self._prev_shift = shift

    def _reduce_for_estimate(self, gray):
        factor = self.config.camera_estimate_downscale
        if factor <= 1:
            return gray
        return cv2.resize(
            gray,
            (gray.shape[1] // factor, gray.shape[0] // factor),
            interpolation=cv2.INTER_LINEAR,
        )

    def _build_patches(self, shape):
        height, width = shape[:2]
        patches = []
        for x1, y1, x2, y2 in CAMERA_PATCHES:
            px1, py1 = int(width * x1), int(height * y1)
            px2, py2 = int(width * x2), int(height * y2)
            # phaseCorrelate needs matching, non-degenerate, even-sized patches.
            px2 -= (px2 - px1) % 2
            py2 -= (py2 - py1) % 2
            if px2 - px1 < 24 or py2 - py1 < 24:
                continue
            size = (px2 - px1, py2 - py1)
            patches.append((px1, py1, px2, py2, cv2.createHanningWindow(size, cv2.CV_32F)))
        return patches

    def _estimate_camera_shift(self, previous, current):
        """Global translation between two frames, in downscaled pixels.

        This is the step that makes the whole detector viable. The camera is
        locked to the player, so without it every pixel of the map registers as
        motion the instant the bot walks.

        Measured over four off-centre patches and reduced with a median, so a
        brawler or an explosion sitting in one patch cannot drag the estimate:
        it would have to corrupt three of the four to matter.
        """
        if not self.config.camera_enabled:
            return (0.0, 0.0), True

        shape = current.shape[:2]
        if self._patches is None or self._patch_shape != shape:
            self._patches = self._build_patches(shape)
            self._patch_shape = shape
        if not self._patches:
            return (0.0, 0.0), True

        xs = []
        ys = []
        for px1, py1, px2, py2, hann in self._patches:
            previous_patch = previous[py1:py2, px1:px2].astype(np.float32)
            current_patch = current[py1:py2, px1:px2].astype(np.float32)
            try:
                (dx, dy), response = cv2.phaseCorrelate(previous_patch, current_patch, hann)
            except cv2.error:
                continue
            if response < self.config.camera_min_response:
                continue
            xs.append(dx)
            ys.append(dy)

        if len(xs) < 2:
            return (0.0, 0.0), False

        factor = self.config.camera_estimate_downscale
        dx = _median(xs) * factor
        dy = _median(ys) * factor

        limit = self.config.camera_max_shift / self.config.downscale
        if abs(dx) > limit or abs(dy) > limit:
            return (0.0, 0.0), False

        return (dx, dy), True

    def _motion_mask(self, gray, shift):
        """Three-frame difference, both references warped onto the current view.

        Two-frame differencing lights up the projectile twice: where it is now
        and where it was. Intersecting two differences leaves only "now", which
        keeps the centroid honest and stops one shot spawning two tracks.
        """
        if self._prev2_gray is None:
            return None

        aligned_prev = self._warp(self._prev_gray, shift)
        total = (shift[0] + self._prev_shift[0], shift[1] + self._prev_shift[1])
        aligned_prev2 = self._warp(self._prev2_gray, total)

        threshold = self.config.motion_threshold
        diff1 = cv2.absdiff(gray, aligned_prev)
        diff2 = cv2.absdiff(gray, aligned_prev2)
        _, mask1 = cv2.threshold(diff1, threshold, 255, cv2.THRESH_BINARY)
        _, mask2 = cv2.threshold(diff2, threshold, 255, cv2.THRESH_BINARY)
        return cv2.bitwise_and(mask1, mask2)

    @staticmethod
    def _warp(image, shift):
        if abs(shift[0]) < 0.01 and abs(shift[1]) < 0.01:
            return image
        matrix = np.array([[1.0, 0.0, shift[0]], [0.0, 1.0, shift[1]]], dtype=np.float32)
        return cv2.warpAffine(
            image,
            matrix,
            (image.shape[1], image.shape[0]),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_REPLICATE,
        )

    def _apply_static_mask(self, mask):
        shape = mask.shape[:2]
        if self._static_mask is None or self._static_mask_shape != shape:
            height, width = shape
            static = np.full(shape, 255, dtype=np.uint8)
            for x1, y1, x2, y2 in HUD_REGIONS:
                cv2.rectangle(
                    static,
                    (int(width * x1), int(height * y1)),
                    (int(width * x2), int(height * y2)),
                    0,
                    -1,
                )
            self._static_mask = static
            self._static_mask_shape = shape
        cv2.bitwise_and(mask, self._static_mask, dst=mask)

    # How far above a brawler its name and trophy count sit, as a fraction of
    # the box height. Measured off frames the bot saved while playing.
    NAMEPLATE_ABOVE = 0.75

    # The gaps HUD_REGIONS leaves open, as fractions of the frame.
    #
    # That constant masks the top tenth of the screen and the side panels, and
    # the kill feed sits below it: measured off frames this tracker labelled
    # while the bot played, the false detections landed between y=0.14 and
    # y=0.23 - under the masked bar, over the play field.
    #
    # Not added to HUD_REGIONS, because a mask blinds. The game is drawn
    # underneath the kill feed and shots do cross that strip, so blanking it
    # would trade a false dodge for a missed one. These are used by the
    # trajectory test below instead.
    INTERFACE_GAPS = (
        (0.00, 0.10, 0.38, 0.24),   # kill feed, under the scoreboard bar
        (0.82, 0.10, 1.00, 0.24),   # between the top bar and the side cards
    )

    def _only_ever_on_the_interface(self, track):
        """Whether this track never left a corner the interface owns.

        HUD_REGIONS already blanks the parts of the screen that are always
        interface. This covers what it deliberately leaves alone: the strip the
        kill feed slides through, which is over the play field and where shots
        really do fly.

        So it is a test, not a mask. Blanking that strip would trade a false
        dodge for a missed one. What separates the two cases is that a shot
        passes through and a kill-feed card slides in and stops, so the test is
        on the whole path: a track every sample of which stayed inside the strip
        never was a shot.
        """
        width = self._frame_width
        height = self._frame_height
        if not width or not height:
            return False

        for _, x, y in track.samples:
            fx, fy = x / width, y / height
            inside = False
            for x1, y1, x2, y2 in self.INTERFACE_GAPS:
                if x1 <= fx <= x2 and y1 <= fy <= y2:
                    inside = True
                    break
            if not inside:
                return False
        return True

    def _apply_entity_mask(self, mask, context, step):
        height, width = mask.shape[:2]
        config = self.config

        def blank(boxes, inflate):
            pad = inflate / step
            for box in boxes:
                if len(box) < 4:
                    continue
                x1 = int(max(0, box[0] / step - pad))
                y1 = int(max(0, box[1] / step - pad))
                x2 = int(min(width, box[2] / step + pad))
                y2 = int(min(height, box[3] / step + pad))
                if x2 > x1 and y2 > y1:
                    mask[y1:y2, x1:x2] = 0

        def blank_nameplate(boxes):
            """The name, trophy count and health bar stacked above a brawler.

            The generic pad was supposed to cover this and does not: collecting
            from this tracker put projectile detections squarely on trophy
            counters and player names. The plate is taller than the pad and it
            flickers whenever a number changes, which is movement in exactly
            the place the pad stops short of.

            Blinding nothing new - the brawler underneath is already blanked,
            and this only extends that upward over its own label.
            """
            for box in boxes:
                if len(box) < 4:
                    continue
                plate = (box[3] - box[1]) * self.NAMEPLATE_ABOVE
                x1 = int(max(0, box[0] / step))
                y1 = int(max(0, (box[1] - plate) / step))
                x2 = int(min(width, box[2] / step))
                y2 = int(min(height, box[1] / step))
                if x2 > x1 and y2 > y1:
                    mask[y1:y2, x1:x2] = 0

        blank(context.enemies, config.entity_mask_inflate)
        blank(context.teammates, config.entity_mask_inflate)
        blank_nameplate(context.enemies)
        blank_nameplate(context.teammates)
        if context.player_box and len(context.player_box) >= 4:
            blank([context.player_box], config.entity_mask_inflate)
            blank_nameplate([context.player_box])
        # Walls have height, so they parallax slightly differently from the
        # ground and leave a seam in the difference image.
        blank(context.walls, config.wall_mask_inflate)

    def _find_blobs(self, mask, not_terrain, step):
        if not cv2.countNonZero(mask):
            self.stats["rejected"] = 0
            return []

        count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8, cv2.CV_32S)
        config = self.config
        area_scale = step * step
        blobs = []
        rejected = 0

        for index in range(1, count):
            area = stats[index, cv2.CC_STAT_AREA] * area_scale
            if area < config.min_blob_area or area > config.max_blob_area:
                rejected += 1
                continue

            box_w = stats[index, cv2.CC_STAT_WIDTH]
            box_h = stats[index, cv2.CC_STAT_HEIGHT]
            longer, shorter = max(box_w, box_h), max(min(box_w, box_h), 1)
            if longer / shorter > config.max_blob_aspect:
                rejected += 1
                continue

            colour_score = 1.0
            if config.palette_mode == "soft":
                terrain_mask = not_terrain()
                if terrain_mask is not None:
                    x1 = stats[index, cv2.CC_STAT_LEFT]
                    y1 = stats[index, cv2.CC_STAT_TOP]
                    patch = terrain_mask[y1:y1 + box_h, x1:x1 + box_w]
                    if patch.size:
                        colour_score = float(cv2.countNonZero(patch)) / patch.size

            radius = min(
                max(math.sqrt(area / math.pi), config.min_radius),
                config.max_radius,
            )
            blobs.append((
                centroids[index][0] * step,
                centroids[index][1] * step,
                radius,
                colour_score,
            ))

        self.stats["rejected"] = rejected
        return blobs

    # ------------------------------------------------------------------
    # tracking
    # ------------------------------------------------------------------

    def _associate(self, blobs, stamp, context):
        """Match this frame's blobs to existing tracks, then seed the leftovers.

        Assignment is global and best-first rather than per-track greedy. The
        greedy version handed blobs out in list order, so a one-frame-old noise
        track sitting near a bullet's path could take the bullet's blob simply
        by being earlier in the array, while the track that predicted it to
        within a couple of pixels went unmatched and died. On real sessions that
        shredded every shot into ~20 short-lived fragments: the median confirmed
        track survived two frames, which is too short to draw a trail, too short
        for the solver's escape hold, and it re-ran confirmation from scratch
        each time.
        """
        config = self.config
        enemy_centres = [
            ((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)
            for b in context.enemies if len(b) >= 4
        ]

        # ---- predict every track forward and size its search gate -----------
        gates = []
        for track in self._tracks:
            velocity = self._fit_velocity(track)
            track.velocity = velocity
            dt = max(stamp - track.last[0], 1e-3)

            if track.hits >= 2:
                speed = math.hypot(velocity[0], velocity[1])
                predicted = track.predict(stamp, velocity)
                # Proportional error only. The old formula used (1 + slack),
                # which let a 1500 px/s track accept a blob 128 px off its own
                # prediction - wide enough to swallow unrelated noise.
                gate = config.gate_base + speed * dt * config.gate_slack
            else:
                # One sample: the velocity is unknown, so the blob may be
                # anywhere on a disc of plausible travel. Using gate_base here
                # (as this did before) capped the search at ~49 px, and a fast
                # shot covers 50-125 px per frame at the rates this tracker
                # really runs at - so fast shots could never acquire a second
                # sample and stayed unconfirmed forever. That is the "yellow
                # circles that never become tracks" symptom exactly.
                predicted = (track.last[1], track.last[2])
                gate = config.gate_base + config.seed_speed * dt
            gates.append((predicted, gate, dt))

        # ---- score every plausible pairing ----------------------------------
        pairs = []
        for track_index, track in enumerate(self._tracks):
            predicted, gate, dt = gates[track_index]
            px, py = predicted
            for blob_index, blob in enumerate(blobs):
                # Box test before the circle test. This loop is the tracker's
                # hot path - tracks times blobs, every frame - and on a frame
                # full of noise almost every pair fails, so the cheap rejection
                # is the one that matters.
                dx = blob[0] - px
                if dx > gate or dx < -gate:
                    continue
                dy = blob[1] - py
                if dy > gate or dy < -gate:
                    continue
                distance = math.hypot(dx, dy)
                if distance > gate:
                    continue

                cost = distance / gate
                if track.hits >= 2:
                    # An established track that predicted well should beat a
                    # newborn competing for the same blob.
                    cost *= 0.55
                elif track.seed_dir is not None and distance > 1e-6:
                    # Newborn next to an enemy: a shot can only fly away from
                    # whoever fired it, so blobs off that ray are much less
                    # likely to be the same object.
                    outward = (dx / distance) * track.seed_dir[0] + (dy / distance) * track.seed_dir[1]
                    cost += 0.7 * (1.0 - outward)
                pairs.append((cost, track_index, blob_index))

        pairs.sort()
        taken_tracks = set()
        taken_blobs = set()
        for _, track_index, blob_index in pairs:
            if track_index in taken_tracks or blob_index in taken_blobs:
                continue
            taken_tracks.add(track_index)
            taken_blobs.add(blob_index)
            track = self._tracks[track_index]
            blob = blobs[blob_index]
            track.samples.append((stamp, blob[0], blob[1]))
            track.radius = 0.7 * track.radius + 0.3 * blob[2]
            track.colour_score = 0.7 * track.colour_score + 0.3 * blob[3]
            track.missed = 0
            track.matched += 1
            # Re-fit now that the new sample is in, so _collect and the merge
            # pass can both read it instead of fitting the same track twice.
            track.velocity = self._fit_velocity(track)
            track.peak_speed = max(
                track.peak_speed,
                math.hypot(track.velocity[0], track.velocity[1]))

        lost = 0
        survivors = []
        for track_index, track in enumerate(self._tracks):
            if track_index not in taken_tracks:
                track.missed += 1
                if track.missed > config.max_missed_frames:
                    if track.confirmed:
                        lost += 1
                    continue
            survivors.append(track)
        self._tracks = survivors
        self.stats["lost"] = lost

        # ---- seed tracks for whatever is left over --------------------------
        born = 0
        suppress = config.gate_base * 0.5
        for blob_index, blob in enumerate(blobs):
            if blob_index in taken_blobs:
                continue
            # Morphology occasionally splits one bright shot into two adjacent
            # components. Spawning a track for the offcut doubles the id churn
            # and gives the merge pass extra work, so drop it here instead.
            if any(abs(blob[0] - t.last[1]) < suppress
                   and abs(blob[1] - t.last[2]) < suppress
                   and math.hypot(blob[0] - t.last[1], blob[1] - t.last[2]) < suppress
                   for t in self._tracks):
                continue
            self._tracks.append(_Track(
                self._next_id, stamp, blob[0], blob[1], blob[2], blob[3],
                enemy_centres, seed_dir=self._seed_direction(blob, enemy_centres),
            ))
            self._next_id += 1
            born += 1

        self.stats["born"] = born
        self._merge_duplicates()

        # Both the assignment and the merge pass are quadratic in track count,
        # so a pathological frame - a screen-filling explosion, a scene wipe -
        # must not be allowed to stall the tracker thread. Confirmed shots and
        # tracks with the most evidence are kept; raw one-sample seeds go first.
        if len(self._tracks) > MAX_TRACKS:
            self._tracks.sort(key=lambda t: (t.confirmed, t.matched, t.born_at),
                              reverse=True)
            self._tracks = self._tracks[:MAX_TRACKS]

    def _seed_direction(self, blob, enemy_centres):
        """Which way a blob born next to an enemy must be travelling."""
        best = None
        best_distance = self.config.spawn_radius
        for enemy in enemy_centres:
            dx = blob[0] - enemy[0]
            dy = blob[1] - enemy[1]
            distance = math.hypot(dx, dy)
            if 1e-6 < distance < best_distance:
                best_distance = distance
                best = (dx / distance, dy / distance)
        return best

    def _merge_duplicates(self):
        """Fold tracks that are plainly following the same object into one.

        Two things create duplicates: a shot that briefly splits into two blobs,
        and a track that was stolen and re-seeded before the fix above landed.
        Either way the solver sees two ids for one shot, the escape hold keys off
        the wrong one, and the log counts one bullet as several.
        """
        config = self.config
        radius = config.merge_radius
        merged = 0
        index = 0
        while index < len(self._tracks):
            track = self._tracks[index]
            if track.hits < 2:
                index += 1
                continue
            speed = math.hypot(track.velocity[0], track.velocity[1])
            other_index = index + 1
            while other_index < len(self._tracks):
                other = self._tracks[other_index]
                if other.hits < 2:
                    other_index += 1
                    continue
                dx = other.last[1] - track.last[1]
                dy = other.last[2] - track.last[2]
                if dx > radius or dx < -radius or dy > radius or dy < -radius:
                    other_index += 1
                    continue
                if math.hypot(dx, dy) > radius:
                    other_index += 1
                    continue
                other_speed = math.hypot(other.velocity[0], other.velocity[1])
                if speed < 1e-6 or other_speed < 1e-6:
                    other_index += 1
                    continue
                alignment = (track.velocity[0] * other.velocity[0]
                             + track.velocity[1] * other.velocity[1]) / (speed * other_speed)
                ratio = speed / other_speed
                if alignment < 0.9 or not 0.6 < ratio < 1.7:
                    other_index += 1
                    continue
                # Keep whichever has more evidence behind it, so the surviving
                # id is the one the solver has already been reacting to.
                if other.matched > track.matched:
                    other.absorb(track)
                    self._tracks[index] = other
                    track = other
                else:
                    track.absorb(other)
                # absorb() can add samples, so the cached fit is now stale.
                track.velocity = self._fit_velocity(track)
                speed = math.hypot(track.velocity[0], track.velocity[1])
                self._tracks.pop(other_index)
                merged += 1
            index += 1
        self.stats["merged"] = merged

    def _fit_velocity(self, track):
        """Least-squares velocity over the recent samples.

        A two-point difference at 30 FPS is dominated by centroid jitter; a fit
        over five samples is steady enough to extrapolate half a second ahead.
        """
        samples = list(track.samples)[-self.config.velocity_window:]
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

    @staticmethod
    def _straightness(track):
        samples = list(track.samples)
        if len(samples) < 3:
            return 0.0
        middle = len(samples) // 2
        ax = samples[middle][1] - samples[0][1]
        ay = samples[middle][2] - samples[0][2]
        bx = samples[-1][1] - samples[middle][1]
        by = samples[-1][2] - samples[middle][2]
        na = math.hypot(ax, ay)
        nb = math.hypot(bx, by)
        if na < 1e-6 or nb < 1e-6:
            return 0.0
        return (ax * bx + ay * by) / (na * nb)

    def _check_origin(self, track, velocity, context):
        """Decide whether this track was plausibly fired by a visible enemy.

        Returns (accepted, origin_point, required_hits). The reason is recorded
        on the track so the log can explain why a shot took the slow path:
        crediting a shot to an enemy confirms it in 3 frames instead of 5, which
        on real data is worth ~86 ms of extra dodging time.
        """
        config = self.config
        speed = math.hypot(velocity[0], velocity[1])
        if speed < 1e-6:
            track.origin_reason = "no_velocity"
            return False, None, config.min_confirm_hits

        ux, uy = velocity[0] / speed, velocity[1] / speed
        origin_x, origin_y = track.origin_pos

        if not track.origin_enemies:
            track.origin_reason = "no_enemy_detected"

        best = None
        best_distance = config.spawn_radius
        nearest = None
        rejected_angle = False
        for enemy in track.origin_enemies:
            dx = origin_x - enemy[0]
            dy = origin_y - enemy[1]
            distance = math.hypot(dx, dy)
            if nearest is None or distance < nearest:
                nearest = distance
            if distance > best_distance or distance < 1e-6:
                continue
            # The shot has to be travelling away from whoever fired it.
            outward = (dx / distance) * ux + (dy / distance) * uy
            if outward < config.min_outward_cosine:
                rejected_angle = True
                continue
            best_distance = distance
            best = enemy

        if best is not None:
            track.origin_reason = "enemy"
            return True, best, config.min_confirm_hits

        if track.origin_enemies:
            track.origin_reason = (
                "wrong_direction" if rejected_angle
                else f"too_far_{int(nearest)}px" if nearest is not None
                else "no_enemy_in_range"
            )

        if not config.require_enemy_origin:
            return True, None, config.min_confirm_hits

        if not config.allow_unknown_origin:
            return False, None, config.min_confirm_hits

        # No visible shooter. Accept only shots actually aimed at us, and make
        # them prove it over more frames.
        player_box = context.player_box
        if not player_box or len(player_box) < 4:
            return False, None, config.unknown_confirm_hits

        px = (player_box[0] + player_box[2]) * 0.5
        py = (player_box[1] + player_box[3]) * 0.5
        last = track.last
        to_player_x = px - last[1]
        to_player_y = py - last[2]
        distance = math.hypot(to_player_x, to_player_y)
        if distance < 1e-6:
            return True, None, config.unknown_confirm_hits

        aim = (to_player_x / distance) * ux + (to_player_y / distance) * uy
        aim = min(max(aim, -1.0), 1.0)
        if math.acos(aim) > config.unknown_max_aim_error:
            return False, None, config.unknown_confirm_hits

        return True, None, config.unknown_confirm_hits

    def _waiting_is_fatal(self, track, velocity, speed, context, stamp):
        """True when one more sample would arrive after the shot does.

        The confirmation gate spends frames buying certainty. That is the right
        trade while the shot is far enough away that the bot can still step
        clear afterwards, and the wrong one the moment it is not: past that
        point the extra evidence is delivered to a bot that has already been
        hit. On a real session 822 shots were missed by a median of 39 ms with
        88 ms of confirmation behind them - all of that certainty arrived too
        late to be spent.

        So the question asked here is not "is this definitely a shot" but "does
        waiting still cost nothing". Time to reach us is compared against the
        time needed to clear our own hitbox, plus the frame we would spend
        waiting. When that is already lost, the caller stops requiring extra
        hits - the checks on what the track IS are untouched.
        """
        config = self.config
        if not config.urgent_confirm or speed <= 1e-6:
            return False
        interval = self._frame_interval
        if interval <= 0.0:
            return False

        player_box = context.player_box
        if not player_box or len(player_box) < 4:
            return False
        px = (player_box[0] + player_box[2]) * 0.5
        py = (player_box[1] + player_box[3]) * 0.5

        last_t, last_x, last_y = track.last
        lead = max(stamp - last_t, 0.0)
        x = last_x + velocity[0] * lead
        y = last_y + velocity[1] * lead

        dx, dy = px - x, py - y
        distance = math.hypot(dx, dy)
        if distance <= 1e-6:
            return True
        # Only the part of the velocity actually closing on us counts; a shot
        # crossing in front is not getting closer however fast it travels.
        closing = (dx * velocity[0] + dy * velocity[1]) / distance
        if closing <= 1e-6:
            return False

        time_to_reach = distance / closing
        # How long stepping out of the way takes, from what the bot is actually
        # doing rather than a constant: its own measured speed, its own size.
        half_height = max((player_box[3] - player_box[1]) * 0.5, 1.0)
        clearance = half_height + config.safety_margin + track.radius
        escape = clearance / max(self._player_speed, 1e-6)

        return time_to_reach <= escape + interval * (1.0 + config.urgent_confirm_slack)

    def _collect(self, stamp, context):
        config = self.config
        projectiles = []
        rejects = {"few_hits": 0, "speed": 0, "bent": 0, "origin": 0,
                   "weak_seed": 0, "interface": 0}

        for track in self._tracks:
            if track.hits < config.min_confirm_hits:
                rejects["few_hits"] += 1
                continue

            # Already fitted during association, for every track that moved.
            velocity = track.velocity
            speed = math.hypot(velocity[0], velocity[1])
            if speed < config.min_speed or speed > config.max_speed:
                rejects["speed"] += 1
                # Too slow to be a shot in flight - but that is exactly what a
                # landed hazard looks like, and this branch is where they were
                # being thrown away.
                self._consider_hazard(track, stamp, speed)
                continue

            # Straightness needs three points to mean anything. With only two
            # the track is a straight line by definition, so the test is
            # skipped rather than failed - otherwise min_confirm_hits = 2 could
            # never confirm anything at all.
            straightness = self._straightness(track)
            if track.hits >= 3 and straightness < config.min_straightness:
                rejects["bent"] += 1
                continue

            # Our own shots never pass the origin gate below - it only accepts
            # things flying AT us - so they are measured here, on the way past.
            self._measure_own_shot(track, velocity, speed, context)

            accepted, origin, required_hits = self._check_origin(track, velocity, context)
            if not accepted:
                rejects["origin"] += 1
                continue
            if track.hits < required_hits:
                # Short of the evidence normally wanted. Taking it anyway is
                # only defensible when the track has a visible shooter behind
                # it, and that restriction is not caution - it is what a
                # session of real data forced.
                #
                # Without it, 99.3% of what this path admitted had no credible
                # origin at all: 61% had no enemy within spawn range, 32% had
                # no enemy on screen, 7% were flying back TOWARD the brawler
                # supposedly firing them. Only 0.7% came off an enemy, against
                # 64.7% of ordinary confirmations. That is not a coincidence -
                # the extra samples demanded of an unknown-origin track are
                # demanded precisely because such tracks are unreliable, and
                # "it is close to the player" is the condition a stray blob
                # near the player meets by definition.
                #
                # The cost was real and the benefit was not: 74% of them were
                # still too late to dodge, while the solver spent the session
                # being handed phantom threats. Dodging fell 90.6% -> 87.7%,
                # impossible situations rose 7.9% -> 10.1%, and the bot spent
                # 26% of its movement samples stuck against 15% before.
                if not (track.origin_reason == "enemy"
                        and track.hits >= config.min_confirm_hits
                        and self._waiting_is_fatal(track, velocity, speed, context, stamp)):
                    rejects["origin"] += 1
                    continue
                track.urgent_confirm = True

            # Confirming on two samples buys ~35 ms of reaction time, which
            # matters: only a third of shots are ever seen with enough time to
            # clear the hitbox. But two samples are also just two blobs in a
            # row, and shimmering grass produces those constantly. So the fast
            # path is allowed only with corroborating evidence - the track came
            # off an enemy and is flying the way that enemy was pointing.
            # Everything else waits for the third sample and the straightness
            # test that comes with it.
            if track.hits < 3 and not track.confirmed:
                if track.seed_dir is None or origin is None:
                    rejects["weak_seed"] += 1
                    continue
                outward = (velocity[0] * track.seed_dir[0]
                           + velocity[1] * track.seed_dir[1]) / speed
                if outward < config.seed_agreement:
                    rejects["weak_seed"] += 1
                    continue

            if self._only_ever_on_the_interface(track):
                rejects["interface"] += 1
                continue

            track.confirmed = True
            last_t, last_x, last_y = track.last
            # Extrapolate to now: the newest sample is already one frame old by
            # the time a decision is acted on.
            lead = max(stamp - last_t, 0.0)
            confidence = min(
                1.0,
                0.45 * min(track.hits / max(required_hits, 1), 1.5)
                + 0.35 * max(straightness, 0.0)
                + 0.20 * (track.colour_score if config.palette_mode == "soft" else 1.0),
            )

            projectiles.append(Projectile(
                track_id=track.id,
                x=last_x + velocity[0] * lead,
                y=last_y + velocity[1] * lead,
                vx=velocity[0],
                vy=velocity[1],
                radius=track.radius,
                hits=track.hits,
                age=stamp - track.born_at,
                origin=origin,
                confidence=confidence,
                origin_reason=track.origin_reason,
                urgent=track.urgent_confirm,
            ))

        self.stats["rejects"] = rejects
        return projectiles

    # ------------------------------------------------------------------
    # self-calibration
    # ------------------------------------------------------------------

    def _measure_own_shot(self, track, velocity, speed, context):
        """Learn how fast THIS brawler's shots fly, from watching them fly.

        The aim solver needs a projectile speed to turn a distance into a lead
        time, and a single configured constant cannot be right: measured across
        one session, real shots ranged from 411 to 3040 px/s with a median of
        1152. The default was 2600 - roughly the 85th percentile - so the lead
        came out less than half of what it should have been, and every shot at
        a moving target landed behind them.

        Our own shots are easy to pick out and nothing else looks like them:
        they appear next to the player and travel away. They are never
        confirmed as projectiles - the origin gate only accepts shots coming
        AT us - so this is measured separately, purely for the aim solver.
        """
        player_box = context.player_box
        if not player_box or len(player_box) < 4:
            return

        px = (player_box[0] + player_box[2]) * 0.5
        py = (player_box[1] + player_box[3]) * 0.5
        origin_x, origin_y = track.origin_pos
        dx, dy = origin_x - px, origin_y - py
        distance = math.hypot(dx, dy)
        if distance < 1e-6 or distance > self.config.spawn_radius:
            return

        # Travelling away from us, not past us.
        outward = (dx / distance) * (velocity[0] / speed) + (dy / distance) * (velocity[1] / speed)
        if outward < self.config.min_outward_cosine:
            return

        self._own_shot_speeds.append(speed)

    @property
    def own_projectile_speed(self):
        """Measured speed of our own shots, or None until enough are seen.

        A median rather than a mean: a mis-tracked frame produces a wild value
        and would drag an average badly, while the median simply ignores it.
        """
        if len(self._own_shot_speeds) < self.config.aim_speed_min_samples:
            return None
        return _median(self._own_shot_speeds)

    def _consider_hazard(self, track, stamp, speed):
        """Promote a stopped shot into a ground hazard.

        The whole design rests on one requirement: it must ALREADY have been a
        confirmed projectile. That is what keeps this free of the garbage a
        colour-based detector produces - and colour is the obvious approach,
        which is exactly why it is not used here. Tick's mines are pink, but so
        are plenty of map decorations; fire is orange, and so is desert sand.
        On the bench, colour alone read 25 of 40 patches of ordinary grass as a
        full health bar, and this would be no better.

        A hazard instead has to have earned a history: born next to an enemy,
        travelled away from them in a straight line, survived confirmation as a
        shot, and only THEN slowed to a stop. No animated scenery has that
        story, because scenery never arrives.
        """
        config = self.config
        if not track.confirmed or speed > config.hazard_max_speed:
            return
        if track.peak_speed < config.min_speed:
            # Never actually flew, so it was never a shot. Refuse it.
            return
        if track.hits < config.hazard_confirm_hits:
            return
        if config.hazard_require_enemy_origin and track.origin_reason != "enemy":
            # Stricter than the dodge path, deliberately. A shot with no
            # visible shooter is still worth sidestepping - that decision lasts
            # a fraction of a second and costs nothing if it was wrong. A
            # hazard fences off ground for seconds, so it is only accepted on
            # the strongest evidence: an enemy was actually seen to throw it.
            return

        _, x, y = track.last
        radius = max(track.radius, config.hazard_min_radius)

        # Already know about this one? A mine sits blinking for seconds and is
        # re-detected constantly; merging keeps one entry per object and lets
        # its lifetime be measured from when it first landed.
        for hazard in self._hazards:
            if math.hypot(hazard.x - x, hazard.y - y) < config.hazard_merge_radius:
                hazard.x = 0.7 * hazard.x + 0.3 * x
                hazard.y = 0.7 * hazard.y + 0.3 * y
                hazard.radius = max(hazard.radius, radius)
                hazard.seen_at = stamp
                return

        self._hazards.append(Hazard(self._next_hazard_id, x, y, radius, stamp, track.id))
        self._next_hazard_id += 1

    def _age_hazards(self, shift, step, stamp):
        """Slide hazards with the camera and drop the ones that have expired.

        They sit still in the world, so on screen they move exactly as the map
        does. Without this they would drift away from the thing they mark the
        moment the bot walks.
        """
        if not self._hazards:
            return

        dx = shift[0] * step
        dy = shift[1] * step
        config = self.config
        alive = []
        for hazard in self._hazards:
            hazard.x += dx
            hazard.y += dy
            if stamp - hazard.seen_at > config.hazard_ttl:
                # Not seen for a while: either it went off or it was never
                # really there. Either way it is no longer worth avoiding.
                continue
            if stamp - hazard.born_at > config.hazard_max_lifetime:
                continue
            alive.append(hazard)
        self._hazards = alive

    def hazards(self):
        return list(self._hazards)

    def _update_player_speed(self, shift, dt, joystick_active, step):
        """Learn how fast this brawler moves, in pixels, from the camera itself.

        The camera is locked to the player, so while the joystick is held the
        magnitude of the camera pan *is* the player's on-screen speed. That
        beats a hardcoded constant across brawlers, hypercharges and speed
        modifiers.

        Estimated as a high percentile of recent samples rather than a running
        maximum. A maximum is destroyed by a single outlier - a super dash, a
        respawn teleport, or two frames arriving with almost identical
        timestamps - and once it is wrong everything downstream breaks: the
        stall detector compares real speed against the inflated estimate, sees
        ~0.34 efficiency during ordinary walking, and reports the bot as stuck
        forever. A percentile ignores the outliers and the stalls alike.
        """
        if not joystick_active or dt < 0.008:
            # Sub-8 ms gaps put the tiny measured shift over a tiny dt, which
            # produces wild speeds. Not worth sampling.
            return

        instant = math.hypot(shift[0], shift[1]) * step / dt
        default = self.config.default_player_speed

        # Frames spent pressed against a wall must not enter the sample. They
        # are what the estimate is used to *detect*, so letting them in is
        # circular: being stuck drags the estimate down, the lower estimate
        # makes the stall look like normal walking, and the bot never notices.
        # Observed live as the estimate collapsing to its 132 px/s floor while
        # the bot sat in a corner for ten seconds.
        if instant < default * 0.25:
            return

        self._speed_samples.append(min(instant, default * 4.0))
        if len(self._speed_samples) < 8:
            return

        ordered = sorted(self._speed_samples)
        # 75th percentile: above the frames spent turning, below a dash burst.
        estimate = ordered[int(len(ordered) * 0.75)]
        self._player_speed = min(max(estimate, default * 0.4), default * 2.5)
