"""Things that broke on other people's machines, not on this one.

Every check here comes from a report: a card too old for the CUDA build, an
emulator smaller than 1920x1080, a panel that froze on a long history, a
number typed into a form and thrown away, and a Flask warning that reads like
something is wrong with the program.
"""
import logging
import sys

from _harness import Failures, read_source

sys.path.insert(0, ".")
import webui.app as webui_app  # noqa: E402
from detect import Detect  # noqa: E402
from lobby_automation import LobbyAutomation  # noqa: E402
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

    def __init__(self, preferred):
        self.preferred_device = preferred


_gpu = _Order("auto").provider_order()
report.check("CPU is always the last resort", _gpu[-1], "CPUExecutionProvider")
report.check("and it is never the only thing tried on a GPU box",
             len(_gpu) >= 1, True)
report.check("asking for CPU tries nothing else",
             _Order("cpu").provider_order(), ["CPUExecutionProvider"])

_source = read_source("detect.py")
report.check("session creation is guarded rather than allowed to kill startup",
             "except Exception as exc:" in _source
             and "problems.append((onnx_provider, exc))" in _source, True)
report.check("and every provider failing is still an error, not a silent no-op",
             "No execution provider could load" in _source, True)


# ── an emulator that is not 1920x1080 ───────────────────────────────────
report.section("the reconnect prompt is clickable on a smaller emulator")

left, top, right, bottom = LobbyAutomation.IDLE_REGION
reference_area = (right - left) * (bottom - top)


def idle_threshold(configured, ratio):
    """What check_for_idle now compares against at a given window scale."""
    actual = max(int((right - left) * ratio) * int((bottom - top) * ratio), 1)
    return configured * (actual / reference_area)


report.check("at the reference resolution the configured number is unchanged",
             round(idle_threshold(75000, 1.0)), 75000)
for ratio in (0.75, 0.5, 0.35):
    area = int((right - left) * ratio) * int((bottom - top) * ratio)
    report.check(f"at x{ratio} the threshold still fits inside the box",
                 idle_threshold(75000, ratio) < area, True)
report.check("which the old fixed 75000 did not at half size",
             75000 < int((right - left) * 0.5) * int((bottom - top) * 0.5), False)

_lobby = read_source("lobby_automation.py")
report.check("the region is named once rather than written out twice",
             _lobby.count("IDLE_REGION"), 2)


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

_app_js = open("static/js/app.js", encoding="utf-8").read()
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


sys.exit(report.finish())
