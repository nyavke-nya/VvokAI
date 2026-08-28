"""Trophies without an API token, and nothing that sells anything.

Two reports from the same screenshot. A brawler pushed from 262 to 871 while
the panel went on saying 262, with no trophy history to show for it. And a
modal headed "Unlock Premium Features" pointing at a paid Discord channel, in
a fork that has no paid tier.

The first one had three separate causes, all of which only bite without the
paid module - which is why it looked like an API problem.
"""
import json
import os
import sys
import tempfile

from _harness import Failures

sys.path.insert(0, ".")
from trophy_observer import TrophyObserver  # noqa: E402
from utils import resolve_project_path  # noqa: E402
from webui.services import WebDataService  # noqa: E402

report = Failures("trophies and the paywall")

PLAYSTYLE = {"name": "unified_dodge", "gamemodes": ["solo"], "brawlers": ["shelly"]}


def observer(seed):
    """A TrophyObserver writing to a scratch file, seeded like the bot seeds it."""
    obs = object.__new__(TrophyObserver)
    TrophyObserver.__init__(obs)
    obs.history_file = os.path.join(tempfile.mkdtemp(), "match_history.csv")
    obs.match_history = []
    obs.last_sent_index = 0
    obs.send_results_to_api = lambda: None
    obs.save_history = lambda: None
    obs.current_trophies = seed
    obs.current_wins = 0
    obs.win_streak = 0
    return obs


report.section("a match that was played gets recorded, whatever the count was")
# add_trophies used to raise on both of these, and end_game does not catch it,
# so one bad queue entry meant no history row and no queue advance - silently.
for seed, label in ((262, "a real count"), (0, "zero"),
                    (None, "never seeded"), ("", "an empty string")):
    obs = observer(seed)
    try:
        obs.add_trophies(obs.parse_game_result("victory"), "shelly", PLAYSTYLE)
        wrote = len(obs.match_history) == 1
    except Exception as exc:
        wrote = f"{type(exc).__name__}: {exc}"
    report.check(f"{label} still records the match", wrote, True)

obs = observer(262)
obs.add_trophies(obs.parse_game_result("victory"), "shelly", PLAYSTYLE)
row = obs.match_history[-1]
report.check("the row carries the count the match started from",
             row["current_trophies"], 262)
report.check("and a delta the History chart can add to it",
             isinstance(row["trophy_delta"], int), True)

# "истории кубков нет" is what an unreadable current_trophies column looks
# like: the chart drops every row it cannot place on an axis.
obs = observer(None)
obs.add_trophies(obs.parse_game_result("victory"), "shelly", PLAYSTYLE)
report.check("an unseeded count still writes a plottable number, not None",
             isinstance(obs.match_history[-1]["current_trophies"], (int, float)), True)


report.section("ten wins with no API move the count")
obs = observer(262)
for _ in range(10):
    obs.add_trophies(obs.parse_game_result("victory"), "shelly", PLAYSTYLE)
report.check("the total went up", obs.current_trophies > 262, True)
report.check("every match is in the history", len(obs.match_history), 10)
report.check("and the streak counted", obs.win_streak, 10)


report.section("the panel shows what the bot wrote, running or stopped")


class Runtime:
    def __init__(self, running):
        self.running = running

    def get_status(self):
        return {"is_running": self.running, "state": "running" if self.running else "idle"}


def entry(trophies):
    return {"brawler": "shelly", "type": "trophies", "trophies": trophies, "wins": 0,
            "push_until": 1000, "automatically_pick": True, "win_streak": 0}


QUEUE = resolve_project_path("latest_brawler_data.json")
backup = QUEUE.read_text(encoding="utf-8") if QUEUE.exists() else None
try:
    for running in (True, False):
        svc = object.__new__(WebDataService)
        svc.runtime_manager = Runtime(running)
        svc._latest_version_cache = None
        svc._runtime_queue_mtime = None
        svc._queue_items = [entry(262)]              # the panel's memory, pre-run
        QUEUE.write_text(json.dumps([entry(871)], indent=4), encoding="utf-8")

        shown = svc.get_queue_data()[0]["trophies"]
        state = "running" if running else "stopped"
        report.check(f"with the bot {state}, the panel reads the file", shown, 871)

    # And the other half of it: an edit made after the run must not write the
    # panel's stale number back over what the bot recorded.
    svc = object.__new__(WebDataService)
    svc.runtime_manager = Runtime(False)
    svc._latest_version_cache = None
    svc._runtime_queue_mtime = None
    svc._queue_items = [entry(262)]
    QUEUE.write_text(json.dumps([entry(871)], indent=4), encoding="utf-8")
    svc.get_queue_data()
    report.check("so a later save cannot throw the run away",
                 svc._queue_items[0]["trophies"], 871)

    # Saving records its own write, or the next read reloads what it just put
    # down and the two fight over every edit.
    svc.save_queue_data([entry(400)])
    report.check("saving remembers its own write",
                 svc._runtime_queue_mtime == QUEUE.stat().st_mtime, True)
    report.check("and the saved value survives a read", svc.get_queue_data()[0]["trophies"], 400)
finally:
    if backup is not None:
        QUEUE.write_text(backup, encoding="utf-8")
    elif QUEUE.exists():
        QUEUE.unlink()


report.section("the API refresh asks whether anything can answer")
_utils = open("utils.py", encoding="utf-8").read()
_refresh = _utils[_utils.index("def api_update_brawler_data"):]
_refresh = _refresh[:_refresh.index("\ndef ", 1)]
# It used to be `if not early_access: return` - the paid module - even though
# every other stats call in the file falls back to the public API.
report.check("a missing paid module is not the end of it",
             "if not early_access:\n        return" in _refresh, False)
report.check("the public API is consulted instead", "is_available" in _refresh, True)


report.section("nothing in the panel sells anything")
_app = open("static/js/app.js", encoding="utf-8").read()
_i18n = open("static/js/i18n.js", encoding="utf-8").read()
_discord = open("discord_bot.py", encoding="utf-8").read()

for phrase in ("Unlock Premium Features", "Get Early Access", "Early Access Feature",
               "requires the", "#how-to-get-early-access"):
    report.check(f"the panel never says {phrase!r}", phrase in _app, False)

report.check("no link to the paid channel anywhere in the panel",
             "1233146889843769417" in _app, False)
report.check("nor in the Discord bot's help", "1233146889843769417" in _discord, False)
report.check("which no longer marks commands Early Access Only",
             "Early Access Only" in _discord, False)
report.check("and the translations for that modal went with it",
             "Открыть платные функции" in _i18n, False)

report.check("what is left points at the free token instead",
             "goToApiTokenSetting" in _app, True)
report.check("and says so in Russian too",
             "бесплатный API-токен" in _i18n, True)


sys.exit(report.finish())
