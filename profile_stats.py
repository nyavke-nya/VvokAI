"""What the match history adds up to.

The history file records every match: when, which brawler, the result, the
trophy change, the playstyle and the modes it covers. Nobody wants to read
several thousand rows of that, so this reduces it to the things people actually
ask - how much have I played, is it working, which brawler is carrying it, and
when does it go well.

Everything is derived, nothing is stored. That matters: the file is the only
record, so a profile can never disagree with it, and deleting the file resets
the profile rather than leaving a stale copy behind.

Stdlib only, and deliberately so - pandas was removed from this project on
purpose, and reading a few thousand CSV rows is not a reason to bring it back.
"""

from datetime import datetime, timedelta

# Rows closer together than this belong to one sitting. Long enough to cover
# queueing, a lobby and a slow match; short enough that overnight is clearly
# two sessions rather than one very long one.
SESSION_GAP_MINUTES = 20

# Charged for the last match of a session, which has no following row to
# measure against and so cannot be timed the way the others are.
ASSUMED_MATCH_MINUTES = 3.0

# How many recent results the form strip shows.
FORM_LENGTH = 25

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")


def _parse_time(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _number(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _rate(part, whole):
    return round(part / whole * 100, 1) if whole else 0.0


def _read(rows):
    """Raw rows to a clean list of matches.

    Only genuinely empty lines are dropped. The history file ends up with them
    - a stray newline, an interrupted write - and one was being read as a
    match with no brawler and no result, which landed in the draw column and
    put the profile one match ahead of every other view of the same file.
    """
    matches = []
    for row in rows or []:
        brawler = str(row.get("brawler_name") or "").strip()
        result = str(row.get("result") or "").strip().lower()
        if not brawler and not result:
            continue

        modes = str(row.get("playstyle_gamemodes") or "").strip()
        matches.append({
            "at": _parse_time(row.get("date_time")),
            "brawler": brawler or "unknown",
            "result": result,
            "delta": _number(row.get("trophy_delta")),
            "trophies": _number(row.get("current_trophies")),
            "playstyle": (str(row.get("playstyle_name") or "").strip() or "unknown"),
            "modes": [m for m in modes.split("|") if m] or ["unknown"],
        })
    return matches


def _group(matches, key, many=False):
    """Aggregate matches under some key - brawler, playstyle or gamemode."""
    groups = {}
    for match in matches:
        names = key(match) if many else [key(match)]
        for name in names:
            entry = groups.setdefault(name, {
                "name": name, "matches": 0, "wins": 0, "losses": 0, "net": 0,
            })
            entry["matches"] += 1
            entry["net"] += match["delta"]
            if match["result"] == "victory":
                entry["wins"] += 1
            elif match["result"] == "defeat":
                entry["losses"] += 1
    for entry in groups.values():
        entry["win_rate"] = _rate(entry["wins"], entry["matches"])
        entry["net_per_match"] = round(entry["net"] / entry["matches"], 2)
    return sorted(groups.values(), key=lambda e: -e["matches"])


def _sessions(stamps):
    """(count, minutes played). Time is measured wherever it can be."""
    if not stamps:
        return 0, 0
    gap = timedelta(minutes=SESSION_GAP_MINUTES)
    minutes = 0.0
    count = 1
    for earlier, later in zip(stamps, stamps[1:]):
        step = later - earlier
        if step <= gap:
            # The gap to the next match IS how long that match took.
            minutes += step.total_seconds() / 60.0
        else:
            count += 1
            minutes += ASSUMED_MATCH_MINUTES
    minutes += ASSUMED_MATCH_MINUTES
    return count, int(round(minutes))


def _streaks(matches):
    """(current, best winning run, worst losing run). Draws break neither."""
    current = best_win = best_loss = 0
    run_win = run_loss = 0
    for match in matches:
        if match["result"] == "victory":
            run_win += 1
            run_loss = 0
            best_win = max(best_win, run_win)
        elif match["result"] == "defeat":
            run_loss += 1
            run_win = 0
            best_loss = max(best_loss, run_loss)
        current = run_win if run_win else -run_loss
    return current, best_win, best_loss


def _buckets(matches, key, size, label):
    """Matches, win rate and trophies for every slot of a fixed-size cycle."""
    found = {}
    for match in matches:
        entry = found.setdefault(key(match), {"matches": 0, "wins": 0, "net": 0})
        entry["matches"] += 1
        entry["net"] += match["delta"]
        if match["result"] == "victory":
            entry["wins"] += 1
    out = []
    for index in range(size):
        slot = found.get(index, {"matches": 0, "wins": 0, "net": 0})
        out.append({
            label: index if label == "hour" else WEEKDAYS[index],
            "matches": slot["matches"],
            "win_rate": _rate(slot["wins"], slot["matches"]),
            "net": slot["net"],
        })
    return out


def empty_profile():
    return {
        "matches": 0, "wins": 0, "losses": 0, "draws": 0, "win_rate": 0.0,
        "trophies_net": 0, "trophies_won": 0, "trophies_lost": 0,
        "net_per_match": 0.0, "best_match": 0, "worst_match": 0,
        "matches_today": 0, "matches_week": 0, "trophies_today": 0,
        "matches_per_day": 0.0, "busiest_day": None,
        "sessions": 0, "play_minutes": 0, "matches_per_session": 0.0,
        "current_streak": 0, "best_streak": 0, "worst_streak": 0,
        "first_played": None, "last_played": None, "days_active": 0,
        "brawlers": [], "playstyles": [], "gamemodes": [],
        "by_hour": [], "by_weekday": [], "form": [],
        "best_brawler": None, "worst_brawler": None, "most_played": None,
    }


def build_profile(rows, now=None):
    """Reduce raw history rows to a profile. Safe on empty or damaged input."""
    now = now or datetime.now()
    matches = _read(rows)
    profile = empty_profile()
    if not matches:
        return profile

    profile["matches"] = len(matches)
    for match in matches:
        result = match["result"]
        if result == "victory":
            profile["wins"] += 1
        elif result == "defeat":
            profile["losses"] += 1
        else:
            profile["draws"] += 1

        delta = match["delta"]
        profile["trophies_net"] += delta
        if delta > 0:
            profile["trophies_won"] += delta
        else:
            profile["trophies_lost"] -= delta
        profile["best_match"] = max(profile["best_match"], delta)
        profile["worst_match"] = min(profile["worst_match"], delta)

    profile["win_rate"] = _rate(profile["wins"], profile["matches"])
    profile["net_per_match"] = round(profile["trophies_net"] / profile["matches"], 2)

    current, best_win, best_loss = _streaks(matches)
    profile["current_streak"] = current
    profile["best_streak"] = best_win
    profile["worst_streak"] = best_loss

    profile["brawlers"] = _group(matches, lambda m: m["brawler"])
    profile["playstyles"] = _group(matches, lambda m: m["playstyle"])
    profile["gamemodes"] = _group(matches, lambda m: m["modes"], many=True)

    by_net = sorted(profile["brawlers"], key=lambda e: -e["net"])
    profile["best_brawler"] = by_net[0] if by_net else None
    profile["worst_brawler"] = by_net[-1] if len(by_net) > 1 else None
    profile["most_played"] = profile["brawlers"][0] if profile["brawlers"] else None

    timed = [m for m in matches if m["at"] is not None]
    # Most recent first, so the form strip reads left to right as "just now,
    # before that, before that".
    recent = sorted(timed, key=lambda m: m["at"], reverse=True)
    profile["form"] = [
        {"result": m["result"], "delta": m["delta"], "brawler": m["brawler"]}
        for m in recent[:FORM_LENGTH]
    ]

    stamps = sorted(m["at"] for m in timed)
    if not stamps:
        return profile

    profile["first_played"] = stamps[0].isoformat(timespec="seconds")
    profile["last_played"] = stamps[-1].isoformat(timespec="seconds")

    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = now - timedelta(days=7)
    profile["matches_today"] = sum(1 for s in stamps if s >= midnight)
    profile["matches_week"] = sum(1 for s in stamps if s >= week)
    profile["trophies_today"] = sum(m["delta"] for m in timed if m["at"] >= midnight)

    days = {}
    for match in timed:
        days.setdefault(match["at"].date(), []).append(match)
    profile["days_active"] = len(days)
    profile["matches_per_day"] = round(len(timed) / len(days), 1)
    busiest = max(days.items(), key=lambda kv: len(kv[1]))
    profile["busiest_day"] = {
        "date": busiest[0].isoformat(),
        "matches": len(busiest[1]),
        "net": sum(m["delta"] for m in busiest[1]),
    }

    sessions, minutes = _sessions(stamps)
    profile["sessions"] = sessions
    profile["play_minutes"] = minutes
    profile["matches_per_session"] = round(len(timed) / sessions, 1) if sessions else 0.0

    # When it plays, and how it does at those times. Neither number means much
    # alone: playing a lot at 3am says nothing until the win rate is beside it.
    profile["by_hour"] = _buckets(timed, lambda m: m["at"].hour, 24, "hour")
    profile["by_weekday"] = _buckets(timed, lambda m: m["at"].weekday(), 7, "day")
    return profile
