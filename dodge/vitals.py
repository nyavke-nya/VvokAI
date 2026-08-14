"""Reading health bars off the screen.

Nothing in the bot knew how much health anything had. It fought the same way at
15% as at 100%, and in Brawl Stars that is the single largest source of
avoidable deaths - far more than missed shots. A human disengages when the
trade stops being winnable; the bot had no way to even ask the question.

The detection model does not have a health-bar class and retraining it is not
on the table, but it does not need to be: a health bar is a saturated coloured
strip in a known place relative to a brawler, on a background that is never
that colour. That is the same observation the projectile palette is built on,
applied to a much easier target.

Geometry, measured off real 1080p frames:

    ┌───────────────┐  <- the bar sits in a band above the box, roughly
    │▓▓▓▓▓▓▓▓░░░░░░░│     0.15-0.55 of the box height above its top edge
    └───────────────┘
    ┌───────────────┐
    │               │  <- the detection box, the brawler sprite
    │      /o\      │
    └───────────────┘

The filled part is bright and saturated - green for the player and teammates,
red for enemies. The spent part is a dark, desaturated version of the same
track, enclosed by a near-black outline. So the fraction is

    filled_run / (filled_run + spent_run)

both measured on the single strongest row in the band, which is what this does.

Every reading carries a confidence, and the caller is expected to ignore low
ones rather than guess: a bar hidden behind a wall, under a shield effect, or
covered by a healing number is better reported as unknown than as a number that
happens to parse. The debug overlay draws whatever this returns, so a bad
reading is visible rather than silently steering the tactics.
"""

import math

import cv2
import numpy as np


class HealthReading:
    __slots__ = ("fraction", "confidence", "bar")

    def __init__(self, fraction, confidence, bar=None):
        # 0.0 - 1.0, or None when nothing was found.
        self.fraction = fraction
        self.confidence = confidence
        # (x1, y1, x2, y2) of the bar in full-frame pixels, for the overlay.
        self.bar = bar

    @property
    def known(self):
        return self.fraction is not None

    def __repr__(self):
        if not self.known:
            return "HealthReading(unknown)"
        return f"HealthReading({self.fraction:.0%} @ {self.confidence:.2f})"


UNKNOWN = HealthReading(None, 0.0)


class HealthReader:
    """Reads health fractions from brawler bounding boxes."""

    def __init__(self, config):
        self.config = config
        # Per-entity smoothing. Health steps down in chunks and the reading is
        # noisy at the edges, so a single frame is not trusted on its own -
        # but the filter has to be fast enough to catch a burst that takes half
        # a bar, which is exactly when the decision matters.
        self._history = {}

    def reset(self):
        self._history.clear()

    # ------------------------------------------------------------------

    def read(self, frame, box, hostile):
        """Health fraction for one brawler.

        `frame` is RGB, `box` is (x1, y1, x2, y2) in full-frame pixels, and
        `hostile` selects the red track rather than the green one.
        """
        config = self.config
        if frame is None or box is None or len(box) < 4:
            return UNKNOWN

        height, width = frame.shape[:2]
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        box_w = x2 - x1
        box_h = y2 - y1
        if box_w < 8 or box_h < 8:
            return UNKNOWN

        # The bar is drawn above the sprite and is a little wider than the box
        # when the brawler is turned side-on, so the search window is padded
        # outward on both axes.
        pad_x = box_w * config.health_search_pad
        top = y1 - box_h * config.health_band_top
        bottom = y1 - box_h * config.health_band_bottom

        sx1 = int(max(0, x1 - pad_x))
        sx2 = int(min(width, x2 + pad_x))
        sy1 = int(max(0, min(top, bottom)))
        sy2 = int(min(height, max(top, bottom)))
        if sx2 - sx1 < 10 or sy2 - sy1 < 3:
            return UNKNOWN

        patch = frame[sy1:sy2, sx1:sx2]
        hsv = cv2.cvtColor(patch, cv2.COLOR_RGB2HSV)

        filled = self._track_mask(hsv, hostile, spent=False)
        spent = self._track_mask(hsv, hostile, spent=True)
        if filled is None:
            return UNKNOWN

        # The WHOLE strip - filled plus spent - is what gets located, not just
        # the coloured part. The strip is full width at any health, so the row
        # is found just as reliably at 5% as at 100%, and the fraction then
        # falls out of how much of that fixed-width run is bright.
        strip = filled if spent is None else cv2.bitwise_or(filled, spent)
        # Near-black: the outline drawn around the bar, and the discriminator
        # that colour alone cannot provide.
        dark = cv2.inRange(
            hsv,
            np.array([0, 0, 0], dtype=np.uint8),
            np.array([179, 255, config.health_outline_value_max], dtype=np.uint8),
        )

        # A health bar is only a few pixels tall. Grass, rocks and shadows are
        # not - so anything taller than this is removed from the strip outright
        # before runs are measured.
        #
        # Removing it rather than merely rejecting it afterwards matters: a
        # bush growing right up against the bar merges with it into one very
        # long run, and the merged run then fails the thickness test and takes
        # the real bar down with it. That was a live miss on the bench - an
        # ally at 8% health, invisible because of the grass next to it.
        max_thickness = max(config.health_min_thickness_px,
                            int(box_h * config.health_max_thickness))
        tall_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max_thickness + 1))
        too_tall = cv2.morphologyEx(strip, cv2.MORPH_OPEN, tall_kernel)
        strip = cv2.subtract(strip, too_tall)
        filled = cv2.subtract(filled, too_tall)

        counts = cv2.reduce(strip, 1, cv2.REDUCE_SUM, dtype=cv2.CV_32S).flatten() // 255
        if not counts.size:
            return UNKNOWN

        rows = strip.shape[0]
        order = np.argsort(counts)[::-1][:config.health_row_candidates]

        best = None
        for row in order:
            row = int(row)
            if counts[row] < config.health_min_bar_pixels:
                break
            run = _longest_run(strip[row] > 0)
            if run is None:
                continue
            start, end = run
            total = end - start
            if total < config.health_min_bar_pixels:
                continue

            top, bottom = _strip_extent(strip, row, (start + end) // 2)
            if not _has_outline(dark, top, bottom, start, end, rows,
                                config.health_outline_coverage):
                # No dark frame above and below. This is the test that keeps a
                # patch of grass from being read as a full health bar: on the
                # bench, colour alone produced 25 false positives out of 40 and
                # reported them all at 100% health, with full confidence.
                continue

            band = slice(top, bottom + 1)
            filled_run = filled[band, start:end].max(axis=0) > 0
            filled_len = int(np.count_nonzero(filled_run))
            score = total * (1.0 - abs(((start + end) * 0.5) / max(strip.shape[1], 1) - 0.5))
            if best is None or score > best[0]:
                best = (score, start, end, total, filled_len, top, bottom)

        if best is None:
            return UNKNOWN

        _, start, end, total, filled_len, top, bottom = best
        fraction = filled_len / float(total)

        # Confidence. A bar close to the width one would expect for a box this
        # size, and solidly filled from one end rather than speckled, is
        # trustworthy.
        expected = box_w * config.health_expected_width
        width_score = min(total / max(expected, 1.0), 1.0)
        contiguous = _longest_run(filled[top:bottom + 1, start:end].max(axis=0) > 0)
        solidity = ((contiguous[1] - contiguous[0]) / max(filled_len, 1)) if contiguous else 0.0
        confidence = max(0.0, min(1.0, 0.55 * width_score + 0.45 * min(solidity, 1.0)))
        if filled_len == 0:
            # An empty bar is a real reading, and an important one, but there
            # is no filled run to judge solidity from.
            confidence = max(0.0, min(1.0, width_score))
        if confidence < config.health_min_confidence:
            return UNKNOWN

        bar = (sx1 + start, sy1 + top, sx1 + end, sy1 + bottom + 1)
        return HealthReading(min(max(fraction, 0.0), 1.0), confidence, bar)

    def read_tracked(self, frame, box, hostile, key):
        """Same as read(), smoothed over recent frames for one entity.

        A single frame occasionally clips a bar behind a wall or a particle and
        reports far too little health. Acting on that would make the bot flee a
        fight it was winning, so readings are filtered - but asymmetrically:
        damage is believed immediately, recovery has to persist. Being wrong
        about being hurt is survivable; being wrong about being healthy is not.
        """
        reading = self.read(frame, box, hostile)
        previous = self._history.get(key)

        if not reading.known:
            # Decay confidence in the old value rather than dropping it: the
            # brawler is probably still there, just momentarily obscured.
            if previous is None:
                return UNKNOWN
            aged = HealthReading(previous.fraction, previous.confidence * 0.75, previous.bar)
            if aged.confidence < self.config.health_min_confidence * 0.5:
                self._history.pop(key, None)
                return UNKNOWN
            self._history[key] = aged
            return aged

        if previous is not None and previous.fraction is not None:
            if reading.fraction < previous.fraction:
                # Took damage: trust it now, no smoothing.
                pass
            else:
                # Healed: ease into it, so one bad frame cannot declare the
                # brawler full again.
                blend = self.config.health_recover_blend
                reading = HealthReading(
                    previous.fraction + (reading.fraction - previous.fraction) * blend,
                    reading.confidence,
                    reading.bar,
                )

        self._history[key] = reading
        return reading

    # ------------------------------------------------------------------

    def _track_mask(self, hsv, hostile, spent):
        """Pixels belonging to the filled or the spent part of the bar."""
        config = self.config
        if hostile:
            # Red wraps around the hue circle, so it needs two bands.
            bands = ((0, config.health_red_hue), (179 - config.health_red_hue, 179))
        else:
            bands = ((config.health_green_hue[0], config.health_green_hue[1]),)

        if spent:
            sat = (config.health_spent_sat_min, 255)
            val = (config.health_spent_val_min, config.health_spent_val_max)
        else:
            sat = (config.health_sat_min, 255)
            val = (config.health_val_min, 255)

        mask = None
        for low, high in bands:
            band = cv2.inRange(
                hsv,
                np.array([low, sat[0], val[0]], dtype=np.uint8),
                np.array([high, sat[1], val[1]], dtype=np.uint8),
            )
            mask = band if mask is None else cv2.bitwise_or(mask, band)
        return mask


def _longest_run(row):
    """Longest contiguous True span in a 1-D boolean array, as (start, end)."""
    best_start = best_len = 0
    start = -1
    for index, value in enumerate(row):
        if value:
            if start < 0:
                start = index
        elif start >= 0:
            if index - start > best_len:
                best_len = index - start
                best_start = start
            start = -1
    if start >= 0 and len(row) - start > best_len:
        best_len = len(row) - start
        best_start = start
    if best_len <= 0:
        return None
    return best_start, best_start + best_len


def _strip_extent(mask, row, column):
    """How far the strip continues above and below `row` at `column`.

    This is the thickness test. A health bar is a few pixels tall; a bush, a
    rock or a shadow that happens to be the right colour is not, so measuring
    the vertical run costs one column scan and removes an entire class of false
    positive that no colour threshold can.
    """
    top = row
    while top > 0 and mask[top - 1, column]:
        top -= 1
    bottom = row
    while bottom + 1 < mask.shape[0] and mask[bottom + 1, column]:
        bottom += 1
    return top, bottom


def _has_outline(dark, top, bottom, start, end, rows, coverage):
    """Is the strip framed above and below by the bar's dark outline?

    Brawl Stars draws every health bar inside a near-black border. Scenery does
    not come with one, which makes this the single most useful test available:
    it is what separates a bar from a patch of grass of the same hue, and it
    works on any map without tuning.
    """
    span = max(end - start, 1)

    def dark_enough(row):
        if row < 0 or row >= rows:
            return False
        return np.count_nonzero(dark[row, start:end]) >= coverage * span

    # Allow a few pixels of slack: the outline is 1-3 px and antialiasing puts
    # a transitional row between it and the coloured track.
    above = any(dark_enough(top - offset) for offset in (1, 2, 3, 4))
    below = any(dark_enough(bottom + offset) for offset in (1, 2, 3, 4))
    return above and below


def entity_key(box, salt=""):
    """A stable-enough identity for smoothing, from where the box is.

    Brawlers do not teleport, so rounding the centre to a coarse grid keeps the
    same entity on the same key between frames without needing a real tracker.
    """
    cx = (box[0] + box[2]) * 0.5
    cy = (box[1] + box[3]) * 0.5
    return f"{salt}{int(cx // 64)}:{int(cy // 64)}"
