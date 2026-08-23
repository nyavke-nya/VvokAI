"""The profile, and the clock that decides when to stop playing."""
import sys
from datetime import datetime, timedelta

from _harness import Failures

from utils import load_toml_as_dict

from profile_stats import build_profile
from schedule_control import Schedule, parse_clock

report = Failures("profile and schedule")


def row(when, brawler, result, delta, trophies=500):
    return {"date_time": when.isoformat(), "brawler_name": brawler,
            "result": result, "trophy_delta": delta, "current_trophies": trophies}


NOW = datetime(2026, 8, 20, 15, 0, 0)
base = NOW - timedelta(hours=2)
rows = [
    row(base + timedelta(minutes=0), "shelly", "victory", 8),
    row(base + timedelta(minutes=5), "shelly", "victory", 9),
    row(base + timedelta(minutes=10), "shelly", "defeat", -4),
    row(base + timedelta(minutes=15), "colt", "victory", 7),
    # A gap far longer than a match: this starts a second session.
    row(base + timedelta(minutes=200), "colt", "draw", 0),
]

report.section("the numbers people actually ask about")
p = build_profile(rows, now=NOW)
report.check("matches", p["matches"], 5)
report.check("wins", p["wins"], 3)
report.check("losses", p["losses"], 1)
report.check("draws", p["draws"], 1)
report.check("win rate", p["win_rate"], 60.0)
report.check("net trophies", p["trophies_net"], 20)
report.check("won and lost counted apart", (p["trophies_won"], p["trophies_lost"]), (24, 4))

report.section("a long gap is a second sitting, not one long one")
report.check("sessions", p["sessions"], 2)
report.at_least("play time is measured, not invented", p["play_minutes"], 15)
report.at_most("and does not swallow the gap", p["play_minutes"], 40)

report.section("streaks")
report.check("best streak", p["best_streak"], 2)
report.check("a draw neither breaks nor extends it", p["current_streak"], 1)

report.section("per-brawler standing")
report.check("best by trophies", p["best_brawler"]["name"], "shelly")
report.check("most played", p["most_played"]["name"], "shelly")

report.section("the detail the profile page is built from")
report.check("trophies per match", p["net_per_match"], 4.0)
report.check("best single match", p["best_match"], 9)
report.check("worst single match", p["worst_match"], -4)
report.check("worst losing run", p["worst_streak"], 1)
report.check("days actually played", p["days_active"], 1)
report.check("busiest day is counted", p["busiest_day"]["matches"], 5)
report.check("brawlers listed", len(p["brawlers"]), 2)
report.check("busiest brawler first", p["brawlers"][0]["name"], "shelly")
report.check("hours cover the whole clock", len(p["by_hour"]), 24)
report.check("weekdays cover the whole week", len(p["by_weekday"]), 7)
report.check("form is newest first", p["form"][0]["result"], "draw")
report.check("form covers every match here", len(p["form"]), 5)
report.check("gamemodes are split apart", len(p["gamemodes"]) >= 1, True)

report.section("an empty profile still has every field the page reads")
from profile_stats import empty_profile
missing = sorted(set(empty_profile()) - set(p))
report.check("no field appears only when there is data", missing, [])

report.section("nothing to report is not a crash")
empty = build_profile([], now=NOW)
report.check("no matches", empty["matches"], 0)
report.check("no win rate", empty["win_rate"], 0.0)
report.check("no best brawler", empty["best_brawler"], None)

report.section("damaged rows are counted, not fatal")
broken = build_profile([{"date_time": "not a date", "brawler_name": "bea",
                         "result": "victory", "trophy_delta": "x"}], now=NOW)
report.check("the match still counts", broken["matches"], 1)
report.check("an unreadable delta reads as zero", broken["trophies_net"], 0)


report.section("the page reads the profile from where the app actually keeps it")
# The profile shipped once reading state.history, which does not exist - every
# other view reads state.bootstrap - so the tab rendered "no matches recorded"
# against 1224 of them. A string check, but it is the exact mistake that got
# through a rendered-in-a-browser test, because the probe supplied a fixture
# shaped the way the code expected rather than the way the app stores it.
app_js = open("static/js/app.js", encoding="utf-8").read()
report.check("it reads state.bootstrap.history.profile",
             "state.bootstrap.history.profile" in app_js, True)
report.check("and never a state.history that does not exist",
             "state.history &&" in app_js, False)
report.check("the tab is registered", 'profile: { label: "Profile"' in app_js, True)
report.check("the view container exists",
             'id="view-profile"' in open("templates/index.html", encoding="utf-8").read(),
             True)
report.check("and it is rendered with the rest", "    renderProfile();" in app_js, True)

report.section("reading a time off the config")
report.check("HH:MM", parse_clock("23:30"), 23 * 60 + 30)
report.check("a dot works too", parse_clock("8.05"), 8 * 60 + 5)
report.check("empty is not a time", parse_clock(""), None)
report.check("nonsense is not a time", parse_clock("bedtime"), None)
report.check("out of range is not a time", parse_clock("25:00"), None)

report.section("a quiet window that crosses midnight")
night = Schedule(stop_at="23:30", resume_at="08:00")
at = lambda h, m: datetime(2026, 8, 20, h, m)
report.check("just before it starts", night.in_quiet_hours(at(23, 29)), False)
report.check("just after it starts", night.in_quiet_hours(at(23, 31)), True)
report.check("the small hours", night.in_quiet_hours(at(3, 0)), True)
report.check("just before it lifts", night.in_quiet_hours(at(7, 59)), True)
report.check("just after it lifts", night.in_quiet_hours(at(8, 0)), False)
report.check("the middle of the day", night.in_quiet_hours(at(15, 0)), False)

report.section("a window inside one day")
day = Schedule(stop_at="09:00", resume_at="17:00")
report.check("before work", day.in_quiet_hours(at(8, 0)), False)
report.check("during work", day.in_quiet_hours(at(12, 0)), True)
report.check("after work", day.in_quiet_hours(at(18, 0)), False)

report.section("a lone stop time is a deadline, not a range")
# This is the one that shut a computer down. Setting 04:00 at 23:50 used to put
# the bot inside "quiet from 04:00 until midnight" immediately, so it stopped
# the moment it was configured - and with the shutdown box ticked, powered the
# machine off. It has to mean the NEXT 04:00.
lone = Schedule(stop_at="4:00")
started = datetime(2026, 8, 20, 23, 50)
report.check("not the moment it is set",
             lone.holding(now=datetime(2026, 8, 20, 23, 51), since=started)[0], False)
report.check("nor later that evening",
             lone.holding(now=datetime(2026, 8, 20, 23, 59), since=started)[0], False)
report.check("nor in the small hours before it",
             lone.holding(now=datetime(2026, 8, 21, 3, 59), since=started)[0], False)
report.check("and yes once it arrives",
             lone.holding(now=datetime(2026, 8, 21, 4, 0), since=started)[0], True)
report.check("a stop time later the same day still works",
             Schedule(stop_at="23:00").holding(
                 now=datetime(2026, 8, 20, 23, 1),
                 since=datetime(2026, 8, 20, 20, 0))[0], True)
report.check("without knowing when the run began it refuses to fire",
             lone.holding(now=datetime(2026, 8, 21, 5, 0), since=None)[0], False)

report.section("the times people actually type are understood")
# "400" used to parse as nothing at all, which silently disabled the schedule -
# indistinguishable from the feature not working.
for text, want in (("400", 240), ("0400", 240), ("4:00", 240), ("4.00", 240),
                   ("4", 240), ("2335", 1415), ("23:35", 1415)):
    report.check(f"{text!r} reads as {want}", parse_clock(text), want)
for bad in ("25:00", "12:99", "abc", ""):
    report.check(f"{bad!r} is refused", parse_clock(bad), None)

report.section("nothing configured is completely inert")
off = Schedule()
report.check("not active", off.active, False)
report.check("never holds", off.holding(now=at(3, 0), since=started)[0], False)

report.section("holding says why")
holding, reason = night.holding(now=at(2, 0), since=started)
report.check("it holds", holding, True)
report.check("and names the reason", reason, "quiet hours")


report.section("the session cap is gone, not merely hidden")
# A duration and a clock time answered the same question in different units,
# and nobody could say which won. Removed rather than left in the config for
# somebody to find later and wonder about.
runtime = open("webui/runtime.py", encoding="utf-8").read()
schedule_src = open("schedule_control.py", encoding="utf-8").read()
report.check("no session clock in the runtime", "_session_started" in runtime, False)
report.check("no cap in the schedule", "max_session_minutes" in schedule_src, False)
report.check("and none in the shipped config",
             "max_session_minutes" in open("cfg/bot_config.toml", encoding="utf-8").read(),
             False)

report.section("the schedule explains itself in words, not setting names")
app_js = open("static/js/app.js", encoding="utf-8").read()
# Looked for as markup, not as prose - the first version of this check matched
# the comment that explains why the label was changed.
report.check("no bare 'Session limit' label",
             "<span>Session limit</span>" in app_js, False)
for phrase in ("Pause at this time", "Start again at",
               "Time of day, 24 hour", "Leave empty to stay paused"):
    report.check(f"says {phrase!r}", phrase in app_js, True)


report.section("the schedule stops the bot, it does not merely pause it")
# Pausing was useless: a paused bot is still running, and a running bot treats
# "Brawl Stars is not open" as a crash and reopens it within a couple of
# seconds. Stopping is what makes closing the game stick.
runtime = open("webui/runtime.py", encoding="utf-8").read()
stop_fn = runtime[runtime.index("def should_stop"):runtime.index("def mark_running")]
pause_fn = runtime[runtime.index("def should_pause"):runtime.index("def should_stop")]
report.check("the schedule is consulted when deciding to stop",
             "self._schedule" in stop_fn, True)
report.check("and no longer when deciding to pause",
             "self._schedule" in pause_fn, False)

report.section("stopping closes the game, and in the right order")
main_src = open("main.py", encoding="utf-8").read()
stop = main_src[main_src.index("def stop_gracefully"):main_src.index("def close_game_on_stop")]
report.check("the game is closed on the way down", "close_brawl_stars()" in stop, True)
report.check("the crash watchdog is stopped first",
             stop.index("stop_crash_watchdog") < stop.index("close_brawl_stars"), True)
report.check("the switch is read, not assumed", "close_game_on_stop()" in stop, True)

report.section("and it comes back when the window opens")
report.check("something is watching for the window to open",
             "_watch_for_resume" in runtime, True)
report.check("it gives up when there is no resume time",
             "schedule.resume_at is None" in runtime, True)
report.check("and does not fight a run that is already going",
             'self.get_status()["state"] in {"running", "pausing"}' in runtime, True)

report.section("finishing the queue closes it too")
stage = open("stage_manager.py", encoding="utf-8").read()
done = stage[stage.index("all targets completed"):stage.index("ping_when_target_is_reached")]
report.check("closes the game when nothing is left to push",
             "close_brawl_stars()" in done, True)
# The call, not the word. The comment on that line explains why config_bool is
# avoided there, and an earlier version of this check matched the explanation.
report.check("without calling a name it does not import",
             "config_bool(" in done, False)

report.section("the app control exists and is survivable")
wc = open("window_controller.py", encoding="utf-8").read()
for name in ("def close_brawl_stars", "def open_brawl_stars"):
    report.check(f"{name} exists", name in wc, True)
close_fn = wc[wc.index("def close_brawl_stars"):wc.index("def open_brawl_stars")]
report.check("a failure to close is caught", "except Exception" in close_fn, True)


report.section("powering off is opt-in, and never on a manual stop")
main_src = open("main.py", encoding="utf-8").read()
stop = main_src[main_src.index("def stop_gracefully"):main_src.index("def close_game_on_stop")]
report.check("it asks whether the clock caused this", "schedule_hold_reason" in stop, True)
report.check("and only powers off when it did", "by_schedule and self.shutdown_when_done()" in stop, True)

helper = main_src[main_src.index("def shutdown_when_done"):main_src.index("def start_state_checker")]
report.check("the default is off", 'get("shutdown_when_done", False)' in helper, True)

utils_src = open("utils.py", encoding="utf-8").read()
fn = utils_src[utils_src.index("def shutdown_computer"):]
report.check("there is a grace period", "grace_seconds=60" in fn, True)
report.check("and it says how to cancel", "shutdown /a" in fn, True)
report.check("a failure to power off is survivable", "except Exception" in fn, True)

# The DEFAULT, not the live config - somebody who has ticked the box on this
# machine is not a test failure, and asserting against their settings file
# makes the suite fail for the wrong reason.
services = open("webui/services.py", encoding="utf-8").read()
report.check("the setting defaults to off",
             '"shutdown_when_done": ("bool", False)' in services, True)


report.section("running out of brawlers does not power the machine off")
stage_all = open("stage_manager.py", encoding="utf-8").read()
done_block = stage_all[stage_all.index("all targets completed"):stage_all.index("ping_when_target_is_reached")]
report.check("no shutdown on the finish path", "shutdown_computer" in done_block, False)


report.section("the brawler list is wound all the way to the top")
# Fourteen swipes left the view short when the selected brawler sat far down
# the list, so the search began halfway and everything above was invisible to
# it - indistinguishable from the brawler not existing.
lobby = open("lobby_automation.py", encoding="utf-8").read()
import re as _re2
swipes = int(_re2.search(r"SCROLL_TOP_SWIPES = (\d+)", lobby).group(1))
report.at_least("enough swipes to reach the top from anywhere", swipes, 18)


report.section("remembered walls follow the camera instead of drifting")
# Walls are re-detected on an interval and reused in between. Reused without
# compensation they stay at last-seen screen coordinates while the camera keeps
# panning, so at a 0.5s refresh and 330 px/s they end up 165 px - nearly five
# tiles - from the walls they describe. A box in the wrong place blocks a line
# of sight that is clear, and the bot will not shoot an enemy who has just
# stepped out of cover.
import play as _play

moved = _play.Play.shift_boxes([[480, 400, 520, 440]], (-165.0, 0.0))
report.check("a box slides with the camera", moved[0][:2], [315.0, 400.0])
report.check("and keeps its size",
             (moved[0][2] - moved[0][0], moved[0][3] - moved[0][1]), (40.0, 40.0))
report.check("no pan means no work", _play.Play.shift_boxes([[1, 2, 3, 4]], (0, 0)),
             [[1, 2, 3, 4]])
report.check("a malformed box is passed through rather than crashing",
             _play.Play.shift_boxes([[1, 2]], (5, 5)), [[1, 2]])
report.check("extra fields on a box survive",
             _play.Play.shift_boxes([[0, 0, 10, 10, "wall", 0.9]], (5, 0))[0][4:],
             ["wall", 0.9])

frame = open("play.py", encoding="utf-8").read()
reuse = frame[frame.index("if current_time - self.time_since_walls_checked"):
              frame.index("data = self.validate_game_data(data)")]
report.check("the reuse path actually shifts them", "shift_boxes" in reuse, True)
report.check("and records where the camera was when they were found",
             "self.last_walls_odometer = odometer" in reuse, True)


report.section("emotes go out on a timer, and only during a match")
play_src = open("play.py", encoding="utf-8").read()
emote = play_src[play_src.index("def send_emote_if_due"):play_src.index("def camera_odometer")]
report.check("it waits for the interval",
             "current_time - self.time_since_emote < self.emote_interval" in emote, True)
report.check("zero turns it off", "self.emote_interval <= 0" in emote, True)
report.check("it pauses between the two taps so the grid can open",
             "time.sleep(self.emote_open_delay)" in emote, True)
# On its own thread. Waiting for the grid on the main loop stops the bot
# reading the screen for a third of a second, which is several dodges.
report.check("and waits off the main loop", "threading.Thread" in emote, True)
report.check("without stacking threads if one is still going",
             "self._emote_thread.is_alive()" in emote, True)
report.check("the button is chosen at random", "random.choice" in emote, True)
report.check("it is only called in a match",
             'if state == "match":\n            self.send_emote_if_due' in play_src, True)

cfg = load_toml_as_dict("cfg/lobby_config.toml").get("emotes") or {}
report.check("the coordinates live in config, not in code", bool(cfg), True)
report.at_least("there are emotes to choose from", len(cfg.get("buttons") or []), 2)
# The grid's bottom-right cell is the chat button itself; clicking it closes
# the panel instead of sending anything, so it must not be in the list.
report.check("the chat button is not among them",
             list(cfg.get("bubble") or []) in [list(b) for b in cfg.get("buttons") or []],
             False)


report.section("an enemy half out of cover can already be shot")
# The line of sight used to be traced to the enemy's centre alone. A brawler is
# about ninety pixels wide, so someone stepping out from a wall was called
# unshootable for the ~140 ms it took their centre to follow their shoulder
# into the open - which is the delay before the bot opened fire.
import play as _play


class _Stub:
    scale_factor = 1.0


_p = object.__new__(_play.Play)
_p.enemy_exposure_radius = 40.0
_p.window_controller = _Stub()
_p.current_brawler = "shelly"
_p.brawlers_info = {"shelly": {}}
_p.can_attack_through_walls = lambda *a: False

_wall = [900, 400, 1000, 700]
_player = (500, 550)


def _first_shot(use_edges):
    for y in range(560, 60, -5):
        enemy = (1300, y)
        if use_edges:
            if _p.is_enemy_hittable(_player, enemy, [_wall], "attack"):
                return y
        elif not _play.Play.walls_block_line_of_sight(_player, enemy, [_wall]):
            return y
    return None


_edges, _centre = _first_shot(True), _first_shot(False)
report.check("fully behind cover is still no shot",
             _p.is_enemy_hittable(_player, (1300, 550), [_wall], "attack"), False)
report.check("in the clear is a shot",
             _p.is_enemy_hittable(_player, (1300, 100), [_wall], "attack"), True)
report.at_least("the edge test fires earlier than the centre test",
                _edges - _centre, 30)


report.section("a brawler switch that fails is retried, not abandoned")
# Selection was only ever attempted at the moment a target was reached. When it
# failed the bot carried on with the finished brawler - and since the queue head
# was then a brawler nobody was playing, its trophies never moved and its target
# was never met, so the moment never came again. The bot pushed a completed
# brawler for the rest of the session while the interface showed a different one.
stage_src = open("stage_manager.py", encoding="utf-8").read()
report.check("a failed switch is remembered",
             "self.brawler_needs_selecting = True" in stage_src, True)
report.check("and cleared once it takes",
             "self.brawler_needs_selecting = False" in stage_src, True)
start_game = stage_src[stage_src.index("def start_game"):stage_src.index("def click_star_drop")]
report.check("the lobby retries it", "self.brawler_needs_selecting and" in start_game, True)
report.check("without retrying a manual pick", 'head.get("automatically_pick")' in start_game, True)
report.check("the flag exists before any match",
             "self.brawler_needs_selecting = False\n        self.ping_when_stuck" in stage_src, True)


report.section("the brawler list is dragged beside the cards, not across them")
# 1700 was on the cards - their right edge is at about x=1696 - so every swipe
# started on a brawler, and a drag the game read as a tap opened whichever one
# it landed on. Colour variation over the band the swipes travel: 85 at x=1700,
# 3 from x=1760 outward. Cards, then plain background.
lobby_src = open("lobby_automation.py", encoding="utf-8").read()
import re as _re3
column = int(_re3.search(r"SCROLL_COLUMN = (\d+)", lobby_src).group(1))
report.at_least("clear of the cards", column, 1760)
report.at_most("and not on the screen edge, where Android takes the gesture", column, 1880)
report.check("no swipe still uses a hardcoded column",
             "int(1700 * wr)" in lobby_src, False)
report.check("every swipe uses the constant",
             lobby_src.count("self.SCROLL_COLUMN * wr"), 3)


report.section("brawler ranges are corrected for the real size of a tile")
# The table in cfg/brawlers_info.json is written as if a map tile were 54 px.
# Measured on a captured frame it is about 98: the border fence draws one post
# per tile and the posts are 98 px apart, and the ring the game paints under
# the player - about one tile across - is 115 px wide. So the bot was opening
# fire at roughly half of every brawler's actual reach, which is what people
# were reporting as "he only shoots point blank" and "Mortis walks all the way
# in and dies".
import play as _play3


class _Win:
    scale_factor = 0.5


def _ranges(multiplier):
    stub = object.__new__(_play3.Play)
    stub.window_controller = _Win()
    stub.attack_range_multiplier = multiplier
    return stub.load_brawler_ranges({"mortis": {"safe_range": 100.0, "attack_range": 200.0,
                                                "super_range": 400.0}})["mortis"]


_safe, _attack, _super = _ranges(1.35)
report.check("attack range is stretched", _attack, int(200 * 0.5 * 1.35))
report.check("so is the super", _super, int(400 * 0.5 * 1.35))
report.check("but safe_range is not - it is a keep-out, not a reach",
             _safe, int(100 * 0.5))
report.check("1.0 leaves the table exactly as it was", _ranges(1.0), [50, 100, 200])
report.check("the window scale still applies on top",
             _ranges(1.35)[1] > _ranges(1.0)[1], True)

_cfg = load_toml_as_dict("cfg/bot_config.toml")
report.check("the multiplier lives in the config, not in code",
             "attack_range_multiplier" in _cfg, True)
report.at_least("and ships pointing further out than the raw table",
                float(_cfg["attack_range_multiplier"]), 1.05)
report.at_most("without pushing brawlers past their real reach",
               float(_cfg["attack_range_multiplier"]), 1.6)

_updater = open("tools/updater.py", encoding="utf-8").read()
report.check("existing installs get it on the next update",
             '"attack_range_multiplier",' in _updater, True)
report.check("and it is editable from the web UI",
             '"attack_range_multiplier"' in open("webui/services.py", encoding="utf-8").read(),
             True)
report.check("with a Russian label, like every other setting",
             "Множитель дальности атаки" in open("static/js/i18n.js", encoding="utf-8").read(),
             True)

# A zero or a negative here would leave every brawler unable to shoot at all.
_play_src = open("play.py", encoding="utf-8").read()
report.check("a nonsense value in the config cannot disarm the bot",
             "if not 0.25 <= self.attack_range_multiplier <= 4.0:" in _play_src, True)


report.section("PowerShell needs the leading .\ and the README says so")
# "start_pyla.bat is not recognized as the name of a cmdlet" - PowerShell does
# not look in the current directory, so the plain name fails there while it
# works in cmd.exe. People hit this the first time they try to start the bot.
_readme = open("README.md", encoding="utf-8").read()
report.check("the launch line is shown the way PowerShell wants it",
             ".\start_pyla.bat" in _readme, True)
report.check("and the error people actually see is named",
             "не распознано как имя командлета" in _readme, True)


report.section("a brawler whose name OCR gets one letter wrong is still found")
# What actually happened: the game renders NORI, easyocr read "norz", and
# difflib scores that 0.75 against "nori" while the fallback needs 0.80. The
# bot scrolled the whole list forty times, gave up, restarted, and never played
# a match. 51 of the 105 brawlers have a name of four letters or fewer, so any
# single misread character lands in that same gap.
from lobby_automation import LobbyAutomation as _Lobby
import json as _json4

_seen = ["brawlers105/105", "leasttrophies", "112635", "960",
         "norz", "grom", "hank", "alli", "lou", "lumi"]
report.check("the misread card is matched", _Lobby._near_miss("nori", _seen), "norz")
report.check("an exact name on screen is left to the exact test",
             _Lobby._near_miss("grom", _seen), None)

# The whole reason the rule is narrow. Each of these pairs is two real
# brawlers one character apart, and picking the wrong one pushes trophies on
# a brawler the user did not queue.
for wanted, decoy in [("colt", "bolt"), ("rico", "mico"),
                      ("sam", "pam"), ("sandy", "mandy")]:
    report.check(f"{decoy!r} is never accepted for {wanted!r}",
                 _Lobby._near_miss(wanted, [decoy, "grom"]), None)

report.check("two near misses at once are refused, not guessed between",
             _Lobby._near_miss("nori", ["norz", "nora"]), None)
report.check("a shorter read is not a near miss",
             _Lobby._near_miss("nori", ["nor"]), None)
report.check("and neither is a longer one",
             _Lobby._near_miss("nori", ["noriz"]), None)
report.check("nothing on screen means no match",
             _Lobby._near_miss("nori", []), None)

_names = _json4.load(open("cfg/names.json", encoding="utf-8"))
report.check("the reading seen in the log is in the alias list too",
             "norz" in _names.get("nori", []), True)

# The guard that makes it safe, checked against the whole roster rather than
# the four pairs listed above.
_info = _json4.load(open("cfg/brawlers_info.json", encoding="utf-8"))
_all = sorted(_info)
_collisions = [(a, b) for i, a in enumerate(_all) for b in _all[i + 1:]
               if len(a) == len(b) and a[0] == b[0]
               and sum(x != y for x, y in zip(a, b)) == 1]
report.check("no two brawlers are one character apart with the same first letter",
             _collisions, [])


report.section("scrolling only starts once the brawler list is really open")
# It used to tap the button, sleep half a second and start swiping regardless.
# If the list had not come up - slow animation, or a popup sitting over the
# lobby - nineteen scroll-to-top swipes went into whatever was on screen
# instead, and only then did the scan loop notice and give up. Which looks,
# from outside, like a bot that says it is picking a brawler and never opens
# the menu.
from lobby_automation import LobbyAutomation as _Lobby2

_taps = []


class _Clicker:
    width_ratio = height_ratio = 1.0

    def click(self, x, y, already_include_ratio=True):
        _taps.append((x, y))


def _open(states):
    """states is consumed one reading per poll, then repeats its last value."""
    _taps.clear()
    lobby = object.__new__(_Lobby2)
    lobby.window_controller = _Clicker()
    lobby.MENU_OPEN_TIMEOUT = 0.5
    lobby.MENU_OPEN_ATTEMPTS = 3
    seq = list(states)

    def latest():
        return seq.pop(0) if len(seq) > 1 else seq[0]

    return lobby._open_brawler_menu(latest)


report.check("it stops as soon as the list is up", _open(["brawler_selection"]), "open")
report.check("one tap was enough", len(_taps), 1)
report.check("a slow animation is waited out",
             _open(["lobby", "lobby", "brawler_selection"]), "open")
report.check("still one tap", len(_taps), 1)
report.check("a list that never opens is reported, not scrolled",
             _open(["lobby"]), "closed")
report.check("after retrying the tap", len(_taps), 3)

_src = open("lobby_automation.py", encoding="utf-8").read()
_body = _src[_src.index("def select_brawler("):_src.index("MAX_SCANS)")]
report.check("select_brawler no longer taps and hopes",
             "time.sleep(0.5)" in _body, False)
report.check("and refuses to scroll a list that is not there",
             _body.index("_open_brawler_menu") < _body.index("_scroll_to_list_top"), True)


sys.exit(report.finish())
