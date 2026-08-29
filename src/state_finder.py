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
        if is_in_shop(image): return "shop"
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

        return "match"
    finally:
        should_print_debug_info = False


def is_in_shop(image) -> bool:
    return is_template_in_region(image, states_path + 'powerpoint.png', region_data.get("powerpoint", [1000, 5, 80, 80]))


def is_in_brawler_selection(image) -> bool:
    return is_template_in_region(image, states_path + 'brawler_menu_heart.png', region_data.get("brawler_menu_heart", [1470, 0, 430, 140]))


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
