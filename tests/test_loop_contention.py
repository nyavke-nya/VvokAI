"""The bot loop must not wait on the dodge tracker.

Reported as "IPS drops a lot when projectiles appear". The loop profile said
where: under fire the pure-Python stages stretched (playstyle 6.7 -> 11.1 ms,
of which the playstyle script itself is 8.9) while YOLO, which drops the GIL
for the duration of an inference, did not move at all (5.0 -> 4.8 ms). So the
extra time was not detection. It was the bot loop waiting for the interpreter,
and for a lock, behind a tracker thread whose cost is not constant: measured
over the session in debug_frames, a tracker frame takes 2.9 ms with the screen
quiet and 10.3 ms once there are eighty tracks on it.

Two things were making the loop pay for that, and these cover both.
"""
import sys
import threading
import time

from _harness import Failures, read_source

sys.path.insert(0, "src")

report = Failures("loop contention")


report.section("the tracker holds its own lock, not the one the loop uses")
_service = read_source("dodge/service.py")

# _process_locked is written to take the state lock only around the short reads
# and writes it needs, with tracker.update() and solver.solve() deliberately
# outside them. Wrapping the whole call in the same lock threw that away.
report.check("frame processing is serialised on a lock of its own",
             "with self._frame_lock:" in _service, True)
_process = _service.split("def _process(self, frame, stamp, emergency):")[1].split("def ")[0]
report.check("and _process no longer takes the state lock across a whole frame",
             "with self._lock:" in _process, False)

# reset() relied on that outer lock to keep an in-flight frame from publishing
# a decision after a new match had cleared the context. It has to take the
# frame lock itself now, or that guarantee quietly disappeared with it.
_reset = _service.split("def reset(self):")[1].split("def ")[0]
report.check("reset still waits for a frame in flight",
             "self._frame_lock" in _reset, True)
report.check("and takes both, in the order _process takes them",
             "with self._frame_lock, self._lock:" in _reset, True)

# The order matters in one direction only: both sites must take the frame lock
# first, or two threads can hold one each and wait for the other.
_order_ok = True
for block in (_process, _reset):
    if "_frame_lock" not in block or "with self._lock" not in block:
        continue
    if block.index("_frame_lock") > block.index("with self._lock"):
        _order_ok = False
report.check("nothing takes the state lock before the frame lock", _order_ok, True)

# The outer lock was not arbitrary - it kept a frame in flight from adding its
# accumulated pan on top of a context that had just replaced it. Removing it
# without replacing that guarantee would trade a slow bot for a drifting one.
report.check("a frame checks whether its context was replaced under it",
             "generation == self._context_generation" in _service, True)
report.check("and the counter is bumped where the context is",
             "self._context_generation += 1" in _service, True)


report.section("and the loop's own calls into the service stay short")
# What the bot loop does every iteration - set_tactical_intent, get_projectiles,
# update_context - against a thread that is busy with a frame the whole time.


class _Service:
    """The locking of DodgeService, and nothing else."""

    def __init__(self, share_the_lock):
        self._lock = threading.RLock()
        self._frame_lock = threading.RLock()
        self._outer = self._lock if share_the_lock else self._frame_lock
        self._projectiles = []
        self._tactical = None

    def process(self, work_seconds):
        with self._outer:
            with self._lock:
                _ = self._tactical
            end = time.perf_counter() + work_seconds
            while time.perf_counter() < end:
                pass
            with self._lock:
                self._projectiles = []

    def loop_calls(self):
        with self._lock:
            self._tactical = (10.0, 0.0)
        with self._lock:
            return list(self._projectiles)


def worst_wait(share_the_lock, frame_ms=8.0, iterations=120):
    service = _Service(share_the_lock)
    stop = threading.Event()

    def tracker():
        while not stop.is_set():
            service.process(frame_ms / 1000.0)
            # The real one waits for the next captured frame; without a gap here
            # the loop starves completely and the number stops meaning anything.
            stop.wait(0.004)

    thread = threading.Thread(target=tracker, daemon=True)
    thread.start()
    time.sleep(0.03)
    worst = 0.0
    for _ in range(iterations):
        started = time.perf_counter()
        service.loop_calls()
        worst = max(worst, (time.perf_counter() - started) * 1000.0)
        time.sleep(0.001)
    stop.set()
    thread.join(timeout=2)
    return worst


_shared = worst_wait(True)
_split = worst_wait(False)
report.check(f"sharing the lock makes the loop wait for a whole frame "
             f"({_shared:.2f} ms)", _shared > 1.0, True)
report.check(f"splitting it does not ({_split:.2f} ms)", _split < 1.0, True)
report.check("and the split is a real improvement, not noise",
             _split < _shared / 2.0, True)


report.section("no thread may sit on the interpreter for a whole 5 ms slice")
_main = read_source("main.py")
report.check("the switch interval is set at startup",
             "sys.setswitchinterval(" in _main, True)
_value = float(_main.split("sys.setswitchinterval(")[1].split(")")[0])
report.check("well under the 5 ms default", _value <= 0.002, True)
report.check("but not so low that switching costs more than it saves",
             _value >= 0.0005, True)
report.check("and it is set before any thread is started - the tracker, the "
             "state checker and the panel all begin after this point",
             _main.index("sys.setswitchinterval(") < _main.index("import threading"),
             True)

sys.exit(report.finish())
