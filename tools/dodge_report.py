"""Turn a dodge log into an answer.

    python tools/dodge_report.py
    python tools/dodge_report.py --log debug_frames/dodge_log.jsonl --detail

Reports how often the bot dodged, and when it did not, which of the five
failure modes was responsible - because each one points at a different fix:

    DETECTED_TOO_LATE  the shot arrived before the bot could physically clear
                       its hitbox. Lower tracking.min_confirm_hits, or raise
                       the emulator framerate so three frames cost less time.
    IMPOSSIBLE         no direction escapes. Usually a fast shot at close
                       range; a human would be hit too. Not a bug.
    BLOCKED            escapes existed but all ran into a wall or the map edge.
                       Positioning, not detection.
    OVERRULED          a clean escape existed but scored worse than staying.
                       Lower dodge.tactical_weight.
    DODGED             it worked.
"""

import argparse
import json
import math
import os
import sys

# Same working-directory fix as dodge_tune.py: the default log path is relative
# to the project root, not to wherever the command was typed.
INVOCATION_DIR = os.getcwd()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.chdir(PROJECT_ROOT)


def user_path(path):
    if path is None or os.path.isabs(path):
        return path
    # An explicitly passed relative path belongs to the user's directory; the
    # default one belongs to the project.
    from_user = os.path.join(INVOCATION_DIR, path)
    return from_user if os.path.exists(from_user) else path


def load(path):
    if not os.path.exists(path):
        try:
            from utils import config_bool, load_toml_as_dict
            on = config_bool(load_toml_as_dict("cfg/dodge_config.toml")
                             .get("logging", {}).get("enabled"), False)
        except Exception:
            on = False
        hint = (
            "Logging is on, so this just means the bot has not run a match yet."
            if on else
            "Turn it on first: logging.enabled = true in cfg/dodge_config.toml"
        )
        raise SystemExit(f"No log at {path}.\n{hint}")
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def bar(value, total, width=28):
    if total <= 0:
        return " " * width
    filled = int(round(value / total * width))
    return "#" * filled + "." * (width - filled)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", default="debug_frames/dodge_log.jsonl")
    parser.add_argument("--detail", action="store_true",
                        help="print each failed decision with its candidate breakdown")
    parser.add_argument("--limit", type=int, default=10,
                        help="how many detailed failures to print (default 10)")
    args = parser.parse_args()

    records = load(user_path(args.log))
    shots = [r for r in records if r.get("event") == "shot_confirmed"]
    decisions = [r for r in records if r.get("event") == "decision"]
    motion = [r for r in records if r.get("event") == "motion"]

    if not shots and not decisions:
        raise SystemExit("Log contains no shots or decisions yet.")

    print("=" * 68)
    print(f"DODGE REPORT  -  {args.log}")
    print("=" * 68)

    print(f"\nShots confirmed: {len(shots)}")
    if shots:
        late = [s for s in shots if s.get("verdict") == "DETECTED_TOO_LATE"]
        speeds = [s["speed"] for s in shots if s.get("speed")]
        ages = [s["age_when_confirmed"] for s in shots if s.get("age_when_confirmed")]
        reach = [s["time_to_reach"] for s in shots if s.get("time_to_reach") is not None]
        from_enemy = sum(1 for s in shots if s.get("from_enemy"))

        print(f"  seen too late to dodge : {len(late)} ({len(late) / len(shots) * 100:.0f}%)")
        print(f"  traced to an enemy box : {from_enemy} ({from_enemy / len(shots) * 100:.0f}%)")

        # Shots taken before the usual evidence had arrived, because waiting
        # would have cost the dodge. If these are mostly still too late the
        # confirmation gate was never the bottleneck - the capture rate is.
        urgent = [s for s in shots if s.get("urgent")]
        if urgent:
            urgent_late = sum(1 for s in urgent if s.get("verdict") == "DETECTED_TOO_LATE")
            saved = len(urgent) - urgent_late
            print(f"  confirmed early to make it : {len(urgent)} "
                  f"({len(urgent) / len(shots) * 100:.0f}%), "
                  f"{saved} of them in time")

        # How much of the deficit was the tracker's to give back. A shot that
        # needed more time than confirmation cost was never winnable, however
        # fast the detector runs; separating the two stops tuning effort going
        # into shots that physics had already decided.
        winnable = hopeless = 0
        for shot in late:
            clear = shot.get("time_to_clear_hitbox")
            arrival = shot.get("time_to_reach")
            age = shot.get("age_when_confirmed")
            if clear is None or arrival is None or age is None:
                continue
            deficit = clear - arrival
            if deficit <= 0:
                continue
            if deficit < age:
                winnable += 1
            else:
                hopeless += 1
        if winnable or hopeless:
            print(f"    of those, winnable   : {winnable} "
                  f"(faster confirmation would have saved them)")
            print(f"    undodgeable anyway   : {hopeless} "
                  f"(needed more time than detection ever costs)")

        # Crediting a shot to an enemy confirms it in 3 frames instead of 5.
        # When this ratio is low the bot is spending ~2 extra frames on every
        # shot, which is the difference between dodging and being hit.
        reasons = {}
        for shot in shots:
            reason = shot.get("origin_reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        if len(reasons) > 1 or "unknown" not in reasons:
            print("  why not credited:")
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                if reason == "enemy":
                    continue
                label = {
                    "no_enemy_detected": "no enemy box visible when the shot appeared",
                    "wrong_direction": "shot was not travelling away from the enemy",
                }.get(reason, reason)
                if reason.startswith("too_far"):
                    label = f"nearest enemy was {reason.split('_')[-1]} away (raise origin.spawn_radius_tiles)"
                print(f"    {count:4d}  {label}")
        if speeds:
            speeds_sorted = sorted(speeds)
            print(f"  speed  median/max      : {speeds_sorted[len(speeds) // 2]:.0f} / "
                  f"{speeds_sorted[-1]:.0f} px/s")
        if ages:
            print(f"  time to confirm a shot : {sum(ages) / len(ages) * 1000:.0f} ms average")
        if reach:
            print(f"  time left on confirm   : {sum(reach) / len(reach) * 1000:.0f} ms average")

    if motion:
        stuck = [m for m in motion if m.get("stuck")]
        speeds = sorted(m["player_speed"] for m in motion if m.get("player_speed"))
        efficiencies = sorted(m["efficiency"] for m in motion if m.get("efficiency") is not None)
        pinned = [m for m in motion if any(m.get("boundary") or [0, 0])]

        print(f"\nMovement samples: {len(motion)}")
        print(f"  reported stuck         : {len(stuck)} ({len(stuck) / len(motion) * 100:.0f}%)")
        print(f"  against a map edge     : {len(pinned)} ({len(pinned) / len(motion) * 100:.0f}%)")
        if speeds:
            print(f"  player speed min/med/max: {speeds[0]:.0f} / "
                  f"{speeds[len(speeds) // 2]:.0f} / {speeds[-1]:.0f} px/s")
        if efficiencies:
            print(f"  move efficiency med    : {efficiencies[len(efficiencies) // 2]:.2f}")
        # The speed estimate is the thing most likely to be wrong, and when it
        # is, the bot reads as permanently stuck and stops pursuing anything.
        if speeds and speeds[-1] > speeds[0] * 2.2:
            print("  ^^ speed estimate swings by more than 2x. If 'stuck' is also high, the "
                  "estimate is inflated and the stall detector is firing during normal walking.")

    print(f"\nDecisions taken while under fire: {len(decisions)}")
    if decisions:
        verdicts = {}
        for record in decisions:
            verdicts[record.get("verdict", "?")] = verdicts.get(record.get("verdict", "?"), 0) + 1
        total = len(decisions)
        order = ["DODGED", "IMPOSSIBLE", "BLOCKED", "OVERRULED"]
        for name in order + [k for k in verdicts if k not in order]:
            count = verdicts.get(name, 0)
            if not count:
                continue
            print(f"  {name:12s} {count:5d}  {count / total * 100:5.1f}%  {bar(count, total)}")

        print("\nWhat to change:")
        advice = []
        if verdicts.get("OVERRULED"):
            advice.append("  OVERRULED -> a clean escape existed but lost to the tactical "
                          "vector. Lower dodge.tactical_weight.")
        if verdicts.get("BLOCKED"):
            advice.append("  BLOCKED -> escapes existed but every one hit a wall or the map "
                          "edge. The bot is fighting from cramped positions; if the walls were "
                          "imaginary, check the wall model, otherwise lower dodge.wall_penalty "
                          "so it accepts scraping a wall over taking the hit.")
        late_count = sum(1 for s in shots if s.get("verdict") == "DETECTED_TOO_LATE")
        if shots and late_count > len(shots) * 0.3:
            advice.append("  Many shots seen too late -> lower tracking.min_confirm_hits to 2, "
                          "or raise the emulator framerate so 3 frames cost less time.")
        if verdicts.get("IMPOSSIBLE", 0) > total * 0.5:
            advice.append("  Mostly IMPOSSIBLE -> shots are arriving faster than the brawler can "
                          "clear its own hitbox, which usually means point-blank range. This is "
                          "a positioning problem, not a dodge problem.")
        print("\n".join(advice) if advice else "  Nothing obviously misconfigured.")

    if args.detail:
        failures = [r for r in decisions if r.get("verdict") != "DODGED"]
        print(f"\n{'=' * 68}\nFAILED DECISIONS (showing up to {args.limit})\n{'=' * 68}")
        for record in failures[:args.limit]:
            print(f"\n[t={record.get('t')}s] {record.get('verdict')}  "
                  f"tti={record.get('time_to_impact')}s  "
                  f"player_speed={record.get('player_speed')}px/s")
            for threat in record.get("threats", []):
                print(f"    threat #{threat['id']}: {threat['speed']:.0f} px/s, "
                      f"impact in {threat['tti'] * 1000:.0f} ms, confidence {threat['conf']}")
            candidates = record.get("candidates")
            if candidates:
                stay = record.get("stay_score")
                print(f"    stay score: {stay}")
                ranked = sorted(candidates, key=lambda c: c["score"])[:5]
                for candidate in ranked:
                    flags = []
                    if candidate.get("wall"):
                        flags.append("wall")
                    if candidate.get("edge"):
                        flags.append("map-edge")
                    angle = math.degrees(math.atan2(candidate["dir"][1], candidate["dir"][0]))
                    label = "stay" if candidate["dir"] == [0, 0] else f"{angle:6.0f} deg"
                    print(f"      {label:10s} score {candidate['score']:7.3f}  "
                          f"hits {candidate['hits']}  {' '.join(flags)}")

    print()


if __name__ == "__main__":
    main()
