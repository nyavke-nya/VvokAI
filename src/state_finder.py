import os
import sys
import cv2
import time
sys.path.append(os.path.abspath('/'))
from utils import load_toml_as_dict, config_bool

last_debug_print_time = 0.0
should_print_debug_info = False

orig_screen_width, orig_screen_height = 1920, 1080

states_path = r"./assets/images/states/"

star_drops_path = r"./assets/images/star_drop_types/"
images_with_star_drop = []
for file in os.listdir(star_drops_path):
    if "star_drop" in file:
        images_with_star_drop.append(file)

end_results_path = r"./assets/images/end_results/"

region_data = load_toml_as_dict("./cfg/lobby_config.toml")['template_matching']
match_result_crop_region = region_data.get('match_result', [20, 10, 650, 200])


def is_template_in_region(image, template_path, region, threshold=0.75):
    current_height, current_width = image.shape[:2]
    orig_x, orig_y, orig_width, orig_height = region
    width_ratio, height_ratio = current_width / orig_screen_width, current_height / orig_screen_height

    new_x, new_y = int(orig_x * width_ratio), int(orig_y * height_ratio)
    new_width, new_height = int(orig_width * width_ratio), int(orig_height * height_ratio)
    cropped_image = image[new_y:new_y + new_height, new_x:new_x + new_width]
    current_height, current_width = image.shape[:2]
    loaded_template = load_template(template_path, current_width, current_height)
    if loaded_template is None:
        return False
    if (loaded_template.shape[0] > cropped_image.shape[0]
            or loaded_template.shape[1] > cropped_image.shape[1]):
        # A template larger than the area it is searched in cannot match, and
        # matchTemplate raises rather than saying so.
        if template_path not in missing_templates:
            missing_templates.add(template_path)
            print(f"State template is bigger than its search region, skipped: "
                  f"{template_path}")
        return False
    result = cv2.matchTemplate(cropped_image, loaded_template,
                               cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
    if should_print_debug_info:
        print(f"Template matching for {template_path} in region {region} yielded max_val: {max_val}")
    return max_val > threshold


cached_templates = {}
missing_templates = set()


def load_template(image_path, width, height):
    if (image_path, width, height) in cached_templates:
        return cached_templates[(image_path, width, height)]
    current_width_ratio, current_height_ratio = width / orig_screen_width, height / orig_screen_height
    image = cv2.imread(image_path)
    if image is None:
        # A missing template used to take the whole state checker down with an
        # AttributeError on None.shape - every frame, so the bot stopped
        # recognising ANY screen because one picture was absent. One screen it
        # cannot detect is a missing feature; no screens at all is a dead bot.
        if image_path not in missing_templates:
            missing_templates.add(image_path)
            print(f"State template missing, that check is skipped: {image_path}")
        cached_templates[(image_path, width, height)] = None
        return None
    orig_height, orig_width = image.shape[:2]
    resized_image = cv2.resize(image, (int(orig_width * current_width_ratio), int(orig_height * current_height_ratio)))
    resized_colored_image = cv2.cvtColor(resized_image, cv2.COLOR_BGR2RGB)
    cached_templates[(image_path, width, height)] = resized_colored_image
    return resized_colored_image

SHOWDOWN_PLACE_THRESHOLD = 0.9
showdown_place_templates = {
    0: ["1st.png"],
    1: ["2nd.png"],
    2: ["3rd.png", "3rd_alt.png"],
    3: ["4th.png"]
}

def find_game_result(screenshot):
    for place, template_files in showdown_place_templates.items():
        for template_file in template_files:
            if is_template_in_region(
                    screenshot,
                    end_results_path + template_file,
                    match_result_crop_region,
                    threshold=SHOWDOWN_PLACE_THRESHOLD
            ):
                return f"trio_showdown_{place}"
    is_victory = is_template_in_region(screenshot, end_results_path + 'victory.png', match_result_crop_region)
    if is_victory:
        return "victory"

    is_defeat = is_template_in_region(screenshot, end_results_path + 'defeat.png', match_result_crop_region)
    if is_defeat:
        return "defeat"

    is_draw = is_template_in_region(screenshot, end_results_path + 'draw.png', match_result_crop_region)
    if is_draw:
        return "draw"
    return False


def get_in_game_state(image):
    global last_debug_print_time, should_print_debug_info
    state_finder_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('state_finder_debug'), False)
    current_time = time.time()
    should_print_debug_info = state_finder_debug and (current_time - last_debug_print_time >= 1.0)
    if should_print_debug_info:
        last_debug_print_time = current_time

    try:
        if should_print_debug_info: print("Checking for match result...")
        game_result = is_in_end_of_a_match(image)
        if game_result: return f"end_{game_result}"
        if should_print_debug_info: print("Checking for lobby...")
        if is_in_lobby(image): return "lobby"
        if should_print_debug_info: print("Checking for match making...")
        if is_in_match_making(image): return "match_making"
        if should_print_debug_info: print("Checking for brawler selection...")
        if is_in_brawler_selection(image): return "brawler_selection"
        if should_print_debug_info: print("Checking for shop")
        if is_in_shop(image):
            # The brawler list's own top filter icons sit where the shop
            # template is looked for, so an open list can read as "shop" - and
            # the stage manager then "closes the shop", throwing the list away
            # mid-selection, while the selection flow aborts because the state
            # is not "brawler_selection". That is the "it found the brawler but
            # never selected it" loop.
            #
            # The tolerant list check is consulted ONLY here, where the shop
            # template has already matched. That cannot bring back the mid-match
            # false positive it was made strict for, because the shop template
            # does not match a match frame at all.
            if is_in_brawler_selection(image, strict=False):
                return "brawler_selection"
            return "shop"
        if should_print_debug_info: print("Checking for offer popup...")
        if is_in_offer_popup(image): return "popup"
        if should_print_debug_info: print("Checking for brawl pass or star road (shop state)...")
        if is_in_brawl_pass(image) or is_in_star_road(image): return "shop"
        if should_print_debug_info: print("Checking for prestige milestone...")
        if is_in_prestige_milestone(image): return "prestige_milestone"
        if should_print_debug_info: print("Checking for nano noodles...")
        if is_in_nano_noodles(image): return "nano_noodles"
        if should_print_debug_info: print("Checking for the buffie machine...")
        if is_at_buffie_machine(image): return "buffie_machine"
        if should_print_debug_info: print("Checking for the daily wins choice...")
        if is_in_daily_wins(image): return "daily_wins"
        if should_print_debug_info: print("Checking for star drop...")
        star_drop_type = is_in_star_drop(image)
        if star_drop_type:
            return f"star_drop_{star_drop_type}"
        if should_print_debug_info: print("Checking for trophy reward...")
        if is_in_trophy_reward(image):
            return "trophy_reward"

        # Last, after every screen with a name of its own has had its say. A
        # card nothing above recognised, but which offers a CONTINUE, is still
        # a card that can be tapped away - and the alternative is what this was
        # written for: the fallback below calls it a match, "match" is exempt
        # from every stuck check by design, and the bot plays into a popup
        # until the eight-minute no-detections restart notices. Ten minutes of
        # nothing for a screen one tap closes.
        #
        # Placing it here is what makes it safe: it can only ever claim a frame
        # that is otherwise about to be called a match on no evidence at all.
        if should_print_debug_info: print("Checking for a dismissable card...")
        if find_continue_button(image) is not None:
            return "continue_card"

        return "match"
    finally:
        should_print_debug_info = False


def is_in_shop(image) -> bool:
    return is_template_in_region(image, states_path + 'powerpoint.png', region_data.get("powerpoint", [1000, 5, 80, 80]))


# Sizes to try the toolbar icons at, as multiples of the resolution-scaled
# template. is_template_in_region scales the template by capture_width/1920,
# which assumes the emulator draws the icon at the same fraction of the screen
# the template was cut from. Emulators do not agree on that: on one the heart
# fills the button, on another it sits smaller inside it, and a heart 20% off
# the expected size drops TM_CCOEFF_NORMED well under any sane threshold. That
# is why selection recognised the list on MuMu and not on LDPlayer/MemU with
# the SAME templates - the icons were there, just the wrong size. Searching a
# spread of sizes finds the icon wherever the emulator drew it.
BRAWLER_ICON_SCALES = (0.55, 0.65, 0.78, 0.9, 1.0, 1.12, 1.28, 1.45, 1.65)


# The multi-scale match is O(search-area x template-area) per scale, which over
# a whole toolbar band and nine sizes runs to ~70 ms/frame at full resolution -
# far too slow for a check the state thread makes every frame. Both the crop and
# the templates are shrunk to this working width first: TM_CCOEFF_NORMED matches
# on shape, which survives the downscale intact, and the cost falls by roughly
# the fourth power of the factor - to a couple of ms. Small enough to be quick,
# large enough that a ~25 px icon stays recognisable.
ICON_MATCH_WIDTH = 260


def _matches_at_any_scale(image, template_path, region, threshold, scales):
    """True if the template matches inside the region at ANY of the scales.

    Same crop and resolution scaling as is_template_in_region, but the template
    is then resized to each scale before matching, so an icon the emulator drew
    larger or smaller than expected is still found. Crop and template are both
    shrunk to a fixed working width first, purely for speed (see ICON_MATCH_WIDTH).
    """
    current_height, current_width = image.shape[:2]
    orig_x, orig_y, orig_width, orig_height = region
    width_ratio, height_ratio = current_width / orig_screen_width, current_height / orig_screen_height
    new_x, new_y = int(orig_x * width_ratio), int(orig_y * height_ratio)
    new_width, new_height = int(orig_width * width_ratio), int(orig_height * height_ratio)
    crop = image[new_y:new_y + new_height, new_x:new_x + new_width]
    if crop.size == 0:
        return False

    base = load_template(template_path, current_width, current_height)
    if base is None:
        return False

    # Shrink the crop to the working width, and the template by the same factor,
    # so their relative sizes - all matchTemplate cares about - are preserved.
    work = min(1.0, ICON_MATCH_WIDTH / max(crop.shape[1], 1))
    if work < 1.0:
        crop = cv2.resize(crop, (max(1, int(crop.shape[1] * work)),
                                 max(1, int(crop.shape[0] * work))),
                          interpolation=cv2.INTER_AREA)
        base = cv2.resize(base, (max(1, int(base.shape[1] * work)),
                                 max(1, int(base.shape[0] * work))),
                          interpolation=cv2.INTER_AREA)

    base_h, base_w = base.shape[:2]
    for scale in scales:
        tw, th = max(1, int(base_w * scale)), max(1, int(base_h * scale))
        if th > crop.shape[0] or tw > crop.shape[1]:
            # Too big for the search area at this scale; a larger one will be too.
            continue
        scaled = base if scale == 1.0 else cv2.resize(
            base, (tw, th), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        result = cv2.matchTemplate(crop, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        if max_val > threshold:
            return True
    return False


# The tolerant settings used only inside the auto-select flow (see below): the
# whole top toolbar band and a loose threshold, so an emulator that draws the
# icons at its own size is still recognised.
SELECTION_FLOW_REGION = [1050, 0, 700, 150]
SELECTION_FLOW_THRESHOLD = 0.68


def is_in_brawler_selection(image, strict: bool = True) -> bool:
    # The sideways-menu update moved everything on this screen except the two
    # icons in its top toolbar: a task clipboard and a heart. The old check
    # looked for the heart alone, in one narrow box the icon no longer sits in,
    # so it stopped matching the moment the layout changed - and a bot that
    # cannot see the open list taps the brawlers button a second time, which on
    # this layout lands on the glory panel. That is the "auto select just opens
    # the list / glory" everyone was reporting.
    #
    # It still tries a spread of icon SIZES, so an emulator that draws the heart
    # a little larger or smaller than the template is recognised - that is what
    # made selection work on MuMu and fail on LDPlayer/MemU with identical
    # templates, and it is a real compatibility need, so it stays.
    #
    # But the earlier version paired that size-sweep with a LOOSE 0.68 threshold,
    # a WIDE top band [1050,0,700,150] and a second "task" icon - and that combo
    # matched the MATCH HUD's own icons. Mid-match the state checker then read
    # "brawler_selection", Play.main() concluded the match had ended, and the bot
    # stopped moving and shooting: the "stands AFK, only attacks while farming"
    # bug people reported on Bluestacks. The three changes that remove the false
    # hit without losing the size tolerance:
    #   - the heart only. The task clipboard was the icon matching HUD clutter.
    #   - the narrower band [1250,0,650,140], where the heart actually sits in
    #     the list, clear of the score/timer/portraits along the top of a match.
    #   - a strict threshold, so only a near-exact heart counts.
    #
    # If it still misses, the list is genuinely unreadable and lobby_automation
    # saves the frame to debug_frames/ to be recut to.
    #
    # The lobby is ruled out first, and this is not redundant. get_in_game_state
    # checks the lobby before this and would never reach here in the lobby - but
    # lobby_automation._list_is_open calls this DIRECTLY on a frame, bypassing
    # that order. Without the guard the size-sweep found a heart-ish blob in the
    # lobby's own top bar and reported the list already open. The lobby's
    # hamburger button is a reliable tell the brawler list does not share.
    if is_in_lobby(image):
        return False

    if strict:
        # The state machine's path, run on every frame including mid-match.
        # Here a FALSE HIT is the expensive mistake: it makes Play.main() decide
        # the match ended, and the bot stops moving and shooting. So: the heart
        # only, the narrow band the heart actually occupies in the list (clear of
        # a match's score/timer/portraits), and a strict threshold. A miss here
        # is cheap - the state simply falls through to "match", which is right.
        region = region_data.get("brawler_menu_heart", [1250, 0, 650, 140])
        return _matches_at_any_scale(image, states_path + "brawler_menu_heart.png",
                                     region, 0.86, BRAWLER_ICON_SCALES)

    # The auto-select path, asked only while that flow is running - right after
    # the Brawlers button was tapped. Here a MISS is the expensive mistake: the
    # bot concludes the list never opened, taps again and lands on the glory
    # panel ("auto select does not work"). It cannot cause the mid-match AFK bug,
    # because nothing consults it mid-match. So it keeps the tolerant settings:
    # either icon, the whole toolbar band, and a loose threshold, which is what
    # made selection work across MuMu / LDPlayer / MemU icon sizes.
    for name in ("brawler_menu_heart.png", "brawler_menu_task.png"):
        if _matches_at_any_scale(image, states_path + name,
                                 SELECTION_FLOW_REGION, SELECTION_FLOW_THRESHOLD,
                                 BRAWLER_ICON_SCALES):
            return True
    return False


def is_in_offer_popup(image) -> bool:
    return is_template_in_region(image, states_path + 'close_popup.png', region_data.get("close_popup", [1740, 140, 140, 100]))


def is_in_lobby(image) -> bool:
    return is_template_in_region(image, states_path + 'lobby_menu.png', region_data.get("lobby_menu", [1790, 20, 75, 65]))


def is_in_end_of_a_match(image):
    return find_game_result(image)


def is_in_trophy_reward(image):
    return is_template_in_region(image, states_path + 'trophies_screen.png', region_data.get("trophies_screen", [1545, 915, 365, 168]))


def is_in_brawl_pass(image):
    return is_template_in_region(image, states_path + 'brawl_pass_house.png', region_data.get('brawl_pass_house', [1750, 0, 169, 100]))


def is_in_star_road(image):
    return is_template_in_region(image, states_path + "go_back_arrow.png", region_data.get('go_back_arrow', [0, 0, 175, 110]))


def is_in_match_making(image):
    return is_template_in_region(image, states_path + "exit_match_making.png", region_data.get('exit_match_making', [1600, 925, 295, 135]))


def is_in_prestige_milestone(image):
    return is_template_in_region(image, states_path + "prestige_continue.png", region_data.get('prestige_continue', [535, 950, 345, 95]))


# The bottom of the screen, where a full-screen card puts the button that
# dismisses it. Far wider than the prestige screen's own narrow box, because
# the SAME button is not in the same place on every card: on the prestige one
# it sits left of centre, on a "NEW SKIN!" one it sits right of centre next to
# EQUIP NOW, and there is no reason to expect the next card Supercell ships to
# agree with either.
CONTINUE_BAND = [140, 860, 1640, 220]

# The button is drawn at very close to the size the template was cut at - it is
# the resolution that changes between emulators, and load_template already
# scales for that. So this is a narrow sweep in FINE steps rather than the wide
# one the toolbar icons need. Fine matters: matching the same word 5% off its
# real size drops TM_CCOEFF_NORMED from 0.89 to 0.59, which is below what a
# perfectly ordinary game screen scores, so a coarse sweep over a wide range
# would find nothing and call everything a maybe. Steps of ~3.5% keep the worst
# case within 2% of the peak.
CONTINUE_SCALES = (0.85, 0.88, 0.91, 0.94, 0.98, 1.0, 1.04, 1.07, 1.11, 1.15, 1.19)

# Measured, not guessed. The word at its right size scores 0.85-0.97; real game
# screens with no CONTINUE on them score up to 0.53 in this band. 0.75 sits in
# the gap with room on both sides.
CONTINUE_THRESHOLD = 0.75

# Anything this good is the button, and there is no point trying the remaining
# sizes to find out whether one of them is better.
CONTINUE_CONFIDENT = 0.9

# The band is shrunk to this width before matching, and the template by the
# same factor. The full-resolution sweep costs 39 ms a frame, and this check
# runs on every frame that is about to be called a match - that is most of a
# running bot's frames, spent looking for a button that is almost never there.
# TM_CCOEFF_NORMED matches on shape, which survives the downscale, and the cost
# falls with roughly the fourth power of the factor. 440 was picked by
# measurement: smaller blurs the letters until ordinary screens start scoring
# 0.6, which eats the margin the threshold above depends on.
CONTINUE_MATCH_WIDTH = 440


def find_continue_button(image):
    """Where the word CONTINUE is on screen, or None.

    Deliberately COLOUR BLIND. prestige_continue.png was cut from a green
    button, and cv2.matchTemplate on three channels scores a blue button
    against it near zero - so the green-cropped word could never find the blue
    CONTINUE on a new-skin card, and the bot sat in front of one for ten
    minutes with nothing recognising it. Converting both sides to grey throws
    the button colour away and keeps the letters, which are what actually
    identify the button: white block capitals with a heavy dark outline, the
    same on every card whatever colour Supercell paints underneath them.

    Returns a centre in the CURRENT frame's pixels, ready to click, rather than
    a bare True - a fixed coordinate is only ever right for one card layout.
    """
    current_height, current_width = image.shape[:2]
    x, y, w, h = CONTINUE_BAND
    width_ratio, height_ratio = current_width / orig_screen_width, current_height / orig_screen_height
    band_x, band_y = int(x * width_ratio), int(y * height_ratio)
    band_w, band_h = int(w * width_ratio), int(h * height_ratio)
    crop = image[band_y:band_y + band_h, band_x:band_x + band_w]
    if crop.size == 0:
        return None

    template = load_template(states_path + "prestige_continue.png",
                             current_width, current_height)
    if template is None:
        return None

    crop_grey = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    base = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)

    # Shrink both by the same factor, so their relative sizes - all
    # matchTemplate cares about - are preserved.
    work = min(1.0, CONTINUE_MATCH_WIDTH / max(crop_grey.shape[1], 1))
    if work < 1.0:
        crop_grey = cv2.resize(crop_grey,
                               (max(1, int(crop_grey.shape[1] * work)),
                                max(1, int(crop_grey.shape[0] * work))),
                               interpolation=cv2.INTER_AREA)
        base = cv2.resize(base, (max(1, int(base.shape[1] * work)),
                                 max(1, int(base.shape[0] * work))),
                          interpolation=cv2.INTER_AREA)

    base_h, base_w = base.shape[:2]
    best = None
    for scale in CONTINUE_SCALES:
        tw, th = max(1, int(base_w * scale)), max(1, int(base_h * scale))
        if th > crop_grey.shape[0] or tw > crop_grey.shape[1]:
            continue
        scaled = base if scale == 1.0 else cv2.resize(
            base, (tw, th),
            interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
        result = cv2.matchTemplate(crop_grey, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > CONTINUE_THRESHOLD and (best is None or max_val > best[0]):
            # Back out of the working scale, into the frame's own pixels.
            best = (max_val,
                    band_x + int((max_loc[0] + tw / 2) / work),
                    band_y + int((max_loc[1] + th / 2) / work))
            if max_val >= CONTINUE_CONFIDENT:
                break

    if best is None:
        return None
    return best[1], best[2]


def is_at_buffie_machine(image):
    return is_template_in_region(image, states_path + "buffie_machine.png", region_data.get('buffie_machine', [1620, 780, 160, 160]))


def is_in_nano_noodles(image):
    return is_template_in_region(image, states_path + "nano_noodles.png", region_data.get('nano_noodles', [360, 880, 215, 150]))


def is_in_daily_wins(image):
    """The daily-wins screen that asks you to pick barrels.

    Matched on the CHOOSE panel rather than on the barrels themselves: the
    barrels move, there are a varying number of them, and two of the three
    slots are already ticked by the time the screen is usually seen. The panel
    is in the same place every time and says the same word.
    """
    return is_template_in_region(image, states_path + 'noodles.png',
                                 region_data.get("daily_wins", [270, 310, 290, 120]))


# Reading a title costs about a quarter of a second, and these checks run every
# two or three seconds for as long as the bot is on. Without something cheap in
# front of them that was three quarters of a second of OCR per three seconds -
# on the bot's own loop, so it came out of the frame rate as well as the CPU.
#
# The gate is a colour test over the same crop: a few hundred microseconds, and
# on almost every frame it is the only thing that runs.
#
# One gate per dialog, because they look nothing alike. The disconnect cards
# are flat dark grey; the team invite is a bright blue modal, and the dark test
# rejects it outright - measured at a dark fraction of 0.000, which would have
# meant an invite decliner that never fired.
#
# Deliberately permissive on both. A gate that lets an ordinary frame through
# costs one wasted read; a gate that turns a real dialog away costs a match,
# and not losing matches is the entire point of these checks.
DARK_CARD = {"max_saturation": 70, "max_brightness": 110, "min_fraction": 0.20}
BLUE_MODAL = {"hue_range": (95, 135), "min_saturation": 90,
              "min_brightness": 70, "min_fraction": 0.20}


def _region_crop(image, region):
    height, width = image.shape[:2]
    x, y, w, h = region
    wr, hr = width / orig_screen_width, height / orig_screen_height
    return image[int(y * hr):int((y + h) * hr), int(x * wr):int((x + w) * wr)]


def _looks_dark(image, region):
    """A flat, colourless card - the disconnect dialogs."""
    crop = _region_crop(image, region)
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    hit = ((hsv[:, :, 1] <= DARK_CARD["max_saturation"])
           & (hsv[:, :, 2] <= DARK_CARD["max_brightness"]))
    return hit.mean() >= DARK_CARD["min_fraction"]


def _looks_blue(image, region):
    """A saturated blue panel - the team invite modal."""
    crop = _region_crop(image, region)
    if crop.size == 0:
        return False
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    low, high = BLUE_MODAL["hue_range"]
    hit = ((hsv[:, :, 0] >= low) & (hsv[:, :, 0] <= high)
           & (hsv[:, :, 1] >= BLUE_MODAL["min_saturation"])
           & (hsv[:, :, 2] >= BLUE_MODAL["min_brightness"]))
    return hit.mean() >= BLUE_MODAL["min_fraction"]


def _title_in_region(image, template, region, title, gate=None):
    """Is this dialog on screen, by its title.

    Two ways, and the cheap one first. If a template has been cut for this
    dialog it is used - matchTemplate on a small region costs almost nothing
    and is exact. Without one the title is read instead, which needs no asset
    and so works on a fresh install.

    The region is deliberately tight around the title and nothing else. That
    is what makes reading it safe where the old approach was not: "REJECT and
    ACCEPT somewhere in the middle of the screen" is a description of many
    screens, while "the words TEAM INVITE in this particular box" is a
    description of one. It also keeps the OCR cheap - the crop is a twentieth
    of the area the old one worked on.
    """
    # Existence first, so a missing template does not print "that check is
    # skipped" every time. It is not skipped - it falls through to the title,
    # and a log line saying otherwise sends people looking for a file that
    # nothing needs.
    if os.path.exists(states_path + template):
        return is_template_in_region(image, states_path + template, region)

    if gate is not None and not gate(image, region):
        return False
    return _read_title(image, region, title)


def _read_title(image, region, title):
    """The title, read out of the region. False on anything going wrong."""
    height, width = image.shape[:2]
    x, y, w, h = region
    wr, hr = width / orig_screen_width, height / orig_screen_height
    crop = image[int(y * hr):int((y + h) * hr), int(x * wr):int((x + w) * wr)]
    if crop.size == 0:
        return False

    try:
        from utils import extract_text_and_positions
        found = extract_text_and_positions(crop)
    except Exception:
        # No OCR engine, no models, no matter - this is one screen the bot
        # cannot recognise, which is a missing feature rather than a fault.
        return False

    flat = "".join(str(key) for key in found).replace(" ", "").lower()
    return title in flat


def is_team_invite_on_screen(image):
    """The team-invite dialog.

    A count of the ACCEPT button's green and a pair of OCR'd words used to
    decide this, over most of the screen, and it fired on things that were not
    invites at all - REJECT and ACCEPT are ordinary words and a green button is
    an ordinary button. The blue TEAM INVITE bar is not, and neither is its
    position.
    """
    return _title_in_region(image, "team_invite.png",
                            region_data.get("team_invite", [740, 200, 440, 170]),
                            "teaminvite", gate=_looks_blue)


def is_connection_lost_on_screen(image):
    """The "Connection lost - please try logging in again" card.

    The same card as the idle box, in the same place, and it wants the same
    answer: RETRY LOGIN reconnects into a battle that carried on without us.
    Only the title tells them apart, which is why each has its own name here
    rather than one check for "a dark card in the middle of the screen".
    """
    return _title_in_region(image, "connection_lost.png",
                            region_data.get("connection_lost", [440, 400, 520, 130]),
                            "connectionlost", gate=_looks_dark)


def is_idle_disconnect_on_screen(image):
    """The "you were disconnected for idling" box.

    This used to be a count of grey pixels in the middle of the screen, which
    a great many screens are. What it triggers now is a restart of the game, so
    a false positive costs a match rather than a stray click.
    """
    return _title_in_region(image, "idle_disconnect.png",
                            region_data.get("idle_disconnect", [430, 380, 500, 160]),
                            "idledisconnect", gate=_looks_dark)


def is_in_star_drop(image):
    for image_filename in images_with_star_drop:
        if is_template_in_region(image, star_drops_path + image_filename, region_data.get('star_drop', [790, 350, 350, 350])):
            if "angelic" in image_filename.lower(): return "angelic"
            if "demonic" in image_filename.lower(): return "demonic"
            if "starr_nova" in image_filename.lower(): return "starr_nova"
            return "regular"
    return False


def get_state(screenshot):
    state = get_in_game_state(screenshot)
    if config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('state_finder_debug'), False): cv2.imwrite(f"./debug_frames/state_screenshot_{state}_{len(os.listdir('./debug_frames'))}.png", cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB))
    return state
