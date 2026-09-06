"""A full-screen card the bot has never seen must still be tapped away.

What happened: a "NEW RARE SKIN!" card came up and the bot sat in front of it
for ten minutes. Nothing in the state cascade recognised it, so it fell through
to the "match" fallback - and "match" is deliberately exempt from every stuck
check, because a long match is a long match. So no watchdog fired, the card
kept animating past the frozen-screen watchdog, and the only thing that would
ever have rescued it was the eight-minute no-detections restart.

The card had a CONTINUE button the whole time. The template for that word
existed too, cut from the prestige screen's GREEN button - and cv2.matchTemplate
on three channels scores a BLUE button against a green crop at nothing, which is
why the word was invisible to a check that was looking straight at it.
"""
import glob
import os
import sys

import cv2
import numpy as np

from _harness import Failures, read_source

sys.path.insert(0, "src")
import state_finder as sf  # noqa: E402

report = Failures("dismissable cards")

TEMPLATE = cv2.imread("assets/images/states/prestige_continue.png")
report.check("the CONTINUE template is there to match against",
             TEMPLATE is not None, True)


def card(button_centre, colour=None, size=1.0):
    """A 1920x1080 frame with the CONTINUE word painted at a given centre.

    RGB, like the frames scrcpy hands the state checker.
    """
    frame = np.full((1080, 1920, 3), 40, dtype=np.uint8)
    # Something card-shaped behind it, so the match is not scoring letters
    # against flat grey.
    cv2.rectangle(frame, (300, 120), (1620, 1010), (120, 190, 210), -1)

    word = cv2.cvtColor(TEMPLATE, cv2.COLOR_BGR2RGB).copy()
    if colour is not None:
        # Repaint only the button, not the lettering: the green pixels are the
        # background, the white-and-black ones are the word.
        green = ((word[:, :, 1] > 140) & (word[:, :, 0] < 140)
                 & (word[:, :, 2] < 140))
        word[green] = colour
    if size != 1.0:
        word = cv2.resize(
            word, (int(word.shape[1] * size), int(word.shape[0] * size)),
            interpolation=cv2.INTER_AREA if size < 1 else cv2.INTER_LINEAR)

    h, w = word.shape[:2]
    x, y = int(button_centre[0] - w / 2), int(button_centre[1] - h / 2)
    frame[y:y + h, x:x + w] = word
    return frame


# Where the prestige screen puts CONTINUE: the centre of its own narrow region.
PRESTIGE_SPOT = (535 + 345 // 2, 950 + 95 // 2)
# Where the new-skin card puts it - right of centre, beside EQUIP NOW. Measured
# off the screenshot of the card the bot hung on.
SKIN_SPOT = (1110, 930)

report.section("the word is found wherever the card puts it")

found = sf.find_continue_button(card(PRESTIGE_SPOT))
report.check("the prestige screen's own button is still found",
             found is not None, True)
if found:
    report.check("and at the right place",
                 abs(found[0] - PRESTIGE_SPOT[0]) < 30
                 and abs(found[1] - PRESTIGE_SPOT[1]) < 30, True)

found = sf.find_continue_button(card(SKIN_SPOT))
report.check("a button right of centre is found too - the old narrow region "
             "only ever covered x 535-880", found is not None, True)
if found:
    report.check("and points at it, not at the fixed [700, 1000] that missed",
                 abs(found[0] - SKIN_SPOT[0]) < 30
                 and abs(found[1] - SKIN_SPOT[1]) < 30, True)

report.section("colour is not what identifies the button")
# The actual bug. Blue button, green template.
blue = sf.find_continue_button(card(SKIN_SPOT, colour=(40, 110, 240)))
report.check("a BLUE continue matches a template cut from a GREEN one",
             blue is not None, True)
purple = sf.find_continue_button(card(SKIN_SPOT, colour=(150, 60, 200)))
report.check("so would any other colour Supercell paints it",
             purple is not None, True)

# And the old colour-matched check demonstrably could not do this, which is
# why the fix is a new function rather than a wider region on the old one.
report.check("while the old colour check is blind to it",
             sf.is_template_in_region(
                 card(SKIN_SPOT, colour=(40, 110, 240)),
                 sf.states_path + "prestige_continue.png",
                 [140, 860, 1640, 220]),
             False)

report.section("a button drawn a little larger or smaller is still found")
# The peak is narrow - the same word 5% off its real size scores 0.59, below
# what an ordinary game screen scores - so the sweep has to step finely. These
# fail the moment somebody widens the steps to save a millisecond.
for size in (0.88, 0.94, 1.06, 1.15):
    report.check(f"a button at {size:g}x",
                 sf.find_continue_button(card(SKIN_SPOT, size=size)) is not None,
                 True)

report.section("and nothing else is mistaken for it")
plain = np.full((1080, 1920, 3), 40, dtype=np.uint8)
cv2.rectangle(plain, (300, 120), (1620, 1010), (120, 190, 210), -1)
report.check("a card with no button at all reports none",
             sf.find_continue_button(plain), None)

rng = np.random.default_rng(7)
noise = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
report.check("nor does noise produce one", sf.find_continue_button(noise), None)

# The one that matters. A false positive here is the AFK bug all over again:
# a real match frame read as a card, the bot tapping the bottom of the screen
# and standing still because the state says it is not in a match.
for _shot in sorted(glob.glob("debug_frames/*.png")):
    _frame = cv2.imread(_shot)
    if _frame is None:
        continue
    report.check(f"a real game screen is not a card ({os.path.basename(_shot)})",
                 sf.find_continue_button(cv2.cvtColor(_frame, cv2.COLOR_BGR2RGB)),
                 None)


report.section("the state cascade uses it as a last resort, never a first")
_stubbed = ("is_in_end_of_a_match", "is_in_lobby", "is_in_match_making",
            "is_in_shop", "is_in_offer_popup", "is_in_brawl_pass",
            "is_in_star_road", "is_in_prestige_milestone", "is_in_nano_noodles",
            "is_at_buffie_machine", "is_in_daily_wins", "is_in_star_drop",
            "is_in_trophy_reward", "is_in_brawler_selection")
_saved = {name: getattr(sf, name) for name in _stubbed}
_saved_find = sf.find_continue_button
try:
    for name in _stubbed:
        setattr(sf, name, lambda *a, **k: False)

    sf.find_continue_button = lambda image: (1110, 930)
    report.check("an unrecognised card with a CONTINUE is a card, not a match",
                 sf.get_in_game_state(None), "continue_card")

    sf.find_continue_button = lambda image: None
    report.check("and one without stays a match, as before",
                 sf.get_in_game_state(None), "match")

    # The point of putting it last: every screen with a name of its own wins.
    sf.find_continue_button = lambda image: (1110, 930)
    sf.is_in_trophy_reward = lambda image: True
    report.check("a trophy reward is still a trophy reward",
                 sf.get_in_game_state(None), "trophy_reward")
    sf.is_in_trophy_reward = lambda image: False
    sf.is_in_lobby = lambda image: True
    report.check("and the lobby is still the lobby",
                 sf.get_in_game_state(None), "lobby")
finally:
    for name, value in _saved.items():
        setattr(sf, name, value)
    sf.find_continue_button = _saved_find


report.section("the card is tapped where it actually is")
_stage = read_source("stage_manager.py")
report.check("the handler is wired to the new state",
             "'continue_card': self.tap_continue" in _stage, True)
report.check("and the prestige screen goes through the same handler",
             "'prestige_milestone': self.tap_continue" in _stage, True)
report.check("it clicks the located button",
             "self.window_controller.click(*spot)" in _stage, True)
report.check("and still falls back to the configured coordinate",
             'self.window_controller.press("continue_or_equip")' in _stage, True)


class _Controller:
    def __init__(self, frame):
        self.frame = frame
        self.clicks = []
        self.presses = []

    def screenshot(self):
        return self.frame

    def click(self, x, y):
        self.clicks.append((x, y))

    def press(self, key):
        self.presses.append(key)


def _tap(frame):
    """Run the real tap_continue against a stub controller."""
    import stage_manager
    manager = stage_manager.StageManager.__new__(stage_manager.StageManager)
    manager.window_controller = _Controller(frame)
    manager.tap_continue()
    return manager.window_controller


controller = _tap(card(SKIN_SPOT, colour=(40, 110, 240)))
report.check("the blue button is tapped", len(controller.clicks), 1)
if controller.clicks:
    report.check("on the button itself",
                 abs(controller.clicks[0][0] - SKIN_SPOT[0]) < 30
                 and abs(controller.clicks[0][1] - SKIN_SPOT[1]) < 30, True)
report.check("without falling back", controller.presses, [])

controller = _tap(plain)
report.check("a card already gone falls back to the old coordinate",
             controller.presses, ["continue_or_equip"])
report.check("and taps nothing else", controller.clicks, [])


report.section("and it cannot hang either")
_main = read_source("main.py")
report.check("a card that will not close is a transient screen, so the stuck "
             "check restarts the game instead of waiting eight minutes",
             '"continue_card"' in _main.split("TRANSIENT_STATES = {")[1].split("}")[0],
             True)

sys.exit(report.finish())
