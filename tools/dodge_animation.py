"""Animate why one shot gets dodged and another cannot be.

    venv\\Scripts\\python.exe tools\\dodge_animation.py
    venv\\Scripts\\python.exe tools\\dodge_animation.py --log debug_frames/dodge_log.jsonl

Picks two real shots out of a session log - the most ordinary member of the
group the bot beat, and of the group it could not - and plays them side by side
on one clock and one distance scale.

The point it makes is a subtraction, not an opinion. A shot has to be dodged
before it arrives, and the bot cannot start until it has seen the shot and the
input has travelled back to the device:

    see it  +  input lag  +  time to step out of the hitbox  <=  time to impact

On the left that comes to 147 ms against a 235 ms deadline, so the bot lives.
On the right the deadline is -142 ms: it would have had to be moving before the
shot existed. No amount of tuning reaches that. It is a fast shot from close
range, and a person would be hit by it too.
"""

import argparse
import json
import pathlib
import statistics
import sys

from PIL import Image, ImageDraw, ImageFont

sys.stdout.reconfigure(encoding="utf-8")

PROJECT = pathlib.Path(__file__).resolve().parent.parent

# From cfg/dodge_config.toml and the engine, not guessed.
REACTION_LATENCY = 0.12
PLAYER_SPEED = 330.0

W, H = 1000, 700
PANEL_W = 470
PANEL_X = (24, 506)
PANEL_TOP, PANEL_BOTTOM = 72, 662
ARENA_TOP, ARENA_H = 150, 250
TL_TOP, TL_H = 412, 50

BG = (11, 13, 18)
PANEL_BG = (18, 21, 28)
GRID = (28, 33, 42)
TEXT = (232, 236, 244)
DIM = (128, 138, 155)
GOOD = (74, 222, 128)
BAD = (248, 113, 113)
SHOT = (250, 204, 21)
BLUE = (122, 162, 255)

SLOWMO = 6.0
FPS = 25


def load_cases(log_path):
    """The most typical shot the bot beat, and the most typical one it did not.

    Typical means closest to the medians of its own group for both speed and
    distance, so neither panel is a cherry-picked extreme.
    """
    shots = []
    with open(log_path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("event") == "shot_confirmed" and record.get("time_to_reach"):
                shots.append(record)
    if not shots:
        raise SystemExit(f"no confirmed shots in {log_path}")

    def typical(rows):
        if not rows:
            return None
        speed = statistics.median(r["speed"] for r in rows)
        distance = statistics.median(r["distance"] for r in rows)
        return min(rows, key=lambda r: abs(r["speed"] - speed) / speed
                   + abs(r["distance"] - distance) / distance)

    beat = typical([s for s in shots if s["verdict"] == "in_time"])
    lost = typical([s for s in shots if s["verdict"] == "DETECTED_TOO_LATE"])
    if not beat or not lost:
        raise SystemExit("the log needs both a dodged and an undodgeable shot")
    print(f"{len(shots)} shots read from {log_path}")
    return beat, lost


def font(size, bold=False):
    names = ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE = font(27, True)
F_HEAD = font(18, True)
F_VERDICT = font(21, True)
F_BODY = font(14)
F_SMALL = font(13)
F_TINY = font(11)
F_CLOCK = font(16, True)


def case_plan(case):
    """When each thing has to happen, in seconds from the shot appearing."""
    impact = case["time_to_reach"]
    clear = case["time_to_clear_hitbox"]
    confirmed = case["age_when_confirmed"]
    moving = confirmed + REACTION_LATENCY
    return {
        "speed": case["speed"],
        "distance": case["distance"],
        "radius": case["radius"],
        "impact": impact,
        "clear": clear,
        "confirmed": confirmed,
        "moving": moving,
        "safe_by": impact - clear,      # be moving by here, or be hit
        "escaped_at": moving + clear,   # when the hitbox is actually vacated
        "clearance": clear * PLAYER_SPEED,
        "survives": moving + clear <= impact,
    }


def draw_panel(d, plan, x, title, subtitle, t, span, scale):
    tone = GOOD if plan["survives"] else BAD
    d.rounded_rectangle([x, PANEL_TOP, x + PANEL_W, PANEL_BOTTOM], 14,
                        fill=PANEL_BG, outline=(38, 44, 56))
    d.text((x + 20, PANEL_TOP + 16), title, font=F_HEAD, fill=tone)
    d.text((x + 20, PANEL_TOP + 40), subtitle, font=F_SMALL, fill=DIM)

    cx = x + PANEL_W - 76
    cy = ARENA_TOP + ARENA_H // 2
    left = x + 20

    for gy in range(0, ARENA_H, 42):
        d.line([(left, ARENA_TOP + gy), (x + PANEL_W - 20, ARENA_TOP + gy)], fill=GRID)
    for gx in range(0, PANEL_W - 40, 42):
        d.line([(left + gx, ARENA_TOP), (left + gx, ARENA_TOP + ARENA_H)], fill=GRID)

    # Frozen at impact: after that the outcome is decided, and the only honest
    # thing to show is where everything stood when it landed.
    shown = min(t, plan["impact"])
    travelled = max(shown - plan["moving"], 0.0) * PLAYER_SPEED

    px, py = cx, cy - travelled * scale
    # Interpolated to arrive exactly at impact rather than stepped by speed.
    # The log measures distance centre to centre and time_to_reach to the edge
    # of the hitbox, so speed x time leaves the shot short of the player at the
    # one moment that matters.
    sx = cx - plan["distance"] * scale * (1.0 - min(shown / plan["impact"], 1.0))

    # Leave this band and the shot misses.
    band = plan["clearance"] * scale
    d.line([(cx - 30, cy - band), (cx + 34, cy - band)], fill=(52, 62, 80))
    d.text((cx + 38, cy - band - 7), "clear", font=F_TINY, fill=(70, 82, 102))

    d.line([(cx - plan["distance"] * scale, cy), (cx + 26, cy)],
           fill=(56, 50, 22), width=2)
    if travelled > 0:
        d.line([(cx, cy), (px, py)], fill=(58, 88, 148), width=3)

    hitbox = 24
    d.ellipse([px - hitbox, py - hitbox, px + hitbox, py + hitbox],
              outline=(70, 110, 190), width=2)
    d.ellipse([px - 10, py - 10, px + 10, py + 10], fill=BLUE)

    r = max(plan["radius"] * scale, 5)
    d.ellipse([sx - r, cy - r, sx + r, cy + r], fill=SHOT)

    if t >= plan["impact"]:
        hit = not plan["survives"]
        if hit:
            d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], outline=BAD, width=3)
        d.text((left, ARENA_TOP + ARENA_H - 30), "HIT" if hit else "MISSED HIM",
               font=F_VERDICT, fill=BAD if hit else GOOD)

    # ---- timeline -------------------------------------------------------
    tw = PANEL_W - 44
    tx = x + 22

    def at(seconds):
        return tx + max(min(seconds / span, 1.0), 0.0) * tw

    d.rounded_rectangle([tx, TL_TOP, tx + tw, TL_TOP + TL_H], 8, fill=(24, 28, 36))
    d.rectangle([tx, TL_TOP + 12, at(plan["confirmed"]), TL_TOP + 28], fill=(96, 84, 40))
    d.rectangle([at(plan["confirmed"]), TL_TOP + 12, at(plan["moving"]), TL_TOP + 28],
                fill=(116, 72, 40))
    d.rectangle([at(plan["moving"]), TL_TOP + 12, at(plan["escaped_at"]), TL_TOP + 28],
                fill=(50, 92, 134))

    imp = at(plan["impact"])
    d.line([(imp, TL_TOP + 4), (imp, TL_TOP + TL_H - 4)], fill=BAD, width=3)
    if plan["safe_by"] > 0:
        dl = at(plan["safe_by"])
        d.line([(dl, TL_TOP + 4), (dl, TL_TOP + TL_H - 4)], fill=tone, width=2)

    now = at(min(t, span))
    d.line([(now, TL_TOP + 2), (now, TL_TOP + TL_H - 2)], fill=TEXT)

    d.text((tx, TL_TOP + 32), f"{plan['confirmed']*1000:.0f} see",
           font=F_TINY, fill=(206, 186, 96))
    d.text((tx + 74, TL_TOP + 32), f"{REACTION_LATENCY*1000:.0f} lag",
           font=F_TINY, fill=(224, 152, 96))
    d.text((tx + 148, TL_TOP + 32), f"{plan['clear']*1000:.0f} ms to step clear",
           font=F_TINY, fill=(126, 174, 232))

    # ---- the numbers ----------------------------------------------------
    ny = TL_TOP + TL_H + 20
    rows = [
        ("shot speed", f"{plan['speed']:.0f} px/s", TEXT),
        ("distance when first seen", f"{plan['distance']:.0f} px", TEXT),
        ("time until it lands", f"{plan['impact']*1000:.0f} ms", TEXT),
        ("must already be moving by", f"{plan['safe_by']*1000:.0f} ms", tone),
        ("actually moving at", f"{plan['moving']*1000:.0f} ms", TEXT),
    ]
    for i, (label, value, colour) in enumerate(rows):
        d.text((tx, ny + i * 20), label, font=F_SMALL, fill=DIM)
        d.text((tx + 250, ny + i * 20), value, font=F_SMALL, fill=colour)

    gap = (plan["impact"] - plan["escaped_at"]) * 1000
    verdict = (f"clears the hitbox {gap:.0f} ms early" if gap >= 0
               else f"clears it {abs(gap):.0f} ms too late")
    d.text((tx, ny + 5 * 20 + 10), verdict, font=F_CLOCK, fill=tone)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", default="debug_frames/dodge_log.jsonl.1")
    parser.add_argument("--out", default="docs/how_dodging_works.gif")
    args = parser.parse_args()

    beat, lost = load_cases(PROJECT / args.log)
    dodged, missed = case_plan(beat), case_plan(lost)

    span = max(dodged["impact"], missed["impact"]) * 1.15
    scale = (PANEL_W - 130) / max(dodged["distance"], missed["distance"])
    total = int(span * SLOWMO * FPS)
    hold = int(1.6 * FPS)          # a beat on the final frame

    frames = []
    for index in range(total + hold):
        t = min(index, total - 1) / (FPS * SLOWMO)
        image = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(image)

        d.text((24, 20), "Why the bot dodges one shot and not the other",
               font=F_TITLE, fill=TEXT)
        d.text((24, 54), "Two real shots from one session, on the same clock "
                         f"and the same distance scale, slowed {SLOWMO:.0f}x.",
               font=F_BODY, fill=DIM)
        d.text((W - 152, 28), f"t = {t*1000:4.0f} ms", font=F_CLOCK, fill=TEXT)

        draw_panel(d, dodged, PANEL_X[0], "DODGED",
                   f"{dodged['speed']:.0f} px/s, seen {dodged['distance']:.0f} px away",
                   t, span, scale)
        draw_panel(d, missed, PANEL_X[1], "UNAVOIDABLE",
                   f"{missed['speed']:.0f} px/s, seen {missed['distance']:.0f} px away",
                   t, span, scale)

        d.text((24, PANEL_BOTTOM + 12),
               "see = confirming it is a shot and not grass   "
               "lag = capture, decode and input delay   "
               "step clear = leaving the hitbox at 330 px/s",
               font=F_TINY, fill=(96, 104, 118))
        frames.append(image)

    path = PROJECT / args.out
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=int(1000 / FPS), loop=0, optimize=True)
    print(f"{path}  {len(frames)} frames  {path.stat().st_size/1024:.0f} KB")


if __name__ == "__main__":
    main()
