"""Settings that ship, and settings that must never be touched.

The updater protects cfg/ wholesale because it holds API tokens and queues.
The exception is calibration - how sure the wall model must be, how big a tile
reads on screen - which is a measurement of the game rather than a preference,
and should reach everyone. These check that the line between the two holds.
"""
import sys

from _harness import Failures

sys.path.insert(0, "tools")
from updater import merge_settings  # noqa: E402

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

sys.exit(report.finish())
