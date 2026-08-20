"""The profile, and the clock that decides when to stop playing."""
import sys
from datetime import datetime, timedelta

from _harness import Failures

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

report.section("a stop time with nothing to lift it holds until midnight")
until_midnight = Schedule(stop_at="22:00")
report.check("before", until_midnight.in_quiet_hours(at(21, 59)), False)
report.check("after", until_midnight.in_quiet_hours(at(22, 1)), True)
report.check("next morning is free again", until_midnight.in_quiet_hours(at(7, 0)), False)

report.section("nothing configured is completely inert")
off = Schedule()
report.check("not active", off.active, False)
report.check("never holds", off.holding(at(3, 0))[0], False)

report.section("holding says why")
holding, reason = night.holding(at(2, 0))
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


report.section("a scheduled pause closes the game, a manual one does not")
main_src = open("main.py", encoding="utf-8").read()
pause = main_src[main_src.index("def wait_while_paused"):main_src.index("def handle_pause_request")]
report.check("it tells the two kinds of pause apart",
             "schedule_hold_reason" in pause, True)
report.check("closes on a scheduled hold", "close_brawl_stars()" in pause, True)
report.check("and opens it again on the way out", "open_brawl_stars()" in pause, True)
report.check("the switch is read, not assumed",
             "close_game_while_scheduled()" in pause, True)

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

sys.exit(report.finish())
