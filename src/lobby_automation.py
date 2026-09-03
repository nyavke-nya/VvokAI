import time

import cv2
from state_finder import (is_connection_lost_on_screen,
                          is_idle_disconnect_on_screen, is_in_brawler_selection,
                          is_team_invite_on_screen)
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
        self.press_enter_after_search = config_bool(
            bot_config.get("brawler_search_press_enter"), True)
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

    # The list is long, so a scan that starts at the beginning needs room to
    # reach the end - but not 100 screens of room. Anything past this is not
    # scrolling, it is stuck, and grinding through it costs minutes before
    # anyone is told.
    MAX_SCANS = 40

    # Reads allowed after a search. The list is down to the cards that matched
    # by then, so these are only waiting for the frame to catch up with the
    # typing - not a hunt. Keeping it short means a search box that quietly did
    # nothing costs a second before the scrolling fallback starts, rather than
    # as long as the scrolling it was meant to replace.
    SEARCH_SCANS = 3

    # How long to give the brawler list to appear after tapping the button,
    # and how many times to tap again if it does not. See _open_brawler_menu.
    MENU_OPEN_TIMEOUT = 4.0
    MENU_OPEN_ATTEMPTS = 3

    # How long to keep waiting when the capture has not produced a single frame
    # since the tap. Past this the feed is the problem, not the game, and there
    # is nothing to be gained by holding the run any longer. Comfortably beyond
    # window_controller's own 15s stale-frame warning, so that gets said first.
    MENU_OPEN_STALE_LIMIT = 20.0

    # Swipes to get back to the start. More than the list is long, because a
    # swipe that lands while the view is still gliding does nothing at all -
    # so the count has to cover the wasted ones as well as the useful ones.
    #
    # Fourteen was not enough in practice: with a brawler far along the list the
    # view still had further to go when the swiping stopped, the search then
    # started from halfway, and everything before that point was invisible to it
    # - which looks exactly like the brawler not existing. Overshooting costs
    # nothing, because a list already at the start ignores the extra swipes.
    SCROLL_TOP_SWIPES = 19

    # The row the list is dragged along, on a 1920x1080 screen.
    #
    # The list used to scroll down a column; it now scrolls along a row, so the
    # thing to keep clear of moved with it. The cards fill three rows - y
    # 190-428, 484-697 and 777-1014 - and a drag that starts on a card and is
    # read as a tap opens that brawler's page, which is how the old bot ended
    # up inside a random brawler mid-scroll. 737 is the middle of the 80-pixel
    # gap between the second and third rows: the widest band the new layout
    # leaves empty right across the screen.
    SWIPE_ROW = 737

    # How far along that row each swipe drags. Both ends stay inside the cards
    # - the left edge of the screen has the offer banner on it and the right
    # edge is close enough that Android may take the gesture as a back swipe.
    SWIPE_NEAR_X = 500
    SWIPE_FAR_X = 1500

    # Where the search box sits in the list's top bar, on a 1920x1080 screen.
    # Measured off the new menu: the magnifier is at x=1447, the word SEARCH at
    # x=1594, and the bar holding them runs from roughly 1410 to 1730 across
    # y 25-90. 1550 is between the two and clear of the button on the right end
    # that clears the query.
    #
    # It is in buttons_config.toml too, so it can be nudged without editing
    # code - but read with this as the default, because a fork updated from an
    # older config will not have the key at all.
    SEARCH_FIELD = (1550, 57)

    # The back arrow in the list's top bar, on a 1920x1080 screen. The same
    # corner the shop's is in, which is why quit_shop uses these numbers too.
    LIST_BACK_BUTTON = (100, 60)

    def _list_is_open(self, get_latest_state=None):
        """Is the brawler list on screen, as of the newest frame there is?

        The state the checker publishes is enough on its own when it says yes.
        The frame is consulted as well because the checker can be a moment
        behind, and every caller of this is about to press something.
        """
        if get_latest_state is not None and get_latest_state() == "brawler_selection":
            return True
        frame, _ = self.window_controller.get_latest_frame()
        if frame is None:
            return False
        try:
            return bool(is_in_brawler_selection(frame))
        except Exception:
            return False

    def _leave_brawler_menu(self):
        """Back out of the list, if the list is what is on screen.

        An attempt that failed used to be left exactly where it stopped, which
        is inside the open list. The next attempt then starts by tapping the
        brawlers button again - and on this screen that button's spot is the
        glory panel down the left, so the bot opened that instead and got no
        further. That is what people were reporting as "auto select does not
        select": not one bad tap, but a second attempt beginning where the
        first one gave up.

        Guarded on the frame, because the very same tap in the lobby lands on
        the player's own profile card and opens that instead.
        """
        if not self._list_is_open():
            return
        x, y = self.LIST_BACK_BUTTON
        self.window_controller.click(x * self.window_controller.width_ratio,
                                     y * self.window_controller.height_ratio)
        print("Left the brawler list open-handed, so the next attempt starts "
              "from the lobby.")
        time.sleep(1.0)

    def _done(self, outcome):
        """Hand back an outcome, leaving the game where the next try expects it."""
        if outcome in ("failed", "error"):
            self._leave_brawler_menu()
        return outcome

    def _search_field(self):
        buttons = load_toml_as_dict("cfg/buttons_config.toml")
        spot = buttons.get("brawler_search") or self.SEARCH_FIELD
        return int(spot[0]), int(spot[1])

    def _search_for_brawler(self, brawler, runtime_control=None, stop_event=None):
        """Type the name into the list's own search box.

        The update that turned the list sideways also gave it a search box, and
        searching is not a workaround for the new layout - it is better than any
        amount of scrolling ever was. One tap and one string leaves the card we
        want on an otherwise empty screen, whatever the account owns, wherever
        the game had scrolled to, and without forty screens of OCR to get there.

        Returns "searched", "unavailable" when the typing did not go through -
        the caller's cue to fall back to scrolling rather than to read an
        unfiltered list three times and give up - or "aborted".
        """
        x, y = self._search_field()
        print(f"Tapping the search box at ({x}, {y}) and typing '{brawler}'.")
        self.window_controller.click(x, y, already_include_ratio=False)
        # The box has to take focus before anything typed can land in it. No
        # keyboard slides up over the game - the box is a plain text line that
        # takes hardware keys - so this waits on the tap, not on an animation.
        if self._sleep_interruptible(0.8, runtime_control, stop_event):
            return "aborted"

        self.window_controller.clear_text_field()
        if not self.window_controller.type_text(brawler):
            return "unavailable"
        if self._sleep_interruptible(0.4, runtime_control, stop_event):
            return "aborted"

        # Whether the query needs committing. A box that filters as it is typed
        # does not, and pressing Enter at one that closes on Enter throws the
        # filter away and puts the whole list back on screen - which reads
        # exactly like a search that did nothing. The two cannot be told apart
        # without running it, and both are recoverable, because the scrolling
        # fallback below still finds the brawler either way. So it is a switch
        # in bot_config rather than a guess baked into the code.
        if self.press_enter_after_search:
            self.window_controller.submit_text()
        if self._sleep_interruptible(1.2, runtime_control, stop_event):
            return "aborted"
        return "searched"

    def _clear_search(self, runtime_control=None, stop_event=None):
        """Empty the search box so the whole list is back on screen.

        Only needed on the way to the scrolling fallback: scrolling a list
        filtered down to nothing would find nothing however far it went.
        """
        x, y = self._search_field()
        self.window_controller.click(x, y, already_include_ratio=False)
        if self._sleep_interruptible(0.6, runtime_control, stop_event):
            return True
        self.window_controller.clear_text_field()
        if self.press_enter_after_search:
            self.window_controller.submit_text()
        return self._sleep_interruptible(1.0, runtime_control, stop_event)

    def _scroll_to_list_start(self, runtime_control=None, stop_event=None):
        """Wind the brawler list back to its first card before reading it.

        The menu opens wherever the currently selected brawler is. That is
        normally fine, and quietly wrong after a reward unlocks a new brawler:
        the game switches to it, it sits near the end of the list, and every
        brawler before it - Shelly, who is first of all of them - is off-screen.
        Searching by scrolling onward from there walks away from the target and
        never comes back, so the scan ran its full length and gave up, over and
        over, on a brawler that was three swipes behind it.

        Starting from the first card makes the scan cover the whole list
        whatever the game had selected when the menu opened.
        """
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        row = int(self.SWIPE_ROW * hr)
        for _ in range(self.SCROLL_TOP_SWIPES):
            if self._should_interrupt(runtime_control, stop_event):
                return True
            # Finger to the right, which drags the list back toward its start.
            self.window_controller.swipe(int(self.SWIPE_NEAR_X * wr), row,
                                         int(self.SWIPE_FAR_X * wr), row, duration=0.25)
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

        What it must never do is tap twice on one open menu. The second tap
        lands wherever the list happens to have put something - on this layout,
        the panel down the left - and opens it, so instead of picking a brawler
        the bot walks into another screen and gives up there. That is the
        "auto select just opens the list" people were seeing, and it needs no
        bug of its own to happen: a machine whose capture runs a little behind
        hands back a frame from BEFORE the tap, the lobby is still in it, and
        four seconds of that is indistinguishable from a tap that missed.

        So a re-tap now needs positive evidence: a frame captured after the tap
        that does not have the list in it. A frame older than the tap says
        nothing about whether the tap worked, and is no longer allowed to argue
        for pressing the button again.
        """
        x, y = load_toml_as_dict("cfg/buttons_config.toml")["brawlers_menu"]
        for attempt in range(self.MENU_OPEN_ATTEMPTS):
            if self._should_interrupt(runtime_control, stop_event):
                return "aborted"

            # Look before pressing. This is the tap that was doing the damage:
            # it fired unconditionally, so anything that called selection twice
            # in a row - a retry after a failure, a queue rotating - tapped the
            # brawlers button while the list from the first attempt was still
            # open. On this screen those coordinates are the glory panel, so
            # that is what opened, and selection never got started.
            if self._list_is_open(get_latest_state):
                print("The brawler list is already open, so the button is not "
                      "tapped again.")
                return "open"

            self.window_controller.click(x, y, already_include_ratio=False)
            tapped_at = time.time()
            deadline = tapped_at + self.MENU_OPEN_TIMEOUT
            while True:
                if self._should_interrupt(runtime_control, stop_event):
                    return "aborted"

                # The shared state can be believed the moment it says the list
                # is up, however old the frame behind it is: the bot was in the
                # lobby a moment ago, so this reading cannot be left over from
                # some earlier visit to the menu. It is only the opposite
                # verdict - "still the lobby" - that a stale frame can get
                # wrong, and that is the one this no longer acts on.
                if get_latest_state() == "brawler_selection":
                    return "open"

                frame, frame_time = self.window_controller.get_latest_frame()
                after_tap = frame is not None and frame_time > tapped_at
                if after_tap and is_in_brawler_selection(frame):
                    return "open"

                if time.time() >= deadline:
                    if after_tap:
                        # We have a frame taken after the tap and it did not
                        # read as the brawler list. Either the tap missed, or -
                        # the case that keeps biting after a game update - the
                        # list IS open and the templates no longer recognise
                        # it. The two look identical from here, so keep the
                        # actual frame: it is the one thing that settles which,
                        # and the one thing nobody could send before.
                        self._dump_unreadable_menu(frame)
                        break
                    if time.time() >= tapped_at + self.MENU_OPEN_STALE_LIMIT:
                        print("No frame has arrived since the brawlers button "
                              "was tapped, so there is no way to tell whether "
                              "the list opened. Leaving it alone rather than "
                              "tapping into whatever is on screen.")
                        return "stuck"
                    # A frame from before the tap proves nothing. Keep waiting
                    # for one that was taken after it.
                    if self.verbose_debug:
                        print("Waiting for a frame taken after the tap before "
                              "deciding whether the brawler list opened.")
                time.sleep(0.2)
            print(f"The brawler list did not open (attempt {attempt + 1} of "
                  f"{self.MENU_OPEN_ATTEMPTS}), tapping again.")
        return "closed"

    # How many "could not read the open list" frames to keep per run. Enough to
    # catch the problem, few enough that an account permanently missing a
    # brawler cannot fill a disk overnight. Unlike the search-missed frames this
    # is NOT gated on verbose_debug: it fires only when the bot is already stuck
    # and about to give up, and it is the one picture that turns "it does not
    # work on my machine" into a template anyone can recut.
    MENU_DEBUG_FRAME_CAP = 6

    def _dump_unreadable_menu(self, frame):
        """Save a frame the bot tapped into but could not recognise as the list."""
        saved = getattr(self, "_menu_debug_saves", 0)
        if saved >= self.MENU_DEBUG_FRAME_CAP or frame is None:
            return
        import os
        try:
            os.makedirs("./debug_frames", exist_ok=True)
            path = f"./debug_frames/brawler_menu_unread_{int(time.time())}.png"
            # get_latest_frame hands back the same RGB the detector saw; imwrite
            # wants BGR, so convert or the saved PNG has red and blue swapped
            # and looks nothing like the screen.
            cv2.imwrite(path, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            self._menu_debug_saves = saved + 1
            print(f"Could not read the brawler list on screen. Saved what the "
                  f"bot saw to {path} - if selection keeps failing, send that "
                  f"file so the menu templates can be fixed to match your game.")
        except Exception as exc:
            print(f"Could not save the unreadable-menu frame: {exc}")

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

    def _save_debug_frame(self, label):
        """Keep the frame the bot just judged, so a bad call can be looked at.

        The brawler list is the one screen the bot drives blind: it types a
        name, waits, and believes whatever OCR reports back. When that goes
        wrong the log says "not found" and nothing says what was on screen -
        which is how a menu redesign became a bot that could not pick a
        brawler at all, with no way to see why short of standing over it.

        Only while verbose_debug is on. An account missing one brawler asks for
        it every match, and a 1920x1080 PNG per attempt would fill a disk.
        """
        if not self.verbose_debug:
            return None
        import os
        try:
            os.makedirs("./debug_frames", exist_ok=True)
            path = f"./debug_frames/brawler_{label}_{int(time.time())}.png"
            cv2.imwrite(path, cv2.cvtColor(self.window_controller.screenshot(),
                                           cv2.COLOR_RGB2BGR))
            print(f"Saved the brawler list frame to {path}")
            return path
        except Exception as exc:
            print(f"Could not save the brawler list frame: {exc}")
            return None

    def _read_names(self):
        """Every word on screen, cleaned up the way brawler names are.

        Raises EasyOCRInitializationError if OCR could not start at all, which
        is a setup problem rather than a bad frame and is worth saying so.
        """
        screenshot = self.window_controller.screenshot()
        screenshot = cv2.resize(screenshot, (int(screenshot.shape[1] * self.ocr_scale_down_factor), int(screenshot.shape[0] * self.ocr_scale_down_factor)), interpolation=cv2.INTER_AREA)

        print("Extracting text on current screen...")
        results = extract_text_and_positions(screenshot)
        results = {k: v for k, v in results.items() if len(k) >= 2}
        clean_results = {}
        for key in results.keys():
            orig_key = key
            for symbol in [' ', '-', '.', "&"]:
                key = key.replace(symbol, "")
            clean_results[key.lower()] = results[orig_key]
        return clean_results

    # The band the cards occupy, on a 1920x1080 screen. The top bar ends at
    # about y=110 and the first row of cards starts at y=190; the third and
    # last row ends at y=1014.
    CARD_AREA = (150, 1020)

    def _cards_only(self, clean_results):
        """Drop what OCR read outside the grid of cards.

        Searching puts the query on screen twice: once on the card and once in
        the search box that is showing it back. Matching the copy in the box
        would tap the box - which is already focused - and then press confirm
        on whichever brawler happened to be selected already, quietly picking
        the wrong one. Nothing but the top bar is above the first row, so
        cutting at its height separates the two.

        The floor matters for the same reason. A keyboard that did not go away
        offers the typed word back as a suggestion along the bottom of the
        screen, which OCR reads just as happily as a card.
        """
        hr = self.window_controller.height_ratio or 1
        scale = hr * self.ocr_scale_down_factor
        top, bottom = (limit * scale for limit in self.CARD_AREA)
        return {name: hit for name, hit in clean_results.items()
                if top <= hit['center'][1] <= bottom}

    def _match_name(self, brawler, clean_results):
        """Which word on screen is this brawler, or None.

        Four ways of saying the same thing, cheapest first: the name itself,
        the alias list, a fuzzy ratio, and finally the single-character rule
        that short names need. See _near_miss for why the last one exists.
        """
        if brawler in clean_results.keys():
            return brawler

        aliases = self.all_brawlers_names.get(brawler) or []
        for detected_name in clean_results.keys():
            if detected_name in aliases:
                print(f"Matched detected name '{detected_name}' to brawler '{brawler}' using alias list.")
                return detected_name

        # Fallback to fuzzy matching
        import difflib
        best_match = None
        best_ratio = 0.0
        for detected_name in clean_results.keys():
            ratio = difflib.SequenceMatcher(None, detected_name, brawler).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = detected_name

        if best_ratio >= 0.8:
            print(f"Fuzzy matched detected name '{best_match}' to brawler '{brawler}' with ratio {best_ratio:.2f}.")
            return best_match

        # Still nothing, and the ratio test cannot help a short name:
        # see _near_miss.
        near = self._near_miss(brawler, clean_results.keys())
        if near:
            print(f"Matched '{near}' to brawler '{brawler}': one character "
                  f"out, and nothing else on screen was close.")
        return near

    def _tap_and_confirm(self, brawler, matched_key, clean_results,
                         runtime_control=None, stop_event=None):
        """Open the card OCR found, then press the button that equips it."""
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

    def _scan_for_brawler(self, brawler, get_latest_state, scans, swipe,
                          runtime_control=None, stop_event=None):
        """Read the screen, and pick the brawler off it if he is there.

        `swipe` says whether to drag the list along between reads. After a
        search there is nothing to drag to - what matched is already on screen
        - so a miss means the search did not take, not that we have not looked
        far enough, and saying so quickly is the point.
        """
        c = 0
        shop_counter = 0
        # What the previous screen read, to notice when scrolling stops moving.
        seen_before = None
        stalled = 0
        wr = self.window_controller.width_ratio
        hr = self.window_controller.height_ratio
        row = int(self.SWIPE_ROW * hr)
        for i in range(scans):
            if self._should_interrupt(runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"

            try:
                clean_results = self._read_names()
            except EasyOCRInitializationError as exc:
                raise RuntimeError(
                    f"Automatic brawler selection could not start OCR: {exc}"
                ) from exc
            except Exception as exc:
                print(f"WARNING: Automatic brawler selection could not read this screen with OCR: {exc}")
                print("The bot will continue without changing the currently selected brawler.")
                return "error"

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

            cards = self._cards_only(clean_results)
            matched_key = self._match_name(brawler, cards)

            # After a search this one line is the whole diagnosis, so it goes in
            # the ordinary log rather than behind verbose_debug. Nothing in the
            # band means the typing never reached the game; six names still in
            # it means the typing arrived and filtered nothing.
            if not swipe:
                print(f"After searching, the cards on screen read: "
                      f"{', '.join(sorted(cards)) or '(nothing)'}")

            if self.verbose_debug:
                print("OCR detected the following potential matches for the brawler name:")
                import difflib
                for detected_name in cards.keys():
                    match_ratio = difflib.SequenceMatcher(None, detected_name, brawler).ratio()
                    if match_ratio >= 0.25:
                        print(f" - '{detected_name}' with match ratio {match_ratio:.2f}")
            if matched_key:
                return self._tap_and_confirm(brawler, matched_key, cards,
                                             runtime_control, stop_event)

            if not swipe:
                # One more read only buys time for the frame to catch up; there
                # is nothing else for it to find.
                if i + 1 < scans and self._sleep_interruptible(0.8, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                continue

            print("Brawler name not found on screen, scrolling along to load more brawlers...")

            # The end of the list looks exactly like a swipe that did not
            # register, so it has to be recognised rather than waited out: two
            # identical screens in a row means the list is not moving and the
            # brawler is genuinely not in it.
            names = frozenset(cards.keys())
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
                # A short drag first. The list is still settling from the wind
                # back to the start, and a full swipe into a moving view is the
                # one most likely to be swallowed.
                self.window_controller.swipe(int(self.SWIPE_FAR_X * wr), row,
                                             int((self.SWIPE_FAR_X - 50) * wr), row, duration=0.5)
                if self._sleep_interruptible(3, runtime_control, stop_event):
                    print("Brawler selection aborted by user.")
                    return "aborted"
                c += 1
                continue

            # Finger to the left, which pulls the later cards into view.
            self.window_controller.swipe(int(self.SWIPE_FAR_X * wr), row,
                                         int(self.SWIPE_NEAR_X * wr), row, duration=0.5)
            if self._sleep_interruptible(3, runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"

        return "failed"

    def select_brawler(self, brawler, get_latest_state, stop_event=None, runtime_control=None):
        """Put the bot on a named brawler.

        Search first, scroll only if searching did not work. The search box
        arrived in the same update that turned the list sideways, and it does
        in one string what the scan below does in up to forty screenfuls of
        OCR - so the scrolling path is kept as a fallback for the case where
        the typing never reaches the game, not as the normal route.
        """
        self.window_controller.screenshot()
        brawler = str(brawler).lower().strip()
        for symbol in [' ', '-', '.', "&"]:
            brawler = brawler.replace(symbol, "")

        print("Automatic brawler selection started for", brawler)
        opened = self._open_brawler_menu(get_latest_state, runtime_control, stop_event)
        if opened == "aborted":
            print("Brawler selection aborted by user.")
            return "aborted"
        if opened != "open":
            print("The brawler list never opened, so there is nothing to search. "
                  "Leaving the selected brawler alone.")
            return "stuck"

        searched = self._search_for_brawler(brawler, runtime_control, stop_event)
        if searched == "aborted":
            print("Brawler selection aborted by user.")
            return "aborted"
        if searched == "searched":
            outcome = self._scan_for_brawler(
                brawler, get_latest_state, self.SEARCH_SCANS, False,
                runtime_control, stop_event)
            if outcome != "failed":
                return self._done(outcome)
            print(f"Searching for '{brawler}' turned up nothing. Falling back to "
                  f"scrolling the whole list.")
            if not self._save_debug_frame("search_missed"):
                print("Turn on verbose_debug in cfg/debug_settings.toml to have "
                      "that frame saved to debug_frames/ next time - it is the "
                      "only way to tell a search box that ignored the typing "
                      "from one that filtered down to nothing.")
            if self._clear_search(runtime_control, stop_event):
                print("Brawler selection aborted by user.")
                return "aborted"

        if self._scroll_to_list_start(runtime_control, stop_event):
            print("Brawler selection aborted by user.")
            return "aborted"

        outcome = self._scan_for_brawler(
            brawler, get_latest_state, self.MAX_SCANS, True,
            runtime_control, stop_event)
        if outcome == "failed":
            print(f"WARNING: Brawler '{brawler}' was not found after {self.MAX_SCANS} "
                  f"scroll attempts.")
        return self._done(outcome)
