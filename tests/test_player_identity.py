"""Three fixes that came out of looking at six thousand frames of real play.

None of these were visible from the code. They came from collecting what the
bot actually saw while it played, labelling it, and then checking the labels
against frames a person had corrected by hand - at which point the same
mistakes showed up over and over in the same shapes.

The measurements each fix rests on are in the docstrings of the functions
themselves. What is tested here is that the fixes do what those numbers say,
and - more importantly - that they do not fire when they should not, because
every one of them can throw away something real if it is too eager.
"""
import sys

from _harness import Failures

sys.path.insert(0, "src")

report = Failures("player identity and interface")

WIDTH, HEIGHT = 1920, 1080


def box(cx, cy, w=100, h=120):
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


# ── which brawler is us ─────────────────────────────────────────────────────
from play import Play  # noqa: E402

settle = Play.settle_player

report.section("the model finds several players; only one of them is us")

# The camera is locked to the player, so the real one is nearest the centre.
# Here the detector happens to list an enemy first, which is the case that was
# costing the bot its own position.
enemy_first = {"player": [box(300, 300), box(960, 540)], "enemy": [], "teammate": []}
settled = settle(enemy_first, WIDTH, HEIGHT)
report.check("the central box becomes the player",
             settled["player"], [box(960, 540)])
report.check("and only one player is left", len(settled["player"]), 1)

# The rejected claims were enemies in ninety cases out of ninety, and the bot
# was not treating them as enemies at all - so it could not see them.
report.check("the rejected claim is not thrown away",
             settled["enemy"], [box(300, 300)])

report.section("and it does nothing when there is nothing to fix")

one = {"player": [box(400, 400)], "enemy": [box(900, 200)], "teammate": []}
kept = settle(dict(one), WIDTH, HEIGHT)
report.check("a single player is left alone", kept["player"], [box(400, 400)])
report.check("the enemy list is untouched", kept["enemy"], [box(900, 200)])

report.check("no player at all does not crash",
             settle({"enemy": []}, WIDTH, HEIGHT).get("player"), None)
report.check("neither does an empty result",
             settle({}, WIDTH, HEIGHT), {})

# Ties matter: two boxes equally far from the centre must still leave exactly
# one player rather than dropping both or keeping both.
tie = settle({"player": [box(860, 540), box(1060, 540)], "enemy": []},
             WIDTH, HEIGHT)
report.check("an exact tie still resolves to one player",
             len(tie["player"]), 1)
report.check("and the other becomes an enemy", len(tie["enemy"]), 1)


# ── the kill feed is not a projectile ───────────────────────────────────────
from dodge.tracker import ProjectileTracker  # noqa: E402
from dodge.config import DodgeConfig  # noqa: E402


class FakeTrack:
    def __init__(self, points):
        self.samples = [(i * 0.05, x, y) for i, (x, y) in enumerate(points)]


tracker = ProjectileTracker(DodgeConfig.load())
tracker._frame_width, tracker._frame_height = WIDTH, HEIGHT
stuck = tracker._only_ever_on_the_interface

report.section("a card that slides into a corner and stops")

# The strip HUD_REGIONS leaves open: under the scoreboard, over the play area.
kill_feed = FakeTrack([(200, 170), (280, 175), (360, 180), (400, 182)])
report.check("a track that never leaves the kill-feed strip is refused",
             stuck(kill_feed), True)

report.section("but a shot flying through it is still a shot")

# This one starts in the same strip and carries on across the screen. Masking
# the strip would have lost it; testing the whole path keeps it.
crossing = FakeTrack([(200, 170), (500, 300), (800, 430), (1100, 560)])
report.check("a track that crosses the strip is kept", stuck(crossing), False)

# And one that never goes near it.
midfield = FakeTrack([(700, 600), (800, 620), (900, 640)])
report.check("a track in open play is kept", stuck(midfield), False)

# Only just leaving still counts as leaving: the test must not need the track
# to be mostly outside.
grazing = FakeTrack([(200, 170), (250, 175), (300, 300)])
report.check("one sample outside is enough to keep it", stuck(grazing), False)

report.section("and it cannot fire before a frame has been seen")

fresh = ProjectileTracker(DodgeConfig.load())
report.check("with no frame size yet, nothing is refused",
             fresh._only_ever_on_the_interface(kill_feed), False)

raise SystemExit(report.finish())
