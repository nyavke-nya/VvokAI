"""Terrain colour model.

Brawl Stars maps are built from a small, stable palette: sand, rock and wood in
the warm hues, grass and cactus in green, water in blue. Projectiles are the
bright saturated things that are *not* those hues.

Two sources feed the model:

  * a static hue table, measured from real frames, that works from frame one;
  * an adaptive hue histogram, because terrain covers most of the screen by
    definition, so the dominant hues of any map are terrain.

The output is a "not terrain" mask. It is used as a score in soft mode and as a
filter in hard mode - never as the only evidence, because a yellow Colt bullet
over desert sand really is the same hue as the ground.
"""

import cv2
import numpy as np

HUE_BINS = 180
# cv2.LUT insists on a full 256-entry table for 8-bit input, even though hue
# only ever reaches 179.
LUT_SIZE = 256


class TerrainPalette:
    def __init__(self, config):
        self.config = config
        self.enabled = config.palette_mode != "off"
        self._static_lut = self._build_static_lut()
        self._adaptive_hist = np.zeros(HUE_BINS, dtype=np.float32)
        self._adaptive_lut = np.zeros(LUT_SIZE, dtype=np.uint8)
        self._lut = self._static_lut.copy()
        self._warmed_up = False

    def _build_static_lut(self):
        # 255 rather than 1 so the table's output can be combined with other
        # masks using cv2 bitwise ops, which are much faster than numpy
        # boolean algebra at this frame size.
        lut = np.zeros(LUT_SIZE, dtype=np.uint8)
        for low, high in (self.config.warm_hue, self.config.green_hue, self.config.water_hue):
            lut[max(0, low):min(HUE_BINS, high + 1)] = 255
        return lut

    def update_adaptive(self, hsv):
        """Fold this frame's hue distribution into the running terrain model."""
        if not self.config.palette_adaptive:
            return

        hist = cv2.calcHist([hsv], [0], None, [HUE_BINS], [0, HUE_BINS]).reshape(-1)
        total = float(hist.sum())
        if total <= 0:
            return
        hist /= total

        alpha = self.config.palette_smoothing if self._warmed_up else 0.0
        self._adaptive_hist = alpha * self._adaptive_hist + (1.0 - alpha) * hist
        self._warmed_up = True

        top = np.argsort(self._adaptive_hist)[-self.config.palette_top_bins:]
        self._adaptive_lut.fill(0)
        for bin_index in top:
            # Include the neighbouring bins: a texture never lands on exactly
            # one hue value, and a one-bin-wide terrain band leaks badly.
            low = max(0, int(bin_index) - 1)
            high = min(HUE_BINS - 1, int(bin_index) + 1)
            self._adaptive_lut[low:high + 1] = 255

        np.bitwise_or(self._static_lut, self._adaptive_lut, out=self._lut)

    def not_terrain_mask(self, hsv):
        """255 where the pixel does not look like map terrain, else 0.

        Built entirely from cv2 ops rather than numpy comparisons: at 640x360
        that is the difference between ~1.0 ms and ~0.2 ms per frame.
        """
        hue, saturation, value = cv2.split(hsv)
        config = self.config

        terrain = cv2.LUT(hue, self._lut)
        saturated = cv2.compare(saturation, config.terrain_min_saturation - 1, cv2.CMP_GT)
        cv2.bitwise_and(terrain, saturated, dst=terrain)

        # Muzzle flashes, energy trails and white-hot projectile cores are
        # bright and washed out. Whatever their hue, they are never terrain.
        bright = cv2.compare(value, config.glow_min_value - 1, cv2.CMP_GT)
        washed = cv2.compare(saturation, config.glow_max_saturation + 1, cv2.CMP_LT)
        cv2.bitwise_and(bright, washed, dst=bright)
        cv2.bitwise_and(terrain, cv2.bitwise_not(bright), dst=terrain)

        return cv2.bitwise_not(terrain)
