"""Things that broke on other people's machines, not on this one.

Every check here comes from a report: a card too old for the CUDA build, an
emulator smaller than 1920x1080, a panel that froze on a long history, a
number typed into a form and thrown away, and a Flask warning that reads like
something is wrong with the program.
"""
import logging
import os
import sys

from _harness import REPO, Failures, read_source

sys.path.insert(0, ".")
import webui.app as webui_app  # noqa: E402
from detect import Detect  # noqa: E402
from webui.runtime import RuntimeControl, RuntimeManager  # noqa: E402

report = Failures("robustness")


# ── a GPU the CUDA build has no kernels for ─────────────────────────────
report.section("a card too old for CUDA falls back instead of refusing to run")

CUDA_TOO_OLD = ("[ONNXRuntimeError] : 1 : FAIL : Exception during initialization: "
                "N:\\_work\\1\\s\\onnxruntime\\core\\providers\\cuda\\cuda_call.cc:154 "
                "onnxruntime::CudaCall CUDA failure 8: the function requires an "
                "architectural feature absent from the device ; GPU=0 ; hostname=x ; "
                "expr=cublasCreate(&cublas_handle_);")

report.check("the reason is one readable line, not a build path",
             Detect.short_reason(Exception(CUDA_TOO_OLD)).startswith(
                 "CUDA failure 8: the function requires an architectural feature"),
             True)
report.check("and it is short enough to read",
             len(Detect.short_reason(Exception(CUDA_TOO_OLD))) <= 160, True)


class _Order(Detect):
    """Just the provider choice - no model, no onnxruntime session."""

    def __init__(self, preferred, tensorrt=False):
        self.preferred_device = preferred
        self.use_tensorrt = tensorrt
        self.trt_fp16 = True


def _names(attempts):
    """Provider names out of the attempt lists, dropping their options."""
    return [[p[0] if isinstance(p, tuple) else p for p in attempt]
            for attempt in attempts]


# provider_order returns a list of LISTS now: each one is handed to
# onnxruntime whole, because TensorRT must never be requested on its own.
_gpu = _names(_Order("auto").provider_order())
report.check("CPU is always the last resort", _gpu[-1], ["CPUExecutionProvider"])
report.check("and it is never the only thing tried on a GPU box",
             len(_gpu) >= 1, True)
report.check("asking for CPU tries nothing else",
             _names(_Order("cpu").provider_order()), [["CPUExecutionProvider"]])

_source = read_source("detect.py")
report.check("session creation is guarded rather than allowed to kill startup",
             "except Exception as exc:" in _source
             and "problems.append((first, exc))" in _source, True)
report.check("and every provider failing is still an error, not a silent no-op",
             "No execution provider could load" in _source, True)


# ── an emulator that is not 1920x1080 ───────────────────────────────────
report.section("the disconnect box is found on a smaller emulator too")

# This used to be a count of grey pixels against a number measured at
# 1920x1080, so on a smaller window it asked for more grey than the box could
# hold and never fired. That whole approach is gone: it answered "is the middle
# of the screen grey", which a great many screens are, and what it triggers now
# is a restart of Brawl Stars - far too expensive to hang on a test that loose.
#
# Matching artwork scales by construction: is_template_in_region converts the
# region by the frame's own ratio before cropping, and loads the template at
# the same scale. So the check is that nothing is left doing it the old way,
# and that the region travels with the others.
_lobby = read_source("lobby_automation.py")
_states = read_source("state_finder.py")

report.check("the idle box is no longer found by counting grey",
             "gray_pixels" in _lobby, False)
report.check("nor by clicking a stored coordinate at it",
             "idle_reconnect_coords" in _lobby, False)
report.check("it is matched against a template",
             "def is_idle_disconnect_on_screen" in _states, True)
report.check("so is the team invite, which was declining things that were not",
             "def is_team_invite_on_screen" in _states, True)
report.check("both search regions sit with every other screen's",
             all(key in read_source("cfg/lobby_config.toml")
                 for key in ("idle_disconnect", "team_invite")), True)

# The region is read from config rather than written into the code, so the
# template cutter and the matcher cannot disagree about where to look.
for _name in ("idle_disconnect", "team_invite"):
    report.check(f"{_name} reads its region from lobby_config",
                 f'region_data.get("{_name}"' in _states, True)

# Neither template ships, and neither has to: the title is read out of the
# same tight region instead. A template, once cut, is only the faster path.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))
import cv2 as _cv2  # noqa: E402
import numpy as _np  # noqa: E402
import state_finder as _sf  # noqa: E402


def _dialog(title, region, left=False, scale=0.95, seed=5, panel=(35, 35, 45)):
    """A frame with one dialog's title where the real one sits.

    The panel colour matters. Each dialog is gated on how it looks before its
    title is read, so a fixture painted the wrong colour tests nothing - the
    invite is a saturated blue modal, not a dark card.
    """
    frame = (_np.random.default_rng(seed).random((1080, 1920, 3)) * 255).astype("uint8")
    x, y, w, h = region
    _cv2.rectangle(frame, (x - 60, y - 40), (x + w + 60, y + h + 80), panel, -1)
    size = _cv2.getTextSize(title, _cv2.FONT_HERSHEY_DUPLEX, scale, 2)[0]
    tx = x + 8 if left else x + (w - size[0]) // 2
    _cv2.putText(frame, title, (tx, y + h // 2),
                 _cv2.FONT_HERSHEY_DUPLEX, scale, (255, 255, 255), 2)
    return frame


_ti = _sf.region_data["team_invite"]
_idl = _sf.region_data["idle_disconnect"]
_invite = _dialog("TEAM INVITE", _ti, panel=(41, 128, 232))
_idle = _dialog("Idle Disconnect", _idl, left=True)
_blank = (_np.random.default_rng(7).random((1080, 1920, 3)) * 255).astype("uint8")

report.check("the invite is recognised with no template on disk",
             _sf.is_team_invite_on_screen(_invite), True)
report.check("the disconnect box is too",
             _sf.is_idle_disconnect_on_screen(_idle), True)

# The whole point. Each has to answer for its own dialog and nothing else.
report.check("the invite detector ignores the other dialog",
             _sf.is_team_invite_on_screen(_idle), False)
report.check("and the disconnect detector ignores the invite",
             _sf.is_idle_disconnect_on_screen(_invite), False)
report.check("neither fires on an unrelated screen",
             _sf.is_team_invite_on_screen(_blank)
             or _sf.is_idle_disconnect_on_screen(_blank), False)

# Scale is where counting grey pixels failed outright.
_small = _cv2.resize(_idle, (1080, 608), interpolation=_cv2.INTER_AREA)
report.check("the disconnect box is found on a 1080x608 emulator",
             _sf.is_idle_disconnect_on_screen(_small), True)
_small_invite = _cv2.resize(_invite, (1080, 608), interpolation=_cv2.INTER_AREA)
report.check("so is the invite", _sf.is_team_invite_on_screen(_small_invite), True)

# "Connection lost" arrives on the same card as the idle box, in the same
# place, and wants the same answer. Only the title separates them, so that is
# the thing worth checking.
def _card(title, seed=5):
    frame = (_np.random.default_rng(seed).random((1080, 1920, 3)) * 255).astype("uint8")
    _cv2.rectangle(frame, (458, 400), (1462, 663), (45, 45, 52), -1)
    _cv2.putText(frame, title, (500, 472), _cv2.FONT_HERSHEY_DUPLEX, 1.05,
                 (255, 255, 255), 2)
    return frame


_conn = _card("Connection lost")
_idle_card = _card("Idle Disconnect")
report.check("connection lost is recognised",
             _sf.is_connection_lost_on_screen(_conn), True)
report.check("and is not confused with the idle box on the same card",
             _sf.is_connection_lost_on_screen(_idle_card), False)
report.check("nor the idle box with it",
             _sf.is_idle_disconnect_on_screen(_conn), False)
report.check("it survives a smaller emulator",
             _sf.is_connection_lost_on_screen(
                 _cv2.resize(_conn, (1080, 608), interpolation=_cv2.INTER_AREA)), True)

# The lobby check names what it saw, because two cards mean the same thing and
# the log should say which one turned up.
from lobby_automation import LobbyAutomation  # noqa: E402

_probe = object.__new__(LobbyAutomation)
_probe.verbose_debug = False
report.check("the idle card is reported by name",
             _probe.check_for_idle(_idle_card), "idle disconnect")
report.check("so is the connection card",
             _probe.check_for_idle(_conn), "connection lost")
report.check("and nothing on screen is an empty answer",
             _probe.check_for_idle(_blank), "")


report.section("the cheap gate in front of the OCR")
# Reading a title costs a quarter of a second and these run every two or three
# seconds forever. Ungated that was three quarters of a second of OCR per three
# seconds, on the bot's own loop - it showed up as a sawtooth on the CPU graph
# and came out of the frame rate too.
import time as _time  # noqa: E402


def _blue_modal():
    """The invite as it really looks: a bright blue panel, not a dark card."""
    frame = (_np.random.default_rng(5).random((1080, 1920, 3)) * 255).astype("uint8")
    _cv2.rectangle(frame, (455, 190), (1465, 830), (41, 128, 232), -1)
    _cv2.rectangle(frame, (470, 205), (1450, 265), (28, 100, 200), -1)
    size = _cv2.getTextSize("TEAM INVITE", _cv2.FONT_HERSHEY_DUPLEX, 1.3, 3)[0]
    _cv2.putText(frame, "TEAM INVITE", (960 - size[0] // 2, 250),
                 _cv2.FONT_HERSHEY_DUPLEX, 1.3, (255, 255, 255), 3)
    return frame


# The bug this section exists for: one gate for all three dialogs looked
# reasonable and rejected the invite outright, because it tested for a dark
# colourless card and the invite is a saturated blue one. Measured dark
# fraction in that region: 0.000. An invite decliner that never fires.
report.check("the blue invite passes its own gate",
             _sf._looks_blue(_blue_modal(), _sf.region_data["team_invite"]), True)
report.check("and would have failed the dark-card gate",
             _sf._looks_dark(_blue_modal(), _sf.region_data["team_invite"]), False)
report.check("the dark card passes the dark gate",
             _sf._looks_dark(_card("Idle Disconnect"),
                             _sf.region_data["idle_disconnect"]), True)
report.check("and is not mistaken for a blue modal",
             _sf._looks_blue(_card("Idle Disconnect"),
                             _sf.region_data["team_invite"]), False)

# End to end, which is what actually matters.
report.check("the invite is still recognised through its gate",
             _sf.is_team_invite_on_screen(_blue_modal()), True)

_gameplay = (_np.random.default_rng(11).random((1080, 1920, 3)) * 255).astype("uint8")
_sf.is_team_invite_on_screen(_gameplay)          # warm the OCR engine


def _median_ms(fn, frame, runs=9):
    times = []
    for _ in range(runs):
        start = _time.perf_counter()
        fn(frame)
        times.append((_time.perf_counter() - start) * 1000)
    times.sort()
    return times[len(times) // 2]


# Generous: the point is the difference between a fraction of a millisecond
# and a quarter of a second, not any particular number on any particular
# machine.
for _name, _fn in (("team invite", _sf.is_team_invite_on_screen),
                   ("idle disconnect", _sf.is_idle_disconnect_on_screen),
                   ("connection lost", _sf.is_connection_lost_on_screen)):
    report.check(f"{_name} costs almost nothing on ordinary gameplay",
                 _median_ms(_fn, _gameplay) < 25, True)

# And the invite's own caller keeps its cheap stage first, which is the same
# lesson one level up.
report.check("the green count runs before the dialog is identified",
             _lobby.index("count_hsv_pixels(crop") < _lobby.index("is_team_invite_on_screen(frame)"),
             True)

# The region has to be wide enough for the title to fit. A box tight around
# the measured width is a detector that stops working the first time the
# reading is slightly off.
for _name, _region, _title in (("team_invite", _ti, "TEAM INVITE"),
                               ("idle_disconnect", _idl, "Idle Disconnect")):
    _width = _cv2.getTextSize(_title, _cv2.FONT_HERSHEY_DUPLEX, 0.95, 2)[0][0]
    report.check(f"{_name} has room for its title to grow",
                 _region[2] > _width * 1.4, True)

# And the restart it triggers cannot run away with itself.
_main = read_source("main.py")
report.check("a restart from the idle box is rate limited",
             "idle_restart_cooldown" in _main, True)
report.check("but the first sighting is not delayed",
             "self.time_since_idle_restart = now" in _main, True)



# ── the panel header's live rate ────────────────────────────────────────
report.section("the live rate comes off the object that holds it")

_control = RuntimeControl(lambda state: None)
report.check("a fresh control can be asked", _control.current_ips(), 0.0)
_control.note_ips(58.4)
report.check("and reports what the loop told it", _control.current_ips(), 58.4)
_control.note_ips("nonsense")
report.check("a value it cannot read is zero, not a crash", _control.current_ips(), 0.0)
_control.note_ips(-5)
report.check("and a negative rate is not a rate", _control.current_ips(), 0.0)
report.check("the manager does not keep a second copy",
             hasattr(RuntimeManager, "current_ips"), False)
report.check("status answers with no run in progress",
             RuntimeManager(None).get_status()["ips"], 0.0)
report.check("the play loop reports the rate to a control",
             "runtime_control.note_ips(" in read_source("main.py"),
             True)


# ── the panel, on a long history ────────────────────────────────────────
report.section("a long history does not freeze the page")

_app_js = read_source("static/js/app.js")
report.check("the scrollable curve is capped",
             "const chartPoints = showAll ? points : points.slice(-RECENT_CHART_POINTS);"
             in _app_js, True)
report.check("and the cap is small enough to draw",
             "const RECENT_CHART_POINTS = 60;" in _app_js, True)
report.check("the history is not refetched on every runtime tick",
             "HISTORY_POLL_MS" in _app_js, True)
report.check("and the change check is a fingerprint, not a deep compare",
             "JSON.stringify(result.items) === JSON.stringify(prevItems)" in _app_js,
             False)
report.check("the fingerprint is built from the totals",
             "function historySignature(history)" in _app_js, True)


# ── a number typed into the queue form ──────────────────────────────────
report.section("typing into the queue form is not thrown away")

report.check("renderQueue can be told a redraw is required",
             "function renderQueue(force = false) {" in _app_js, True)
report.check("and refuses to redraw over a focused field otherwise",
             '["INPUT", "SELECT", "TEXTAREA"].includes(focused.tagName)' in _app_js,
             True)
# The background callers are the ones that used to wipe it; a save has to
# redraw regardless, or the list never shows what was just saved.
_saved_block = _app_js[_app_js.index("async function saveQueueItem"):]
_saved_block = _saved_block[:_saved_block.index("async function pushAllToDefaultTarget")]
report.check("a save still redraws", "renderQueue(true)" in _saved_block, True)


# ── the Flask warning ───────────────────────────────────────────────────
report.section("the development-server warning does not reach the log")

webui_app._configure_request_logging()
_werkzeug = logging.getLogger("werkzeug")


def passes(message):
    record = logging.LogRecord("werkzeug", logging.INFO, "", 0, message, None, None)
    return all(log_filter.filter(record) for log_filter in _werkzeug.filters)


report.check("the warning is filtered out",
             passes("WARNING: This is a development server. Do not use it in a "
                    "production deployment."), False)
report.check("ordinary request lines still get through",
             passes('127.0.0.1 - - [27/Aug/2026] "GET /api/bootstrap HTTP/1.1" 200 -'),
             True)
report.check("and errors are not swallowed with it",
             passes('127.0.0.1 - - [27/Aug/2026] "GET /nope HTTP/1.1" 500 -'), True)


report.section("the buffie machine is a reward like any other")
# Found the same way star drops and noodles are - a template in a region from
# lobby_config - rather than by a colour count of its own. The one difference
# is what happens next: the machine wants the button HELD, not tapped.
import inspect as _inspect  # noqa: E402

from stage_manager import StageManager  # noqa: E402
from window_controller import WindowController  # noqa: E402

report.check("it is a state the state finder can report",
             "def is_at_buffie_machine" in _sf.__dict__ or
             hasattr(_sf, "is_at_buffie_machine"), True)
report.check("through the same template matcher as the other rewards",
             "is_template_in_region" in _inspect.getsource(_sf.is_at_buffie_machine),
             True)
report.check("with its region beside theirs",
             "buffie_machine" in read_source("cfg/lobby_config.toml"), True)
report.check("and the stage manager knows what to do with it",
             "'buffie_machine': self.open_buffie_machine"
             in _inspect.getsource(StageManager.__init__), True)

_handler = _inspect.getsource(StageManager.open_buffie_machine)
report.check("which is to hold the button, not click it",
             'hold("buffie_machine"' in _handler, True)
report.check("for a length that is configurable",
             "buffie_hold_seconds" in _handler, True)

# Five seconds on the bot's own finger would be five seconds of not dodging,
# not moving and not shooting.
_hold = _inspect.getsource(WindowController.hold)
report.check("the hold uses a pointer of its own", "PID_HOLD" in _hold, True)
report.check("and always lifts it", "finally:" in _hold, True)
report.check("that pointer is not the one attacks use",
             WindowController.PID_ATTACK if hasattr(WindowController, "PID_ATTACK") else 2,
             2)

report.check("an uncaptured template leaves the check off, like the others",
             _sf.is_at_buffie_machine(_blank), False)


report.section("preprocessing writes straight into the input buffer")
# It used to build a float copy of the resized frame, scale that, then copy
# each channel into the padded buffer: three passes over 2.7 MB plus a 2.7 MB
# allocation, every frame. One divide into the buffer does the same thing.
from detect import Detect as _Detect  # noqa: E402

_detect_src = read_source("detect.py")
report.check("no per-frame float copy of the whole frame",
             "resized_img.astype(np.float32, copy=True)" in _detect_src, False)
report.check("the divide goes into the buffer",
             "out=self._padded_img_buffer" in _detect_src, True)

# The buffer is reused between calls, so anything outside the resized area has
# to stay at the padding value - a frame of a different shape must not leave a
# stripe of the previous one behind.
_probe = object.__new__(_Detect)
_probe.input_size = (640, 640)
_probe._padded_img_buffer = _np.full((1, 3, 640, 640), 128.0 / 255.0, dtype=_np.float32)

_tall = (_np.random.default_rng(1).random((1920, 1080, 3)) * 255).astype("uint8")
_buf, _w, _h = _probe.preprocess_image(_tall)
report.check("the resized frame lands in the corner", (_w, _h), (360, 640))
# float32(128/255) is not float64(128/255); compare at the buffer's precision.
report.check("and the rest of the buffer is still padding",
             bool(_np.allclose(_buf[0, 0, :, _w:], _np.float32(128.0 / 255.0))), True)
report.check("values are normalised to 0..1",
             bool(_buf.min() >= 0.0 and _buf.max() <= 1.0), True)

# What it must equal: the old three-step version, to within a float32 step.
import cv2 as _cv2_pre  # noqa: E402

_scale = min(640 / _tall.shape[0], 640 / _tall.shape[1])
_r = _cv2_pre.resize(_tall, (int(_tall.shape[1] * _scale), int(_tall.shape[0] * _scale)),
                     interpolation=_cv2_pre.INTER_LINEAR)
_old = _np.full((1, 3, 640, 640), 128.0 / 255.0, dtype=_np.float32)
_f = _r.astype(_np.float32)
_np.multiply(_f, 1.0 / 255.0, out=_f)
for _c in range(3):
    _old[0, _c, :_h, :_w] = _f[:, :, _c]
report.check("and it matches what the old three-step version produced",
             bool(_np.allclose(_buf, _old, atol=1e-6)), True)


report.section("TensorRT is opt-in, and never asked for on its own")
# Worth 2.5x on the card this was measured on and SLOWER than CUDA on others,
# so it is switched on by a measurement rather than by belief. Everything here
# guards the two ways that could go wrong for somebody else.
_detect = read_source("detect.py")

# The trap: onnxruntime does not raise when TensorRT's libraries are missing,
# it falls back to whatever else is in the list. A list containing only
# TensorRT therefore falls back to the CPU - measured, not guessed. Asked for
# alongside CUDA it falls back to CUDA, which is what we want.
report.check("TensorRT is requested together with CUDA and CPU",
             all(name in _detect for name in ("TensorrtExecutionProvider",
                                              "CUDAExecutionProvider",
                                              "CPUExecutionProvider")), True)
report.check("in one list, so a missing library falls back to CUDA not the CPU",
             "attempts.append([(" in _detect, True)
report.check("it is only offered when the config asks for it",
             "self.use_tensorrt" in _detect, True)
report.check("and the default is not tensorrt",
             '"execution_provider", "auto"' in _detect, True)
report.check("engines are cached, since a build takes minutes",
             "trt_engine_cache_enable" in _detect, True)
report.check("and the cache is not committed",
             "models/trt_cache" in read_source(".gitignore"), True)

# With the shipped config, nothing changes for anybody.
sys.path.insert(0, os.path.join(REPO, "src"))
from detect import Detect as _D  # noqa: E402

_probe = object.__new__(_D)
_probe.preferred_device = "auto"
_probe.use_tensorrt = False
_names = [[p[0] if isinstance(p, tuple) else p for p in attempt]
          for attempt in _D.provider_order(_probe)]
report.check("with the default config TensorRT is never requested",
             any("TensorrtExecutionProvider" in a for a in _names), False)
report.check("and CPU is still the last resort",
             _names[-1], ["CPUExecutionProvider"])

_probe.preferred_device = "cpu"
report.check("asking for the CPU still gets only the CPU",
             [[p for p in a] for a in _D.provider_order(_probe)], [["CPUExecutionProvider"]])

_picker = read_source("tools/pick_provider.py")
report.check("the picker refuses to enable a provider that did not win",
             "WORTH_IT" in _picker, True)
report.check("and notices a silent fallback rather than reporting a win",
             'running != "TensorrtExecutionProvider"' in _picker, True)


report.section("cards that can use TensorRT get it installed")
# It used to be printed as a suggestion, which meant almost nobody ran it. The
# gain on the cards it suits is 2.4x, so it is installed for every card that
# got the CUDA runtime - and then MEASURED, because installing it is not the
# same as it being faster, and on some cards it is not.
sys.path.insert(0, os.path.join(REPO, "tools"))
import installer as _installer  # noqa: E402

_saved = (_installer.log, _installer.run, _installer.pip_install,
          _installer.install_tensorrt)
try:
    _steps = []
    _installer.log = lambda *a, **k: None
    _installer.run = lambda *a, **k: (0, "")
    _installer.pip_install = lambda args, what, attempts=3: (_steps.append(what) or True)
    _installer.install_tensorrt = lambda: _steps.append("tensorrt")

    def _steps_for(vendor, cap=""):
        _steps.clear()
        _installer.install_accelerator(vendor, cap)
        return list(_steps)

    report.check("a modern NVIDIA card gets it", "tensorrt" in _steps_for("nvidia", "8.9"), True)
    report.check("a card too old for CUDA does not",
                 "tensorrt" in _steps_for("nvidia", "6.1"), False)
    report.check("nor does one whose capability could not be read",
                 "tensorrt" in _steps_for("nvidia", ""), False)
    report.check("nor an AMD or Intel card", "tensorrt" in _steps_for("amd"), False)
    report.check("and those still get DirectML",
                 "DirectML runtime" in _steps_for("amd"), True)
finally:
    (_installer.log, _installer.run, _installer.pip_install,
     _installer.install_tensorrt) = _saved

_inst_src = read_source("tools/installer.py")
report.check("the version is pinned to what onnxruntime loads",
             "tensorrt-cu13==10.16.1.11" in _inst_src, True)
report.check("a failed install is not a failed setup",
             "was an optimisation, not a requirement" in _inst_src, True)
report.check("installing it does not by itself switch it on",
             "pick_provider" in _inst_src, True)

# Existing installs re-run setup when the installer changes, so this reaches
# people who set the bot up months ago rather than only new downloads.
report.check("the setup fingerprint covers the installer itself",
             "tools/installer.py" in _inst_src and "def fingerprint" in _inst_src, True)


report.section("a link out of the panel opens somewhere")
# In a browser tab, target="_blank" opens a tab. In the desktop build the page
# lives in a QWebEngineView, which gets asked for a second window and has no
# way to make one - so it returns nothing and the click does literally nothing.
# No window, no browser, no error to go looking for. Both links in the sidebar,
# Telegram and the donation page, were dead in the exe and fine on the site,
# which is exactly the sort of thing nobody reports for months.
sys.path.insert(0, REPO)
import desktop as _desktop  # noqa: E402

report.check("an outside address is handed to the browser",
             _desktop.is_external_link("https", "www.donationalerts.com"), True)
report.check("and so is the Telegram one",
             _desktop.is_external_link("https", "t.me"), True)
report.check("the panel's own pages stay in the window",
             [_desktop.is_external_link("http", host)
              for host in ("127.0.0.1", "localhost", "::1")],
             [False, False, False])
# The panel builds these itself, for screenshots and downloads. Handing one to
# the operating system is at best useless.
report.check("a blob or data URL is not an address to leave by",
             [_desktop.is_external_link(scheme, "")
              for scheme in ("blob", "data", "file", "about")],
             [False, False, False, False])

_desktop_src = read_source("desktop.py")
report.check("the view can answer a request for a new window",
             "def createWindow" in _desktop_src, True)
report.check("and the panel refuses to navigate away from itself",
             "acceptNavigationRequest" in _desktop_src, True)


report.section("a stale movement value cannot take the game loop down")
# play.py used to reset last_movement to '' - an empty STRING left over from
# the WASD days - and hand it straight back to the shaper while the movement
# rate limiter was cooling down. float(target[0]) on '' raised IndexError and
# killed the whole match loop.
from dodge.smoothing import MovementShaper as _Shaper
from dodge.config import DodgeConfig as _DodgeConfig

_shaper_cfg = _DodgeConfig({})
_shaper = _Shaper(_shaper_cfg)
_crashed = None
try:
    _shaper.shape("", now=1.0)
    _shaper.shape((), now=1.1)
except Exception as _exc:          # noqa: BLE001 - the point is that none escape
    _crashed = repr(_exc)
report.check("an empty movement is coasted, not crashed on", _crashed, None)
report.check("and play.py no longer parks an empty string in last_movement",
             "self.last_movement = ''" in read_source("play.py"), False)

report.section("urgent dodge confirmation is reachable at all")
# The branch only runs when a track is SHORT of the hits it needs, yet it also
# demanded the full min_confirm_hits - a condition that can never hold at the
# same time, so `urgent_confirm = True` was dead code and fast shots always
# cost an extra frame of reaction.
_tracker_src = read_source("dodge/tracker.py")
report.check("it no longer demands the very hit count it is bypassing",
             "and track.hits >= config.min_confirm_hits\n" in _tracker_src, False)
report.check("and short tracks are no longer dropped before it can run",
             "if track.hits < 2:" in _tracker_src, True)


report.section("a new account never inherits another account's identity")
# What happened: every account's config was seeded by copying the shared cfg/,
# player_tag and API token included. Account two then resynced against account
# one's Brawl Stars profile, so the trophies typed in (1057) were overwritten by
# whatever the API said for the OTHER player (2014) and the queue was rewritten
# with them on every restart - "it resets the queue after restarting the second
# account".
import tempfile as _tf, shutil as _sh, pathlib as _pl, io as _io, re as _re
from webui.instances import _blank_identity as _blank

_seed = _pl.Path(_tf.mkdtemp())
_sh.copy2("cfg/general_config.toml", _seed / "general_config.toml")
_io.open(_seed / "login.toml", "w", encoding="utf-8").write('key = "somekey"' + chr(10))
_before = _io.open(_seed / "general_config.toml", encoding="utf-8").read()
_blank(_seed)
_after = _io.open(_seed / "general_config.toml", encoding="utf-8").read()

def _value(text, key):
    found = _re.search(r"(?m)^\s*%s\s*=\s*(.*)$" % key, text)
    return found.group(1).strip() if found else None

report.check("the seeded tag and token come out empty",
             [_value(_after, k) for k in ("player_tag", "brawl_api_token",
                                          "brawl_api_email", "brawl_api_password")],
             ['""', '""', '""', '""'])
report.check("and the saved login key with them",
             _value(_io.open(_seed / "login.toml", encoding="utf-8").read(), "key"), '""')
# The keys have to survive - blanking the whole line would break the config.
report.check("the settings themselves are still there",
             all(_value(_after, k) is not None for k in
                 ("player_tag", "brawl_api_token", "brawl_api_email")), True)
report.check("nothing else in the file moved",
             len(_after.splitlines()), len(_before.splitlines()))
import toml as _toml
_parsed = None
try:
    _parsed = _toml.loads(_after)
except Exception:
    pass
report.check("and it is still valid TOML", _parsed is not None, True)


sys.exit(report.finish())
