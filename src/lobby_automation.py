import time

import cv2
from state_finder import (is_connection_lost_on_screen,
                          is_idle_disconnect_on_screen, is_team_invite_on_screen)
from utils import (
    EasyOCRInitializationError,
    count_hsv_pixels,
    extract_text_and_positions,
    load_toml_as_dict, load_all_brawlers_names, config_bool,
)


class LobbyAutomation:

    def __init__(self, window_controller):
        bot_config = load_toml_as_dict("./cfg/bot_config.toml")
        self.decline_team_invites = config_bool(bot_config.get("decline_team_invites"), True)
        self.team_invite_green_minimum = float(bot_config.get("team_invite_green_minimum", 3500))
        self.mute_x_factor = float(bot_config.get("team_invite_mute_x", self.MUTE_X_FACTOR))
        self.mute_y_factor = float(bot_config.get("team_invite_mute_y", self.MUTE_Y_FACTOR))
        self.last_team_invite_handled = 0.0
        self.ocr_scale_down_factor = max(0.5, min(1, load_toml_as_dict("./cfg/general_config.toml").get('ocr_scale_down_factor', 1)))
        self.ocr_scale_up_factor = 1 / self.ocr_scale_down_factor
        self.all_brawlers_names = load_all_brawlers_names()
        self.window_controller = window_controller
        self.verbose_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('verbose_debug'), False)

    # The idle box's own region now lives in cfg/lobby_config.toml, with every
    # other screen's, so the search area and the template that was cut from it
    # cannot disagree. See tools/make_state_template.py.

    # Where the team-invite modal lands. It is centred, so this is a generous
    # box around it rather than its exact bounds - it only has to contain the
    # two buttons and the mute line, and being generous costs nothing because
    # OCR only ever runs on it after the green check below has already passed.
    TEAM_INVITE_REGION = (470, 170, 1460, 910)

    # The ACCEPT button's green. Converted from the button's own RGB rather
    # than picked by eye: (76, 209, 55) is H 56, S 188, V 209 in the ranges
    # OpenCV uses, so this is that hue with room either side.
    ACCEPT_GREEN = ((40, 110, 110), (75, 255, 255))

    # Where the mute checkbox sits, as multiples of the distance between the
    # two button labels. Both buttons are found by OCR, so this rides along
    # with whatever size the window is instead of being pixels that only work
    # at one resolution: the box is just inside the modal's right edge, a bit
    # under a third of a button-span below the buttons.
    #
    # These two came off a screenshot rather than a live frame, so they are in
    # bot_config where they can be nudged without editing code. Turn on
    # verbose_debug and the bot prints where it decided to click.
    MUTE_X_FACTOR = 0.89
    MUTE_Y_FACTOR = 0.29

    # Long enough that one dialog cannot be clicked twice while the game plays
    # its dismiss animation.
    TEAM_INVITE_COOLDOWN = 4.0

    def check_for_team_invite(self, frame):
        """Turn down a team invite, and mute whoever sent it on the way out.

        Three stages now, and the first one is new. The dialog is identified by
        its own banner, the way star drops are - "the words REJECT and ACCEPT
        together" turned out not to be this dialog and nothing else, and the
        bot was declining things that were never invites.

        The green count and the OCR stay, but they no longer decide anything:
        the count is a cheap way to skip the expensive step, and OCR is there
        to LOCATE the buttons, not to recognise the screen. Reading them rather
        than storing coordinates is still the point - the modal is centred and
        scales with the window, so a pixel pair measured on one machine is
        wrong on the next.

        Returns True when an invite was dealt with.
        """
        if not self.decline_team_invites:
            return False
        now = time.time()
        if now - self.last_team_invite_handled < self.TEAM_INVITE_COOLDOWN:
            return False

        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        left, top, right, bottom = self.TEAM_INVITE_REGION
        x1, y1 = int(left * wr), int(top * hr)
        x2, y2 = int(right * wr), int(bottom * hr)
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return False

        # The cheap stage first. An inRange over one crop against a quarter of
        # a second of OCR is not a close call, and on almost every frame there
        # is no green button to be found and nothing else has to run.
        green = count_hsv_pixels(crop, *self.ACCEPT_GREEN)
        if self.verbose_debug:
            print(f"team invite: {green} green pixels "
                  f"(need {self.team_invite_green_minimum:.0f})")
        if green < self.team_invite_green_minimum:
            return False

        # Then whether it is really this dialog rather than something else
        # green. This is what stopped the bot declining things that were not
        # invites; it is second because it is the expensive one.
        if not is_team_invite_on_screen(frame):
            return False

        found = self._read_invite_buttons(crop)
        if not found:
            return False
        reject, accept, mute_y = found

        span = accept[0] - reject[0]
        if span <= 0:
            return False

        centre_x = (reject[0] + accept[0]) / 2.0
        mute_x = centre_x + self.mute_x_factor * span
        if mute_y is None:
            mute_y = accept[1] + self.mute_y_factor * span

        # Mute first. The dialog closes on REJECT, and a checkbox on a closed
        # dialog is just a click on whatever was behind it.
        if self.verbose_debug:
            print(f"team invite: mute at ({x1 + mute_x:.0f}, {y1 + mute_y:.0f}), "
                  f"reject at ({x1 + reject[0]:.0f}, {y1 + reject[1]:.0f})")
        self.window_controller.click(x1 + mute_x, y1 + mute_y)
        time.sleep(0.15)
        self.window_controller.click(x1 + reject[0], y1 + reject[1])
        self.last_team_invite_handled = time.time()
        print("Team invite declined, and the sender muted.")
        return True

    def _read_invite_buttons(self, crop):
        """(reject, accept, mute_y) in crop pixels, or None if this is not it.

        The mute line is optional: the sender's name is part of it, so it is
        the least reliable thing on the dialog to read. Without it the checkbox
        is placed from the buttons instead, which is where it is anyway.
        """
        scale = self.ocr_scale_down_factor
        small = cv2.resize(crop, (int(crop.shape[1] * scale), int(crop.shape[0] * scale)),
                           interpolation=cv2.INTER_AREA) if scale != 1 else crop
        try:
            results = extract_text_and_positions(small)
        except EasyOCRInitializationError:
            return None
        except Exception as exc:
            if self.verbose_debug:
                print(f"team invite: OCR failed ({exc})")
            return None

        words = {key.replace(" ", ""): value for key, value in results.items()}
        reject = words.get("reject")
        accept = words.get("accept")
        if not reject or not accept:
            if self.verbose_debug and words:
                print(f"team invite: green matched but the buttons did not "
                      f"({', '.join(sorted(words))[:120]})")
            return None

        up = 1.0 / scale
        reject_c = (reject["center"][0] * up, reject["center"][1] * up)
        accept_c = (accept["center"][0] * up, accept["center"][1] * up)

        mute_y = None
        mute_lines = [value["center"][1] * up for key, value in words.items()
                      if "mute" in key or "forthenext" in key]
        if mute_lines:
            mute_y = sum(mute_lines) / len(mute_lines)

        return reject_c, accept_c, mute_y

    def check_for_idle(self, frame):
        """Which "you are no longer in this match" card is on screen, if any.

        Returns its name, or an empty string. A name rather than True because
        two different cards mean the same thing and the log should say which
        one turned up.

        Only reports it; what to do about it is the caller's business. Pressing
        the button on either used to be the answer and it is not a reliable
        one - RELOAD and RETRY LOGIN both return to a battle that has already
        carried on without us, so the bot sat in a dead match until something
        else noticed.

        Recognised by title rather than counted in grey pixels. The old test
        was "is the middle of the screen grey", which a great many screens are,
        and the answer to this one is now a restart of the game - far too
        expensive to hang on a heuristic that loose.
        """
        for name, seen in (("idle disconnect", is_idle_disconnect_on_screen),
                           ("connection lost", is_connection_lost_on_screen)):
            if seen(frame):
                if self.verbose_debug:
                    print(f"{name}: recognised")
                return name
        return ""

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

    # How long to give the brawler list to appear after tapping the button,
    # and how many times to tap again if it does not. See _open_brawler_menu.
    MENU_OPEN_TIMEOUT = 4.0
    MENU_OPEN_ATTEMPTS = 3

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

    # The column the list is dragged by, on a 1920-wide screen.
    #
    # 1700 was on the cards. Their right edge sits at about x=1696, so every
    # swipe started on a brawler, and a drag that the game read as a tap opened
    # whichever one it landed on - the bot would be scrolling and suddenly be
    # inside a random brawler's page. Measured across the band the swipes
    # travel through, colour variation at x=1700 is 85 and at x=1760 and beyond
    # it is 3: cards, then plain background.
    #
    # 1820 is clear of the cards with room to spare, and far enough from the
    # screen edge that Android does not read it as a back gesture.
    SCROLL_COLUMN = 1820

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
            column = int(self.SCROLL_COLUMN * wr)
            self.window_controller.swipe(column, int(650 * hr),
                                         column, int(1000 * hr), duration=0.25)
            time.sleep(0.15)
        # Let the overscroll bounce settle, or the first OCR reads a blur.
        return self._sleep_interruptible(1.0, runtime_control, stop_event)

    def _open_brawler_menu(self, get_latest_state, runtime_control=None, stop_event=None):
        """Tap the brawlers button and wait until the list is actually up.

        It used to tap once, sleep half a second and start swiping. Half a
        second is not always long enough for the list to animate in, and the
        tap can land on a popup that opened over the lobby instead - in which
        case nineteen scroll-to-top swipes went into whatever WAS on screen,
        and only then did the scan loop notice the state was wrong and bail.
        From the outside that reads exactly as "it says it is picking a brawler
        and never opens the menu".

        The state comes from the checker thread, which keeps reading frames
        while this blocks, so polling it here sees the screen change.
        """
        x, y = load_toml_as_dict("cfg/buttons_config.toml")["brawlers_menu"]
        for attempt in range(self.MENU_OPEN_ATTEMPTS):
            if self._should_interrupt(runtime_control, stop_event):
                return "aborted"
            self.window_controller.click(x, y, already_include_ratio=False)
            deadline = time.time() + self.MENU_OPEN_TIMEOUT
            while time.time() < deadline:
                if self._should_interrupt(runtime_control, stop_event):
                    return "aborted"
                if get_latest_state() == "brawler_selection":
                    return "open"
                time.sleep(0.2)
            print(f"The brawler list did not open (attempt {attempt + 1} of "
                  f"{self.MENU_OPEN_ATTEMPTS}), tapping again.")
        return "closed"

    @staticmethod
    def _near_miss(brawler, detected_names):
        """The brawler's name with exactly one character misread, or None.

        A four-letter name cannot clear the 0.80 fuzzy threshold with a single
        character wrong - "norz" against "nori" scores 0.75 - and 51 of the 105
        brawlers are four letters or fewer. That is not a corner case: it is
        why the bot walked past a Nori sitting on screen the whole time and
        scrolled the list forty times before giving up, over and over.

        Lowering the threshold is not the fix. bolt/colt, mico/rico, pam/sam
        and mandy/sandy are each one character apart and each pair is two real
        brawlers, so a loose rule would confidently pick the wrong one and push
        trophies on it. All four differ in their FIRST character, and OCR
        mangles the tail of a word far more often than the head - so requiring
        the head to match rules out every one of them. Checked across the whole
        roster: no two brawlers share a length and a first letter while
        differing by a single character.

        The match must also be the only one of its kind on screen. If two cards
        are each one character out, neither is worth trusting.
        """
        hits = []
        for name in detected_names:
            if not name or name == brawler or len(name) != len(brawler):
                continue
            if name[0] != brawler[0]:
                continue
            if sum(a != b for a, b in zip(name, brawler)) == 1:
                hits.append(name)
        return hits[0] if len(hits) == 1 else None

    def select_brawler(self, brawler, get_latest_state, stop_event=None, runtime_control=None):
        self.window_controller.screenshot()
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        brawler = str(brawler).lower().strip()
        for symbol in [' ', '-', '.', "&"]:
            brawler = brawler.replace(symbol, "")

        print("Automatic brawler selection started for", brawler)
        opened = self._open_brawler_menu(get_latest_state, runtime_control, stop_event)
        if opened == "aborted":
            print("Brawler selection aborted by user.")
            return "aborted"
        if opened != "open":
            print("The brawler list never opened, so there is nothing to scroll. "
                  "Leaving the selected brawler alone.")
            return "stuck"
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
                aliases = self.all_brawlers_names.get(brawler) or []
                for detected_name in clean_results.keys():
                    if detected_name in aliases:
                        matched_key = detected_name
                        print(f"Matched detected name '{detected_name}' to brawler '{brawler}' using alias list.")
                        break
                
                # Fallback to fuzzy matching
                if not matched_key:
                    import difflib
                    best_match = None
                    best_ratio = 0.0
                    for detected_name in clean_results.keys():
                        ratio = difflib.SequenceMatcher(None, detected_name, brawler).ratio()
                        if ratio > best_ratio:
                            best_ratio = ratio
                            best_match = detected_name
                    
                    if best_ratio >= 0.8:
                        matched_key = best_match
                        print(f"Fuzzy matched detected name '{best_match}' to brawler '{brawler}' with ratio {best_ratio:.2f}.")

                # Still nothing, and the ratio test cannot help a short name:
                # see _near_miss.
                if not matched_key:
                    near = self._near_miss(brawler, clean_results.keys())
                    if near:
                        matched_key = near
                        print(f"Matched '{near}' to brawler '{brawler}': one character "
                              f"out, and nothing else on screen was close.")

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
                column = int(self.SCROLL_COLUMN * wr)
                self.window_controller.swipe(column, int(900 * hr), column, int(850 * hr), duration=0.5)
                if self._sleep_interruptible(3, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                c += 1
                continue

            column = int(self.SCROLL_COLUMN * wr)
            self.window_controller.swipe(column, int(900 * hr), column, int(650 * hr), duration=0.5)
            if self._sleep_interruptible(3, runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"

        print(f"WARNING: Brawler '{brawler}' was not found after {self.MAX_SCANS} "
              f"scroll attempts.")
        return "failed"
