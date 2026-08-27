"""One history file, one set of numbers.

The dashboard, the History tab and the Profile all read cfg/match_history.csv
and all report "how many matches, how many won". They did not agree: the
summary counted 1918 matches at a 64.7% win rate while the profile counted
2281 at 54.4%, from the same rows. Two separate causes, one per view, so both
are pinned here - the point of the file is that the two totals match, whatever
the input.
"""
import sys

from _harness import Failures

sys.path.insert(0, ".")
from profile_stats import build_profile  # noqa: E402
from webui.services import WebDataService  # noqa: E402

report = Failures("history totals agree")


def summarise(rows):
    """The History/dashboard summary, built the way services.py builds it."""
    grouped = {}
    for row in rows:
        brawler = str(row.get("brawler_name", "")).strip()
        if not brawler:
            continue
        result = str(row.get("result", "")).strip().lower()
        item = grouped.setdefault(brawler, {"wins": 0, "losses": 0, "draws": 0,
                                            "total_matches": 0})
        if result == "victory":
            item["wins"] += 1
        elif result == "defeat":
            item["losses"] += 1
        elif result:
            item["draws"] += 1
        else:
            continue
        item["total_matches"] += 1

    items = [dict(stats, brawler=name) for name, stats in grouped.items()]
    return WebDataService._build_match_history_response(items)["summary"]


def row(brawler="mortis", result="victory", delta=8, at="2026-08-01 12:00:00"):
    return {"brawler_name": brawler, "result": result, "trophy_delta": str(delta),
            "current_trophies": "1000", "date_time": at, "new_winstreak": "0",
            "playstyle_name": "unified_dodge", "playstyle_gamemodes": "showdown"}


def compare(label, rows):
    summary = summarise(rows)
    profile = build_profile(rows)
    report.check(f"{label}: same match count",
                 summary["total_matches"], profile["matches"])
    report.check(f"{label}: same wins", summary["wins"], profile["wins"])
    report.check(f"{label}: same losses", summary["losses"], profile["losses"])
    report.check(f"{label}: same draws", summary["draws"], profile["draws"])
    report.check(f"{label}: same win rate", summary["win_rate"], profile["win_rate"])
    return summary, profile


report.section("a draw is a match that was played")
rows = [row(result="victory")] * 6 + [row(result="defeat")] * 2 + [row(result="draw")] * 2
summary, profile = compare("six wins, two losses, two draws", rows)
report.check("all ten are counted, not just the decided eight",
             summary["total_matches"], 10)
report.check("and the rate is over ten, not over eight",
             summary["win_rate"], 60.0)
# 6/8 would be 75.0 - the old number, and the reason the dashboard read high.
report.check("which is not the wins-and-losses-only figure",
             summary["win_rate"] == 75.0, False)

report.section("a blank line is not a match")
rows = [row(), row(), {"brawler_name": "", "result": "", "trophy_delta": "",
                       "current_trophies": "", "date_time": ""}]
summary, profile = compare("two matches and one empty row", rows)
report.check("the empty row is not counted anywhere",
             summary["total_matches"], 2)
report.check("and it did not land in the draw column", profile["draws"], 0)

report.section("results the file has never held before")
rows = [row(result="victory"), row(result="VICTORY"), row(result="Defeat"),
        row(result="surrender")]
summary, profile = compare("odd casing and an unknown result", rows)
report.check("an unrecognised result still counts as a match played",
             summary["total_matches"], 4)

report.section("nothing at all")
summary, profile = compare("an empty file", [])
report.check("no matches means no rate rather than a division by zero",
             summary["win_rate"], 0.0)

report.section("the real history file, if it is here")
try:
    import csv
    with open("cfg/match_history.csv", newline="", encoding="utf-8-sig") as handle:
        real = list(csv.DictReader(handle))
except OSError:
    real = None

if real:
    summary, profile = compare(f"{len(real)} recorded rows", real)
    report.check("wins, losses and draws add up to the total",
                 summary["wins"] + summary["losses"] + summary["draws"],
                 summary["total_matches"])
else:
    report.check("no history file to check against, which is not a failure",
                 True, True)


sys.exit(report.finish())
