import inspect
import os
import sys

# The modules live in src/ rather than loose in the project root. Their names
# are unchanged - this only tells Python where to find them, so every
# `from utils import ...` in the codebase still reads the same.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

# Monkey-patch inspect.getfile to prevent Nuitka + PyTorch crash
_original_getfile = inspect.getfile
def _patched_getfile(obj):
    res = _original_getfile(obj)
    return res if res is not None else "<unknown_nuitka_file>"

inspect.getfile = _patched_getfile

# How long one thread may hold the interpreter before it has to offer it back.
#
# The default is 5 ms, and that is the size of the hitch. The dodge tracker runs
# on its own thread, and its cost is not constant: measured over a real session,
# a frame takes 2.9 ms with the screen quiet and 10.3 ms once there are eighty
# tracks on it - so the moment shots appear there is a second thread wanting the
# interpreter almost continuously. The bot loop then waits up to a full 5 ms
# slice at a time, which is why the profile shows the pure-Python stages
# stretching under fire (playstyle 6.7 -> 11.1 ms) while YOLO, which drops the
# GIL for the duration of an inference, does not move at all (5.0 -> 4.8).
#
# 1 ms, measured on the same shape of workload: the worst stall the latency
# sensitive loop sees falls from 5.5 ms to 1.5 ms, and its median does not
# change. Below that the extra switching starts costing more than it saves -
# 0.5 ms measured worse than 1 ms.
sys.setswitchinterval(0.001)


if __name__ == "__main__" and len(sys.argv) >= 9 and sys.argv[1] == "--debug-viewer-worker":
    from debug_view import DEFAULT_DEBUG_VIEW_FPS, run_viewer_worker

    run_viewer_worker(
        shared_memory_name=sys.argv[2],
        debug_memory_name=sys.argv[3],
        height=int(sys.argv[4]),
        width=int(sys.argv[5]),
        channels=int(sys.argv[6]),
        dtype_text=sys.argv[7],
        title=sys.argv[8],
        clip_fps=float(sys.argv[9]) if len(sys.argv) >= 10 else DEFAULT_DEBUG_VIEW_FPS,
        record_clips=(len(sys.argv) >= 11 and sys.argv[10] == "1"),
    )
    sys.exit(0)

from adbutils import AdbError
import socket
import threading
import time
import webbrowser
from lobby_automation import LobbyAutomation
from play import Play
from stage_manager import StageManager
from state_finder import get_state
from time_management import TimeManagement
from utils import load_toml_as_dict, current_wall_model_is_latest, api_base_url, load_vvok_script, save_brawler_data, \
    clean_queue, get_discord_link
from utils import get_brawler_list, update_missing_brawlers_info, check_version, notify_user, update_wall_model_classes, get_latest_wall_model_file, cprint, config_bool
from utils import resolve_project_path
from window_controller import WindowController
from webui import create_app


def apply_play_order(queue_data):
    play_order = str(load_toml_as_dict("cfg/general_config.toml").get("play_order", "in_order")).strip().lower()
    if play_order == "lowest_to_highest":
        ordered_data = sorted(queue_data, key=lambda item: int(item.get("trophies", 0) or 0))
    elif play_order == "highest_to_lowest":
        ordered_data = sorted(queue_data, key=lambda item: int(item.get("trophies", 0) or 0), reverse=True)
    else:
        return queue_data

    for item in ordered_data:
        item["automatically_pick"] = True
    return ordered_data


def vvok_main(remote, queue_data, stop_event=None, runtime_control=None):
    class Main:
        def __init__(self):
            current_playstyle = load_toml_as_dict("cfg/bot_config.toml").get("current_playstyle", "unified_dodge.vvok")
            try:
                self.max_ips = int(load_toml_as_dict("cfg/general_config.toml")['max_ips'])
            except ValueError:
                self.max_ips = None

            if self.max_ips:
                self.window_controller = WindowController(self.max_ips)
            else:
                self.window_controller = WindowController()
            data = clean_queue(queue_data)
            data = apply_play_order(data)
            if not data:
                raise ValueError("No valid brawler data found. Please add a brawler configuration in the UI before starting the bot.")
            save_brawler_data(data)
            print("Starting with queue data:", data)
            self.playstyle_info, vvok_code = load_vvok_script(current_playstyle)
            self.Play = Play(*self.load_models(), self.window_controller, vvok_code,
                             playstyle_info=self.playstyle_info)
            self.Play.runtime_control = runtime_control
            self.Time_management = TimeManagement()
            self.lobby_automator = LobbyAutomation(self.window_controller)
            self.runtime_control = runtime_control
            self.Stage_manager = StageManager(data, self.lobby_automator, self.window_controller, self.playstyle_info, self.get_latest_state, runtime_control=runtime_control)
            self.states_requiring_data = ["lobby"]
            # Eight minutes, not thirty: this fires restart_brawl_stars() when
            # the player model has not been seen for this long, and loading
            # screens, matchmaking and brawler select all regularly exceed 30s
            # without a detection. At 30 the bot restarts the game in the
            # middle of perfectly normal waits.
            self.no_detections_action_threshold = 60 * 8
            self.state = None
            self.stop_event = stop_event
            self.state_lock = threading.Lock()
            self.latest_state_frame_time = 0.0
            self.max_cached_state_age = 1.0
            self.state_checker_stop_event = threading.Event()
            self.state_checker_thread = None
            self.crash_check_stop_event = threading.Event()
            self.crash_check_thread = None
            self.update_trophy_observer()

            self.run_for_minutes = int(load_toml_as_dict("cfg/general_config.toml")['run_for_minutes'])
            self.webhook_ping_every_minutes = load_toml_as_dict("cfg/webhook_config.toml")['ping_every_x_minutes']
            self.time_since_last_webhook_ping = time.time()
            self.start_time = time.time()
            self.time_to_stop = False
            self.in_cooldown = False
            self.cooldown_start_time = 0
            self.cooldown_duration = 3 * 60
            self.window_controller.screenshot()
            remote.set_window_controller(self.window_controller)
            self.start_state_checker()
            print("Initialization complete, starting main loop.")
            self.picked_first_brawler = False
            self.time_since_checked_if_brawl_stars_crashed = time.time()
            self.check_if_brawl_stars_crashed_timer = load_toml_as_dict("cfg/time_tresholds.toml")["check_if_brawl_stars_crashed"]
            self.activity_watchdog = self.build_activity_watchdog()
            self._stuck_state = None
            self._stuck_count = 0
            self.stuck_state_limit = int(load_toml_as_dict(
                "cfg/time_tresholds.toml").get("stuck_state_checks", 30))
            # Long enough that a restart has finished and the game is back
            # before another one can be triggered by the same grey box.
            self.idle_restart_cooldown = float(
                load_toml_as_dict("cfg/time_tresholds.toml").get("idle_restart_cooldown", 90.0))
            self.time_since_idle_restart = 0.0
            self.ping_when_stuck = load_toml_as_dict("cfg/webhook_config.toml")["ping_when_stuck"]

        @staticmethod
        def _as_count(value):
            """A queue field as a number. Anything unreadable counts as zero.

            Only `wins` used to get this treatment, so a queue entry carrying
            an empty string for its trophies - which happens for an entry
            pushing wins rather than trophies - seeded the observer with "" and
            broke the first end of match.
            """
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                return 0
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0

        def update_trophy_observer(self):
            current_brawler_data = self.Stage_manager.brawlers_pick_data[0]
            observer = self.Stage_manager.Trophy_observer
            observer.win_streak = self._as_count(current_brawler_data.get('win_streak'))
            observer.current_trophies = self._as_count(current_brawler_data.get('trophies'))
            observer.current_wins = self._as_count(current_brawler_data.get('wins'))

        @staticmethod
        def load_models():
            folder_path = "./models/"
            return [
                folder_path + 'mainInGameModel.onnx',
                folder_path + 'tileDetector.onnx',
                folder_path + 'closeTileDetector.onnx',
            ]

        def handle_idle_disconnect(self, reason="disconnect"):
            """The game says we are no longer in this match.

            Restart it, at once. Pressing the button on either card - RELOAD or
            RETRY LOGIN - returns to a battle that has carried on without us,
            so that left the bot staring at a match it could no longer affect;
            a restart puts it back in the lobby where the normal flow picks up.

            The guard is not a delay before acting - the first sighting acts
            immediately. It stops a repeat, because a restart is expensive
            enough that a detector firing every few seconds would be worse than
            the thing it is fixing.
            """
            now = time.time()
            if now - self.time_since_idle_restart < self.idle_restart_cooldown:
                return
            self.time_since_idle_restart = now
            print(f"{reason.capitalize()} on screen - restarting Brawl Stars.")
            self.restart_brawl_stars()

        def note_state_for_stuck_check(self, state):
            """Restart the game if a passing screen refuses to pass.

            Reported from a run that printed "State: match_making" a hundred
            times over: matchmaking had wedged, and nothing noticed because
            everything else was healthy - frames arriving at a hundred a
            second, the picture animating, the models running. The frozen
            screen watchdog cannot catch this one; the spinner is still turning.
            """
            if state != self._stuck_state:
                self._stuck_state = state
                self._stuck_count = 0
                return

            if state not in self.TRANSIENT_STATES:
                return

            self._stuck_count += 1
            if self._stuck_count < self.stuck_state_limit:
                return

            print(f"Stuck on '{state}' for {self._stuck_count} checks in a row "
                  f"- restarting Brawl Stars.")
            self._stuck_count = 0
            self._stuck_state = None
            self.restart_brawl_stars()

        def note_ips_for_stats(self, rate):
            """Hand the rate to the statistics, and report when one is due.

            Reporting only at startup meant every report ever sent carried
            "ips": null - the bot had not run yet - so the data collected to
            find out whether TensorRT helped anybody could not answer that.
            send() is rate limited to once every six hours, so calling it once
            a second costs a dictionary lookup.
            """
            try:
                from telemetry import note_ips, note_provider, send

                note_ips(rate)
                detector = getattr(self.Play, "Detect_main_info", None)
                if detector is not None:
                    note_provider(getattr(detector, "device", ""))
                send(profile=self.stats_profile)
            except Exception:
                pass

        def stats_profile(self):
            from profile_stats import build_profile
            import csv

            path = resolve_project_path("cfg", "match_history.csv")
            if not path.exists():
                return {}
            with open(path, newline="", encoding="utf-8") as handle:
                return build_profile(list(csv.DictReader(handle)))

        def stats_version(self):
            try:
                return resolve_project_path(".vvok_version").read_text(
                    encoding="utf-8").strip()[:12]
            except OSError:
                return ""

        def build_activity_watchdog(self):
            """The thing that notices a screen which has stopped moving."""
            from activity_watchdog import ActivityWatchdog

            times = load_toml_as_dict("cfg/time_tresholds.toml")
            return ActivityWatchdog(
                restart_game=self.restart_brawl_stars,
                game_after=float(times.get("frozen_screen_restart_game", 180)),
                emulator_after=float(times.get("frozen_screen_restart_emulator", 600)),
            )

        def feed_resumed(self, wait=6.0):
            """Whether frames started arriving again after a reconnect."""
            deadline = time.time() + wait
            _, before = self.window_controller.get_latest_frame()
            while time.time() < deadline:
                if self.sleep_interruptible(0.5) == "stop":
                    return True  # stopping; not our problem to fix
                _, now = self.window_controller.get_latest_frame()
                if now > before:
                    return True
            return False

        def restart_brawl_stars(self):
            self.window_controller.restart_brawl_stars()
            watchdog = getattr(self, "activity_watchdog", None)
            if watchdog is not None:
                watchdog.reset("Brawl Stars restarted")
            self._stuck_state = None
            self._stuck_count = 0
            self.time_since_checked_if_brawl_stars_crashed = time.time()
            self.Play.time_since_detections["player"] = time.time()
            self.Play.time_since_detections["enemy"] = time.time()
            if not self.window_controller.is_brawl_stars_running():
                ping_when_stuck = load_toml_as_dict("cfg/webhook_config.toml")["ping_when_stuck"]
                if ping_when_stuck:
                    screenshot = self.window_controller.screenshot()
                    notify_user("bot_is_stuck", screenshot, self.Stage_manager)
                    print("Bot got stuck. User notified.")
                print("Shutting down.")
                if self.Play.dodge_service is not None:
                    self.Play.dodge_service.stop()
                self.window_controller.release_movement(priority=True)
                self.window_controller.close()
                remote.set_window_controller(None)
                sys.exit(1)

        def should_stop(self):
            return bool(self.stop_event and self.stop_event.is_set()) or bool(self.runtime_control and self.runtime_control.should_stop())

        def should_pause(self):
            return bool(self.runtime_control and self.runtime_control.should_pause())

        def sleep_interruptible(self, duration, allow_pause=True, poll_interval=0.1):
            end_time = time.time() + duration
            while time.time() < end_time:
                if self.should_stop():
                    return "stop"
                if allow_pause and self.should_pause():
                    return "pause"
                time.sleep(min(poll_interval, max(end_time - time.time(), 0)))
            return None

        def stop_gracefully(self):
            cprint("Stop requested from UI - shutting down gracefully", "#AAE5A4")
            # Order matters. The crash watchdog treats "Brawl Stars is not
            # running" as a crash and starts it again within a couple of
            # seconds, so closing the game while anything is still watching is
            # pointless - it has to go first.
            # Was this the clock, or somebody pressing Stop? It decides
            # whether the machine is allowed to power off, and getting it wrong
            # would shut the computer down under a person who is sitting at it.
            by_schedule = bool(
                getattr(self.runtime_control, "schedule_hold_reason", lambda: "")()
            )
            self.stop_state_checker()
            self.stop_crash_watchdog()
            if self.close_game_on_stop():
                self.window_controller.close_brawl_stars()
            if self.Play.dodge_service is not None:
                self.Play.dodge_service.stop()
            # priority=True so a dodge that grabbed the joystick a moment ago
            # cannot leave the stick held down after shutdown.
            self.window_controller.release_movement(priority=True)
            self.window_controller.close()
            remote.set_window_controller(None)
            if by_schedule and self.shutdown_when_done():
                from utils import shutdown_computer
                shutdown_computer()

        def close_game_on_stop(self):
            """Whether shutting down should also close Brawl Stars.

            Read each time rather than cached, so changing it takes effect at
            the next stop instead of the next launch.
            """
            raw = load_toml_as_dict("./cfg/bot_config.toml").get("close_game_when_scheduled", True)
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}

        def shutdown_when_done(self):
            """Whether finishing should power the machine off. Off by default."""
            raw = load_toml_as_dict("./cfg/bot_config.toml").get("shutdown_when_done", False)
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}

        def start_state_checker(self):
            if self.state_checker_thread and self.state_checker_thread.is_alive():
                return
            self.state_checker_stop_event.clear()
            self.state_checker_thread = threading.Thread(
                target=self.state_checker_loop,
                daemon=True,
                name="vvok-state-checker"
            )
            self.state_checker_thread.start()

        def stop_state_checker(self):
            self.state_checker_stop_event.set()
            if self.state_checker_thread and self.state_checker_thread.is_alive():
                self.state_checker_thread.join(timeout=1.0)

        # How many readings in a row have to agree that a match is over before
        # the rest of the bot is told so.
        #
        # A single misread was enough to stop the bot mid-match: the brawler
        # list's icons matching the shop template, a HUD element clearing a menu
        # template for one frame. do_state() then acted on that menu - and the
        # play loop, seeing a state that is not "match", stopped moving and
        # shooting and stood there. That is the "just stands AFK, only attacks"
        # report.
        #
        # A real match end lasts for many frames, so waiting for a few costs
        # about a tenth of a second and nothing else. Only match -> not-match is
        # held back; entering a match, and every change outside one, is instant.
        MATCH_EXIT_CONFIRM = 3

        def set_latest_state(self, state):
            with self.state_lock:
                if self.state == "match" and state != "match" and state is not None:
                    self._match_exit_votes = getattr(self, "_match_exit_votes", 0) + 1
                    if self._match_exit_votes < self.MATCH_EXIT_CONFIRM:
                        # Not convinced yet - keep telling everyone the match is
                        # still on, so the bot keeps playing.
                        return
                else:
                    self._match_exit_votes = 0
                self.state = state

        def get_latest_state(self):
            with self.state_lock:
                return self.state

        # Screens that are a step on the way somewhere. Sitting on one of
        # these forever means the step never completed, which a restart fixes
        # and waiting does not.
        #
        # "match" and "lobby" are deliberately absent: a long match is a long
        # match, and the lobby is where a paused or finished bot is supposed to
        # sit. Restarting either would break something that was working.
        # "continue_card" is one of these too: it means a card is on screen
        # that only the CONTINUE button identifies, so if tapping that
        # button is not making it go away, nothing here will, and a restart
        # beats sitting in front of it.
        TRANSIENT_STATES = {"match_making", "popup", "shop", "brawler_selection",
                            "continue_card"}

        def handle_detected_state(self, state):
            if state is None:
                return
            self.set_latest_state(state)

            print(f"State: {state}")
            self.note_state_for_stuck_check(state)
            frame_data = None
            self.Stage_manager.do_state(state, frame_data)
            if state != "match":
                self.Play.time_since_last_proceeding = time.time()

        def state_checker_loop(self):
            last_checked_frame_time = 0.0
            while not self.state_checker_stop_event.is_set():
                frame, frame_time = self.window_controller.get_latest_frame()
                if frame is None or frame_time <= last_checked_frame_time:
                    self.state_checker_stop_event.wait(0.01)
                    continue

                last_checked_frame_time = frame_time
                try:
                    self.set_latest_state(get_state(frame))
                except Exception as e:
                    print(f"State checker failed: {e}")
                    self.state_checker_stop_event.wait(0.1)

        def wait_while_paused(self):
            if not self.runtime_control:
                return

            # priority so a dodge in flight cannot keep the stick held while
            # the run is paused from the UI.
            self.window_controller.release_movement(priority=True)
            self.runtime_control.mark_paused()
            cprint("VvokAI is paused in the lobby. Waiting for Start to resume.", "#AAE5A4")

            while self.should_pause() and not self.should_stop():
                state = self.get_latest_state()
                if state is None:
                    if self.sleep_interruptible(0.25, allow_pause=False) == "stop":
                        return
                    continue
                if self.sleep_interruptible(0.75, allow_pause=False) == "stop":
                    return

            if not self.should_stop():
                self.runtime_control.mark_running()
                self.time_since_last_webhook_ping = time.time()
                print("Pause released, resuming run.")

        def handle_pause_request(self):
            if self.should_pause() and not self.should_stop():
                cprint("Pause requested from UI - waiting", "#AAE5A4")
                self.wait_while_paused()

        def manage_time_tasks(self, frame):
            if self.Time_management.state_check():
                state = self.get_latest_state()
                if state is not None:
                    self.handle_detected_state(state)
            if self.Time_management.no_detections_check():
                frame_data = self.Play.time_since_detections
                t_now = time.time()
                for key, value in frame_data.items():
                    if t_now - value > self.no_detections_action_threshold:
                        self.restart_brawl_stars()
            # One read of the timer, not two: check_time resets on the way
            # out, so asking twice means the second caller never fires.
            if self.Time_management.idle_check():
                dropped = self.lobby_automator.check_for_idle(frame)
                if dropped:
                    self.handle_idle_disconnect(dropped)
            # Only outside a match. Invites do arrive mid-match, but the dialog
            # sits over the lobby, and OCR in the play loop would cost frames
            # for something that can wait until the match ends.
            if (self.get_latest_state() != "match"
                    and self.Time_management.team_invite_check()):
                self.lobby_automator.check_for_team_invite(frame)

            current_time = time.time()
            if self.webhook_ping_every_minutes and current_time - self.time_since_last_webhook_ping >= self.webhook_ping_every_minutes * 60:
                screenshot = self.window_controller.screenshot()
                notify_user("regular_minutes_ping", screenshot, self.Stage_manager)
                self.time_since_last_webhook_ping = current_time
                print(f"Sent regular webhook ping after {self.webhook_ping_every_minutes} minutes.")

        def start_crash_watchdog(self):
            """Run the crash check on its own thread, not in the bot loop.

            The check is a blocking ADB shell round-trip (app_current), which
            can cost anywhere from a few to a few hundred milliseconds
            depending on how busy the emulator is. Called inline, as it used
            to be, it froze detection, playstyle and joystick output for that
            long every 2.4 s - a periodic stutter visible as the bot briefly
            ignoring everything, mid-fight included.
            """
            if self.crash_check_thread and self.crash_check_thread.is_alive():
                return
            self.crash_check_stop_event.clear()
            self.crash_check_thread = threading.Thread(
                target=self.crash_watchdog_loop,
                daemon=True,
                name="vvok-crash-watchdog"
            )
            self.crash_check_thread.start()

        def stop_crash_watchdog(self):
            self.crash_check_stop_event.set()
            if self.crash_check_thread and self.crash_check_thread.is_alive():
                self.crash_check_thread.join(timeout=1.0)

        def crash_watchdog_loop(self):
            while not self.crash_check_stop_event.is_set():
                if self.should_stop():
                    return
                try:
                    self.check_and_handle_brawl_stars_crash()
                except Exception as exc:
                    # One bad pass must not end the watch. This thread died on
                    # an ADB error raised from inside the handler for an ADB
                    # error, and nothing noticed - the bot simply stopped
                    # having crash detection, silently, for the rest of the run.
                    print(f"Crash watchdog: {type(exc).__name__}: "
                          f"{str(exc)[:150]}. Carrying on.")
                self.crash_check_stop_event.wait(self.check_if_brawl_stars_crashed_timer)

        def check_and_handle_brawl_stars_crash(self):
            try:
                opened_app = self.window_controller.device.app_current().package.strip()
                if not self.window_controller.is_brawl_stars_running():
                    print(f"Brawl stars has crashed, {opened_app} is the app opened ! Restarting...")
                    self.window_controller.device.app_start(self.window_controller.BRAWL_STARS_PACKAGE)
                    time.sleep(3)
                    self.time_since_checked_if_brawl_stars_crashed = time.time()
                else:
                    self.time_since_checked_if_brawl_stars_crashed = time.time()
            except AdbError:
                print("There was an error checking if Brawl Stars is running. Attempting to reconnect scrcpy...")
                if not self.window_controller.reconnect_scrcpy():
                    print("Reconnect failed -- restarting Brawl Stars")
                    self.restart_brawl_stars()

        def main(self):
            s_time = time.time()
            c = 0
            self.time_since_last_webhook_ping = time.time()
            if self.runtime_control:
                self.runtime_control.mark_running()

            while True:
                if self.get_latest_state() == "lobby":
                    if self.should_stop():
                        self.stop_gracefully()
                        break

                    if self.should_pause():
                        self.handle_pause_request()
                        if self.should_stop():
                            self.stop_gracefully()
                            break
                        if self.should_pause():
                            continue

                if not self.picked_first_brawler and self.get_latest_state() == "lobby":
                    if self.Stage_manager.brawlers_pick_data[0]['automatically_pick']:
                        next_brawler_name = self.Stage_manager.brawlers_pick_data[0]['brawler']
                        print("Picking brawler automatically")
                        if self.runtime_control:
                            self.runtime_control.mark_running()
                        select_brawler = self.lobby_automator.select_brawler(next_brawler_name, self.get_latest_state, runtime_control=self.runtime_control)

                        # Rotating the queue and trying the next one only helps
                        # while there IS a next one. With a single brawler
                        # queued, the rotation hands back the same brawler and
                        # this loops until somebody closes the window - which is
                        # exactly what a one-brawler queue looks like in the
                        # wild. Give every entry one turn, then get on with it.
                        attempts_left = max(1, len(self.Stage_manager.brawlers_pick_data))
                        while select_brawler in ["failed", "error"] and attempts_left > 0:
                            print("Automatic brawler selection failed.")
                            if self.ping_when_stuck:
                                screenshot = self.window_controller.screenshot()
                                notify_user("bot_failed_brawler_selection", screenshot, self.Stage_manager)
                            attempts_left -= 1
                            if attempts_left <= 0:
                                print("No queued brawler could be selected. Playing "
                                      "with whichever brawler the game has selected.")
                                break
                            failed_brawler = self.Stage_manager.brawlers_pick_data.pop(0)
                            self.Stage_manager.brawlers_pick_data.append(failed_brawler)
                            next_brawler_name = self.Stage_manager.brawlers_pick_data[0]['brawler']
                            select_brawler = self.lobby_automator.select_brawler(next_brawler_name, self.get_latest_state, runtime_control=self.runtime_control)

                        if select_brawler == "aborted" or select_brawler == "stuck":
                            continue
                        self.picked_first_brawler = True
                        self.update_trophy_observer()
                    else:
                        self.picked_first_brawler = True
                t_now = time.time()
                if self.max_ips:
                    frame_start = time.perf_counter()

                if self.run_for_minutes > 0 and not self.in_cooldown:
                    elapsed_time = (t_now - self.start_time) / 60
                    if elapsed_time >= self.run_for_minutes:
                        cprint(f"timer is done, {self.run_for_minutes} is over. continuing for 3 minutes if in game", "#AAE5A4")
                        self.in_cooldown = True
                        self.cooldown_start_time = t_now
                        self.Stage_manager.states['lobby'] = lambda: 0

                if self.in_cooldown and t_now - self.cooldown_start_time >= self.cooldown_duration:
                    cprint("stopping bot fully", "#AAE5A4")
                    self.stop_gracefully()
                    break

                if abs(s_time - t_now) > 1:
                    elapsed = t_now - s_time
                    if elapsed > 0:
                        rate = c / elapsed
                        print(f"{rate:.2f} IPS")
                        # Same number the console has always shown, now also
                        # reaching the header trace.
                        if runtime_control:
                            runtime_control.note_ips(rate)
                        self.note_ips_for_stats(rate)
                    s_time = t_now
                    c = 0
                self.start_crash_watchdog()
                frame = self.window_controller.screenshot()
                # Before anything reads the frame: has the picture moved at
                # all? Deliberately not asked of the bot's own state, which
                # can be sure a match is running while the screen has been
                # frozen for ten minutes.
                self.activity_watchdog.note(frame)

                _, last_ft = self.window_controller.get_latest_frame()
                if last_ft > 0 and (t_now - last_ft) > self.window_controller.FRAME_STALE_TIMEOUT:
                    stale_age = t_now - last_ft
                    self.Play.window_controller.release_movement(priority=True)
                    if stale_age > 30:
                        print(f"Scrcpy feed stale for {stale_age:.0f}s -- attempting reconnect")
                        # A reconnect can succeed against a game that is itself
                        # wedged: a new connection to the same frozen picture.
                        # So the frames have to actually start moving again -
                        # reconnecting and carrying on regardless is how this
                        # ended up printing the same warning for minutes.
                        if not self.window_controller.reconnect_scrcpy():
                            print("Reconnect failed -- restarting Brawl Stars")
                            self.restart_brawl_stars()
                        elif not self.feed_resumed():
                            print("Reconnected but the feed is still stuck -- "
                                  "restarting Brawl Stars")
                            self.restart_brawl_stars()
                        self.activity_watchdog.reset("feed reconnected")
                    else:
                        print("Stale frame detected -- pausing actions until feed resumes")
                        if self.sleep_interruptible(1) == "stop":
                            self.stop_gracefully()
                            break
                    continue

                self.manage_time_tasks(frame)

                brawler = self.Stage_manager.brawlers_pick_data[0]['brawler']
                self.Play.current_brawler = brawler
                self.Play.main(frame, brawler, self)
                c += 1

                if self.max_ips:
                    target_period = 1 / self.max_ips
                    work_time = time.perf_counter() - frame_start
                    if work_time < target_period:
                        time.sleep(target_period - work_time)

    os.makedirs("debug_frames", exist_ok=True)
    main = Main.__new__(Main)
    try:
        main.__init__()
        main.main()
    finally:
        # Construction can fail after capture has started. Clean up every
        # resource independently, including on SystemExit from a finished queue.
        for name in ("state_checker_stop_event", "crash_check_stop_event"):
            event = getattr(main, name, None)
            if event is not None:
                event.set()
        play = getattr(main, "Play", None)
        dodge = getattr(play, "dodge_service", None)
        controller = getattr(main, "window_controller", None)
        actions = [lambda: dodge.stop()] if dodge is not None else []
        if controller is not None:
            actions += [lambda: controller.release_movement(priority=True), controller.close]
        for action in actions:
            try:
                action()
            except Exception as error:
                print(f"Session cleanup: {error}")
        remote.set_window_controller(None)


all_brawlers = get_brawler_list()
if api_base_url != "localhost":
    update_missing_brawlers_info(all_brawlers)
    check_version()
    update_wall_model_classes()
    if not current_wall_model_is_latest():
        print("New Wall detection model found, downloading... (this might take a few minutes depending on your internet)")
        get_latest_wall_model_file()


def find_open_port(start_port=5185, host="127.0.0.1"):
    for port in range(start_port, start_port + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sock.connect_ex((host, port)) != 0:
                return port
    raise RuntimeError("Could not find an open localhost port for the Flask UI.")


def report_stats(app):
    """Send the anonymous figures, if they are switched on.

    At startup rather than at shutdown: the question this exists to answer is
    how many people run the fork, and a bot that is killed with the window
    close button never reaches a shutdown hook.
    """
    try:
        from telemetry import send

        service = app.config.get("data_service")
        profile = {}
        if service is not None:
            profile = (service.get_match_history_payload() or {}).get("profile") or {}

        general = load_toml_as_dict("cfg/general_config.toml")
        send(profile=profile,
             provider=str(general.get("execution_provider", "auto")))
    except Exception:
        # Statistics are the least important thing here by a wide margin.
        pass


def start_auto_update(app):
    """Poll for updates while the bot runs, and resume farming after one.

    Nothing happens on a poll that finds no update - the point of an hourly
    check is that it stays invisible until there is a reason not to be. The
    bot is only stopped, and the process only restarted, once one has actually
    installed.
    """
    from auto_update import AutoUpdater, take_resume_marker

    manager = app.config.get("runtime_manager")
    discord_bot = app.config.get("discord_bot")
    if manager is None:
        return None

    if take_resume_marker():
        # The last shutdown was ours and the bot was working when it happened,
        # so put it back to work. There is no auto-start otherwise, and a
        # machine left farming overnight would come back up idle.
        def resume():
            time.sleep(5)  # let the panel and the queue finish loading
            try:
                result = manager.start_current_queue(discord_bot)
                print(f"Auto-update: resumed after the update "
                      f"({result.get('message', 'started')}).")
            except Exception as exc:
                print(f"Auto-update: could not resume after the update ({exc}).")

        threading.Thread(target=resume, daemon=True, name="vvok-resume").start()

    updater = AutoUpdater(manager, discord_bot)
    updater.start()
    return updater


def open_browser_later(local_url):
    def _open():
        time.sleep(1.5)
        webbrowser.open(local_url)

    threading.Thread(target=_open, daemon=True, name="vvok-browser-launcher").start()


if __name__ == "__main__":
    # The same log the desktop application writes. Which entry point was
    # used should not decide whether there is anything to look at afterwards.
    from logging_tee import start_logging
    start_logging()

    print("VvokAI - Brawl Stars bot with projectile dodging and aimed fire")
    print("Telegram: https://t.me/nyavke")
    # Multi-instance: the supervisor pins a fixed panel port per account so each
    # has a predictable URL, and suppresses the browser pop-up so launching ten
    # accounts does not open ten tabs. Both fall back to the single-instance
    # behaviour (a free port, browser opens) when unset.
    _forced_port = os.environ.get("VVOK_WEB_PORT")
    port = int(_forced_port) if _forced_port else find_open_port()
    app = create_app(vvok_main, start_discord_bot=True)
    local_url = f"http://127.0.0.1:{port}"
    print(f"VvokAI web UI: {local_url}")
    if not os.environ.get("VVOK_NO_BROWSER"):
        open_browser_later(local_url)
    start_auto_update(app)
    report_stats(app)
    # threaded is Flask's default, but it is spelled out because the panel now
    # holds long-lived connections: a live-view stream never returns while
    # somebody is watching, and on a single-threaded server that one request
    # would block every other one and freeze the whole panel.
    # Local only unless asked otherwise. The panel has no login, and it can
    # import a playstyle - a Python file the bot then executes - so reachable
    # from the whole network meant anyone on it could run code on this machine.
    # Set panel_allow_lan = true in cfg/general_config.toml to open it up (for
    # reaching the panel from a phone); Discord and Telegram already work
    # remotely without exposing it.
    allow_lan = config_bool(
        load_toml_as_dict("cfg/general_config.toml").get("panel_allow_lan"), False)
    app.run(host=("0.0.0.0" if allow_lan else "127.0.0.1"),
            port=port, debug=False, use_reloader=False, threaded=True)
