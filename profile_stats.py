"""What the match history adds up to.

The history file already records every match: when, which brawler, the result
and the trophy change. Nobody wants to read 1200 rows of that, so this reduces
it to the handful of numbers people actually ask about - how much have I
played, is it working, which brawler is carrying it.

Everything is derived, nothing is stored. That matters: the file is the only
record, so a profile can never disagree with it, and deleting the file resets
the profile rather than leaving a stale copy behind.

Stdlib only, and deliberately so - pandas was removed from this project on
purpose and reading a few thousand rows does not need it back.
"""

from datetime import datetime, timedelta

# Rows closer together than this are treated as one sitting. Long enough to
# cover queueing, a lobby and a slow match; short enough that overnight is
# clearly two sessions rather than one very long one.
SESSION_GAP_MINUTES = 20

# Counted for a match with no other evidence of how long it took. Only used for
# the last match of a session, which has no following row to measure against.
ASSUMED_MATCH_MINUTES = 3.0


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


def build_profile(rows, now=None):
    """Reduce raw history rows to a profile. Safe on empty or damaged input."""
    now = now or datetime.now()

    matches = []
    for row in rows or []:
        stamp = _parse_time(row.get("date_time"))
        if stamp is None:
            # A row without a usable timestamp still counts as a match; it just
            # cannot take part in anything time-based.
            stamp = None
        matches.append({
            "at": stamp,
            "brawler": str(row.get("brawler_name") or "").strip(),
            "result": str(row.get("result") or "").strip().lower(),
            "delta": _number(row.get("trophy_delta")),
            "trophies": _number(row.get("current_trophies")),
        })

    profile = {
        "matches": len(matches),
        "wins": 0, "losses": 0, "draws": 0,
        "win_rate": 0.0,
        "trophies_net": 0, "trophies_won": 0, "trophies_lost": 0,
        "matches_today": 0, "matches_week": 0,
        "sessions": 0, "play_minutes": 0,
        "best_brawler": None, "worst_brawler": None,
        "most_played": None,
        "first_played": None, "last_played": None,
        "current_streak": 0, "best_streak": 0,
    }
    if not matches:
        return profile

    per_brawler = {}
    streak = 0
    for match in matches:
        if match["result"] == "victory":
            profile["wins"] += 1
            streak += 1
            profile["best_streak"] = max(profile["best_streak"], streak)
        elif match["result"] == "defeat":
            profile["losses"] += 1
            streak = 0
        else:
            profile["draws"] += 1
            # A draw breaks nothing and continues nothing.

        delta = match["delta"]
        profile["trophies_net"] += delta
        if delta > 0:
            profile["trophies_won"] += delta
        else:
            profile["trophies_lost"] -= delta

        name = match["brawler"] or "unknown"
        entry = per_brawler.setdefault(name, {"brawler": name, "matches": 0,
                                              "wins": 0, "net": 0})
        entry["matches"] += 1
        entry["net"] += delta
        if match["result"] == "victory":
            entry["wins"] += 1

    profile["current_streak"] = streak
    profile["win_rate"] = round(profile["wins"] / len(matches) * 100, 1)

    stamps = sorted(m["at"] for m in matches if m["at"] is not None)
    if stamps:
        profile["first_played"] = stamps[0].isoformat(timespec="seconds")
        profile["last_played"] = stamps[-1].isoformat(timespec="seconds")

        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week = now - timedelta(days=7)
        profile["matches_today"] = sum(1 for s in stamps if s >= midnight)
        profile["matches_week"] = sum(1 for s in stamps if s >= week)

        # Time played, measured rather than assumed wherever possible: the gap
        # to the next match IS how long that match took, as long as the two
        # belong to the same sitting.
        gap = timedelta(minutes=SESSION_GAP_MINUTES)
        minutes = 0.0
        sessions = 1
        for earlier, later in zip(stamps, stamps[1:]):
            step = later - earlier
            if step <= gap:
                minutes += step.total_seconds() / 60.0
            else:
                sessions += 1
                minutes += ASSUMED_MATCH_MINUTES
        minutes += ASSUMED_MATCH_MINUTES  # the final match of the last session
        profile["sessions"] = sessions
        profile["play_minutes"] = int(round(minutes))

    ranked = sorted(per_brawler.values(), key=lambda e: e["net"], reverse=True)
    for entry in ranked:
        entry["win_rate"] = round(entry["wins"] / entry["matches"] * 100, 1)
    profile["best_brawler"] = ranked[0] if ranked else None
    profile["worst_brawler"] = ranked[-1] if len(ranked) > 1 else None
    profile["most_played"] = max(per_brawler.values(),
                                 key=lambda e: e["matches"], default=None)
    return profile
