"""Settings that ship, and settings that must never be touched.

The updater protects cfg/ wholesale because it holds API tokens and queues.
The exception is calibration - how sure the wall model must be, how big a tile
reads on screen - which is a measurement of the game rather than a preference,
and should reach everyone. These check that the line between the two holds.
"""
import sys

from _harness import Failures

sys.path.insert(0, "tools")
from updater import JSON_ADDITIONS, merge_json, merge_settings  # noqa: E402

report = Failures("updater settings merge")

TUNING = {"wall_detection_confidence", "perceived_tile_size",
          "poison_gas_fraction"}

SHIPPED = """perceived_tile_size = 35
wall_detection_confidence = 0.7
poison_gas_fraction = 0.078
play_again_on_win = "no"
current_playstyle = "unified_dodge.pyla"
"""


def merged(current, keys=TUNING, shipped=SHIPPED):
    out = merge_settings(shipped, current, keys)
    return out if out is not None else current


report.section("calibration arrives, personal choices do not move")
theirs = """perceived_tile_size = 54
wall_detection_confidence = 0.85
play_again_on_win = "yes"
current_playstyle = "my_own_style.pyla"
"""
out = merged(theirs)
report.check("the tuned tile size arrives",
             "perceived_tile_size = 35" in out, True)
report.check("so does the wall confidence",
             "wall_detection_confidence = 0.7" in out, True)
report.check("their playstyle is untouched",
             'current_playstyle = "my_own_style.pyla"' in out, True)
report.check("and so is their play-again choice",
             'play_again_on_win = "yes"' in out, True)

report.section("a setting they have never seen is added, not withheld")
report.check("the new key arrives", "poison_gas_fraction = 0.078" in out, True)

report.section("keys outside the tuning list are never overwritten")
out = merged(theirs, keys=set())
report.check("tile size left alone", "perceived_tile_size = 54" in out, True)
report.check("confidence left alone",
             "wall_detection_confidence = 0.85" in out, True)
report.check("but the missing key still arrives",
             "poison_gas_fraction = 0.078" in out, True)

report.section("nothing to do means the file is not rewritten at all")
report.check("identical file returns None",
             merge_settings(SHIPPED, SHIPPED, TUNING), None)

report.section("settings the user added themselves survive")
theirs_plus = theirs + "my_own_experiment = 7\n"
out = merged(theirs_plus)
report.check("their own key is still there",
             "my_own_experiment = 7" in out, True)

report.section("a file with sections is left alone rather than guessed at")
report.check("sectioned config is refused",
             merge_settings("[a]\nx = 1\n", "[a]\nx = 2\n", {"x"}), None)

report.section("comments and blank lines survive a merge")
commented = """# how big a tile looks
perceived_tile_size = 54

# do not touch
current_playstyle = "mine.pyla"
"""
out = merged(commented)
report.check("the comment is still there",
             "# how big a tile looks" in out, True)
report.check("the blank line too", "\n\n" in out, True)
report.check("and the value updated", "perceived_tile_size = 35" in out, True)

report.section("a new brawler reaches somebody who installed last month")
# cfg/ is protected wholesale, and brawlers_info.json is not in TUNING, so a
# brawler added to the table used to reach nobody running from a zip. The bot
# does ask an upstream service about brawlers it does not recognise, but the
# one released this week is exactly the one that service has not got yet.
import json as _json

report.check("both brawler files are on the additions list",
             sorted(JSON_ADDITIONS),
             ["cfg/brawlers_info.json", "cfg/names.json"])

SHIPPED_JSON = _json.dumps({
    "shelly": {"attack_range": 490.0, "hold_attack": 0},
    "nori": {"attack_range": 448.0, "quick_attack_range": 192.0, "hold_attack": 2},
})


def merged_json(current, shipped=SHIPPED_JSON):
    out = merge_json(shipped, current)
    return _json.loads(out) if out is not None else _json.loads(current)


theirs_json = _json.dumps({
    "shelly": {"attack_range": 490.0, "hold_attack": 0},
    # tuned by hand, and not ours to undo
    "mortis": {"attack_range": 400.0, "hold_attack": 0},
})
out = merged_json(theirs_json)
report.check("the new brawler arrives", "nori" in out, True)
report.check("with all of its numbers", out["nori"]["quick_attack_range"], 192.0)
report.check("a brawler only they have is untouched", out["mortis"]["attack_range"], 400.0)

report.section("a value they changed themselves is never overwritten")
tuned = _json.dumps({"shelly": {"attack_range": 900.0, "hold_attack": 0},
                     "nori": {"attack_range": 448.0, "quick_attack_range": 192.0,
                              "hold_attack": 2}})
report.check("their range survives", merged_json(tuned)["shelly"]["attack_range"], 900.0)
report.check("and an already-complete file is not rewritten at all",
             merge_json(SHIPPED_JSON, tuned), None)

report.section("a brawler can gain a field, not just a file gain a brawler")
# Anyone who picked Nori up from the upstream service got him without
# quick_attack_range, and a record missing that reads as "no second attack".
partial = _json.dumps({"shelly": {"attack_range": 490.0, "hold_attack": 0},
                       "nori": {"attack_range": 448.0, "hold_attack": 2}})
out = merged_json(partial)
report.check("the missing field is filled in", out["nori"]["quick_attack_range"], 192.0)
report.check("without disturbing the rest of the record", out["nori"]["hold_attack"], 2)

report.section("names.json merges the same way, with lists instead of records")
shipped_names = _json.dumps({"shelly": ["shey"], "nori": ["norl", "nor1"]})
theirs_names = _json.dumps({"shelly": ["shey", "myownalias"]})
out = merge_json(shipped_names, theirs_names)
out = _json.loads(out)
report.check("the new brawler's aliases arrive", out["nori"], ["norl", "nor1"])
report.check("an alias list they extended is left alone",
             out["shelly"], ["shey", "myownalias"])

report.section("nonsense in either file is refused rather than guessed at")
report.check("half-written JSON is refused", merge_json("{oops", "{}"), None)
report.check("a list at the top level is refused", merge_json("[1,2]", "{}"), None)


sys.exit(report.finish())
