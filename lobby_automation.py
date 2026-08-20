import time

import cv2
from utils import (
    EasyOCRInitializationError,
    count_hsv_pixels,
    extract_text_and_positions,
    load_toml_as_dict, load_all_brawlers_names, config_bool,
)


class LobbyAutomation:

    def __init__(self, window_controller):
        self.gray_pixels_treshold = load_toml_as_dict("./cfg/bot_config.toml").get('idle_pixels_minimum', 500)
        self.idle_reconnect_coords = load_toml_as_dict("cfg/buttons_config.toml")["idle_reconnect"]
        self.ocr_scale_down_factor = max(0.5, min(1, load_toml_as_dict("./cfg/general_config.toml").get('ocr_scale_down_factor', 1)))
        self.ocr_scale_up_factor = 1 / self.ocr_scale_down_factor
        self.all_brawlers_names = load_all_brawlers_names()
        self.window_controller = window_controller
        self.verbose_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('verbose_debug'), False)

    def check_for_idle(self, frame):
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        x_start, x_end = int(460 * wr), int(1460 * wr)
        y_start, y_end = int(400 * hr), int(675 * hr)
        gray_pixels = count_hsv_pixels(frame[y_start:y_end, x_start:x_end], (0, 0, 10), (30, 60, 67))
        if self.verbose_debug: print(f"gray pixels (if > {self.gray_pixels_treshold} then bot will try to unidle) :", gray_pixels)
        if gray_pixels > self.gray_pixels_treshold:
            self.window_controller.click(self.idle_reconnect_coords[0], self.idle_reconnect_coords[1], already_include_ratio=False)
            print("Idle detected, clicking to unidle")

    @staticmethod
    def _should_interrupt(runtime_control=None, stop_event=None):
        if runtime_control and (runtime_control.should_stop() or runtime_control.should_pause()):
            return True
        return stop_event is not None and stop_event.is_set()

    @staticmethod
    def _sleep_interruptible(duration, runtime_control=None, stop_event=None, poll_interval=0.1):
        end_time = time.time() + duration
        while time.time() < end_time:
            if LobbyAutomation._should_interrupt(runtime_control, stop_event):
                return True
            time.sleep(min(poll_interval, max(end_time - time.time(), 0)))
        return False

    # The list is long, so a scan that starts at the top needs room to reach the
    # bottom - but not 100 screens of room. Anything past this is not scrolling,
    # it is stuck, and grinding through it costs minutes before anyone is told.
    MAX_SCANS = 40

    # Swipes to get back to the top. More than the list is tall, because a
    # swipe that lands while the view is still gliding does nothing at all -
    # so the count has to cover the wasted ones as well as the useful ones.
    #
    # Fourteen was not enough in practice: with a brawler far down the list the
    # view still had further to go when the swiping stopped, the search then
    # started from halfway, and everything above that point was invisible to it
    # - which looks exactly like the brawler not existing. Overshooting costs
    # nothing, because a list already at the top ignores the extra swipes.
    SCROLL_TOP_SWIPES = 19

    def _scroll_to_list_top(self, runtime_control=None, stop_event=None):
        """Put the brawler list back at the top before searching it.

        The menu opens wherever the currently selected brawler is. That is
        normally fine, and quietly wrong after a reward unlocks a new brawler:
        the game switches to it, it sits near the bottom of the list, and every
        brawler above it - Shelly, who is first of all of them - is off-screen
        upward. Searching by scrolling down from there walks away from the
        target and never comes back, so the scan ran its full length and gave
        up, over and over, on a brawler that was three swipes above it.

        Starting from the top makes the search cover the whole list whatever
        the game had selected when the menu opened.
        """
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        for _ in range(self.SCROLL_TOP_SWIPES):
            if self._should_interrupt(runtime_control, stop_event):
                return True
            # Finger downward, which moves the list up.
            self.window_controller.swipe(int(1700 * wr), int(650 * hr),
                                         int(1700 * wr), int(1000 * hr), duration=0.25)
            time.sleep(0.15)
        # Let the overscroll bounce settle, or the first OCR reads a blur.
        return self._sleep_interruptible(1.0, runtime_control, stop_event)

    def select_brawler(self, brawler, get_latest_state, stop_event=None, runtime_control=None):
        self.window_controller.screenshot()
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        brawler = str(brawler).lower().strip()
        for symbol in [' ', '-', '.', "&"]:
            brawler = brawler.replace(symbol, "")

        x, y = load_toml_as_dict("cfg/buttons_config.toml")["brawlers_menu"]
        self.window_controller.click(x, y, already_include_ratio=False)
        time.sleep(0.5)
        print("Automatic brawler selection started for", brawler)
        if self._scroll_to_list_top(runtime_control, stop_event):
            print("Brawler selection aborted by user.")
            return "aborted"

        c = 0
        shop_counter = 0
        # What the previous screen read, to notice when scrolling stops moving.
        seen_before = None
        stalled = 0
        for i in range(self.MAX_SCANS):
            if self._should_interrupt(runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"
            screenshot = self.window_controller.screenshot()
            screenshot = cv2.resize(screenshot, (int(screenshot.shape[1] * self.ocr_scale_down_factor), int(screenshot.shape[0] * self.ocr_scale_down_factor)), interpolation=cv2.INTER_AREA)

            print("Extracting text on current screen...")
            try:
                results = extract_text_and_positions(screenshot)
            except EasyOCRInitializationError as exc:
                raise RuntimeError(
                    f"Automatic brawler selection could not start OCR: {exc}"
                ) from exc
            except Exception as exc:
                print(f"WARNING: Automatic brawler selection could not read this screen with OCR: {exc}")
                print("The bot will continue without changing the currently selected brawler.")
                return "error"
            results = {k: v for k, v in results.items() if len(k) >= 2}
            clean_results = {}
            for key in results.keys():
                orig_key = key
                for symbol in [' ', '-', '.', "&"]:
                    key = key.replace(symbol, "")
                clean_results[key.lower()] = results[orig_key]

            current_state = get_latest_state()
            if "shop" in clean_results.keys():
                print("Latest screenshot is still of the lobby, waiting for the frame to update...")
                shop_counter += 1
                if shop_counter > 5:
                    print("WARNING: The bot has been waiting for the lobby screen to update for a long time. It's possible that the game is stuck or the OCR is having trouble reading the screen. The bot will continue without changing the currently selected brawler.")
                    return "stuck"
                continue
            elif current_state != "brawler_selection":
                print("Latest screenshot is no longer of the lobby, aborting brawler selection...")
                return "stuck"
            elif brawler in clean_results.keys():
                matched_key = brawler
            else:
                matched_key = None
                # .get, not [], because a brawler released after this copy of
                # the name table was written is not in it - and the reward that
                # hands somebody a brand new brawler is exactly the moment the
                # bot goes looking. A KeyError here took the whole bot down.
                aliases = self.all_brawlers_names.get(brawler) or []
                for detected_name in clean_results.keys():
                    if detected_name in aliases:
                        matched_key = detected_name
                        print(f"Matched detected name '{detected_name}' to brawler '{brawler}' using alias list.")
                        break

            if self.verbose_debug:
                print("OCR detected the following potential matches for the brawler name:")
                import difflib
                for detected_name in clean_results.keys():
                    match_ratio = difflib.SequenceMatcher(None, detected_name, brawler).ratio()
                    if match_ratio >= 0.25:
                        print(f" - '{detected_name}' with match ratio {match_ratio:.2f}")
            if matched_key:
                x, y = clean_results[matched_key]['center']
                y_offset = 50*self.ocr_scale_down_factor
                y -= y_offset
                self.window_controller.click(int(x * self.ocr_scale_up_factor), int(y * self.ocr_scale_up_factor))
                print(f"Found brawler {brawler} ({matched_key}) clicking on its icon at {int(x * self.ocr_scale_up_factor)} {int(y * self.ocr_scale_up_factor)}")
                if self._sleep_interruptible(1, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                select_x, select_y = load_toml_as_dict("cfg/buttons_config.toml")["select_brawler"]
                self.window_controller.click(select_x, select_y, already_include_ratio=False)
                if self._sleep_interruptible(1.5, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                self.window_controller.screenshot()
                print("Selected brawler ", brawler)
                return "success"
            else:
                print("Brawler name not found on screen, scrolling down to load more brawlers...")

            # The bottom of the list looks exactly like a swipe that did not
            # register, so it has to be recognised rather than waited out: two
            # identical screens in a row means the list is not moving and the
            # brawler is genuinely not in it.
            names = frozenset(clean_results.keys())
            if names and names == seen_before:
                stalled += 1
                if stalled >= 2:
                    print(f"Reached the end of the brawler list without finding "
                          f"'{brawler}'. It may be spelled differently in game, "
                          f"or not unlocked on this account.")
                    return "failed"
            else:
                stalled = 0
            seen_before = names

            if c == 0:
                wr = self.window_controller.width_ratio
                hr = self.window_controller.height_ratio
                self.window_controller.swipe(int(1700 * wr), int(900 * hr), int(1700 * wr), int(850 * hr), duration=0.5)
                if self._sleep_interruptible(3, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                c += 1
                continue

            self.window_controller.swipe(int(1700 * wr), int(900 * hr), int(1700 * wr), int(650 * hr), duration=0.5)
            if self._sleep_interruptible(3, runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"

        print(f"WARNING: Brawler '{brawler}' was not found after {self.MAX_SCANS} "
              f"scroll attempts.")
        return "failed"
