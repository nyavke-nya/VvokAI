import atexit
import math
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import scrcpy
from adbutils import adb, AdbDevice
from debug_view import DebugViewPublisher
from utils import config_bool, load_toml_as_dict, save_dict_as_toml, invalidate_toml_cache

brawl_stars_width, brawl_stars_height = 1920, 1080

press_coords_dict = load_toml_as_dict("cfg/buttons_config.toml")
KNOWN_BS_PACKAGES = ("com.supercell.brawlstars", "bsd.suitcase.release")


def restart_adb_server() -> None:
    try:
        adb.server_kill()
    except Exception:
        pass
    time.sleep(0.5)
    try:
        adb.server_start()
    except Exception:
        pass
    time.sleep(0.5)


def online_devices():
    out = []
    for d in adb.device_list():
        try:
            state = d.get_state() if hasattr(d, "get_state") else d.state
        except Exception:
            state = "device"
        if state == "device":
            out.append(d)
    return out


def discover_device(verbose: bool = False) -> AdbDevice:
    preferred_port = load_toml_as_dict("cfg/general_config.toml")["emulator_port"]
    candidates = [5137, 5555, 16384, 7555, 5635, 62001, 62025, 62026, 7556, 7565, 16416] + list(range(5556, 5566)) + list(range(5565, 5756, 10))

    def _safe_connect(port: int):
        dev = adb.connect(f"127.0.0.1:{port}")
        return dev

    def _try(port):
        try:
            _safe_connect(port)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        executor.map(_try, candidates)

    devices = online_devices()
    if verbose:
        print(f"Online devices after scan: {[d.serial for d in devices]}")

    if not devices:
        raise ConnectionError("No ADB devices came online after scan.")

    if preferred_port:
        pref = next((d for d in devices if d.serial.endswith(f"{preferred_port}")), None)
        if pref:
            if verbose and len(devices) > 1:
                print(f"Multiple devices online; using configured port {preferred_port} ({pref.serial})")
            return pref

    if len(devices) == 1:
        return devices[0]

    chosen = devices[0]
    print(f"Multiple ADB devices online and no port configured. "
          f"Picking {chosen.serial} (first one). Others: "
          f"{[d.serial for d in devices if d is not chosen]}")
    return chosen

class WindowController:
    def __init__(self, max_ips="auto"):
        self.scale_factor = None
        self.width = None
        self.height = None
        self.width_ratio = None
        self.height_ratio = None
        self.joystick_x, self.joystick_y = None, None
        self.BRAWL_STARS_PACKAGE = load_toml_as_dict("cfg/general_config.toml")["brawl_stars_package"]
        self.verbose_debug = config_bool(
            load_toml_as_dict("cfg/debug_settings.toml").get("verbose_debug"),
            False
        )
        print("Connecting to ADB (might take up to 2 minutes)...")
        try:
            self.device = discover_device(verbose=self.verbose_debug)
            print(f"Connected to device: {self.device.serial}")

            self.frame_lock = threading.Lock()
            self.max_ips = max_ips
            self.scrcpy_client = self.build_scrcpy_client()
            self.last_frame = None
            self.last_frame_time = 0.0
            self.last_joystick_pos = (None, None)
            self.FRAME_STALE_TIMEOUT = 15.0
            self.re_apply_movement = config_bool(
                load_toml_as_dict("cfg/debug_settings.toml").get("re_apply_movement"),
                True
            )
            self.debug_view = DebugViewPublisher.from_config()

            def on_frame(frame):
                if frame is not None:
                    with self.frame_lock:
                        self.last_frame = frame
                        self.last_frame_time = time.time()

            self.scrcpy_client.add_listener(scrcpy.EVENT_FRAME, on_frame)
            self.scrcpy_client.start(threaded=True)
            atexit.register(self.close)
            print("Scrcpy client started successfully.")

        except Exception:
            raise Exception(f"Error during ADB/scrcpy initialization\nFailed to connect to the emulator/device.\nMake sure you have ADB enabled in your emulator settings. If you don't know how, check https://vimeo.com/1174882529?fl=pl&fe=s.\n if it still doesn't work, check https://discord.com/channels/1205263029269438574/1227618442073342002/1499331741838610433 to try fixing it.")
        self.are_we_moving = False
        self.PID_JOYSTICK = 1
        self.PID_ATTACK = 2
        # A finger of its own. Holding a button for seconds on PID_ATTACK
        # would mean the bot cannot shoot for as long as it is held, and on
        # PID_JOYSTICK it could not move; a real hand would use a third.
        self.PID_HOLD = 3
        self._hold_thread = None
        # The dodge tracker runs on its own thread and can grab the joystick
        # mid-iteration, so every joystick touch is serialised and the bot loop
        # is locked out for a short window after an emergency dodge.
        self.joystick_lock = threading.RLock()
        self.joystick_priority_until = 0.0

    def build_scrcpy_client(self):
        """Create the screen-capture client.

        The capture settings matter far more than they look. scrcpy makes the
        *device* grab and H.264-encode its screen in real time, and an emulator
        usually has no hardware encoder, so that work competes directly with
        rendering the game. Left uncapped on a 120 FPS emulator the encoder was
        pushed to 120 frames of 1080p per second and the game itself dropped to
        ~20 FPS - while the host sat at 45% CPU and 74% GPU, because the
        bottleneck was never on the host at all.

        Capture rate is deliberately separate from max_ips: the bot loop and
        the video stream have no reason to be tied together.
        """
        config = load_toml_as_dict("cfg/general_config.toml")

        def as_int(key, default):
            try:
                return int(config.get(key, default))
            except (TypeError, ValueError):
                return default

        capture_fps = as_int("capture_fps", 60)
        max_width = as_int("capture_max_width", 0)
        bitrate = as_int("capture_bitrate", 4000000)

        # An explicit max_ips still caps capture, since asking for more frames
        # than the bot can consume only burdens the encoder.
        if self.max_ips and self.max_ips != "auto":
            capture_fps = min(capture_fps, int(self.max_ips)) if capture_fps else int(self.max_ips)

        kwargs = {"device": self.device, "max_width": max_width, "bitrate": bitrate}
        if capture_fps > 0:
            kwargs["max_fps"] = capture_fps

        print(
            f"Screen capture: {capture_fps or 'uncapped'} FPS, "
            f"{'native width' if not max_width else str(max_width) + 'px wide'}, "
            f"{bitrate // 1000} kbps"
        )
        return scrcpy.Client(**kwargs)

    def get_latest_frame(self):
        with self.frame_lock:
            if self.last_frame is None:
                return None, 0.0
            return self.last_frame, self.last_frame_time

    def force_rediscover(self) -> bool:
        print("Restarting ADB server and re-discovering device.")
        try:
            self.scrcpy_client.stop()
        except Exception:
            pass
        restart_adb_server()
        try:
            new_dev = discover_device(self.verbose_debug)
        except ConnectionError:
            return False
        self.device = new_dev
        print(f"Re-discovered device: {self.device.serial}")
        return True

    def reconnect_scrcpy(self, max_retries=3):
        for attempt in range(1, max_retries + 1):
            print(f"Scrcpy reconnect attempt {attempt}/{max_retries}")
            try:
                self.scrcpy_client.stop()
            except Exception:
                pass
            time.sleep(1)

            with self.frame_lock:
                self.last_frame = None
                self.last_frame_time = 0.0

            self.are_we_moving = False
            self.last_joystick_pos = (None, None)

            try:
                _ = self.device.get_state()
            except Exception:
                if not self.force_rediscover():
                    print("Device gone and re-discovery failed.")
                    time.sleep(2 * attempt)
                    continue

            def on_frame(frame):
                if frame is not None:
                    with self.frame_lock:
                        self.last_frame = frame
                        self.last_frame_time = time.time()

            try:
                self.scrcpy_client = self.build_scrcpy_client()
                self.scrcpy_client.add_listener(scrcpy.EVENT_FRAME, on_frame)
                self.scrcpy_client.start(threaded=True)
            except Exception as e:
                print(f"Scrcpy client creation failed: {e}")
                time.sleep(2 * attempt)
                continue

            deadline = time.time() + 8
            while time.time() < deadline:
                _, ft = self.get_latest_frame()
                if ft > 0 and (time.time() - ft) < 2:
                    print(f"Scrcpy feed restored on attempt {attempt}")
                    return True
                time.sleep(0.5)

            print(f"Attempt {attempt} did not restore frame feed")
            time.sleep(2 * attempt)

        print("All scrcpy reconnect attempts exhausted")
        return False

    def close_brawl_stars(self):
        """Stop the game and leave it stopped.

        Everything needed was already here - restart_brawl_stars is app_stop
        followed by app_start. This is the same call without the second half,
        for the case where the work is finished and the point is that the
        account goes offline rather than sitting in a lobby all night.
        """
        try:
            self.device.app_stop(self.BRAWL_STARS_PACKAGE)
            print("Brawl Stars closed.")
            return True
        except Exception as error:
            # Never fatal: the bot has finished either way, and failing to
            # close the game is not a reason to fail the run that succeeded.
            print(f"Could not close Brawl Stars: {error}")
            return False

    def close_brawl_stars(self):
        """Stop the game and leave it stopped.

        Everything needed was already here: restart_brawl_stars is app_stop
        followed by app_start. This is the same call without the second half,
        for when the point is that the account goes offline rather than sitting
        in a lobby all night.
        """
        try:
            self.device.app_stop(self.BRAWL_STARS_PACKAGE)
            print("Brawl Stars closed.")
            return True
        except Exception as error:
            # Never fatal. Failing to close the game is not a reason to fail a
            # run that was otherwise finished or paused correctly.
            print(f"Could not close Brawl Stars: {error}")
            return False

    def open_brawl_stars(self, wait=8.0):
        """Start the game again and give it time to reach a usable screen."""
        try:
            self.device.app_start(self.BRAWL_STARS_PACKAGE)
        except Exception as error:
            print(f"Could not start Brawl Stars: {error}")
            return False
        # Loading takes a while on a cold start, and the state finder reading a
        # splash screen would otherwise decide it is lost and restart the game.
        time.sleep(max(wait, 0.0))
        print("Brawl Stars started.")
        return True

    def restart_brawl_stars(self):
        """Stop and start the game. Returns whether it worked.

        Never raises. This is the thing called WHEN something has already gone
        wrong - usually the device dropping off ADB - so the device is exactly
        as likely to be missing here as it was a moment ago. Throwing from a
        recovery path took the crash watchdog's thread down with it, and after
        that nothing was watching for crashes at all for the rest of the run.
        """
        try:
            self.device.app_stop(self.BRAWL_STARS_PACKAGE)
            time.sleep(1)
            self.device.app_start(self.BRAWL_STARS_PACKAGE)
            time.sleep(3)
            print("Brawl stars restarted successfully.")
            return True
        except Exception as exc:
            print(f"Could not restart Brawl Stars ({type(exc).__name__}: "
                  f"{str(exc)[:120]}). Trying to reconnect to the device first.")

        try:
            if not self.reconnect_scrcpy():
                print("The device is still unreachable; leaving the game alone.")
                return False
            self.device.app_stop(self.BRAWL_STARS_PACKAGE)
            time.sleep(1)
            self.device.app_start(self.BRAWL_STARS_PACKAGE)
            time.sleep(3)
            print("Brawl stars restarted after reconnecting.")
            return True
        except Exception as exc:
            print(f"Still could not restart Brawl Stars ({type(exc).__name__}). "
                  f"The emulator may need restarting by hand.")
            return False

    def is_brawl_stars_running(self):
        try:
            opened_app = self.device.app_current().package.strip()
            detected_known_package = False
            for package in KNOWN_BS_PACKAGES:
                if opened_app == package:
                    detected_known_package = True
                    break
            if detected_known_package:
                if opened_app != self.BRAWL_STARS_PACKAGE:
                    general_config = load_toml_as_dict("cfg/general_config.toml")
                    general_config["brawl_stars_package"] = opened_app
                    save_dict_as_toml(general_config, "cfg/general_config.toml")
                    self.BRAWL_STARS_PACKAGE = opened_app
                    invalidate_toml_cache("cfg/general_config.toml")
                    print(f"Detected Brawl Stars running under the '{opened_app}' package. Updating configuration to match.")
            return opened_app == self.BRAWL_STARS_PACKAGE.strip()
        except Exception as e:
            print(f"Error checking if Brawl Stars is running: {e}")
            return False

    def screenshot(self):
        frame, frame_time = self.get_latest_frame()

        deadline = time.time() + 15
        while frame is None:
            if time.time() > deadline:
                raise ConnectionError(
                    "No frame received from scrcpy within 15s. "
                    "Check USB/emulator connection."
                )
            print("Waiting for first frame...")
            time.sleep(0.1)
            frame, frame_time = self.get_latest_frame()

        age = time.time() - frame_time
        if frame_time > 0 and age > self.FRAME_STALE_TIMEOUT:
            print(f"WARNING: scrcpy frame is {age:.1f}s stale -- feed may be frozen")

        if not self.width or not self.height:
            self.width = frame.shape[1]
            self.height = frame.shape[0]
            if (self.width, self.height) != (brawl_stars_width, brawl_stars_height):
                print(f"WARNING: Unexpected resolution: {self.width}x{self.height}. Expected {brawl_stars_width}x{brawl_stars_height}. Please set your emulator resolution to 1920x1080 for best results.")
            self.width_ratio = self.width / brawl_stars_width
            self.height_ratio = self.height / brawl_stars_height
            self.joystick_x, self.joystick_y = 220 * self.width_ratio, 870 * self.height_ratio
            self.scale_factor = min(self.width_ratio, self.height_ratio)
        return frame

    def touch_down(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_DOWN, pointer_id)
        except Exception as e:
            print(f"Error during touch_down at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy() :
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_DOWN, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_down at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def touch_move(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_MOVE, pointer_id)
        except Exception as e:
            print(f"Error during touch_move at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy():
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_MOVE, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_move at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def touch_up(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_UP, pointer_id)
        except Exception as e:
            print(f"Error during touch_up at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy():
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_UP, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_up at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def move(self, x, y, priority=False):
        with self.joystick_lock:
            if not priority and time.time() < self.joystick_priority_until:
                return

            target_x = self.joystick_x + x
            target_y = self.joystick_y + y
            if not self.are_we_moving:
                self.touch_down(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
                self.touch_move(target_x, target_y, pointer_id=self.PID_JOYSTICK)
                self.are_we_moving = True
                self.last_joystick_pos = (target_x, target_y)
                return

            if not self.re_apply_movement and self.last_joystick_pos == (target_x, target_y):
                return

            self.touch_move(target_x, target_y, pointer_id=self.PID_JOYSTICK)
            self.last_joystick_pos = (target_x, target_y)

    def move_with_priority(self, x, y, hold=0.1):
        """Take the joystick for `hold` seconds, locking out the bot loop.

        Used by the dodge thread when an impact is closer than one bot
        iteration: by the time the main loop next ran, the shot would have
        landed.
        """
        with self.joystick_lock:
            self.joystick_priority_until = time.time() + max(hold, 0.0)
            self.move(x, y, priority=True)

    def release_movement(self, priority=False):
        with self.joystick_lock:
            if not priority and time.time() < self.joystick_priority_until:
                return
            if self.are_we_moving:
                self.touch_up(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
                self.are_we_moving = False
                self.last_joystick_pos = (None, None)

    def click(self, x: int, y: int, delay=0.02, already_include_ratio=True, touch_up=True, touch_down=True):
        if not already_include_ratio:
            x = x * self.width_ratio
            y = y * self.height_ratio
        if touch_down: self.touch_down(x, y, pointer_id=self.PID_ATTACK)
        time.sleep(delay)
        if touch_up: self.touch_up(x, y, pointer_id=self.PID_ATTACK)

    def press(self, key, delay=0.02, touch_up=True, touch_down=True):
        if key not in press_coords_dict:
            return
        x, y = press_coords_dict[key]
        target_x = x * self.width_ratio
        target_y = y * self.height_ratio
        self.click(target_x, target_y, delay, touch_up=touch_up, touch_down=touch_down)

    def hold(self, key, seconds, background=True):
        """Press and keep pressing a named button.

        On its own pointer, and on its own thread by default: five seconds is
        an eternity to the bot loop, which in that time would not dodge, would
        not move and would not fire. The game sees a finger held down while
        everything else carries on.

        Returns the thread, or None if there was nothing to press.
        """
        if key not in press_coords_dict:
            return None
        if self._hold_thread is not None and self._hold_thread.is_alive():
            return None  # one hold at a time; a second finger on the same spot

        x, y = press_coords_dict[key]
        target_x, target_y = x * self.width_ratio, y * self.height_ratio

        def press_and_wait():
            try:
                self.touch_down(target_x, target_y, pointer_id=self.PID_HOLD)
                time.sleep(max(0.0, seconds))
            finally:
                # Always lifts. A finger left down would be read as a stuck
                # touch and could block every later press on that pointer.
                self.touch_up(target_x, target_y, pointer_id=self.PID_HOLD)

        if not background:
            press_and_wait()
            return None

        self._hold_thread = threading.Thread(
            target=press_and_wait, daemon=True, name=f"vvok-hold-{key}")
        self._hold_thread.start()
        return self._hold_thread

    def aimed_attack(self, dx, dy, radius=130.0, hold=0.02, button="attack"):
        """Fire in a specific direction by dragging the attack stick.

        Tapping the attack control hands the shot to the game's auto-aim, which
        targets where the enemy currently is. Dragging it aims manually, which
        is the only way to lead a moving target.

        `dx, dy` is a direction in screen space; its length does not matter.
        """
        if button not in press_coords_dict:
            return False

        length = math.hypot(dx, dy)
        if length < 1e-6:
            return False

        x, y = press_coords_dict[button]
        center_x = x * self.width_ratio
        center_y = y * self.height_ratio
        # `radius` arrives already scaled to this screen by DodgeConfig.
        target_x = center_x + dx / length * radius
        target_y = center_y + dy / length * radius

        self.touch_down(center_x, center_y, pointer_id=self.PID_ATTACK)
        # One intermediate point: releasing straight after touch_down is
        # sometimes read as a tap, which would silently fall back to auto-aim.
        self.touch_move((center_x + target_x) / 2, (center_y + target_y) / 2,
                        pointer_id=self.PID_ATTACK)
        self.touch_move(target_x, target_y, pointer_id=self.PID_ATTACK)
        if hold > 0:
            time.sleep(hold)
        self.touch_up(target_x, target_y, pointer_id=self.PID_ATTACK)
        return True

    def swipe(self, start_x, start_y, end_x, end_y, duration=0.2):
        dist_x = end_x - start_x
        dist_y = end_y - start_y
        distance = math.sqrt(dist_x ** 2 + dist_y ** 2)

        if distance == 0:
            return

        step_len = 25
        steps = max(int(distance / step_len), 1)
        step_delay = duration / steps

        self.touch_down(int(start_x), int(start_y), pointer_id=self.PID_ATTACK)
        for i in range(1, steps + 1):
            t = i / steps
            cx = start_x + dist_x * t
            cy = start_y + dist_y * t
            time.sleep(step_delay)
            self.touch_move(int(cx), int(cy), pointer_id=self.PID_ATTACK)
        self.touch_up(int(end_x), int(end_y), pointer_id=self.PID_ATTACK)

    def type_text(self, text):
        """Type a string into whatever field the game has focused.

        scrcpy hands the string to the device's input method in one message, so
        this needs no on-screen keyboard, no per-character taps and no guessing
        where the letters are. That is the whole reason searching the brawler
        list beats scrolling it.

        Returns False if the text never made it, so the caller can fall back to
        something that does not depend on typing rather than carry on believing
        a search box was filled in.
        """
        if not text:
            return True
        try:
            self.scrcpy_client.control.text(str(text))
            return True
        except Exception as e:
            print(f"Error while typing '{text}': {e}")
            if self.reconnect_scrcpy():
                try:
                    self.scrcpy_client.control.text(str(text))
                    return True
                except Exception as e2:
                    print(f"Retry after reconnect failed while typing '{text}': {e2}")
        return False

    def send_key(self, keycode, times=1):
        """Press a hardware key, down and up, `times` in a row.

        Both halves are sent explicitly: a keycode with only ACTION_DOWN leaves
        the key held as far as Android is concerned, and the next one to arrive
        behaves as a repeat rather than a fresh press.
        """
        try:
            for _ in range(max(1, int(times))):
                self.scrcpy_client.control.keycode(keycode, scrcpy.ACTION_DOWN)
                self.scrcpy_client.control.keycode(keycode, scrcpy.ACTION_UP)
            return True
        except Exception as e:
            print(f"Error while sending keycode {keycode}: {e}")
            return False

    def clear_text_field(self, presses=24):
        """Backspace over anything already in the focused field.

        Twenty-four covers the longest brawler name several times over, and a
        backspace on an empty field does nothing - so this is cheap insurance
        against a search box that kept the last query and would otherwise be
        asked to find "jessiecolt".
        """
        return self.send_key(scrcpy.KEYCODE_DEL, times=presses)

    def submit_text(self):
        """Enter: commits the text and drops the keyboard.

        The keyboard matters more than the commit. It covers the bottom of the
        screen, which is where the button that confirms a brawler lives, so
        leaving it up means the next tap lands on a key instead of the game.
        Enter rather than Back because Back with no keyboard up would close the
        menu itself.
        """
        return self.send_key(scrcpy.KEYCODE_ENTER)

    def close(self):
        try:
            self.debug_view.close()
        except Exception as exc:
            print(f"Debug view close failed: {exc}")
        self.stop_scrcpy_with_timeout()

    def stop_scrcpy_with_timeout(self, timeout=2.0):
        def stop_client():
            try:
                self.scrcpy_client.stop()
            except Exception as exc:
                print(f"Scrcpy stop failed: {exc}")

        stop_thread = threading.Thread(target=stop_client, daemon=True, name="scrcpy-stop")
        stop_thread.start()
        stop_thread.join(timeout=timeout)
        if stop_thread.is_alive():
            print("Scrcpy stop is still running in the background; continuing shutdown.")
