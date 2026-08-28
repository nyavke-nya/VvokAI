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

from _harness import Failures, read_source

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
_utils = read_source("utils.py")
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
_discord = read_source("discord_bot.py")

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


report.section("power level was the last thing still behind the paid module")
# `power_level = None if not early_access else ...` - so the history column sat
# empty for everyone on the free path, even though Supercell publishes `power`
# in the same payload as the trophies. The names come back upper case and
# spaced, so the lookup has to normalise both sides.
from brawl_api import get_brawler_power, get_brawler_stats as _api_stats  # noqa: E402
import stage_manager as _sm  # noqa: E402

_INFO = {"brawlers": [
    {"name": "EL PRIMO", "trophies": 871, "power": 11},
    {"name": "MR. P", "trophies": 262, "power": 9},
    {"name": "8-BIT", "trophies": 500},
    {"name": "SHELLY", "trophies": 300, "power": "7"},
]}

report.check("a spaced upper-case name still finds its brawler",
             get_brawler_power(_INFO, "elprimo"), 11)
report.check("so does one with a full stop in it", get_brawler_power(_INFO, "mrp"), 9)
report.check("a power level written as text is still a number",
             get_brawler_power(_INFO, "shelly"), 7)
report.check("a brawler with no power field reports nothing, not a guess",
             get_brawler_power(_INFO, "8bit"), None)
report.check("a brawler the account does not own reports nothing",
             get_brawler_power(_INFO, "nori"), None)
report.check("and no payload at all is not a crash",
             get_brawler_power(None, "shelly"), None)

report.check("reading the power level did not disturb the trophies",
             _api_stats(_INFO, "elprimo"), (871, None))

report.check("the caller's three-value form works on the free path",
             _sm.get_brawler_stats(_INFO, "elprimo", power_level=True), (871, None, 11))
report.check("and the two-value form is unchanged",
             _sm.get_brawler_stats(_INFO, "elprimo"), (871, None))
report.check("an unknown power still fills the third slot",
             _sm.get_brawler_stats(_INFO, "8bit", power_level=True), (500, None, None))

_stage = read_source("stage_manager.py")
report.check("the paid gate is gone from the call site",
             "None if not early_access else get_brawler_stats" in _stage, False)
report.check("and a missing player tag never reaches the network",
             "if not self.player_tag:" in _stage and "return None" in _stage, True)



report.section("throwers aim at a point, so a fixed drag lands in a fixed place")
# "метатели по себе атакают". The attack control is a stick: for most brawlers
# the drag ANGLE picks the direction and its length means nothing, which is
# what aimed_attack was written on and what dodge_config still documents. For
# a thrower the length is the RANGE, so one constant drag lobbed the shot the
# same distance no matter where the enemy stood.
import json as _json  # noqa: E402

from play import Play  # noqa: E402

_INFO = _json.load(open("cfg/brawlers_info.json", encoding="utf-8"))

report.check("barley is flagged as aiming at a point",
             Play.has_placed_attack("barley", _INFO), True)
report.check("so is tick", Play.has_placed_attack("tick", _INFO), True)
report.check("shelly is not", Play.has_placed_attack("shelly", _INFO), False)
report.check("nor is piper", Play.has_placed_attack("piper", _INFO), False)
report.check("a brawler nobody has heard of is not, rather than a crash",
             Play.has_placed_attack("nonesuch", _INFO), False)

# Hank and Mico ignore walls too, but they do not place their shot - the flag
# had to be its own thing rather than a reuse of ignore_walls_for_attacks.
report.check("ignore_walls_for_attacks was not reused for this",
             Play.has_placed_attack("mico", _INFO), False)
report.check("every brawler carries the flag either way",
             all("placed_attack" in entry for entry in _INFO.values()), True)
report.check("and nothing else lost a field",
             all({"attack_range", "safe_range", "hold_attack"} <= set(entry)
                 for entry in _INFO.values()), True)


class _Config:
    aim_placed_attacks = False
    aim_swipe_radius = 130.0
    aim_swipe_full_radius = 200.0
    aim_swipe_min_radius = 40.0
    aim_swipe_hold = 0.02


class _Window:
    scale_factor = 1.0


def _play(brawler="barley", attack_range=469):
    play = object.__new__(Play)
    play.brawlers_info = _INFO
    play.current_brawler = brawler
    play.window_controller = _Window()
    play.last_player_box = [900, 900, 1000, 1000]
    play.brawler_ranges = {brawler: [200, attack_range, 500]}
    return play


_cfg = _Config()
_p = _play()
_centre = _p.get_player_hit_circle(_p.last_player_box)[0]

report.check("off by default, so the shot is tapped and the game aims it",
             _p.placed_attack_radius((_centre[0] + 400, _centre[1]), _cfg), None)

_cfg.aim_placed_attacks = True
report.check("switched on, a target at full range asks for the full drag",
             _p.placed_attack_radius((_centre[0] + 469, _centre[1]), _cfg), 200.0)
report.check("half way out asks for half the drag",
             round(_p.placed_attack_radius((_centre[0] + 234.5, _centre[1]), _cfg), 1), 100.0)
report.check("beyond maximum range does not ask for more than the stick has",
             _p.placed_attack_radius((_centre[0] + 5000, _centre[1]), _cfg), 200.0)
report.check("an enemy stood on top of us still clears the dead zone",
             _p.placed_attack_radius(_centre, _cfg), 40.0)

_blind = _play()
_blind.last_player_box = None
report.check("with no player box there is nothing to measure from",
             _blind.placed_attack_radius((100, 100), _cfg), None)

_nameless = _play()
_nameless.current_brawler = None
report.check("nor with no brawler", _nameless.placed_attack_radius((100, 100), _cfg), None)

_dodge_cfg = open("cfg/dodge_config.toml", encoding="utf-8").read()
report.check("the config says the drag length is the range for throwers",
             "the drag length is the" in _dodge_cfg or "range" in _dodge_cfg, True)
report.check("and ships the aimed version off",
             "aim_placed_attacks = false" in _dodge_cfg, True)

sys.exit(report.finish())
