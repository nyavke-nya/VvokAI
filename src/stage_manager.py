import sys
import threading
import time
import cv2

from state_finder import get_state
from trophy_observer import TrophyObserver, MatchResult
from utils import find_template_center, load_toml_as_dict, notify_user, save_brawler_data

try:
    from early_access.early_access import get_brawler_stats, get_player_info

    early_access = True
except (ImportError, ModuleNotFoundError):
    early_access = False


    def get_brawler_stats(player_info, brawler_name, power_level=False):
        """Match the paid module's shape, including its three-value form.

        The paid version returns (trophies, win_streak, power) when asked for
        the power level, and the caller indexes [2] for it. The public API has
        the same field, so the free path answers the same question rather than
        declining it.
        """
        from brawl_api import get_brawler_power, get_brawler_stats as _stats
        trophies, win_streak = _stats(player_info, brawler_name)
        if power_level:
            return trophies, win_streak, get_brawler_power(player_info, brawler_name)
        return trophies, win_streak


    def get_player_info(tag):
        from brawl_api import get_player_info as _info
        return _info(tag)


def load_image(image_path, scale_factor):
    image = cv2.imread(image_path)
    orig_height, orig_width = image.shape[:2]

    new_width = int(orig_width * scale_factor)
    new_height = int(orig_height * scale_factor)

    resized_image = cv2.resize(image, (new_width, new_height))
    return resized_image


class StageManager:
    def __init__(self, brawlers_data, lobby_automator, window_controller, playstyle_info, state_getting, runtime_control=None):
        self.Lobby_automation = lobby_automator
        self.lobby_config = load_toml_as_dict("./cfg/lobby_config.toml")
        self.close_popup_icon = None
        self.brawlers_pick_data = brawlers_data
        self.Trophy_observer = TrophyObserver()
        self.time_since_last_stat_change = time.time()
        # When a match result was last written. One end screen is recorded once
        # without needing a long gap between matches - see end_game.
        self._last_result_recorded_at = 0.0
        self.play_again_on_win = load_toml_as_dict("./cfg/bot_config.toml")["play_again_on_win"] == "yes"
        self.window_controller = window_controller
        self.states = {
            'shop': self.quit_shop,
            'brawler_selection': self.quit_shop,
            'popup': self.close_pop_up,
            'match': lambda: 0,
            'match_making': lambda: 0,
            'lobby': self.start_game,
            'star_drop_regular': lambda: self.click_star_drop("regular"),
            'star_drop_angelic': lambda: self.click_star_drop("angelic"),
            'star_drop_demonic': lambda: self.click_star_drop("demonic"),
            'star_drop_starr_nova': lambda: self.click_star_drop("starr_nova"),
            'trophy_reward': lambda: self.window_controller.press("proceed"),
            'prestige_milestone': lambda: self.window_controller.press("continue_or_equip"),
            'end_draw': self.end_game,
            'end_victory': self.end_game,
            'end_defeat': self.end_game,
            'end_trio_showdown_0': self.end_game,
            'end_trio_showdown_1': self.end_game,
            'end_trio_showdown_2': self.end_game,
            'end_trio_showdown_3': self.end_game,
            'nano_noodles': self.click_nano_noodles,
            'buffie_machine': self.open_buffie_machine,
            'daily_wins': self.pick_daily_wins,
        }
        self.matches_since_last_webhook_ping = 0
        self.ping_every_x_match = load_toml_as_dict("cfg/webhook_config.toml")['ping_every_x_match']
        self.runtime_control = runtime_control
        # Always read, never conditionally. This used to be set only when the
        # paid module was installed, so without it the attribute did not exist
        # at all and anything that touched it raised AttributeError. The tag is
        # plain config and the API path needs it either way.
        self.player_tag = str(
            load_toml_as_dict("./cfg/general_config.toml").get('player_tag', "") or ""
        ).strip()
        # Set when a switch to the queue head did not take, cleared when it
        # does. Checked on every visit to the lobby.
        self.brawler_needs_selecting = False
        self.ping_when_stuck = load_toml_as_dict("cfg/webhook_config.toml")["ping_when_stuck"]
        self.playstyle_info = playstyle_info
        self.get_latest_state = state_getting

    def _should_stop(self):
        return bool(self.runtime_control and self.runtime_control.should_stop())

    def _should_pause(self):
        return bool(self.runtime_control and self.runtime_control.should_pause())

    def _sleep_interruptible(self, duration, allow_pause=True, poll_interval=0.1):
        end_time = time.time() + duration
        while time.time() < end_time:
            if self._should_stop():
                return True
            if allow_pause and self._should_pause():
                return True
            time.sleep(min(poll_interval, max(end_time - time.time(), 0)))
        return False

    @staticmethod
    def validate_trophies(trophies_string):
        trophies_string = trophies_string.lower()
        while "s" in trophies_string:
            trophies_string = trophies_string.replace("s", "5")
        numbers = ''.join(filter(str.isdigit, trophies_string))

        if not numbers:
            return False

        trophy_value = int(numbers)
        return trophy_value

    def adopt_current_brawler(self):
        """Point the trophy observer at whoever is at the head of the queue."""
        head = self.brawlers_pick_data[0]
        self.Trophy_observer.change_trophies(head['trophies'])
        self.Trophy_observer.current_wins = head['wins'] if head['wins'] != "" else 0
        self.Trophy_observer.win_streak = head['win_streak']

    def resync_from_api(self, when, expect_change_from=None, background=False):
        """Take the trophy count from the API instead of from our own running sum.

        Trophies are otherwise only ever a local total: a starting number plus
        every delta this bot believed it earned. Any delta it got wrong - a
        match it misread, a result it never saw, a game played by hand between
        sessions - stays wrong forever and the push target drifts with it.

        Two things used to stop this running at all.

        It was behind `if early_access`, the paid module, which is not part of
        this fork - so for everyone here the resync simply never happened. The
        fallbacks at the top of this file already provide both functions from
        brawl_api, so there is nothing to gate on.

        And it then required BOTH trophies and a win streak to be present. The
        official API does not publish per-brawler win streaks and never will;
        get_brawler_stats documents that it returns None for the streak. So the
        condition could not be true, and the trophy figure sitting right next
        to it - which the API does publish, and which is the whole point - was
        thrown away on account of a value nobody can supply.

        They are now judged separately: the trophy count is taken whenever it
        arrives, and the streak is left exactly as it was when the API has
        nothing to say about it.
        """
        if not self.player_tag:
            return

        if background:
            # Nothing waits on this. The point of asking the API is to stop the
            # bot's own arithmetic drifting, and that is worth nothing if the
            # asking is what makes the rematch slow.
            threading.Thread(
                target=self.resync_from_api,
                args=(when,),
                kwargs={"expect_change_from": expect_change_from},
                daemon=True,
            ).start()
            return

        requested_entry = self.brawlers_pick_data[0]
        current_brawler = requested_entry['brawler']

        # The bot's own cache would happily answer with the figure from before
        # the match that just finished, which is precisely the number being
        # corrected here.
        try:
            from brawl_api import clear_cache
            clear_cache()
        except (ImportError, ModuleNotFoundError):
            pass

        player_info = get_player_info(self.player_tag)
        if not player_info:
            print(f"Could not reach the API to refresh stats ({when}).")
            return

        trophies, win_streak = get_brawler_stats(player_info, current_brawler)

        if not self.brawlers_pick_data or self.brawlers_pick_data[0] is not requested_entry:
            return  # The response belongs to a brawler we have already left.

        # Supercell publishes the new total a moment after a match ends, so an
        # answer asked for immediately afterwards is often still the old one.
        # Writing that back would erase the win that just happened. Rather than
        # wait for it - waiting is the one thing this must not do - the stale
        # answer is simply declined, and the local figure stands until the next
        # resync, which will have had a whole match to catch up.
        if (expect_change_from is not None and trophies is not None
                and trophies == expect_change_from):
            print("API has not caught up with the last match yet, keeping the "
                  "local total for now.")
            return

        if trophies is not None:
            if trophies != self.Trophy_observer.current_trophies:
                print(f"Trophies resynced from the API ({when}): "
                      f"{self.Trophy_observer.current_trophies} -> {trophies}")
            self.Trophy_observer.current_trophies = trophies
            if self.brawlers_pick_data[0].get('type') == "trophies":
                self.brawlers_pick_data[0]["trophies"] = trophies
                save_brawler_data(self.brawlers_pick_data)

        # Only when the API actually knows. None means "no information", and
        # overwriting a real streak with it would reset the count every match.
        if win_streak is not None:
            self.Trophy_observer.win_streak = win_streak

    def start_game(self):
        if self._should_stop() or self._should_pause():
            return

        if self.player_tag:
            print("Waiting 3 seconds for API to update with latest data...")
            time.sleep(3)
            self.resync_from_api("before starting")

        # A switch that failed earlier is retried here, once per visit to the
        # lobby, until it takes. Without this the bot plays the brawler it has
        # already finished with, forever.
        if self.brawler_needs_selecting and self.brawlers_pick_data:
            head = self.brawlers_pick_data[0]
            if head.get("automatically_pick"):
                print(f"Retrying the switch to {head['brawler']}.")
                result = self.Lobby_automation.select_brawler(
                    head['brawler'], self.get_latest_state,
                    runtime_control=self.runtime_control)
                if result == "success":
                    self.brawler_needs_selecting = False
                    self.adopt_current_brawler()
                elif result in ("aborted", "stuck"):
                    return
            else:
                # Manual mode: nothing to retry, the person switches.
                self.brawler_needs_selecting = False

        print("state is lobby, starting game")
        values = {
            "trophies": self.Trophy_observer.current_trophies,
            "wins": self.Trophy_observer.current_wins
        }

        type_of_push = self.brawlers_pick_data[0]['type']
        value = values[type_of_push]
        push_current_brawler_till = self.brawlers_pick_data[0]['push_until']

        if value >= push_current_brawler_till:
            if len(self.brawlers_pick_data) <= 1:
                print("Brawler reached required trophies/wins. No more brawlers selected for pushing in the menu. "
                      "Bot will now pause itself until closed.", value, push_current_brawler_till)
                screenshot = self.window_controller.screenshot()
                notify_user("completed", screenshot, self)
                print("Bot stopping: all targets completed with no more brawlers.")
                if self.runtime_control:
                    self.runtime_control.request_stop()
                self.window_controller.release_movement(priority=True)
                # Nothing left to push, so nothing left to stay online for.
                # Same switch the scheduled pause uses - and read here with
                # plain code, because config_bool is not imported in this
                # module and a NameError on the finish path would turn a
                # completed run into a crash.
                raw = load_toml_as_dict("./cfg/bot_config.toml").get(
                    "close_game_when_scheduled", True)
                if raw is True or str(raw).strip().lower() in {"1", "true", "yes", "on"}:
                    self.window_controller.close_brawl_stars()
                self.window_controller.close()
                # Deliberately no shutdown here. Running out of brawlers can
                # happen at any hour, often minutes after a session starts, and
                # powering the machine off because a short queue emptied is not
                # what anybody means by it. Only the clock does that.
                sys.exit(0)
            ping_when_target_is_reached = load_toml_as_dict("cfg/webhook_config.toml")["ping_when_target_is_reached"]
            if ping_when_target_is_reached:
                screenshot = self.window_controller.screenshot()
                notify_user("brawler_goal", screenshot, self)
            print(f'Bot has reached the target trophies/wins for {self.brawlers_pick_data[0]["brawler"]}, moving on to the next one in the list.', value, push_current_brawler_till)
            self.brawlers_pick_data.pop(0)
            next_brawler_name = self.brawlers_pick_data[0]['brawler']
            if self.brawlers_pick_data[0]["automatically_pick"]:
                select_brawler = self.Lobby_automation.select_brawler(next_brawler_name, self.get_latest_state, runtime_control=self.runtime_control)
                # Bounded for the same reason as in main.py: rotating a queue of
                # one hands back the brawler that just failed, and the loop
                # never ends. Every entry gets one turn, then the bot carries on
                # rather than sitting in the lobby forever.
                attempts_left = max(1, len(self.brawlers_pick_data))
                while select_brawler in ["failed", "error"] and attempts_left > 0:
                    if self.ping_when_stuck:
                        screenshot = self.window_controller.screenshot()
                        notify_user("bot_failed_brawler_selection", screenshot, self)
                        print(f"Skipping {select_brawler}")
                    if self._should_stop() or self._should_pause():
                        return
                    attempts_left -= 1
                    if attempts_left <= 0:
                        print("No queued brawler could be selected. Will try "
                              "again before the next match.")
                        # Remembered, because giving up here used to be
                        # permanent. Selection is only attempted when a target
                        # is reached, and once the game is left on the finished
                        # brawler that never happens again - the queue head is
                        # a brawler nobody is playing, so its trophies never
                        # move and its target is never met. The bot sat pushing
                        # the completed brawler for the rest of the session.
                        self.brawler_needs_selecting = True
                        break
                    current_brawler = self.brawlers_pick_data.pop(0)
                    self.brawlers_pick_data.append(current_brawler)
                    next_brawler_name = self.brawlers_pick_data[0]['brawler']
                    self.quit_shop()
                    select_brawler = self.Lobby_automation.select_brawler(next_brawler_name, self.get_latest_state, runtime_control=self.runtime_control)
                if select_brawler == "aborted" or select_brawler == "stuck":
                    self.brawler_needs_selecting = True
                    return
                if select_brawler == "success":
                    self.brawler_needs_selecting = False
                # Adopted whether or not the switch worked, and that is the
                # point. This used to sit inside "if success", so a failed
                # selection left the observer holding the FINISHED brawler's
                # trophies - still above its target - and the very next lobby
                # read the target as reached again, removed another brawler and
                # tried again. The queue emptied one entry per match while the
                # bot played the same brawler throughout, which is what this
                # looked like from outside: brawlers vanishing from the list and
                # nothing ever changing on screen.
                #
                # brawlers_pick_data[0] is the brawler being pushed now, so the
                # observer has to track it either way. If the switch failed the
                # game is still on the old one for a match, and one match of
                # trophies goes to the wrong name - which the API resync
                # corrects, and which is a great deal cheaper than eating the
                # whole queue.
                self.adopt_current_brawler()
            else:
                self.adopt_current_brawler()
                print("Next brawler is in manual mode, waiting 10 seconds to let user switch.")
                if self._sleep_interruptible(10):
                    return
        save_brawler_data(self.brawlers_pick_data)
        self.matches_since_last_webhook_ping += 1
        if self.ping_every_x_match and self.matches_since_last_webhook_ping >= self.ping_every_x_match:
            screenshot = self.window_controller.screenshot()
            notify_user("regular_matches_ping", screenshot, self)
            self.matches_since_last_webhook_ping = 0

        if self._should_stop() or self._should_pause():
            return
        self.window_controller.release_movement(priority=True)
        self.window_controller.press("proceed")
        print("Pressed to start a match")
        time.sleep(2)

    def click_star_drop(self, drop_type="regular"):
        if hasattr(self, '_star_drop_thread') and self._star_drop_thread.is_alive():
            return

        def _handle_drop():
            if drop_type in ["angelic", "demonic", "starr_nova"]:
                self.window_controller.press("proceed", 8)
            else:
                for _ in range(8):
                    self.window_controller.press("proceed", 0.05)
                    time.sleep(0.1)

        import threading
        self._star_drop_thread = threading.Thread(target=_handle_drop, daemon=True)
        self._star_drop_thread.start()

    # Where the barrels stand on a 1920x1080 screen, left to right. The row is
    # fixed; which of the slots still hold an unopened barrel is not.
    DAILY_WINS_SLOTS = [
        (300, 725), (637, 768), (969, 785), (1300, 768), (1640, 733),
    ]

    def pick_daily_wins(self):
        """Open three barrels on the daily-wins screen.

        Three because that is what the screen offers, and three distinct ones
        because clicking the same barrel twice wastes a pick. Which three does
        not matter - the reward is behind whichever is chosen - so they are
        picked at random rather than pretending to choose.

        Same shape as click_nano_noodles: taps in one pass with a short gap
        between them, no thread. The gap is longer here because a barrel plays
        an opening animation and a tap during it lands on the reward covering
        the next barrel rather than on the barrel.
        """
        import random

        for x, y in random.sample(self.DAILY_WINS_SLOTS, 3):
            if self._should_stop():
                return
            self.window_controller.click(x, y, already_include_ratio=False)
            time.sleep(0.6)

    def open_buffie_machine(self):
        """Hold the machine's button until it opens.

        A press is not enough here - the machine wants the button held, so this
        is the one reward that is not a click. Held on its own pointer and its
        own thread, because five seconds of the bot loop is five seconds of not
        dodging, not moving and not shooting.
        """
        seconds = float(load_toml_as_dict("cfg/bot_config.toml").get(
            "buffie_hold_seconds", 5.0))
        print(f"Buffie machine: holding the button for {seconds:g}s.")
        self.window_controller.hold("buffie_machine", seconds)
        time.sleep(seconds + 0.5)

    def click_nano_noodles(self):
        noodle_x, noodle_y = 960, 740
        offset_x = 330
        self.window_controller.click(
            noodle_x,
            noodle_y,
            already_include_ratio=False
        )
        time.sleep(0.1)
        self.window_controller.click(
            noodle_x + offset_x,
            noodle_y,
            already_include_ratio=False
        )
        time.sleep(0.1)
        self.window_controller.click(
            noodle_x - offset_x,
            noodle_y,
            already_include_ratio=False
        )

    def read_power_level(self, brawler):
        """The brawler's power level, or None when nothing can say.

        None rather than a guess: add_trophies writes -1 for "not known", and
        a made-up 11 in the history would be worse than an honest blank.
        """
        if not self.player_tag:
            return None
        try:
            player_info = get_player_info(self.player_tag)
            if not player_info:
                return None
            return get_brawler_stats(player_info, brawler, power_level=True)[2]
        except Exception as exc:
            print(f"Could not read the power level for {brawler} ({exc}).")
            return None

    def _target_reached(self):
        """Has the brawler at the front of the queue met its goal?

        The same comparison start_game() rotates the queue on. end_game() has to
        be able to ask it too, because a rematch never reaches start_game().
        Anything unreadable answers "no": pushing a little too long is a much
        smaller mistake than stopping a queue that was still running.
        """
        try:
            entry = self.brawlers_pick_data[0]
            value = {
                "trophies": self.Trophy_observer.current_trophies,
                "wins": self.Trophy_observer.current_wins,
            }[entry["type"]]
            if value is None:
                return False
            return value >= entry["push_until"]
        except (IndexError, KeyError, TypeError):
            return False

    def end_game(self):
        screenshot = self.window_controller.screenshot()

        current_state = get_state(screenshot)
        end_screen_time = time.time()
        parsed_result = None
        while current_state.startswith("end") and time.time() - end_screen_time < 35:

            # Recorded once per end screen. `parsed_result` already guarantees
            # that within this call, and the short window below covers the rare
            # case where the loop times out with the screen still up and the
            # function is entered again on the same one.
            #
            # This used to require 25 seconds since the last stat change, which
            # is a gap BETWEEN matches, not within one. A quick death in
            # showdown or a fast knockout round finished inside that window, so
            # the whole block was skipped and the match was never recorded at
            # all - no trophies, no win, no history row.
            if parsed_result is None and time.time() - self._last_result_recorded_at > 5:
                raw_found_result = '_'.join(current_state.split("_")[1:])
                parsed_result = self.Trophy_observer.parse_game_result(raw_found_result)

                current_brawler = self.brawlers_pick_data[0]['brawler']
                # Was gated on the paid module. The public API carries the same
                # field, so the only thing the gate did was leave the column
                # empty for everyone on the free path.
                power_level = self.read_power_level(current_brawler)
                # Kept so the resync below can tell "the API has the new total"
                # from "the API has not caught up yet".
                trophies_before_match = self.Trophy_observer.current_trophies
                self.Trophy_observer.add_trophies(parsed_result, current_brawler, self.playstyle_info, power_level)
                self.Trophy_observer.add_win(parsed_result)
                self.time_since_last_stat_change = time.time()
                self._last_result_recorded_at = time.time()
                values = {
                    "trophies": self.Trophy_observer.current_trophies,
                    "wins": self.Trophy_observer.current_wins
                }
                type_to_push = self.brawlers_pick_data[0]['type']
                value = values[type_to_push]
                self.brawlers_pick_data[0][type_to_push] = value
                self.brawlers_pick_data[0]['win_streak'] = self.Trophy_observer.win_streak
                save_brawler_data(self.brawlers_pick_data)

                # Rematching never passes through the lobby, so start_game -
                # and with it the only other resync - is skipped for as long as
                # the wins keep coming. That is exactly the run over which a
                # drifting total does the most damage.
                self.resync_from_api("after the match",
                                     expect_change_from=trophies_before_match,
                                     background=True)

            wants_rematch = (self.play_again_on_win and parsed_result
                             and parsed_result.result == MatchResult.VICTORY
                             # A rematch goes straight into the next game and
                             # never passes through start_game(), which is the
                             # only place the queue rotates when a brawler meets
                             # its goal. Without this the bot kept winning on a
                             # brawler that was already finished and pushed it
                             # hundreds of trophies past the target, while the
                             # panel and Telegram both said the goal was met.
                             and not self._target_reached()
                             and not self._should_pause() and not self._should_stop())

            if wants_rematch:
                # Keep asking for the rematch for as long as the end screen is
                # still up, and never fall through to "proceed" here.
                #
                # This used to press play_again once and then, because the flag
                # was set, take the else branch on every following pass - so
                # three seconds after asking for a rematch it pressed proceed
                # and cancelled it. Whether that happened came down to how fast
                # the end screen cleared: quick enough and the loop had already
                # exited, slow enough and the rematch was thrown away, the wait
                # below then timed out with no match, and the game restarted.
                # Same setting, same build, working for one person and hanging
                # for another for twenty seconds.
                #
                # Repeating the press also covers the button simply not being
                # ready for the first one. The loop is capped at 35 seconds and
                # the screen is re-read every pass, so this cannot spin.
                self.window_controller.press("play_again")
            else:
                print("Game has ended, proceeding")
                self.window_controller.press("proceed")

            time.sleep(3)
            screenshot = self.window_controller.screenshot()
            current_state = get_state(screenshot)

        # Stop was missing from this condition while pause was in it, so after
        # Stop the bot still came in here to wait for a rematch it had already
        # decided not to ask for. The wait then broke out on the stop, fell
        # through to the line below, and RESTARTED Brawl Stars - which from the
        # outside is a bot that carries on after you have told it to stop.
        if (self.play_again_on_win and parsed_result
                and parsed_result.result == MatchResult.VICTORY
                and not self._target_reached()
                and not self._should_pause() and not self._should_stop()):
            print("Waiting for match to start...")
            start_wait_time = time.time()
            interrupted = False
            while time.time() - start_wait_time < 25:
                if self._should_stop() or self._should_pause():
                    interrupted = True
                    break
                screenshot = self.window_controller.screenshot()
                current_state = get_state(screenshot)
                if current_state == "match":
                    print("Match started successfully!")
                    return
                if self._sleep_interruptible(0.5):
                    interrupted = True
                    break

            # Told to stop, rather than nothing happening. Restarting the game
            # is the answer to a rematch that never arrived, and the wrong
            # answer to somebody pressing Stop while we waited for one.
            if interrupted:
                print("Stopped while waiting for the rematch.")
                return

            print("Match did not start within 25s, restarting the game.")
            self.window_controller.restart_brawl_stars()
            time.sleep(2)
        elif time.time() - end_screen_time > 35:
            print("End screen timeout reached, restarting the game.")
            self.window_controller.restart_brawl_stars()
        print("Game has ended", current_state)

    def quit_shop(self):
        self.window_controller.click(100 * self.window_controller.width_ratio, 60 * self.window_controller.height_ratio)
        time.sleep(1)

    def close_pop_up(self):
        screenshot = self.window_controller.screenshot()
        if self.close_popup_icon is None:
            self.close_popup_icon = load_image("assets/images/states/close_popup.png", self.window_controller.scale_factor)
        popup_location = find_template_center(screenshot, self.close_popup_icon)
        if popup_location:
            self.window_controller.click(*popup_location)

    def do_state(self, state, data=None):
        if data is not None:
            self.states[state](data)
            return
        self.states[state]()
