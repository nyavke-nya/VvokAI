import atexit
import json
import math
import os
import struct
import subprocess
import sys
import time
from multiprocessing import shared_memory

import cv2
import numpy as np


DEFAULT_DEBUG_VIEW_FPS = 30
DEBUG_DATA_SIZE = 262144
DEBUG_DATA_HEADER_SIZE = 12


def _running_as_compiled_executable():
    return bool(getattr(sys, "frozen", False) or globals().get("__compiled__"))


def _get_screen_size(fallback_width, fallback_height):
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
            screen_width = int(user32.GetSystemMetrics(0))
            screen_height = int(user32.GetSystemMetrics(1))
            if screen_width > 0 and screen_height > 0:
                return screen_width, screen_height
        except Exception:
            pass

    if not _running_as_compiled_executable():
        try:
            import tkinter

            root = tkinter.Tk()
            root.withdraw()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.destroy()
            return screen_width, screen_height
        except Exception:
            pass

    return fallback_width, fallback_height


def _gui_support_hint():
    """Explain the one failure that looks like a bug but is a dependency clash.

    easyocr depends on opencv-python-headless. Both packages install into the
    same cv2/ directory, so whichever lands last wins outright - and when that
    is the headless build, every GUI entry point in OpenCV raises "the function
    is not implemented" and the debug window can never open, no matter what the
    settings say.
    """
    try:
        build = cv2.getBuildInformation()
    except Exception:
        return ""

    # Checked by reading the build info rather than by opening a probe window:
    # this runs in the bot's own process, and creating and destroying a window
    # here would flash on screen every time the worker is restarted.
    backends = ("WIN32UI", "GTK", "COCOA", "QT")
    section = build.split("GUI:", 1)[-1][:400].upper() if "GUI:" in build else ""
    if any(backend in section for backend in backends):
        return ""

    return (
        "OpenCV has no GUI support: opencv-python-headless (a dependency of "
        "easyocr) has overwritten opencv-python, and they share the same cv2 "
        "folder. Fix with:\n"
        "  venv\\Scripts\\python.exe -m pip uninstall -y opencv-python-headless opencv-python\n"
        '  venv\\Scripts\\python.exe -m pip install "opencv-python~=4.11"\n'
    )


class DebugViewPublisher:
    def __init__(
        self,
        enabled=False,
        max_fps=DEFAULT_DEBUG_VIEW_FPS,
        title="VvokAI Debug View",
        advanced_visuals=False,
        record_clips=False,
    ):
        self.enabled = enabled
        self.advanced_visuals = bool(advanced_visuals)
        self.record_clips = bool(record_clips)
        try:
            self.max_fps = max(float(max_fps or DEFAULT_DEBUG_VIEW_FPS), 1.0)
        except (TypeError, ValueError):
            self.max_fps = DEFAULT_DEBUG_VIEW_FPS
        self.title = title
        self.publish_delay = 1.0 / self.max_fps
        self.last_publish = 0.0
        self.shape = None
        self.dtype = None
        self.shared_memory = None
        self.debug_memory = None
        self.frame_array = None
        self.process = None
        self.process_started_at = 0.0
        self.worker_log_file = None
        self.frame_id = 0
        atexit.register(self.close)

    @classmethod
    def from_config(cls):
        from utils import config_bool, load_toml_as_dict

        config = load_toml_as_dict("cfg/debug_settings.toml")
        debug_view_enabled = config_bool(config.get("debug_view"), False)
        return cls(
            enabled=debug_view_enabled,
            max_fps=config.get("debug_view_fps", DEFAULT_DEBUG_VIEW_FPS),
            advanced_visuals=debug_view_enabled and config_bool(config.get("advanced_debug_visuals"), False),
            record_clips=debug_view_enabled and config_bool(config.get("record_debug_preview_clips"), False),
        )

    def publish(self, frame, debug_data=None):
        if not self.enabled or frame is None:
            return

        now = time.perf_counter()
        if now - self.last_publish < self.publish_delay:
            return

        try:
            self.start_viewer(frame)
            source = frame if frame.flags["C_CONTIGUOUS"] else np.ascontiguousarray(frame)
            np.copyto(self.frame_array, source)
            self.publish_debug_data(debug_data)
            self.last_publish = now
        except Exception as exc:
            print(f"Debug view disabled after error: {exc}")
            self.enabled = False
            self.close()

    def publish_debug_data(self, debug_data):
        if self.debug_memory is None:
            return

        self.frame_id += 1
        if debug_data is None:
            debug_data = {}
        debug_data["frame_id"] = self.frame_id
        data = json.dumps(debug_data, separators=(",", ":")).encode("utf-8")
        max_data_size = DEBUG_DATA_SIZE - DEBUG_DATA_HEADER_SIZE
        if len(data) > max_data_size:
            debug_data = dict(debug_data)
            debug_data["wall"] = []
            data = json.dumps(debug_data, separators=(",", ":")).encode("utf-8")
        if len(data) > max_data_size:
            return

        self.debug_memory.buf[DEBUG_DATA_HEADER_SIZE:DEBUG_DATA_HEADER_SIZE + len(data)] = data
        struct.pack_into("<QI", self.debug_memory.buf, 0, self.frame_id, len(data))

    def start_viewer(self, frame):
        shape = tuple(frame.shape)
        dtype = np.dtype(frame.dtype)
        if self.process and self.process.poll() is not None:
            exit_code = self.process.returncode
            ran_for = time.perf_counter() - self.process_started_at
            self.close()
            if ran_for < 2.0:
                raise RuntimeError(
                    f"Debug view worker exited immediately with code {exit_code}. "
                    f"{_gui_support_hint()}See debug_view_worker.log for details."
                )

        if self.frame_array is not None and self.shape == shape and self.dtype == dtype:
            return

        self.close()
        self.shape = shape
        self.dtype = dtype
        self.shared_memory = shared_memory.SharedMemory(create=True, size=frame.nbytes)
        self.debug_memory = shared_memory.SharedMemory(create=True, size=DEBUG_DATA_SIZE)
        self.frame_array = np.ndarray(shape, dtype=dtype, buffer=self.shared_memory.buf)
        self.frame_array.fill(0)
        self.debug_memory.buf[:] = b"\0" * DEBUG_DATA_SIZE

        height, width = shape[:2]
        channels = shape[2] if len(shape) > 2 else 1
        worker_args = [
            self.shared_memory.name,
            self.debug_memory.name,
            str(height),
            str(width),
            str(channels),
            dtype.str,
            self.title,
            str(self.max_fps),
            "1" if self.record_clips else "0",
        ]
        if _running_as_compiled_executable():
            command = [sys.executable, "--debug-viewer-worker", *worker_args]
        else:
            command = [sys.executable, "-u", os.path.abspath(__file__), "--viewer-worker", *worker_args]
        kwargs = {}
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        # Always log the worker, not just when compiled.
        #
        # Previously the worker's output was discarded when running from
        # source, and CREATE_NO_WINDOW means it has no console of its own
        # either - so when it died on startup there was nowhere for the reason
        # to go. A missing GUI build of OpenCV, which is a one-line fix, looked
        # from the outside exactly like "the debug window just does not work".
        self.worker_log_file = open("debug_view_worker.log", "a", encoding="utf-8")
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.worker_log_file.write(f"\n[{timestamp}] Starting debug view worker: {' '.join(command)}\n")
        self.worker_log_file.flush()
        kwargs["stdout"] = self.worker_log_file
        kwargs["stderr"] = subprocess.STDOUT
        self.process = subprocess.Popen(command, **kwargs)
        self.process_started_at = time.perf_counter()
        print(f"Debug view started at up to {self.max_fps:.0f} FPS.")

    def close(self):
        process = self.process
        self.process = None
        self.process_started_at = 0.0
        if process and process.poll() is None:
            try:
                process.terminate()
            except Exception:
                pass
        if self.worker_log_file is not None:
            try:
                self.worker_log_file.close()
            except Exception:
                pass
            self.worker_log_file = None

        self.frame_array = None
        self.shape = None
        self.dtype = None

        shared_memory_list = [self.shared_memory, self.debug_memory]
        self.shared_memory = None
        self.debug_memory = None
        for shared_memory_handle in shared_memory_list:
            if shared_memory_handle is None:
                continue
            try:
                shared_memory_handle.close()
            except Exception:
                pass
            try:
                shared_memory_handle.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass


def read_debug_data(debug_memory, last_frame_id):
    frame_id, data_size = struct.unpack_from("<QI", debug_memory.buf, 0)
    if frame_id == last_frame_id or data_size <= 0:
        return last_frame_id, None

    data_size = min(data_size, DEBUG_DATA_SIZE - DEBUG_DATA_HEADER_SIZE)
    data = bytes(debug_memory.buf[DEBUG_DATA_HEADER_SIZE:DEBUG_DATA_HEADER_SIZE + data_size])
    try:
        return frame_id, json.loads(data.decode("utf-8"))
    except Exception:
        return frame_id, None




def draw_boxes(image, boxes, color, thickness=3):
    for box in boxes or []:
        if len(box) < 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in box[:4]]
        cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)


def draw_lines(image, lines, color, thickness=6):
    for line in lines or []:
        if len(line) < 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in line[:4]]
        cv2.line(image, (x1, y1), (x2, y2), color, thickness, cv2.LINE_AA)


def draw_player_hit_circle(image, hit_circle):
    if not hit_circle or len(hit_circle) < 3:
        return

    try:
        x, y, radius = [int(v) for v in hit_circle[:3]]
    except (TypeError, ValueError):
        return

    if radius <= 0:
        return

    cv2.circle(image, (x, y), radius, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.circle(image, (x, y), radius, (0, 255, 120), 2, cv2.LINE_AA)


def draw_range_circles(image, player_boxes, attack_range, super_range):
    if not player_boxes:
        return

    player_box = player_boxes[0]
    if len(player_box) < 4:
        return

    try:
        attack_radius = int(float(attack_range or 0))
        super_radius = int(float(super_range or 0))
    except (TypeError, ValueError):
        return

    if attack_radius <= 0 and super_radius <= 0:
        return

    x1, y1, x2, y2 = [int(v) for v in player_box[:4]]
    center = ((x1 + x2) // 2, (y1 + y2) // 2)
    if attack_radius > 0 and super_radius > 0 and attack_radius == super_radius:
        cv2.circle(image, center, attack_radius, (0, 165, 255), 5)
        return
    if attack_radius > 0:
        cv2.circle(image, center, attack_radius, (0, 0, 170), 5)
    if super_radius > 0:
        cv2.circle(image, center, super_radius, (0, 255, 255), 5)


def draw_poison_gas_lines(image, player_boxes, poison_gas):
    if not player_boxes or not poison_gas:
        return

    player_box = player_boxes[0]
    if len(player_box) < 4:
        return

    x1, y1, x2, y2 = [int(v) for v in player_box[:4]]
    center_x = (x1 + x2) // 2
    center_y = (y1 + y2) // 2
    player_width = max(x2 - x1, 1)
    player_height = max(y2 - y1, 1)
    line_color = (0, 90, 0)
    line_thickness = 6

    if poison_gas.get("up"):
        cv2.line(image, (center_x, center_y), (center_x, max(0, y1 - player_height)), line_color, line_thickness)
    if poison_gas.get("down"):
        cv2.line(image, (center_x, center_y), (center_x, min(image.shape[0] - 1, y2 + player_height)), line_color, line_thickness)
    if poison_gas.get("left"):
        cv2.line(image, (center_x, center_y), (max(0, x1 - player_width), center_y), line_color, line_thickness)
    if poison_gas.get("right"):
        cv2.line(image, (center_x, center_y), (min(image.shape[1] - 1, x2 + player_width), center_y), line_color, line_thickness)


class DebugClipRecorder:
    def __init__(self, width, height, fps=30.0, missing_player_grace=1.0, min_player_seen_before_recording=3.0):
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.missing_player_grace = float(missing_player_grace)
        self.min_player_seen_before_recording = float(min_player_seen_before_recording)
        self.writer = None
        self.path = None
        self.frames_written = 0
        self.player_seen_since = None
        self.last_player_seen = None
        self.last_frame_written_at = None
        self.pending_frames = []

    def update(self, image, debug_data, frame_advanced):
        if not debug_data or not frame_advanced:
            return

        now = time.time()
        player_detected = bool(debug_data.get("player"))
        if player_detected:
            if self.player_seen_since is None:
                self.player_seen_since = now
            self.last_player_seen = now
            if self.writer is None and now - self.player_seen_since >= self.min_player_seen_before_recording:
                self.start(now)
                self.flush_pending_frames()
        else:
            self.player_seen_since = None

        if self.writer is None:
            if player_detected:
                self.pending_frames.append((now, image.copy()))
                self.prune_pending_frames(now)
            else:
                self.pending_frames.clear()
            return

        self.write_frame(image, now)

        if (
            not player_detected
            and self.last_player_seen is not None
            and now - self.last_player_seen > self.missing_player_grace
        ):
            self.stop()

    def write_frame(self, image, timestamp):
        if self.last_frame_written_at is None:
            frames_to_write = 1
        else:
            elapsed = max(timestamp - self.last_frame_written_at, 0)
            frames_to_write = max(1, int(round(elapsed * self.fps)))
            frames_to_write = min(frames_to_write, int(max(self.fps * 2, 1)))

        for _ in range(frames_to_write):
            self.writer.write(image)
            self.frames_written += 1
        self.last_frame_written_at = timestamp

    def prune_pending_frames(self, now):
        keep_seconds = self.min_player_seen_before_recording + self.missing_player_grace
        self.pending_frames = [
            (timestamp, frame)
            for timestamp, frame in self.pending_frames
            if now - timestamp <= keep_seconds
        ]

    def flush_pending_frames(self):
        for timestamp, frame in self.pending_frames:
            self.write_frame(frame, timestamp)
        self.pending_frames.clear()

    def start(self, now):
        clip_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_frames", "clips")
        os.makedirs(clip_dir, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        self.path = os.path.join(clip_dir, f"debug_clip_{timestamp}.mp4")
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        self.writer = cv2.VideoWriter(self.path, fourcc, self.fps, (self.width, self.height))
        self.frames_written = 0
        self.last_frame_written_at = None
        if not self.writer.isOpened():
            print(f"Debug clip recorder could not open {self.path}")
            self.writer.release()
            self.writer = None
            self.path = None

    def stop(self):
        if self.writer is None:
            return

        self.writer.release()
        saved_path = self.path
        frames_written = self.frames_written
        self.writer = None
        self.path = None
        self.frames_written = 0
        self.player_seen_since = None
        self.last_player_seen = None
        self.last_frame_written_at = None
        self.pending_frames.clear()
        if frames_written:
            print(f"Saved debug clip: {saved_path}")

    def close(self):
        self.stop()


def draw_candidates(image, candidates, pending):
    """Draw what the detector saw before it decided anything.

    Grey  = a raw blob that passed the size/shape filter. This is the noise
            floor: if the screen fills with grey while nothing is being fired,
            vision.motion_threshold is too low.
    Amber = a track with history that has not been confirmed. These are the
            near-misses; if they never turn into confirmed shots, the origin
            or straightness gates are too strict.
    """
    for blob in candidates or []:
        if not isinstance(blob, dict):
            continue
        try:
            x, y, radius = int(blob["x"]), int(blob["y"]), max(int(blob.get("r", 6)), 3)
        except (KeyError, TypeError, ValueError):
            continue
        cv2.circle(image, (x, y), radius, (150, 150, 150), 1, cv2.LINE_AA)
        cv2.drawMarker(image, (x, y), (150, 150, 150), cv2.MARKER_CROSS, 8, 1)

    for track in pending or []:
        if not isinstance(track, dict):
            continue
        try:
            x, y, radius = int(track["x"]), int(track["y"]), max(int(track.get("r", 8)), 4)
        except (KeyError, TypeError, ValueError):
            continue
        cv2.circle(image, (x, y), radius, (0, 190, 255), 2, cv2.LINE_AA)
        _draw_trail(image, track.get("trail"), (0, 190, 255), 1)
        hits = track.get("hits")
        if hits is not None:
            label = str(hits)
            age = track.get("age")
            if age:
                label = f"{hits}/{age:.2f}s"
            cv2.putText(image, label, (x + radius + 3, y - radius),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 190, 255), 1, cv2.LINE_AA)


def _draw_trail(image, trail, colour, thickness):
    """Connect the positions a track was actually measured at.

    Worth more than the predicted line when judging the tracker: a healthy
    track is a long unbroken polyline, while a track that is being torn apart
    and re-seeded every couple of frames shows up as a scatter of two-point
    stubs no matter how confident each fragment claims to be.
    """
    points = trail or []
    for index in range(1, len(points)):
        try:
            start = (int(points[index - 1][0]), int(points[index - 1][1]))
            end = (int(points[index][0]), int(points[index][1]))
        except (TypeError, ValueError, IndexError):
            continue
        cv2.line(image, start, end, colour, thickness, cv2.LINE_AA)


def draw_health(image, readings):
    """Outline every health bar the reader found and label it.

    This exists so a wrong reading is obvious instead of silent. The bar the
    reader measured is boxed where it thinks it is, and the percentage is
    printed next to it - if the box is around a bush, or the number disagrees
    with the game, it shows up immediately rather than as the bot inexplicably
    running away from a fight it was winning.
    """
    for reading in readings or []:
        if not isinstance(reading, dict):
            continue
        bar = reading.get("bar")
        if not bar or len(bar) < 4:
            continue
        x1, y1, x2, y2 = (int(v) for v in bar[:4])
        hostile = bool(reading.get("hostile"))
        colour = (60, 60, 255) if hostile else (80, 230, 120)

        cv2.rectangle(image, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), (0, 0, 0), 3)
        cv2.rectangle(image, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), colour, 1, cv2.LINE_AA)

        label = f"{reading.get('pct', 0)}%"
        confidence = reading.get("conf")
        if confidence is not None and confidence < 0.6:
            # Flag readings the playstyle is only half-trusting.
            label += f" ?{confidence:.2f}"
        cv2.putText(image, label, (x2 + 6, y2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(image, label, (x2 + 6, y2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, colour, 1, cv2.LINE_AA)


def draw_playstyle_error(image, message):
    """A banner across the top when the playstyle is throwing.

    This is the loudest thing the overlay draws, deliberately. When the script
    raises, movement comes back None and the bot neither walks nor shoots - and
    every other indicator still looks healthy, because detection, tracking and
    dodging all keep working. Without this the only evidence is a traceback in
    a console, and the visible symptom is a brawler standing perfectly still.
    """
    if not message:
        return

    height, width = image.shape[:2]
    banner = image[0:64, 0:width]
    cv2.rectangle(banner, (0, 0), (width, 64), (0, 0, 130), -1)
    cv2.addWeighted(banner, 0.75, image[0:64, 0:width], 0.25, 0, image[0:64, 0:width])
    cv2.line(image, (0, 64), (width, 64), (0, 0, 255), 2)

    cv2.putText(image, "PLAYSTYLE CRASHED - bot cannot move or attack", (20, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, "PLAYSTYLE CRASHED - bot cannot move or attack", (20, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(image, str(message)[:150], (20, 51),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, str(message)[:150], (20, 51),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (170, 200, 255), 1, cv2.LINE_AA)


def draw_tracker_stats(image, stats, motion):
    if not stats and not motion:
        return

    lines = []
    if stats:
        lines.append(
            f"blobs {stats.get('blobs', 0)}  rejected {stats.get('rejected', 0)}  "
            f"tracks {stats.get('tracks', 0)}  confirmed {stats.get('confirmed', 0)}  "
            f"{stats.get('ms', 0):.1f}ms"
        )
        # Track churn. Healthy looks like a handful born per frame and almost
        # nothing lost; a wall of amber circles with `born` in the dozens means
        # tracks are being re-seeded instead of followed.
        lines.append(
            f"born {stats.get('born', 0)}  merged {stats.get('merged', 0)}  "
            f"lost {stats.get('lost', 0)}"
        )
        reasons = "  ".join(
            f"{name} {count}"
            for name, count in (stats.get("rejects") or {}).items() if count
        )
        if reasons:
            lines.append("unconfirmed: " + reasons)
    if motion:
        boundary = motion.get("boundary") or [0, 0]
        edge = "none"
        if boundary[0] or boundary[1]:
            parts = []
            if boundary[0]:
                parts.append("right" if boundary[0] > 0 else "left")
            if boundary[1]:
                parts.append("bottom" if boundary[1] > 0 else "top")
            edge = "+".join(parts)
        lines.append(
            f"map edge {edge}  move eff {motion.get('efficiency', 1.0):.2f}"
            f"{'  STUCK' if motion.get('stuck') else ''}"
        )

    y = image.shape[0] - 18 * len(lines) - 12
    for line in lines:
        cv2.putText(image, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(image, line, (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        y += 18


def draw_aim(image, aim, player_boxes):
    if not aim or not player_boxes or len(player_boxes[0]) < 4:
        return

    point = aim.get("point")
    if not point or len(point) < 2:
        return

    x1, y1, x2, y2 = [int(v) for v in player_boxes[0][:4]]
    center = ((x1 + x2) // 2, (y1 + y2) // 2)
    target = (int(point[0]), int(point[1]))

    cv2.line(image, center, target, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.line(image, center, target, (80, 255, 80), 2, cv2.LINE_AA)
    cv2.drawMarker(image, target, (0, 0, 0), cv2.MARKER_TILTED_CROSS, 26, 5)
    cv2.drawMarker(image, target, (80, 255, 80), cv2.MARKER_TILTED_CROSS, 22, 2)
    label = f"lead {aim.get('lead', 0)}px / {aim.get('flight', 0):.2f}s"
    cv2.putText(image, label, (target[0] + 16, target[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, label, (target[0] + 16, target[1] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (80, 255, 80), 1, cv2.LINE_AA)


def draw_projectiles(image, projectiles):
    """Draw each tracked shot and the path it is predicted to take.

    This is the view to watch when tuning: real projectiles show up as a small
    circle with a straight line ahead of it, while noise appears as circles
    that flicker in and out without a consistent heading.
    """
    for projectile in projectiles or []:
        if not isinstance(projectile, dict):
            continue
        try:
            x, y = int(projectile["x"]), int(projectile["y"])
            end_x, end_y = int(projectile["ex"]), int(projectile["ey"])
            radius = max(int(projectile.get("r", 10)), 3)
        except (KeyError, TypeError, ValueError):
            continue

        confidence = float(projectile.get("c", 1.0))
        # Faint for a shaky track, solid magenta once the tracker is sure.
        colour = (int(120 + 135 * confidence), 40, int(200 + 55 * confidence))
        cv2.line(image, (x, y), (end_x, end_y), (0, 0, 0), 6, cv2.LINE_AA)
        cv2.line(image, (x, y), (end_x, end_y), colour, 3, cv2.LINE_AA)
        cv2.circle(image, (x, y), radius, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.circle(image, (x, y), radius, colour, 2, cv2.LINE_AA)


def draw_dodge(image, dodge, player_boxes):
    if not dodge or not player_boxes or len(player_boxes[0]) < 4:
        return

    vector = dodge.get("vector")
    if not vector or len(vector) < 2:
        return

    x1, y1, x2, y2 = [int(v) for v in player_boxes[0][:4]]
    center = ((x1 + x2) // 2, (y1 + y2) // 2)
    length = math.hypot(vector[0], vector[1])
    if length < 1e-3:
        return

    reach = 130
    tip = (
        int(center[0] + vector[0] / length * reach),
        int(center[1] + vector[1] / length * reach),
    )
    colour = (0, 80, 255) if dodge.get("urgency") == "critical" else (0, 220, 255)
    cv2.arrowedLine(image, center, tip, (0, 0, 0), 11, cv2.LINE_AA, tipLength=0.3)
    cv2.arrowedLine(image, center, tip, colour, 6, cv2.LINE_AA, tipLength=0.3)

    label = f"DODGE {dodge.get('urgency', '')} {dodge.get('tti', 0):.2f}s"
    cv2.putText(image, label, (x1, y1 - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(image, label, (x1, y1 - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA)


def draw_joystick_path_probe(image, joystick, directions, joystick_radius):
    if not joystick or len(joystick) < 2 or not directions:
        return

    try:
        center = (int(joystick[0]), int(joystick[1]))
        radius = int(float(joystick_radius or 0))
    except (TypeError, ValueError):
        return

    if radius <= 0:
        return

    overlay = image.copy()
    half_sector = 360.0 / max(len(directions), 1) / 2.0
    axes = (radius, radius)
    for direction in directions:
        if not isinstance(direction, dict):
            continue
        try:
            angle = float(direction.get("angle", 0.0))
        except (TypeError, ValueError):
            continue

        color = (0, 55, 210) if direction.get("blocked") else (40, 170, 45)
        cv2.ellipse(
            overlay,
            center,
            axes,
            0,
            angle - half_sector,
            angle + half_sector,
            color,
            -1,
            cv2.LINE_AA,
        )

    cv2.addWeighted(overlay, 0.58, image, 0.42, 0, image)
    cv2.circle(image, center, radius, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.circle(image, center, max(3, radius // 4), (0, 0, 0), 3, cv2.LINE_AA)


def draw_debug_data(image, debug_data, width, height):
    if not debug_data:
        return

    advanced_visuals = bool(debug_data.get("advanced_visuals"))
    player_boxes = debug_data.get("player")
    draw_range_circles(image, player_boxes, debug_data.get("attack_range"), debug_data.get("super_range"))
    draw_poison_gas_lines(image, player_boxes, debug_data.get("poison_gas"))
    draw_boxes(image, debug_data.get("wall"), (80, 80, 80), 3)
    draw_boxes(image, player_boxes, (0, 255, 0))
    if advanced_visuals:
        draw_player_hit_circle(image, debug_data.get("player_hit_circle"))
    draw_boxes(image, debug_data.get("enemy"), (0, 0, 255))
    draw_boxes(image, debug_data.get("teammate"), (255, 0, 0))
    draw_health(image, debug_data.get("health"))
    if advanced_visuals:
        draw_lines(image, debug_data.get("enemy_los_lines") or debug_data.get("clear_los_lines"), (0, 0, 120), 7)
        draw_lines(image, debug_data.get("teammate_los_lines"), (255, 180, 0), 7)

    # Candidates first so confirmed projectiles draw on top of the noise.
    draw_candidates(image, debug_data.get("candidates"), debug_data.get("pending"))
    for track in debug_data.get("trails") or []:
        if isinstance(track, dict):
            _draw_trail(image, track.get("trail"), (255, 120, 255), 2)
    draw_projectiles(image, debug_data.get("projectiles"))
    draw_aim(image, debug_data.get("aim"), player_boxes)
    draw_dodge(image, debug_data.get("dodge"), player_boxes)
    draw_tracker_stats(image, debug_data.get("tracker_stats"), debug_data.get("motion"))
    # Last, so it draws over everything else.
    draw_playstyle_error(image, debug_data.get("playstyle_error"))

    movement = debug_data.get("movement")
    joystick = debug_data.get("joystick")
    if advanced_visuals:
        draw_joystick_path_probe(image, joystick, debug_data.get("joystick_directions"), debug_data.get("joystick_radius"))
    if (
        movement
        and joystick
        and len(movement) >= 2
        and len(joystick) >= 2
        and joystick[0] is not None
        and joystick[1] is not None
    ):
        x = int(joystick[0] + movement[0])
        y = int(joystick[1] + movement[1])
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        cv2.circle(image, (x, y), 22, (0, 0, 0), -1, cv2.LINE_AA)
        cv2.circle(image, (x, y), 18, (0, 255, 255), -1)
        cv2.circle(image, (x, y), 22, (0, 0, 0), 4, cv2.LINE_AA)

    state = debug_data.get("state")
    if state:
        cv2.putText(image, str(state), (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(image, str(state), (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)


def run_viewer_worker(
    shared_memory_name,
    debug_memory_name,
    height,
    width,
    channels,
    dtype_text,
    title,
    clip_fps=DEFAULT_DEBUG_VIEW_FPS,
    record_clips=False,
):
    print(
        f"Debug viewer worker attaching to shared memory: frame={shared_memory_name} "
        f"debug={debug_memory_name} shape={width}x{height}x{channels} dtype={dtype_text}",
        flush=True,
    )
    shape = (height, width, channels) if channels > 1 else (height, width)
    shared_memory_handle = shared_memory.SharedMemory(name=shared_memory_name)
    debug_memory = shared_memory.SharedMemory(name=debug_memory_name)
    frame = np.ndarray(shape, dtype=np.dtype(dtype_text), buffer=shared_memory_handle.buf)
    fullscreen = False
    last_frame_id = 0
    debug_data = None
    clip_recorder = DebugClipRecorder(width, height, fps=clip_fps) if record_clips else None
    f11_keys = (0x7A0000, 0xFFC8, 65480)
    worker_started_at = time.perf_counter()
    frames_shown = 0

    cv2.namedWindow(title, cv2.WINDOW_NORMAL)
    print(f"Debug viewer window created: {title}", flush=True)
    screen_width, screen_height = _get_screen_size(width, height)

    available_width = max(screen_width - 80, 300)
    available_height = max(screen_height - 120, 300)
    window_scale = min(1.0, available_width / width, available_height / height)
    window_width = int(width * window_scale)
    window_height = int(height * window_scale)
    cv2.resizeWindow(title, window_width, window_height)

    try:
        while True:
            image = frame.copy()
            if channels == 3:
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            last_frame_id, new_debug_data = read_debug_data(debug_memory, last_frame_id)
            frame_advanced = new_debug_data is not None
            if new_debug_data is not None:
                debug_data = new_debug_data
            draw_debug_data(image, debug_data, width, height)
            if clip_recorder is not None:
                clip_recorder.update(image, debug_data, frame_advanced)
            cv2.imshow(title, image)
            frames_shown += 1

            key = cv2.waitKeyEx(1)
            if key in (27, ord("q"), ord("Q")):
                print(f"Debug viewer closed by key: {key}", flush=True)
                break
            if key in f11_keys or key in (ord("f"), ord("F")):
                fullscreen = not fullscreen
                if fullscreen:
                    cv2.setWindowProperty(title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                else:
                    cv2.setWindowProperty(title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(title, window_width, window_height)

            if frames_shown > 10 and time.perf_counter() - worker_started_at > 1.0:
                try:
                    visible = cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE)
                    if visible < 1:
                        print(f"Debug viewer window is no longer visible: {visible}", flush=True)
                        break
                except cv2.error:
                    print("Debug viewer window property check failed.", flush=True)
                    break
    finally:
        print("Debug viewer worker shutting down.", flush=True)
        try:
            cv2.destroyWindow(title)
        except cv2.error:
            pass
        if clip_recorder is not None:
            clip_recorder.close()
        shared_memory_handle.close()
        debug_memory.close()


if __name__ == "__main__":
    if len(sys.argv) >= 9 and sys.argv[1] == "--viewer-worker":
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
