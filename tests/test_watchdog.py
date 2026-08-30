"""Noticing that nothing is happening, without asking the bot's opinion.

The bot can be certain a match is in progress while the screen has not moved
for ten minutes - the last frame it saw looked like a match, and that frame is
still what it is being handed. So the watchdog compares pixels instead, and
these check that it tells a frozen picture from a moving one, that the two
clocks fire when they should, and that it will not kill an emulator it cannot
start again.
"""
import sys

import cv2
import numpy as np

from _harness import Failures

sys.path.insert(0, "src")
from activity_watchdog import (CHANGE_THRESHOLD, ActivityWatchdog,  # noqa: E402
                               KNOWN_EMULATORS, frame_signature, frames_differ,
                               restart_emulator_process)
import activity_watchdog as _watchdog  # noqa: E402

report = Failures("activity watchdog")


def scene(shift=0, seed=1):
    """A frame that looks like a game rather than like noise.

    Random pixels are the wrong fixture here: averaged down to the comparison
    size they all land on the same grey, so two completely different noise
    frames read as identical. Large coloured shapes behave the way a game does.
    """
    frame = np.zeros((1080, 1920, 3), np.uint8)
    rng = np.random.default_rng(seed)
    for _ in range(14):
        x = int(rng.integers(0, 1700)) + shift
        y = int(rng.integers(0, 900))
        cv2.rectangle(frame, (x, y), (x + 180, y + 150),
                      tuple(int(v) for v in rng.integers(40, 240, 3)), -1)
    return frame


report.section("telling a frozen picture from a moving one")
_base = scene(0)
report.check("a frame is not different from itself",
             frames_differ(frame_signature(_base), frame_signature(_base)), False)
report.check("a real movement registers",
             frames_differ(frame_signature(_base), frame_signature(scene(40))), True)

# The margin that matters: encoder noise on a static screen must not read as
# movement, or the watchdog never fires at all.
_noisy = np.clip(_base.astype(np.int16)
                 + np.random.default_rng(5).integers(-3, 4, _base.shape),
                 0, 255).astype(np.uint8)
_noise_level = float(np.abs(frame_signature(_base) - frame_signature(_noisy)).mean())
report.check("compression noise does not count as movement",
             frames_differ(frame_signature(_base), frame_signature(_noisy)), False)
report.check("and it sits well under the threshold",
             _noise_level < CHANGE_THRESHOLD * 0.7, True)

report.check("a frame that could not be read is not treated as frozen",
             frames_differ(None, frame_signature(_base)), True)
report.check("nor is a missing one", frame_signature(None), None)


report.section("the two clocks")


def run(frames, minutes=13, step=10):
    """Feed one frame every `step` seconds and see what it does."""
    clock = {"t": 0.0}
    games, emulators = [], []
    watchdog = ActivityWatchdog(
        restart_game=lambda: games.append(clock["t"]),
        restart_emulator=lambda: (emulators.append(clock["t"]) or True),
        now=lambda: clock["t"])
    for index in range(int(minutes * 60 / step)):
        clock["t"] = index * step
        watchdog.note(frames(index))
    return ([round(t / 60, 1) for t in games],
            [round(t / 60, 1) for t in emulators])


_frozen = scene(0)
_games, _emulators = run(lambda i: _frozen)
report.check("Brawl Stars is restarted after three minutes of nothing",
             _games[:1], [3.0])
report.check("and again three minutes later if that did not help",
             _games[:3], [3.0, 6.0, 9.0])
report.check("the emulator is restarted at ten minutes", _emulators, [10.0])
report.check("and not before - the cheap answer gets tried first",
             all(g < e for g in _games[:1] for e in _emulators), True)

_games, _emulators = run(lambda i: scene(shift=i * 11), minutes=20)
report.check("a game that is actually running is left alone",
             (_games, _emulators), ([], []))

# A match sitting on one screen for a while is normal; two minutes is not
# three.
_games, _emulators = run(lambda i: _frozen, minutes=2.5)
report.check("two and a half minutes of stillness is not enough",
             (_games, _emulators), ([], []))


report.section("restarting the emulator is the careful half")
_calls = []
_watchdog.running_emulators = lambda: []
report.check("with no emulator recognised, nothing is killed",
             restart_emulator_process(), False)

# The rule that matters: an emulator that cannot be started again must not be
# stopped. A frozen emulator can still be fixed by the person sitting there; a
# killed one with no command line cannot.
_watchdog.running_emulators = lambda: [("HD-Player.exe", 1234, "")]
report.check("nor one whose command line we could not read",
             restart_emulator_process(), False)

report.check("only known emulators are ever candidates",
             all(name.endswith(".exe") for name in KNOWN_EMULATORS), True)
report.check("and the list names the ones people actually use",
             {"HD-Player.exe", "dnplayer.exe"} <= KNOWN_EMULATORS, True)

_source = open("src/activity_watchdog.py", encoding="utf-8").read()
report.check("the command line is read before anything is killed",
             _source.index("startable = ") < _source.index("taskkill"), True)
report.check("and the kill is by pid, not by name",
             '"/PID", str(pid)' in _source, True)


report.section("it does not depend on what the bot believes")
report.check("nothing in here asks for the game state",
             "get_latest_state" in _source or "state ==" in _source, False)
report.check("the decision comes from the frames",
             "frame_signature" in _source, True)


report.section("a recovery path that fails must not end the watch")
# Reported from a real run: an ADB error was caught, the handler called
# restart_brawl_stars to recover, that touched the same missing device and
# raised again - from inside an except block, so nothing caught it. The crash
# watchdog thread died and the bot silently had no crash detection for the
# rest of the session.
from window_controller import WindowController  # noqa: E402


class _DeadDevice:
    def app_stop(self, package):
        raise RuntimeError("device offline")

    def app_start(self, package):
        raise RuntimeError("device offline")


class _LiveDevice:
    def app_stop(self, package):
        pass

    def app_start(self, package):
        pass


def _controller(reconnect):
    controller = object.__new__(WindowController)
    controller.device = _DeadDevice()
    controller.BRAWL_STARS_PACKAGE = "com.supercell.brawlstars"
    controller.reconnect_scrcpy = reconnect
    return controller


_hopeless = _controller(lambda *a, **k: False)
_raised = None
try:
    _result = _hopeless.restart_brawl_stars()
except Exception as exc:                      # noqa: BLE001 - that is the point
    _raised, _result = type(exc).__name__, None
report.check("restarting a gone device does not raise", _raised, None)
report.check("it says it failed instead", _result, False)


def _revive(controller):
    def reconnect(*args, **kwargs):
        controller.device = _LiveDevice()
        return True
    return reconnect


_recovering = object.__new__(WindowController)
_recovering.device = _DeadDevice()
_recovering.BRAWL_STARS_PACKAGE = "com.supercell.brawlstars"
_recovering.reconnect_scrcpy = _revive(_recovering)
report.check("and it retries after a reconnect that worked",
             _recovering.restart_brawl_stars(), True)

_main = open("main.py", encoding="utf-8").read()
_start = _main.index("def crash_watchdog_loop")
_loop = _main[_start:_start + 900]
report.check("the crash watchdog survives a bad pass", "except Exception" in _loop, True)
report.check("and keeps looping rather than returning",
             "Carrying on" in _loop, True)


sys.exit(report.finish())
