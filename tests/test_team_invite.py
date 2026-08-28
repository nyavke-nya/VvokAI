"""Turning down team invites without turning down anything else.

The dialog is found in two stages - a count of the ACCEPT button's green, then
OCR on what that green sits in - and both stages matter. The green alone is a
colour that other screens can have; the OCR alone is too slow to run every few
seconds. What is checked here is that the cheap stage really does gate the
expensive one, that the two clicks happen in the order that works, and that the
arithmetic putting the checkbox on screen survives the crop offset and the OCR
downscale.

The dialog geometry below was measured off a real screenshot of it and then
scaled to 1920x1080. That makes these tests honest about the arithmetic and
silent about the pixels: if the modal ever moves, the factors in bot_config
move with it and these still pass. Whether the click lands on the checkbox in
the real game is a question only the real game answers.
"""
import sys

import numpy as np

from _harness import Failures

sys.path.insert(0, ".")
import lobby_automation  # noqa: E402
from lobby_automation import LobbyAutomation  # noqa: E402

report = Failures("team invite")

# The dialog at 1920x1080, from the screenshot. Full-frame pixels.
REJECT_CENTRE = (789, 684)
ACCEPT_CENTRE = (1178, 684)
CHECKBOX_BOX = (1292, 753, 1369, 842)     # x1, y1, x2, y2


class FakeWindow:
    width_ratio = 1.0
    height_ratio = 1.0

    def __init__(self):
        self.clicks = []

    def click(self, x, y, **kwargs):
        self.clicks.append((round(x), round(y)))


def automator(green=99999, ocr=None, enabled=True, scale=1.0, verbose=False,
              template=True):
    """A LobbyAutomation with no OCR engine and no emulator behind it.

    __init__ loads four config files and builds an OCR reader; everything under
    test is arithmetic over what that reader would have returned.
    """
    a = object.__new__(LobbyAutomation)
    a.window_controller = FakeWindow()
    a.decline_team_invites = enabled
    a.team_invite_green_minimum = 3500
    a.mute_x_factor = 0.89
    a.mute_y_factor = 0.29
    a.ocr_scale_down_factor = scale
    a.verbose_debug = verbose
    a.last_team_invite_handled = 0.0
    a._green = green
    a._ocr = ocr if ocr is not None else {}
    # Stand in for the three things that touch the outside world.
    a._read_invite_buttons = lambda crop: _stub_read(a, crop)
    _templates.append(template)
    return a


# What is_team_invite_on_screen answers, per automator built. The real one
# reads a PNG off disk and runs matchTemplate; here it is just the next value.
_templates = []


def _stub_template(_frame):
    return _templates[-1] if _templates else True


lobby_automation.is_team_invite_on_screen = _stub_template


def _stub_read(a, crop):
    """What _read_invite_buttons would return, in crop coordinates."""
    if "reject" not in a._ocr or "accept" not in a._ocr:
        return None
    left, top, _, _ = LobbyAutomation.TEAM_INVITE_REGION
    reject = (a._ocr["reject"][0] - left, a._ocr["reject"][1] - top)
    accept = (a._ocr["accept"][0] - left, a._ocr["accept"][1] - top)
    mute = a._ocr.get("mute_y")
    return reject, accept, (mute - top) if mute is not None else None


def run(a, frame=None):
    frame = frame if frame is not None else np.zeros((1080, 1920, 3), np.uint8)
    # count_hsv_pixels is the only other outside call; feed it a fixed answer.
    import lobby_automation
    real = lobby_automation.count_hsv_pixels
    lobby_automation.count_hsv_pixels = lambda *args, **kwargs: a._green
    try:
        return a.check_for_team_invite(frame)
    finally:
        lobby_automation.count_hsv_pixels = real


FULL = {"reject": REJECT_CENTRE, "accept": ACCEPT_CENTRE}


report.section("the artwork decides whether this is the dialog at all")
# The green count and the OCR used to be the whole test, and between them they
# declined things that were never invites: REJECT and ACCEPT are ordinary words
# and a green button is an ordinary button. The banner is not.
a = automator(green=99999, ocr=FULL, template=False)
report.check("everything else matching is not enough on its own", run(a), False)
report.check("and nothing was clicked", a.window_controller.clicks, [])

a = automator(green=99999, ocr=FULL, template=True)
report.check("with the banner there, it goes ahead", run(a), True)


report.section("the cheap check gates the expensive one")
a = automator(green=0, ocr=FULL)
report.check("no green means no invite", run(a), False)
report.check("and nothing was clicked", a.window_controller.clicks, [])

a = automator(green=3499, ocr=FULL)
report.check("just under the threshold is still no", run(a), False)

a = automator(green=3500, ocr=FULL)
report.check("at the threshold it looks properly", run(a), True)


report.section("green alone is not the dialog")
a = automator(green=99999, ocr={})
report.check("green with no buttons under it is ignored", run(a), False)
report.check("and nothing was clicked", a.window_controller.clicks, [])

a = automator(green=99999, ocr={"reject": REJECT_CENTRE})
report.check("one button is not two", run(a), False)


report.section("mute, then reject, in that order")
a = automator(ocr=FULL)
run(a)
report.check("two clicks", len(a.window_controller.clicks), 2)
mute_click, reject_click = a.window_controller.clicks
report.check("the second one is REJECT", reject_click, REJECT_CENTRE)
# REJECT closes the dialog, so a checkbox ticked after it is a click on
# whatever was behind it.
report.check("and the checkbox is ticked before that, not after",
             mute_click != reject_click and mute_click[1] > reject_click[1], True)

x1, y1, x2, y2 = CHECKBOX_BOX
report.check("the mute click lands inside the checkbox",
             x1 <= mute_click[0] <= x2 and y1 <= mute_click[1] <= y2, True)


report.section("the mute line is optional")
a = automator(ocr={**FULL, "mute_y": 797})
run(a)
with_line = a.window_controller.clicks[0]
a = automator(ocr=FULL)
run(a)
without_line = a.window_controller.clicks[0]
report.check("reading it puts the click in the checkbox",
             x1 <= with_line[0] <= x2 and y1 <= with_line[1] <= y2, True)
report.check("not reading it still does",
             x1 <= without_line[0] <= x2 and y1 <= without_line[1] <= y2, True)
report.check("and the two agree on the column", with_line[0], without_line[0])


report.section("one dialog is handled once")
a = automator(ocr=FULL)
report.check("the first invite is taken", run(a), True)
report.check("the same one a moment later is not", run(a), False)
report.check("still only two clicks", len(a.window_controller.clicks), 2)
a.last_team_invite_handled -= LobbyAutomation.TEAM_INVITE_COOLDOWN + 0.1
report.check("after the cooldown a new one is", run(a), True)


report.section("the setting is a setting")
a = automator(ocr=FULL, enabled=False)
report.check("switched off, nothing happens", run(a), False)
report.check("not even the green count", a.window_controller.clicks, [])


report.section("scaling and offsets")
# The buttons are read in a crop, at a downscale. Both have to be undone or
# the click lands somewhere up and to the left of the dialog.
left, top, _, _ = LobbyAutomation.TEAM_INVITE_REGION
report.check("the crop starts where the region says", (left, top), (470, 170))
a = automator(ocr=FULL)
run(a)
report.check("REJECT is clicked in full-frame coordinates, not crop ones",
             a.window_controller.clicks[1], REJECT_CENTRE)
report.check("which is well right of the crop origin",
             a.window_controller.clicks[1][0] > left, True)


report.section("the region contains the dialog it is looking for")
for name, point in (("reject", REJECT_CENTRE), ("accept", ACCEPT_CENTRE)):
    report.check(f"{name} is inside the search box",
                 left <= point[0] <= LobbyAutomation.TEAM_INVITE_REGION[2]
                 and top <= point[1] <= LobbyAutomation.TEAM_INVITE_REGION[3], True)
report.check("so is the checkbox",
             left <= x1 and x2 <= LobbyAutomation.TEAM_INVITE_REGION[2]
             and top <= y1 and y2 <= LobbyAutomation.TEAM_INVITE_REGION[3], True)


report.section("the green it looks for is the button's own")
low, high = LobbyAutomation.ACCEPT_GREEN
# (76, 209, 55) is the ACCEPT green; in OpenCV's ranges that is H 56, S 188.
report.check("the hue is inside the range", low[0] <= 56 <= high[0], True)
report.check("so is the saturation", low[1] <= 188 <= high[1], True)
report.check("a grey lobby background is not", low[1] <= 20, False)


sys.exit(report.finish())
