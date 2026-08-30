"""What the bot is looking at, in the panel, while it plays.

The debug window has always existed and has always been an OpenCV window on
the machine running the bot - no use at all to somebody checking an overnight
run from their phone, which is most of the point of having a panel.

This serves the same picture over HTTP as MJPEG. No websocket, no player, no
library: a multipart response browsers have understood for twenty years, and
an <img> tag pointed at it.

Four rules keep it off the bot's back, because a machine that can barely run
the models has nothing to spare for a video feed.

Nobody watching costs nothing. publish() returns on an integer comparison -
a tenth of a microsecond, measured - so a user who never opens the view pays
that once per frame and cannot notice it.

It never touches the bot's loop. Resizing and encoding happen on the web
server's thread. The loop hands over a reference and moves on; a slow browser
or a dead connection cannot hold up a match.

It gives itself a CPU budget and keeps to it. The stream measures its own
work and paces itself to stay under a fixed share of one core. On a fast
machine that is the full frame rate. On a slow one it quietly sends fewer
frames rather than taking cycles the bot needs - the picture gets choppier,
the bot does not get worse.

And all of it is in the config, so a user who wants it sharper, smoother or
gone entirely does not need a new build.
"""

import threading
import time

import cv2

from utils import config_bool, load_toml_as_dict

# Defaults, overridable per machine in cfg/bot_config.toml. Chosen so the
# stream costs about a fiftieth of one core on the machine this was written
# on: wide enough to follow a match, far below what the game itself renders.
DEFAULTS = {
    "live_view_enabled": True,
    "live_view_width": 720,
    "live_view_fps": 10,
    "live_view_quality": 70,
    # The share of one core the stream may use. Everything else is paced
    # around this number - it is the promise the feature makes to the bot.
    "live_view_cpu_share": 0.05,
    # Give up after this long with no frame. Without it, opening the view
    # while the bot is stopped leaves the browser holding a connection that
    # will never produce a picture and never fail either - so the user gets an
    # empty box with no way to tell early from broken.
    "live_view_idle_timeout": 6.0,
}

_settings = None
_settings_read = 0.0


def settings():
    """The live-view config, re-read now and then so edits apply without a restart."""
    global _settings, _settings_read
    now = time.time()
    if _settings is None or now - _settings_read > 30:
        values = dict(DEFAULTS)
        try:
            config = load_toml_as_dict("cfg/bot_config.toml")
            for key, fallback in DEFAULTS.items():
                if key not in config:
                    continue
                if isinstance(fallback, bool):
                    values[key] = config_bool(config[key], fallback)
                else:
                    values[key] = type(fallback)(config[key])
        except Exception:
            pass  # A missing or broken config means defaults, never a crash.
        _settings, _settings_read = values, now
    return _settings


# One frame and its overlay, published by the bot loop and read by whoever is
# watching. A lock rather than a queue: a viewer wants the newest frame, and
# every older one is worth nothing to them.
_lock = threading.Lock()
_latest = {"frame": None, "debug": None, "stamp": 0.0}

# How many browsers have this open. Zero means publish() does nothing at all.
_viewers = 0


def viewers():
    return _viewers


def publish(frame, debug_data=None):
    """Called by the bot loop with the frame it just worked on.

    Deliberately the cheapest thing in the file: two comparisons and, only if
    somebody is actually watching, a pair of reference assignments. Nothing is
    copied, resized or encoded here - that belongs on the viewer's thread.
    """
    if _viewers <= 0 or frame is None:
        return
    with _lock:
        _latest["frame"] = frame
        _latest["debug"] = debug_data
        _latest["stamp"] = time.time()


def _render(config=None):
    """The newest frame with its overlay, as JPEG bytes, or None."""
    config = config or settings()
    with _lock:
        frame = _latest["frame"]
        debug = _latest["debug"]
    if frame is None:
        return None

    height, width = frame.shape[:2]
    target = int(config["live_view_width"])
    if width > target:
        # INTER_LINEAR, not INTER_AREA. Area sampling is the better-looking
        # way to shrink a photograph, and here it costs five times as much
        # (2.55 ms against 0.50 ms on a 1080p frame, measured) - which on a
        # weak machine is the difference between a free feature and one the
        # user can feel. At ten frames a second of a moving game, nobody sees
        # the aliasing it would have saved.
        scale = target / float(width)
        frame = cv2.resize(frame, (target, int(height * scale)),
                           interpolation=cv2.INTER_LINEAR)
    else:
        frame = frame.copy()
        scale = 1.0

    # The bot works in RGB; JPEG wants BGR. Convert BEFORE drawing, not after,
    # because the debug window does it in that order and draw_debug_data picks
    # its colours to look right on a BGR image. Converting afterwards would
    # leave the game picture correct and swap red and blue in every box and
    # circle drawn on top of it.
    if frame.ndim == 3 and frame.shape[2] == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    if debug:
        try:
            from debug_view import draw_debug_data
            draw_debug_data(frame, _scaled(debug, scale),
                            frame.shape[1], frame.shape[0])
        except Exception:
            pass  # A picture without boxes beats no picture.

    ok, buffer = cv2.imencode(".jpg", frame,
                              [int(cv2.IMWRITE_JPEG_QUALITY),
                               int(config["live_view_quality"])])
    return buffer.tobytes() if ok else None


def _scaled(debug, factor):
    """The overlay's boxes and lines at the streamed size."""
    if factor == 1.0:
        return debug

    def scale_boxes(boxes):
        return [[value * factor for value in box[:4]] + list(box[4:])
                for box in boxes or [] if len(box) >= 4]

    out = dict(debug)
    for key in ("player", "enemy", "teammate", "wall"):
        out[key] = scale_boxes(debug.get(key))
    for key in ("attack_range", "super_range", "joystick_radius"):
        if debug.get(key):
            out[key] = int(debug[key] * factor)
    # Anything else is drawn from these, or is text, and both survive as they
    # are. Getting this wrong costs a misplaced circle, not a broken stream.
    return out


def frames():
    """A generator of multipart JPEG chunks, one per viewer connection.

    Paced by two limits, whichever is slower: the configured frame rate, and
    the CPU share the stream is allowed. The second is what makes this safe on
    a machine that is already struggling - if a frame takes 20 ms to encode
    instead of 1.5, the stream waits proportionally longer before the next
    one, so its cost stays flat and the bot keeps its cycles. Somebody on an
    old laptop gets a slower picture; they do not get a slower bot.
    """
    global _viewers
    config = settings()
    if not config["live_view_enabled"]:
        return

    _viewers += 1
    try:
        interval = 1.0 / max(1, int(config["live_view_fps"]))
        share = max(0.005, min(1.0, float(config["live_view_cpu_share"])))
        patience = float(config["live_view_idle_timeout"])
        opened = time.time()
        sent_stamp = 0.0
        while True:
            started = time.perf_counter()

            # Judge the frame by its age, not by whether one exists. The last
            # frame the bot published stays in memory after it stops, so a
            # check for "did we manage to produce a picture" is always yes -
            # and the stream would go on re-encoding one stale frame for ever,
            # burning CPU and showing something that looks live and is not.
            #
            # Before the first publish the newest stamp belongs to some earlier
            # session, so the clock runs from whichever is later: that stamp or
            # the moment this viewer connected. That gives a bot which is just
            # starting up its full patience, and gives a bot which has stopped
            # none at all.
            with _lock:
                stamp = _latest["stamp"]
            if time.time() - max(stamp, opened) > patience:
                # Ending the response is what tells the browser that nothing
                # is coming; the page turns that into a line saying the bot is
                # not running. Hanging silently would say nothing at all, and
                # an empty box for ever reads as a broken feature.
                return

            # Never encode the same frame twice. The stream's clock and the
            # bot's are unrelated, so without this a bot thinking at 4 frames a
            # second would still be charged for 10 encodes a second, all but
            # four of them producing a JPEG identical to the last one. It also
            # means a bot that has just stopped costs nothing at all while the
            # patience above runs out.
            payload = (_render(config)
                       if stamp > opened and stamp > sent_stamp else None)
            if payload:
                sent_stamp = stamp
            if payload:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(payload)).encode() +
                       b"\r\n\r\n" + payload + b"\r\n")
            spent = time.perf_counter() - started
            # Working for `spent` and then resting until `spent / share` has
            # passed is, by definition, using `share` of one core.
            time.sleep(max(0.0, max(interval, spent / share) - spent))
    finally:
        # Runs whether the browser closed the tab, navigated away or the
        # connection died - all of which look the same from here, and all of
        # which mean stop paying for it.
        _viewers -= 1
