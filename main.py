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
from utils import load_toml_as_dict, current_wall_model_is_latest, api_base_url, load_pyla_script, save_brawler_data, \
    clean_queue, get_discord_link
from utils import get_brawler_list, update_missing_brawlers_info, check_version, notify_user, update_wall_model_classes, get_latest_wall_model_file, cprint
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


def pyla_main(remote, queue_data, stop_event=None, runtime_control=None):
    class Main:
        def __init__(self):
            current_playstyle = load_toml_as_dict("cfg/bot_config.toml").get("current_playstyle", "unified_dodge.pyla")
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
            self.playstyle_info, pyla_code = load_pyla_script(current_playstyle)
            self.Play = Play(*self.load_models(), self.window_controller, pyla_code,
                             playstyle_info=self.playstyle_info)
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

        def handle_idle_disconnect(self):
            """The game says it dropped us for idling or a bad connection.

            Restart it, at once. Pressing RELOAD returns to a battle that has
            already ended, so the old behaviour left the bot staring at a
            finished match; a restart puts it back in the lobby where the
            normal flow can pick up.

            The guard is not a delay before acting - the first sighting acts
            immediately. It stops a repeat: this is a pixel count over a grey
            box, and a false positive that restarted the game every three
            seconds would be far worse than the thing it is fixing.
            """
            now = time.time()
            if now - self.time_since_idle_restart < self.idle_restart_cooldown:
                return
            self.time_since_idle_restart = now
            print("Idle disconnect on screen - restarting Brawl Stars.")
            self.restart_brawl_stars()

        def restart_brawl_stars(self):
            self.window_controller.restart_brawl_stars()
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
                name="pyla-state-checker"
            )
            self.state_checker_thread.start()

        def stop_state_checker(self):
            self.state_checker_stop_event.set()
            if self.state_checker_thread and self.state_checker_thread.is_alive():
                self.state_checker_thread.join(timeout=1.0)

        def set_latest_state(self, state):
            with self.state_lock:
                self.state = state

        def get_latest_state(self):
            with self.state_lock:
                return self.state

        def handle_detected_state(self, state):
            if state is None:
                return
            self.set_latest_state(state)

            print(f"State: {state}")
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
            cprint("Pyla is paused in the lobby. Waiting for Start to resume.", "#AAE5A4")

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
            if self.Time_management.idle_check() and self.lobby_automator.check_for_idle(frame):
                self.handle_idle_disconnect()
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
                name="pyla-crash-watchdog"
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
                self.check_and_handle_brawl_stars_crash()
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
                    s_time = t_now
                    c = 0
                self.start_crash_watchdog()
                frame = self.window_controller.screenshot()

                _, last_ft = self.window_controller.get_latest_frame()
                if last_ft > 0 and (t_now - last_ft) > self.window_controller.FRAME_STALE_TIMEOUT:
                    stale_age = t_now - last_ft
                    self.Play.window_controller.release_movement(priority=True)
                    if stale_age > 30:
                        print(f"Scrcpy feed stale for {stale_age:.0f}s -- attempting reconnect")
                        if not self.window_controller.reconnect_scrcpy():
                            print("Reconnect failed -- restarting Brawl Stars")
                            self.restart_brawl_stars()
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
    main = Main()
    main.main()


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


def open_browser_later(local_url):
    def _open():
        time.sleep(1.5)
        webbrowser.open(local_url)

    threading.Thread(target=_open, daemon=True, name="pyla-browser-launcher").start()


if __name__ == "__main__":
    print("VvokAI - Brawl Stars bot with projectile dodging and aimed fire")
    print("Telegram: https://t.me/nyavke")
    print("Fork of PylaAI (ivanyordanovgt, AngelFireLA, awarzu), CC BY-NC 4.0")
    port = find_open_port()
    app = create_app(pyla_main, start_discord_bot=True)
    local_url = f"http://127.0.0.1:{port}"
    print(f"VvokAI web UI: {local_url}")
    open_browser_later(local_url)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
