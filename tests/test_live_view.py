"""The live view has to be free when unwatched and bounded when watched.

Everything else in this bot is judged on how fast it thinks, so a feature that
streams video off the same machine is guilty until proven cheap. Two claims are
worth testing and both are about cost rather than about pictures: that a user
who never opens it pays nothing at all, and that a user who does opens a tap
with a known maximum rather than an open one.

The second matters most on exactly the machines that can least afford it. A
fast PC would absorb a careless stream; a slow one would hand the bot a worse
frame rate, and the user would blame the bot.
"""
import sys
import threading
import time

import cv2
import numpy as np

from _harness import Failures

sys.path.insert(0, "src")
import live_view  # noqa: E402

report = Failures("live view")

FRAME = (np.random.default_rng(7).random((1080, 1920, 3)) * 255).astype("uint8")


def reset(**overrides):
    live_view._settings = dict(live_view.DEFAULTS, **overrides)
    live_view._settings_read = time.time()
    live_view._viewers = 0
    live_view._latest = {"frame": None, "debug": None, "stamp": 0.0}


report.section("free while nobody is watching")

reset()
live_view.publish(FRAME, {"state": "match"})
report.check("an unwatched publish stores nothing",
             live_view._latest["frame"] is None, True)

started = time.perf_counter()
for _ in range(20000):
    live_view.publish(FRAME, None)
per_call_us = (time.perf_counter() - started) / 20000 * 1_000_000
# Three orders of magnitude below a single detection pass. The limit is
# generous so a loaded machine cannot fail the build over scheduling noise.
report.at_most(f"microseconds per unwatched publish ({per_call_us:.2f})",
               per_call_us, 10.0)


report.section("switched off means switched off")

reset(live_view_enabled=False)
report.check("a disabled stream yields no frames",
             list(live_view.frames()), [])
report.check("a disabled stream registers no viewer",
             live_view.viewers(), 0)


report.section("a connection is real MJPEG, and it lets go")

reset()
# Publish from a second thread, the way the bot loop does. It has to be a
# thread: publish() only stores a frame while a viewer is counted, and the
# viewer is not counted until frames() has started running, so doing both in
# one thread is a deadlock. It also has to be a frame published AFTER the
# viewer connects - the stream deliberately refuses to serve a picture that
# was already stale when somebody opened it.
publishing = threading.Event()


def publisher():
    while not publishing.is_set():
        live_view.publish(FRAME, None)
        time.sleep(0.01)


pump = threading.Thread(target=publisher, daemon=True)
pump.start()

stream = live_view.frames()
chunk = next(stream)

report.check("a connected browser is counted", live_view.viewers(), 1)
report.check("the stream sends multipart JPEG parts",
             chunk.startswith(b"--frame") and b"image/jpeg" in chunk, True)
report.check("the part carries a real JPEG",
             bytes([0xFF, 0xD8, 0xFF]) in chunk, True)

stream.close()
publishing.set()
pump.join(timeout=2)
# If this ever fails, the bot keeps paying to publish frames to nobody - the
# exact leak the whole design is built to avoid.
report.check("closing the connection stops the cost", live_view.viewers(), 0)


report.section("colours survive the trip")

# The bot works in RGB, JPEG wants BGR, and the overlay is drawn by code that
# picks its colours for a BGR image. Convert in the wrong order and the game
# picture stays right while every box drawn on it swaps red for blue - which
# looks like a detection bug and is not one.
reset()
red_rgb = np.zeros((1080, 1920, 3), dtype=np.uint8)
red_rgb[:, :, 0] = 255  # pure red, in RGB

live_view._viewers = 1
live_view.publish(red_rgb, None)
live_view._viewers = 0
decoded = cv2.imdecode(np.frombuffer(live_view._render(), np.uint8),
                       cv2.IMREAD_COLOR)  # imdecode hands back BGR
blue, green, redness = (int(v) for v in decoded[decoded.shape[0] // 2,
                                                decoded.shape[1] // 2])
report.at_least(f"red stays red through the stream (R={redness})", redness, 200)
report.at_most(f"and does not come back as blue (B={blue})", blue, 60)


report.section("a stopped bot ends the stream instead of hanging")

# The panel is often opened before the bot is started. Without a timeout the
# browser holds a connection that never yields and never fails, and the user
# stares at an empty box with nothing to tell them which it is.
reset(live_view_idle_timeout=0.3)
began = time.perf_counter()
produced = list(live_view.frames())
waited = time.perf_counter() - began

report.check("no frames when the bot has published none", produced, [])
report.at_most(f"seconds before giving up ({waited:.2f})", waited, 3.0)
report.check("giving up releases the viewer count", live_view.viewers(), 0)


report.section("a bot that stops mid-watch does not look alive")

# The frame the bot published last stays in memory after it stops. A stream
# that asked only "can I produce a picture" would answer yes for ever, re-
# encoding that one frame, costing CPU and showing a still image that a person
# would read as live play.
reset(live_view_idle_timeout=0.4)
handed_over = threading.Event()


def publish_briefly():
    began = time.time()
    while time.time() - began < 0.3:
        live_view.publish(FRAME, None)
        time.sleep(0.01)
    handed_over.set()


pump = threading.Thread(target=publish_briefly, daemon=True)
pump.start()

began = time.perf_counter()
parts = 0
for chunk in live_view.frames():
    parts += 1
waited = time.perf_counter() - began
pump.join(timeout=2)

report.at_least(f"frames were served while it was playing ({parts})", parts, 1)
report.at_most(f"seconds before noticing it stopped ({waited:.2f})", waited, 2.0)
report.check("and it let the connection go", live_view.viewers(), 0)


report.section("the CPU budget holds on a slow machine")


def measured_share(render_ms, share, seconds=0.8):
    """Run the real generator against a renderer of a known, awful cost."""
    reset(live_view_cpu_share=share)
    real = live_view._render
    worked = [0.0]

    def slow(config=None):
        began = time.perf_counter()
        while (time.perf_counter() - began) * 1000 < render_ms:
            pass
        worked[0] += time.perf_counter() - began
        return b"x" * 64

    # A publisher has to keep running, or the stream decides the bot has
    # stopped and closes before it has done any work - which would leave this
    # measuring nothing at all and passing.
    done = threading.Event()

    def publisher():
        while not done.is_set():
            live_view.publish(FRAME, None)
            time.sleep(0.005)

    live_view._render = slow
    pump = threading.Thread(target=publisher, daemon=True)
    pump.start()
    try:
        stream = live_view.frames()
        began = time.perf_counter()
        for _ in stream:
            if time.perf_counter() - began > seconds:
                break
        wall = time.perf_counter() - began
        stream.close()
    finally:
        done.set()
        pump.join(timeout=2)
        live_view._render = real
    return worked[0] / wall * 100


# 25 ms a frame is roughly a machine fifteen times slower than a modern one.
# Unpaced it would run flat out; paced, the share is the share, and the user
# loses picture smoothness instead of the bot losing cycles.
for render_ms in (10.0, 25.0):
    used = measured_share(render_ms, 0.05)
    report.at_least(f"the {render_ms:.0f} ms renderer actually ran", used, 0.5)
    report.at_most(
        f"percent of one core with a {render_ms:.0f} ms frame, 5% budget "
        f"({used:.1f}%)", used, 9.0)

reset()
raise SystemExit(report.finish())
