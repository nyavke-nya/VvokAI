import math
import random
import threading
import time
import cv2
import numpy as np
import os

from detect import Detect
from dodge.config import DodgeConfig
from dodge.service import DodgeService
from dodge.smoothing import MovementShaper
from dodge.solver import DodgeSolver
from dodge.vitals import HealthReader, entity_key
try:
    from early_access.early_access import add_advanced_visuals
    early_access = True
except ImportError:
    early_access = False
    def add_advanced_visuals(a, b):
        return None
from state_finder import get_state
from utils import load_toml_as_dict, count_hsv_pixels, load_brawlers_info, interpret_pyla_code, \
    count_mask_pixels, JOYSTICK_RADIUS, clamp, config_bool, is_safe_ast


brawl_stars_width, brawl_stars_height = 1920, 1080
super_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['super']
gadget_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['gadget']
hypercharge_crop_area = load_toml_as_dict("./cfg/lobby_config.toml")['pixel_counter_crop_area']['hypercharge']
POISON_LOW_HSV = np.array((30, 90, 221), dtype=np.uint8)
POISON_HIGH_HSV = np.array((57, 114, 235), dtype=np.uint8)
PLAYER_HIT_CIRCLE_RADIUS = 53

# Half a brawler, near enough. Used to decide whether an enemy who is partly
# out from behind a wall can be shot at - the centre alone answers that
# question about 140 ms too late at normal walking speed.
ENEMY_EXPOSURE_RADIUS = 40

# Every attack_range in cfg/brawlers_info.json is multiplied by this before the
# playstyle sees it.
#
# Those numbers are inherited from PylaAI and they are all short. Measured on a
# captured frame: the map border fence draws one post per tile and the posts sit
# 98 px apart, and the ring under the player - which the game draws at about one
# tile across - is 115 px wide. So a tile is roughly 98 px on screen, while the
# range table is written as if a tile were 54. Checked against the projectile
# speeds in the dodge log too: at 98 px/tile the fastest shot seen reads 15
# tiles/s, which is a real Brawl Stars speed; at 54 it would read 28, which
# nothing in the game does.
#
# The result was a bot that opened fire at roughly half of its brawler's actual
# reach, walked most of the way in before it could shoot at all, and ate the
# whole approach. Two people described exactly that within a day of each other:
# "he barely attacks until he is practically touching them" on Mortis, and "mine
# only ever shoots point blank" on everyone else.
#
# Not corrected to the full 1.9x it measures at. The table is not a clean
# scaling of the real ranges - El Primo is already over his, most others are at
# 50-65% - so a blanket doubling would push several brawlers past their reach
# and have them firing into empty air. 1.35 moves the whole set from about
# half of true range to about three quarters, which is where a brawler wants
# to be fighting anyway: inside its own reach with margin to spare.
#
# Configurable because it is a calibration, not a preference. 1.0 restores the
# old behaviour exactly.
ATTACK_RANGE_MULTIPLIER = 1.35

# How much of the region beside the player must match the gas colour before the
# bot treats that direction as gassed.
#
# A fraction of the region's area, and it has to be, because the region is
# measured in screen pixels and therefore scales with the square of the
# emulator resolution. The old absolute count of 7000 was tuned at 1920x1080,
# where it is 7.8% of the area searched. The identical code on a 1280x720
# emulator demands 17.5% of a smaller cloud, and at 960x540 it demands 31% -
# so the same build, on the same game, walked into gas on one machine and
# avoided it on another, which is exactly the report this came from.
#
# 0.078 reproduces the old behaviour at 1920x1080 and fixes every other size.
POISON_GAS_FRACTION = 0.078

# Wall collision is NOT the same circle as the projectile hitbox.
#
# A brawler's damage hitbox is roughly a tile wide, but its movement collision
# is much smaller - in game they walk through one-tile gaps without touching
# the sides. Using the 53 px hit radius for pathing meant a two-tile corridor
# (108 px) had to fit a 106 px circle, so the bot declared it blocked and
# refused to enter.
#
# 24 px leaves 6 px of clearance in a single-tile gap and passes a two-tile gap
# comfortably. Raise it if the bot starts clipping wall corners.
PLAYER_COLLISION_RADIUS = 24

class Play:

    def __init__(self, main_info_model, tile_detector_model, close_tile_detector_model, window_controller,
                 pyla_code, playstyle_info=None):
        # A playstyle can opt out of the whole projectile stack by putting
        # "dodge": false in its metadata header. That is not the same as simply
        # never calling solve_dodge(): the tracker runs on its own thread at
        # capture rate whether or not anybody reads its output, so a playstyle
        # that ignores it still pays for it.
        self.playstyle_info = playstyle_info or {}
        bot_config = load_toml_as_dict("cfg/bot_config.toml")
        time_config = load_toml_as_dict("cfg/time_tresholds.toml")
        self.fix_movement_keys = {
            "delay_to_trigger": bot_config["unstuck_movement_delay"],
            "duration": bot_config["unstuck_movement_hold_time"],
            "toggled": False,
            "started_at": time.time(),
            "fixed": (0, 0),
            "last_direction_key": None,
            "rotation_sign": 1,
            "rotation_angle_step": 1,
            "max_rotation_angle_step": 4,
        }
        self.super_treshold = time_config["super"]
        self.gadget_treshold = time_config["gadget"]
        self.hypercharge_treshold = time_config["hypercharge"]
        self.walls_treshold = time_config["wall_detection"]
        self.last_walls_data = []
        self.last_bushes_data = []
        # Where the camera was when those walls were detected. Reusing wall
        # boxes without this leaves them at last-seen screen coordinates while
        # the camera keeps panning, so they drift away from the walls they
        # describe - by up to 165 px at a 0.5 s refresh and a normal walking
        # speed. A box in the wrong place blocks a line of sight that is
        # actually clear, which is a bot that will not shoot an enemy who has
        # stepped out from cover until the next refresh.
        self.last_walls_odometer = (0.0, 0.0)

        emotes = load_toml_as_dict("./cfg/lobby_config.toml").get("emotes") or {}
        self.emote_interval = float(emotes.get("every_seconds", 0) or 0)
        self.emote_bubble = emotes.get("bubble")
        self.emote_buttons = emotes.get("buttons") or []
        # How long the grid takes to animate open. In config because it is a
        # property of the game and the device, not of this code.
        self.emote_open_delay = float(emotes.get("open_delay", 0.35) or 0.35)
        # Started in the future rather than at zero, so the first emote waits
        # its turn instead of firing on the opening frame of every match.
        self.time_since_emote = time.time()
        self._emote_thread = None
        self.keys_hold = []
        self.time_since_different_movement = time.time()
        self.time_since_gadget_checked = time.time()
        self.is_gadget_ready = False
        self.time_since_hypercharge_checked = time.time()
        self.is_hypercharge_ready = False
        self.time_since_super_checked = time.time()
        self.is_super_ready = False
        self.window_controller = window_controller
        self.TILE_SIZE = bot_config.get("perceived_tile_size", 54)
        # Configurable so a different emulator resolution or a map with tighter
        # geometry can be tuned without editing code.
        try:
            self.collision_radius = float(
                bot_config.get("player_collision_radius", PLAYER_COLLISION_RADIUS))
        except (TypeError, ValueError):
            self.collision_radius = float(PLAYER_COLLISION_RADIUS)
        self.centered_wall_detection = config_bool(bot_config.get("centered_wall_detection"), False)
        # How far to either side of an enemy still counts as the enemy, when
        # deciding whether a wall is in the way. Roughly half a brawler.
        try:
            self.enemy_exposure_radius = float(
                bot_config.get("enemy_exposure_radius", ENEMY_EXPOSURE_RADIUS))
        except (TypeError, ValueError):
            self.enemy_exposure_radius = float(ENEMY_EXPOSURE_RADIUS)
        self.centered_wall_crop_size = 640
        # How much of the area beside the player has to look like gas before it
        # counts. A FRACTION, not a pixel count - see _measure_poison_gas.
        try:
            self.poison_gas_fraction = float(
                bot_config.get("poison_gas_fraction", POISON_GAS_FRACTION))
        except (TypeError, ValueError):
            self.poison_gas_fraction = POISON_GAS_FRACTION
        # See ATTACK_RANGE_MULTIPLIER. Clamped rather than trusted: a zero or a
        # negative here would make every brawler unable to shoot at all, and a
        # typo in a config file should not be able to do that.
        try:
            self.attack_range_multiplier = float(
                bot_config.get("attack_range_multiplier", ATTACK_RANGE_MULTIPLIER))
        except (TypeError, ValueError):
            self.attack_range_multiplier = ATTACK_RANGE_MULTIPLIER
        if not 0.25 <= self.attack_range_multiplier <= 4.0:
            print(f"attack_range_multiplier {self.attack_range_multiplier} is out of "
                  f"the sensible 0.25-4.0 range, using {ATTACK_RANGE_MULTIPLIER}")
            self.attack_range_multiplier = ATTACK_RANGE_MULTIPLIER

        bot_config = load_toml_as_dict("cfg/bot_config.toml")
        time_config = load_toml_as_dict("cfg/time_tresholds.toml")
        self.verbose_debug = config_bool(load_toml_as_dict("cfg/debug_settings.toml").get('verbose_debug'), False)
        if self.verbose_debug:
            if not os.path.exists("debug_frames"):
                os.makedirs("debug_frames")
        self.Detect_main_info = Detect(main_info_model, classes=['enemy', 'teammate', 'player'])
        self.tile_detector_model_classes = bot_config["wall_model_classes"]
        self.Detect_tile_detector = None if self.centered_wall_detection else Detect(
            tile_detector_model,
            classes=self.tile_detector_model_classes
        )
        self.Detect_centered_tile_detector = Detect(
            close_tile_detector_model,
            classes=self.tile_detector_model_classes
        ) if self.centered_wall_detection else None

        self.time_since_walls_checked = 0
        self.time_since_player_last_found = time.time()
        self.current_brawler = None
        self.brawlers_info = load_brawlers_info()
        self.brawler_ranges = None
        self.time_since_detections = {
            "player": time.time(),
            "enemy": time.time(),
        }
        self.time_since_last_proceeding = time.time()

        self.last_movement = ''
        self.last_movement_change_time = time.time()
        self.minimum_movement_delay = bot_config["minimum_movement_delay"]
        self.no_detection_proceed_delay = time_config["no_detection_proceed"]
        self.gadget_pixels_minimum = bot_config["gadget_pixels_minimum"]
        self.hypercharge_pixels_minimum = bot_config["hypercharge_pixels_minimum"]
        self.super_pixels_minimum = bot_config["super_pixels_minimum"]
        self.wall_detection_confidence = bot_config["wall_detection_confidence"]
        self.entity_detection_confidence = bot_config["entity_detection_confidence"]
        self.seconds_to_hold_attack_after_reaching_max = load_toml_as_dict("cfg/bot_config.toml")["seconds_to_hold_attack_after_reaching_max"]
        self.persistent_data = {"time_since_holding_attack": None}
        if isinstance(pyla_code, str):
            is_safe, error_msg = is_safe_ast(pyla_code)
            if not is_safe:
                print(f"Security/Syntax Validation Failed for playstyle: {error_msg}")
                self.pyla_code = compile("", "<string>", "exec")
            else:
                self.pyla_code = compile(pyla_code, "<pyla_script>", "exec")
        else:
            self.pyla_code = pyla_code
        self.context = None
        self.frame = None

        # Dodging needs the window controller's scale factor, which only exists
        # after the first frame arrives, so the service is built lazily.
        self.dodge_service = None
        self.health_reader = None
        self.health_readings = {}
        self.last_gas_reading = None
        self.last_gas_center = None
        self._stage_times = {}
        self._stage_iterations = 0
        self._stage_reported = time.perf_counter()
        self.dodge_config = None
        self.dodge_solver = None
        self.movement_shaper = None
        self.last_pyla_globals = {}
        self.last_player_box = None
        self.last_dodge_decision = None
        self.last_aim_solution = None
        self.was_dodging = False

    @staticmethod
    def get_entity_pos(entity):
        return (entity[0] + entity[2]) / 2, (entity[1] + entity[3]) / 2

    @staticmethod
    def get_distance(enemy_coords, player_coords):
        return math.hypot(enemy_coords[0] - player_coords[0], enemy_coords[1] - player_coords[1])

    @staticmethod
    def is_there_enemy(enemy_data):
        if not enemy_data:
            return False
        return True

    def attack(self, touch_up=True, touch_down=True):
        self.window_controller.press("attack", touch_up=touch_up, touch_down=touch_down)

    def use_hypercharge(self):
        print("Using hypercharge")
        self.window_controller.press("hypercharge")
        self.time_since_hypercharge_checked = time.time()
        self.is_hypercharge_ready = False

    def use_gadget(self):
        print("Using gadget")
        self.window_controller.press("gadget")
        self.time_since_gadget_checked = time.time()
        self.is_gadget_ready = False

    def use_super(self):
        print("Using super")
        self.window_controller.press("super")
        self.time_since_super_checked = time.time()
        self.is_super_ready = False

    @staticmethod
    def get_random_movement():
        random_movement = random.randint(-75, 75), random.randint(-75, 75)
        return random_movement

    @staticmethod
    def movement_to_vector(movement):
        if not isinstance(movement, (tuple, list)) or len(movement) != 2:
            return None

        x, y = movement
        if x is None or y is None:
            return None

        try:
            return float(x), float(y)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def rotate_movement(movement, angle_radians):
        x, y = movement
        cos_angle = math.cos(angle_radians)
        sin_angle = math.sin(angle_radians)
        return (
            x * cos_angle - y * sin_angle,
            x * sin_angle + y * cos_angle,
        )

    @staticmethod
    def movement_direction_key(movement):
        x, y = movement
        magnitude = math.hypot(x, y)
        if magnitude < 1:
            return None

        angle = math.atan2(y, x)
        return round(angle / (math.pi / 8)) % 16

    def unstuck_movement_if_needed(self, movement, current_time=None):
        if current_time is None:
            current_time = time.time()

        movement_vector = self.movement_to_vector(movement)
        if movement_vector is None:
            self.fix_movement_keys["toggled"] = False
            self.fix_movement_keys["last_direction_key"] = None
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time
            return movement

        direction_key = self.movement_direction_key(movement_vector)
        if direction_key is None:
            self.fix_movement_keys["toggled"] = False
            self.fix_movement_keys["last_direction_key"] = None
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time
            return movement_vector

        if self.fix_movement_keys['toggled']:
            if current_time - self.fix_movement_keys['started_at'] > self.fix_movement_keys['duration']:
                self.fix_movement_keys['toggled'] = False
                self.fix_movement_keys["last_direction_key"] = direction_key
                self.time_since_different_movement = current_time
                return movement_vector

            return self.fix_movement_keys['fixed']

        if self.fix_movement_keys["last_direction_key"] != direction_key:
            self.fix_movement_keys["last_direction_key"] = direction_key
            self.fix_movement_keys["rotation_sign"] = 1
            self.fix_movement_keys["rotation_angle_step"] = 1
            self.time_since_different_movement = current_time

        if current_time - self.time_since_different_movement > self.fix_movement_keys["delay_to_trigger"]:
            self.fix_movement_keys["rotation_sign"] *= -1
            angle_step = self.fix_movement_keys["rotation_angle_step"]
            rotated_movement = self.rotate_movement(
                movement_vector,
                self.fix_movement_keys["rotation_sign"] * angle_step * math.pi / 4
            )
            if self.fix_movement_keys["rotation_sign"] > 0:
                self.fix_movement_keys["rotation_angle_step"] += 1
                if self.fix_movement_keys["rotation_angle_step"] > self.fix_movement_keys["max_rotation_angle_step"]:
                    self.fix_movement_keys["rotation_angle_step"] = 1

            self.fix_movement_keys['fixed'] = rotated_movement
            self.fix_movement_keys['toggled'] = True
            self.fix_movement_keys['started_at'] = current_time
            return rotated_movement

        return movement_vector

    def load_brawler_ranges(self, brawlers_info=None):
        if not brawlers_info:
            brawlers_info = load_brawlers_info()
        screen_size_ratio = self.window_controller.scale_factor
        # safe_range is deliberately left alone. It is not a reach, it is "do
        # not let anything get closer than this", and the table's version of
        # that is already about right - stretching it would push brawlers away
        # from fights they should be taking.
        reach_ratio = screen_size_ratio * self.attack_range_multiplier
        ranges = {}
        for brawler, info in brawlers_info.items():
            attack_range = info['attack_range']
            safe_range = info['safe_range']
            super_range = info['super_range']
            ranges[brawler] = [int(safe_range * screen_size_ratio),
                               int(attack_range * reach_ratio),
                               int(super_range * reach_ratio)]
        return ranges

    @staticmethod
    def can_attack_through_walls(brawler, skill_type, brawlers_info=None):
        if not brawlers_info: brawlers_info = load_brawlers_info()
        if skill_type == "attack":
            return brawlers_info[brawler]['ignore_walls_for_attacks']
        elif skill_type == "super":
            return brawlers_info[brawler]['ignore_walls_for_supers']
        raise ValueError("skill_type must be either 'attack' or 'super'")

    @staticmethod
    def has_placed_attack(brawler, brawlers_info=None):
        """True when the attack lands where the stick points, not just along it.

        Throwers - Barley, Dynamike, Tick and the rest - aim at a POINT: the
        angle of the drag picks the direction and its length picks the range.
        Everyone else fires along the angle and the length means nothing, which
        is the assumption aimed_attack was written on.
        """
        if not brawlers_info:
            brawlers_info = load_brawlers_info()
        entry = brawlers_info.get(brawler) or {}
        return bool(entry.get("placed_attack"))

    @staticmethod
    def must_brawler_hold_attack(brawler, brawlers_info=None):
        if not brawlers_info: brawlers_info = load_brawlers_info()
        return brawlers_info[brawler]['hold_attack'] > 0

    @staticmethod
    def walls_block_line_of_sight(p1, p2, walls):
        if not walls:
            return False

        p1_t = (int(p1[0]), int(p1[1]))
        p2_t = (int(p2[0]), int(p2[1]))
        min_x, max_x = min(p1_t[0], p2_t[0]), max(p1_t[0], p2_t[0])
        min_y, max_y = min(p1_t[1], p2_t[1]), max(p1_t[1], p2_t[1])
        for wall in walls:
            x1, y1, x2, y2 = wall

            if max_x < x1 or min_x > x2 or max_y < y1 or min_y > y2:
                continue

            rect = (int(x1), int(y1), int(x2 - x1), int(y2 - y1))
            if cv2.clipLine(rect, p1_t, p2_t)[0]:
                return True
        return False

    def get_player_hit_circle(self, player_box):
        radius = PLAYER_HIT_CIRCLE_RADIUS * (self.window_controller.scale_factor or 1)
        if player_box and len(player_box) >= 4:
            x1, y1, x2, y2 = player_box[:4]
            return ((x1 + x2) / 2, y2 - radius), radius

        return None, radius

    def get_actual_player_box(self, player_box):
        center, radius = self.get_player_hit_circle(player_box)
        if center is None:
            return None
        return [
            center[0] - radius,
            center[1] - radius,
            center[0] + radius,
            center[1] + radius,
        ]

    @staticmethod
    def point_rect_distance_sq(point, rect):
        x, y = point
        x1, y1, x2, y2 = rect
        dx = max(x1 - x, 0, x - x2)
        dy = max(y1 - y, 0, y - y2)
        return dx * dx + dy * dy

    @staticmethod
    def walls_block_swept_circle(p1, p2, radius, walls):
        if not walls:
            return False

        p1_t = (int(p1[0]), int(p1[1]))
        p2_t = (int(p2[0]), int(p2[1]))
        min_x, max_x = min(p1_t[0], p2_t[0]), max(p1_t[0], p2_t[0])
        min_y, max_y = min(p1_t[1], p2_t[1]), max(p1_t[1], p2_t[1])
        radius = int(math.ceil(radius))

        for wall in walls:
            x1, y1, x2, y2 = wall[:4]
            wall_rect = (x1, y1, x2, y2)
            expanded_x1 = int(x1 - radius)
            expanded_y1 = int(y1 - radius)
            expanded_x2 = int(x2 + radius)
            expanded_y2 = int(y2 + radius)

            if max_x < expanded_x1 or min_x > expanded_x2 or max_y < expanded_y1 or min_y > expanded_y2:
                continue

            rect = (
                expanded_x1,
                expanded_y1,
                max(1, expanded_x2 - expanded_x1),
                max(1, expanded_y2 - expanded_y1),
            )
            if cv2.clipLine(rect, p1_t, p2_t)[0]:
                radius_sq = radius * radius
                start_distance_sq = Play.point_rect_distance_sq(p1, wall_rect)
                end_distance_sq = Play.point_rect_distance_sq(p2, wall_rect)
                if start_distance_sq <= radius_sq and end_distance_sq > start_distance_sq:
                    continue
                return True

        return False

    def send_emote_if_due(self, current_time):
        """Tap the chat bubble and one emote, on a timer.

        On its own thread, and that is not a detail. The two taps need a pause
        between them - the grid animates open, and a tap during it goes through
        to the map, which in a match means walking somewhere or firing at
        nothing - and waiting for that on the main loop stops the bot reading
        the screen for a third of a second. A third of a second is several
        dodges. The star drop handler is threaded for the same reason.

        The grid's own bottom-right cell is the chat button again, so it is not
        among the buttons; picking it would close the panel and send nothing.
        """
        if self.emote_interval <= 0 or not self.emote_bubble or not self.emote_buttons:
            return
        if current_time - self.time_since_emote < self.emote_interval:
            return
        if self._emote_thread is not None and self._emote_thread.is_alive():
            return
        self.time_since_emote = current_time

        def _send():
            import random

            bubble_x, bubble_y = self.emote_bubble
            self.window_controller.click(bubble_x, bubble_y)
            time.sleep(self.emote_open_delay)
            button_x, button_y = random.choice(self.emote_buttons)
            self.window_controller.click(button_x, button_y)

        self._emote_thread = threading.Thread(target=_send, daemon=True,
                                              name="pyla-emote")
        self._emote_thread.start()

    def camera_odometer(self):
        """How far the camera has panned since the run started, in px."""
        service = self.dodge_service
        motion = service.motion if service else None
        return motion.odometer if motion else (0.0, 0.0)

    @staticmethod
    def shift_boxes(boxes, shift):
        """Move detection boxes by (dx, dy), leaving anything malformed alone."""
        dx, dy = shift
        if not dx and not dy:
            return boxes
        moved = []
        for box in boxes:
            if len(box) >= 4:
                moved.append([box[0] + dx, box[1] + dy,
                              box[2] + dx, box[3] + dy] + list(box[4:]))
            else:
                moved.append(box)
        return moved

    def is_enemy_hittable(self, player_pos, enemy_pos, walls, skill_type):
        if self.can_attack_through_walls(self.current_brawler, skill_type, self.brawlers_info):
            return True
        if not self.walls_block_line_of_sight(player_pos, enemy_pos, walls):
            return True

        # The centre is behind cover, which is not the same as the brawler
        # being behind cover. A brawler is about ninety pixels wide, so someone
        # stepping out from a wall is shootable for the ~140 ms it takes their
        # centre to follow their shoulder into the open - and for all of that
        # time a centre-only test says "no shot" while the enemy is plainly
        # standing there. That is the delay before the bot opens fire.
        #
        # So the edges are tried too, offset across the line of sight. Two more
        # traces, and only when the centre was already blocked.
        for point in self.exposed_edges(player_pos, enemy_pos):
            if not self.walls_block_line_of_sight(player_pos, point, walls):
                return True
        return False

    def exposed_edges(self, player_pos, enemy_pos):
        """The two points either side of an enemy, across the line of sight."""
        dx = enemy_pos[0] - player_pos[0]
        dy = enemy_pos[1] - player_pos[1]
        length = math.hypot(dx, dy)
        if length < 1e-6:
            return ()
        # Perpendicular to the line, so the offsets are the parts of the
        # brawler that come out from behind a wall first.
        offset = self.enemy_exposure_radius * (self.window_controller.scale_factor or 1)
        nx, ny = -dy / length * offset, dx / length * offset
        return ((enemy_pos[0] + nx, enemy_pos[1] + ny),
                (enemy_pos[0] - nx, enemy_pos[1] - ny))

    def find_closest_enemy(self, enemy_data, player_coords, walls, skill_type):
        player_pos_x, player_pos_y = player_coords
        closest_hittable_distance = float('inf')
        closest_unhittable_distance = float('inf')
        closest_hittable = None
        closest_unhittable = None
        for enemy in enemy_data:
            enemy_pos = self.get_entity_pos(enemy)
            distance = self.get_distance(enemy_pos, player_coords)
            if self.is_enemy_hittable((player_pos_x, player_pos_y), enemy_pos, walls, skill_type):
                if distance < closest_hittable_distance:
                    closest_hittable_distance = distance
                    closest_hittable = [enemy_pos, distance]
            else:
                if distance < closest_unhittable_distance:
                    closest_unhittable_distance = distance
                    closest_unhittable = [enemy_pos, distance]
        if closest_hittable:
            return closest_hittable
        elif closest_unhittable:
            return closest_unhittable

        return None, None

    def find_closest_teammate(self, teammate_data, player_coords, walls):
        closest_distance = float('inf')
        closest_teammate = None
        for teammate in teammate_data:
            teammate_pos = self.get_entity_pos(teammate)
            distance = self.get_distance(teammate_pos, player_coords)
            if distance < closest_distance:
                closest_distance = distance
                closest_teammate = teammate_pos
        return closest_teammate, closest_distance

    def is_there_poison_gas(self, player_data, threshold=None, area_from_player_checked=1.5):
        # None means "use the configured fraction". An explicit value is still
        # a fraction of the area, never the pixel count this used to take.
        if threshold is None:
            threshold = self.poison_gas_fraction
        reading = self._measure_poison_gas(player_data, threshold, area_from_player_checked)
        # Kept so the dodge service's emergency path can veto an escape without
        # re-scanning the frame from another thread.
        self.last_gas_reading = reading
        box = player_data[0] if isinstance(player_data, list) and player_data             and isinstance(player_data[0], list) else player_data
        self.last_gas_center = self.get_entity_pos(box) if box else None
        return reading

    def _measure_poison_gas(self, player_data, threshold=POISON_GAS_FRACTION,
                            area_from_player_checked=1.5):
        actual_player_box = self.get_actual_player_box(player_data) or player_data
        px1, py1, px2, py2 = actual_player_box
        player_width = max(px2 - px1, 1)
        player_height = max(py2 - py1, 1)
        min_x = int(max(px1 - player_width*area_from_player_checked, 0))
        max_x = int(min(px2 + player_width*area_from_player_checked, self.window_controller.width))
        min_y = int(max(py1 - player_height*area_from_player_checked, 0))
        max_y = int(min(py2 + player_height*area_from_player_checked, self.window_controller.height))

        if min_x >= max_x or min_y >= max_y:
            return {
                "up": 0,
                "down": 0,
                "left": 0,
                "right": 0,
            }

        roi = self.frame[min_y:max_y, min_x:max_x]
        hsv_roi = cv2.cvtColor(roi, cv2.COLOR_RGB2HSV)

        mask = cv2.inRange(hsv_roi, POISON_LOW_HSV, POISON_HIGH_HSV)
        x, y = self.get_entity_pos(actual_player_box)
        roi_w = int(max_x - min_x)
        roi_h = int(max_y - min_y)
        local_px = int(clamp(x - min_x, 0, roi_w))
        local_py = int(clamp(y - min_y, 0, roi_h))

        counts = {
            "up": count_mask_pixels(mask, 0, 0, roi_w, local_py),
            "down": count_mask_pixels(mask, 0, local_py, roi_w, roi_h),
            "left": count_mask_pixels(mask, 0, 0, local_px, roi_h),
            "right": count_mask_pixels(mask, local_px, 0, roi_w, roi_h),
        }

        # Each direction is judged against its own area. They are not equal -
        # the player is rarely dead centre, and near the edge of the map the
        # region is clipped - so one shared number would be a different
        # standard for each of the four.
        areas = {
            "up": roi_w * local_py,
            "down": roi_w * max(roi_h - local_py, 0),
            "left": local_px * roi_h,
            "right": max(roi_w - local_px, 0) * roi_h,
        }
        result = {
            direction: count if count > threshold * areas[direction] else 0
            for direction, count in counts.items()
        }

        if self.verbose_debug:
            print("Poison gas pixels:", counts)
            print("  needed:", {d: int(threshold * a) for d, a in areas.items()})

            ts = int(time.time())

            debug_regions = {
                "up": roi[0:local_py, 0:roi_w],
                "down": roi[local_py:roi_h, 0:roi_w],
                "left": roi[0:roi_h, 0:local_px],
                "right": roi[0:roi_h, local_px:roi_w],
            }

            for direction, img in debug_regions.items():
                if img.size > 0:
                    cv2.imwrite(
                        f"debug_frames/poison_gas_{direction}_debug_{ts}.png",
                        cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                    )

        return result

    def get_main_data(self, frame):
        data = self.Detect_main_info.detect_objects(frame, conf_tresh=self.entity_detection_confidence)
        return data

    def is_path_blocked(self, player_box, move_direction, walls, distance=None):
        if distance is None:
            distance = self.TILE_SIZE*self.window_controller.scale_factor
        movement = self.movement_to_vector(move_direction)
        if movement is None:
            return False

        magnitude = math.hypot(movement[0], movement[1])
        if magnitude < 1:
            return False

        dx = movement[0] / magnitude * distance
        dy = movement[1] / magnitude * distance
        hit_circle_center, _ = self.get_player_hit_circle(player_box)
        if hit_circle_center is None:
            return False

        # Movement uses the collision radius, not the projectile hitbox: the
        # latter is nearly a tile wide and made two-tile gaps look impassable.
        radius = self.collision_radius
        new_pos = (hit_circle_center[0] + dx, hit_circle_center[1] + dy)
        return self.walls_block_swept_circle(hit_circle_center, new_pos, radius, walls)

    @staticmethod
    def validate_game_data(data):
        incomplete = False
        if "player" not in data.keys():
            incomplete = True  # This is required so track_no_detections can also keep track if enemy is missing

        if "enemy" not in data.keys():
            data['enemy'] = []

        if "teammate" not in data.keys():
            data['teammate'] = []

        if 'wall' not in data.keys() or not data['wall']:
            data['wall'] = []

        if 'bush' not in data.keys() or not data['bush']:
            data['bush'] = []

        return False if incomplete else data

    def track_no_detections(self, data):
        if not data:
            data = {
                "enemy": None,
                "player": None
            }
        for key in self.time_since_detections:
            if key in data and data[key]:
                self.time_since_detections[key] = time.time()

    def do_movement(self, movement):
        movement_vector = self.movement_to_vector(movement)
        if movement_vector is None:
            self.window_controller.release_movement()
            return
        self.window_controller.move(*movement_vector)

    def get_brawler_range(self, brawler):
        if self.brawler_ranges is None:
            self.brawler_ranges = self.load_brawler_ranges(self.brawlers_info)
        return self.brawler_ranges[brawler]

    def clamp_movement(self, movement):
        x, y = movement
        target_x = clamp(x, -JOYSTICK_RADIUS*self.window_controller.width_ratio, JOYSTICK_RADIUS*self.window_controller.width_ratio)
        target_y = clamp(y, -JOYSTICK_RADIUS*self.window_controller.height_ratio, JOYSTICK_RADIUS*self.window_controller.height_ratio)
        return target_x, target_y

    def ensure_dodge_service(self):
        if self.dodge_service is not None:
            return self.dodge_service
        if not self.window_controller.scale_factor:
            return None

        self.dodge_config = DodgeConfig.load(
            scale_factor=self.window_controller.scale_factor,
            tile_size=self.TILE_SIZE,
        )
        if self.playstyle_info.get("dodge") is False:
            # The tracker costs ~3.3 ms of a core per captured frame and the
            # aim solver needs its camera-pan estimate to tell a moving enemy
            # apart from a moving camera, so both go together. Health reading
            # and movement smoothing do not depend on either and stay on.
            self.dodge_config.enabled = False
            self.dodge_config.aim_enabled = False
            print(f"Playstyle '{self.playstyle_info.get('name', '?')}' declares dodge off: "
                  "projectile tracker and aim solver will not start.")
        self.movement_shaper = MovementShaper(self.dodge_config)
        # A second solver instance, so the playstyle calling solve_dodge() does
        # not fight the tracker thread over the shared commitment state.
        self.dodge_solver = DodgeSolver(self.dodge_config)
        self.dodge_service = DodgeService(
            self.window_controller,
            config=self.dodge_config,
            tile_size=self.TILE_SIZE,
        )
        self.dodge_service.start()
        self.health_reader = HealthReader(self.dodge_config)
        self.health_readings = {}
        if not self.dodge_config.enabled:
            print("Dodge tracker disabled in cfg/dodge_config.toml.")
        return self.dodge_service

    def read_vitals(self, data):
        """Health for every brawler on screen, keyed by where it is.

        Read once per bot iteration rather than per tracker frame: health moves
        in visible chunks and the decisions it feeds - disengage, push, pick a
        target - are made at the playstyle's rate anyway.
        """
        self.health_readings = {}
        config = self.dodge_config
        if self.health_reader is None or not config or not config.health_enabled:
            return
        frame = self.frame
        if frame is None:
            return

        def record(boxes, hostile, salt):
            for box in boxes or []:
                if len(box) < 4:
                    continue
                key = entity_key(box, salt)
                reading = self.health_reader.read_tracked(frame, box, hostile, key)
                if reading.known:
                    self.health_readings[key] = reading

        record(data.get('player'), False, "p")
        record(data.get('enemy'), True, "e")
        # Teammate health is measured only to be drawn. No tactic consults it -
        # assess_fight counts nearby teammates but never asks how hurt they
        # are - so outside the debug view it is pure cost, and at six brawlers
        # on screen it was about a third of this function.
        if self.dodge_config.debug_overlay:
            record(data.get('teammate'), False, "t")

    def escape_leads_into_hazard(self, vector):
        """Poison, or something a thrower left on the ground.

        One veto covering both, because to the bot they are the same problem:
        somewhere it must not walk, that no amount of dodging justifies
        entering. Handed to the dodge service so its emergency path can refuse,
        and exposed to the playstyle so ordinary movement refuses too.
        """
        if self.escape_leads_into_gas(vector):
            return True
        service = self.dodge_service
        if service is None or not service.enabled:
            return False
        center = self.last_gas_center
        if center is None:
            return False
        return service.leads_into_hazard(center, vector)

    def escape_leads_into_gas(self, vector):
        """Would sidestepping this way put the brawler in poison?

        Handed to the dodge service so its emergency path can refuse. That path
        exists precisely to skip the playstyle when a shot is seconds from
        landing, which also skips the playstyle's own gas veto - and poison is
        the one thing that is unambiguously worse than the shot. A projectile
        takes a chunk of health once; standing in gas takes one every tick,
        and the bot would have walked in on purpose.

        Read from the gas measurement the playstyle already made this
        iteration, so this costs nothing beyond a dictionary lookup.
        """
        reading = self.last_gas_reading
        if not reading or not vector:
            return False

        x, y = vector[0], vector[1]
        # The reading is pixel counts per side of the player. Any side the
        # escape has a real component toward, that is showing gas, vetoes it.
        if x > 1e-6 and reading.get("right"):
            return True
        if x < -1e-6 and reading.get("left"):
            return True
        if y > 1e-6 and reading.get("down"):
            return True
        if y < -1e-6 and reading.get("up"):
            return True
        return False

    def health_of(self, box, hostile=None):
        """Health fraction for a box, or None when it could not be read.

        The playstyle is expected to treat None as "no information" and fall
        back to distance, rather than assuming full health - guessing here
        would make the bot commit to fights on the strength of a reading that
        never happened.
        """
        if not box or len(box) < 4:
            return None
        for salt in (("e",) if hostile else ("p", "t") if hostile is False else ("p", "t", "e")):
            reading = self.health_readings.get(entity_key(box, salt))
            if reading is not None:
                return reading.fraction
        return None

    def get_projectiles(self):
        if self.dodge_service is None or not self.dodge_service.enabled:
            return []
        return self.dodge_service.get_projectiles()

    def solve_dodge(self, tactical_movement=None, projectiles=None):
        """Playstyle entry point: where should I go to not get hit?

        Returns None when nothing is incoming, so a playstyle can simply write
        `decision = solve_dodge(movement)` and check for None.
        """
        if self.dodge_service is None or not self.dodge_service.enabled:
            return None
        if self.dodge_solver is None or not self.last_player_box:
            return None

        center, radius = self.get_player_hit_circle(self.last_player_box)
        if center is None:
            return None

        if projectiles is None:
            projectiles = self.dodge_service.get_projectiles()
        if not projectiles:
            return None

        walls = (self.context or {}).get('walls') or self.last_walls_data
        player_box = self.last_player_box

        def blocked(vector):
            return self.is_path_blocked(player_box, vector, walls)

        decision = self.dodge_solver.solve(
            projectiles,
            center,
            radius,
            tactical_movement,
            blocked,
            player_speed=self.dodge_service.player_speed,
            motion=self.dodge_service.motion,
            # Same veto the emergency path uses, so the two cannot pick
            # different escapes for the same shot.
            hazard_veto=self.escape_leads_into_hazard,
        )
        self.last_dodge_decision = decision
        return decision

    def predict_aim(self, target_pos, projectile_speed=None):
        """Where to aim to hit a moving target, without firing."""
        if self.dodge_service is None or not self.dodge_service.config.aim_enabled:
            return None
        if not self.last_player_box:
            return None

        center, _ = self.get_player_hit_circle(self.last_player_box)
        if center is None:
            return None
        return self.dodge_service.aim_at(center, target_pos, projectile_speed)

    def aimed_attack(self, target_pos, projectile_speed=None, fallback=True):
        """Fire at where the target will be, by dragging the attack stick.

        Falls back to a plain tap (the game's auto-aim) when the target is not
        moving enough to be worth leading, or when aiming is unavailable.
        Returns True if the shot was actually aimed.
        """
        solution = self.predict_aim(target_pos, projectile_speed)
        config = self.dodge_config

        if solution is None or config is None or solution.lead_distance < config.aim_min_lead_distance:
            if fallback:
                self.attack()
            return False

        # A thrower's shot lands where the stick points, so a drag of a fixed
        # length lands it at a fixed distance - wherever the enemy actually is.
        # The swipe radius is documented as needing only to "clear the dead
        # zone", which is true for every brawler except these, and is why they
        # were lobbing at their own feet.
        #
        # Tapping instead hands the shot to the game's auto-aim, which does put
        # it on the enemy. That loses the lead, which hurts most for exactly
        # these slow arcs - so the aimed version is still available, but it
        # needs a stick geometry this cannot measure from here, and a guessed
        # constant that throws short is worse than no lead at all.
        if self.has_placed_attack(self.current_brawler, self.brawlers_info):
            radius = self.placed_attack_radius(target_pos, config)
            if radius is None:
                if fallback:
                    self.attack()
                return False
        else:
            radius = config.aim_swipe_radius

        aimed = self.window_controller.aimed_attack(
            solution.direction[0],
            solution.direction[1],
            radius=radius,
            hold=config.aim_swipe_hold,
        )
        self.last_aim_solution = solution
        if not aimed and fallback:
            self.attack()
        return aimed

    def placed_attack_radius(self, target_pos, config):
        """How far to drag the stick so a thrown shot lands on the target.

        None means "do not drag at all" - the caller taps instead and lets the
        game aim. That is the default, because turning a distance into a stick
        deflection needs the control's full throw in pixels, and a wrong figure
        puts every shot short.
        """
        if not config.aim_placed_attacks:
            return None
        if not self.last_player_box or not self.current_brawler:
            return None

        center, _ = self.get_player_hit_circle(self.last_player_box)
        if center is None:
            return None

        try:
            _, attack_range, _ = self.get_brawler_range(self.current_brawler)
        except Exception:
            return None
        if not attack_range:
            return None

        distance = self.get_distance(target_pos, center)
        fraction = min(1.0, max(0.0, distance / float(attack_range)))
        # Below the dead zone the game reads the drag as a tap, which would
        # silently be auto-aim rather than the short throw that was asked for.
        return max(config.aim_swipe_min_radius, config.aim_swipe_full_radius * fraction)

    def publish_dodge_context(self, data):
        service = self.dodge_service
        if service is None or not service.enabled:
            return

        player_box = data['player'][0] if data.get('player') else None
        center, radius = self.get_player_hit_circle(player_box) if player_box else (None, None)
        # The vector actually being held, not the one the playstyle asked for:
        # map-boundary detection compares commanded movement against measured
        # camera pan, so it has to be the command that is really in effect.
        held = self.movement_shaper.current if self.movement_shaper else None
        service.update_context(
            player_box=player_box,
            enemies=data.get('enemy'),
            teammates=data.get('teammate'),
            walls=data.get('wall'),
            player_center=center,
            player_radius=radius,
            joystick_active=self.window_controller.are_we_moving,
            joystick_vector=held,
        )

    def loop(self, brawler, data, current_time):
        inner = time.perf_counter()
        self.last_player_box = data['player'][0]
        self.read_vitals(data)
        inner = self.stage("| vitals", inner)
        projectiles = self.get_projectiles()
        player_center, player_radius = self.get_player_hit_circle(self.last_player_box)
        service = self.dodge_service
        motion = service.motion if service else None
        self.context = {
                'projectiles': projectiles,
                'solve_dodge': self.solve_dodge,
                'dodge_enabled': bool(service and service.enabled),
                'player_speed': service.player_speed if service else 0.0,
                'PLAYER_RADIUS': player_radius,
                'player_center': player_center,
                # The playstyle sets this to True on the frame it wants the
                # joystick to snap instead of glide.
                'sharp_movement': False,

                # Aiming: lead a moving target instead of using the game's
                # auto-aim, which fires at where the enemy already was.
                'aim_enabled': bool(service and service.config.aim_enabled),
                'aimed_attack': self.aimed_attack,
                'predict_aim': self.predict_aim,
                'tracked_enemies': service.tracked_enemies() if service else [],
                # Allies with ids that survive between frames, and a measured
                # speed. Without these a playstyle can only ask "who is
                # nearest", which changes identity whenever anybody walks.
                'tracked_teammates': service.tracked_teammates() if service else [],

                # Map boundaries, measured from the camera rather than guessed
                # from the wall model, which cannot see the edge of the arena.
                'map_boundary': motion.boundary if motion else (0, 0),
                'is_toward_boundary': motion.is_toward_boundary if motion else (lambda v, **k: False),
                'is_direction_blocked': motion.is_direction_blocked if motion else (lambda v, **k: False),
                'is_stuck': bool(motion.stuck) if motion else False,
                'stuck_for': motion.stuck_for if motion else 0.0,
                'movement_efficiency': motion.efficiency if motion else 1.0,
                # World frame, accumulated from the camera pan. Add it to any
                # on-screen coordinate to compare positions across time:
                # screen coordinates alone cannot tell a teammate standing still
                # apart from one the camera is sliding past.
                'odometer': motion.odometer if motion else (0.0, 0.0),

                # Health, read straight off the health bars. None means it
                # could not be read - the playstyle must treat that as "no
                # information" and fall back to distance, never as "full".
                'player_health': self.health_of(self.last_player_box, hostile=False),
                'health_of': self.health_of,
                'health_enabled': bool(
                    self.dodge_config and self.dodge_config.health_enabled
                ),

                # Ground hazards left by throwers - mines, puddles, fire. Same
                # veto the emergency path uses, so the two never disagree.
                'leads_into_hazard': self.escape_leads_into_hazard,
                'hazards': (self.dodge_service.hazards()
                            if self.dodge_service is not None else []),
                'player_data': data['player'][0],
                'enemy_data': data['enemy'],
                'teammate_data': data['teammate'],
                'brawler': brawler,
                'walls': data['wall'],
                'bushes': data['bush'],
                'brawlers_info': self.brawlers_info,
                'must_brawler_hold_attack': self.must_brawler_hold_attack,
                'is_gadget_ready': self.is_gadget_ready,
                'is_hypercharge_ready': self.is_hypercharge_ready,
                'is_super_ready': self.is_super_ready,
                'TILE_SIZE': self.TILE_SIZE*self.window_controller.scale_factor,
                'get_entity_pos': self.get_entity_pos,
                'get_distance': self.get_distance,
                'get_actual_player_box': self.get_actual_player_box,
                'get_brawler_range': self.get_brawler_range,
                'is_there_enemy': self.is_there_enemy,
                'attack': self.attack,
                'use_hypercharge': self.use_hypercharge,
                'use_super': self.use_super,
                'use_gadget': self.use_gadget,
                'get_random_movement': self.get_random_movement,
                'current_brawler': self.current_brawler,
                'last_movement': self.last_movement,
                'last_movement_change_time': self.last_movement_change_time,
                'seconds_to_hold_attack_after_reaching_max': self.seconds_to_hold_attack_after_reaching_max,
                "width": brawl_stars_width,
                "height": brawl_stars_height,
                'find_closest_enemy': self.find_closest_enemy,
                'find_closest_teammate': self.find_closest_teammate,
                'is_there_poison_gas': self.is_there_poison_gas,
                'is_path_blocked': self.is_path_blocked,
                'is_enemy_hittable': self.is_enemy_hittable,
                'time': time,
                'random': random,
                "persistent_data": self.persistent_data,
                'debug': self.verbose_debug,
                'JOYSTICK_RADIUS': JOYSTICK_RADIUS,
                'rotate_movement': self.rotate_movement
            }
        inner = self.stage("| context", inner)
        movement = self.get_movement()
        inner = self.stage("| pyla", inner)
        sharp = bool(self.last_pyla_globals.get('sharp_movement'))
        current_time = time.time()
        vector = self.movement_to_vector(movement)

        if self.dodge_service is not None:
            self.dodge_service.set_tactical_intent(
                vector,
                is_blocked=lambda candidate, box=self.last_player_box,
                walls=data['wall']: self.is_path_blocked(box, candidate, walls),
                gas_veto=self.escape_leads_into_hazard,
            )

        if vector is None:
            # Ease the stick back to centre rather than dropping it, so a
            # playstyle that returns nothing for one frame does not stutter.
            coasting = self.movement_shaper.shape(None, now=current_time) if self.movement_shaper else None
            if coasting is None:
                self.window_controller.release_movement()
                self.last_movement = ''
                return None
            return coasting

        movement = self.clamp_movement(vector)

        if sharp:
            # Dodging: skip the rate limiter and the unstuck rotation, both of
            # which exist to stop dithering and would blunt the escape.
            self.last_movement = movement
            self.last_movement_change_time = current_time
            self.time_since_different_movement = current_time
            self.fix_movement_keys['toggled'] = False
            self.fix_movement_keys['last_direction_key'] = self.movement_direction_key(movement)
        else:
            if movement != self.last_movement:
                if current_time - self.last_movement_change_time >= self.minimum_movement_delay:
                    self.last_movement = movement
                    self.last_movement_change_time = current_time
                else:
                    movement = self.last_movement
            else:
                self.last_movement_change_time = current_time
            movement = self.unstuck_movement_if_needed(movement, current_time)

        self.was_dodging = sharp
        if self.movement_shaper is not None:
            movement = self.movement_shaper.shape(movement, sharp=sharp, now=current_time)
            if movement is None:
                self.window_controller.release_movement()
                self.last_movement = ''
                self.stage("| shape", inner)
                return None
        self.stage("| shape", inner)
        return movement

    def check_if_hypercharge_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(hypercharge_crop_area[0] * wr), int(hypercharge_crop_area[1] * hr)
        x2, y2 = int(hypercharge_crop_area[2] * wr), int(hypercharge_crop_area[3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        purple_pixels = count_hsv_pixels(screenshot, (137, 158, 159), (179, 255, 255))
        if self.verbose_debug:
            print("hypercharge purple pixels:", purple_pixels, "(if > ", self.hypercharge_pixels_minimum, " then hypercharge is ready)")
            cv2.imwrite(f"debug_frames/hypercharge_debug_{purple_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))

        if purple_pixels > self.hypercharge_pixels_minimum:
            return True
        return False

    def check_if_gadget_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(gadget_crop_area[0] * wr), int(gadget_crop_area[1] * hr)
        x2, y2 = int(gadget_crop_area[2] * wr), int(gadget_crop_area[3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        green_pixels = count_hsv_pixels(screenshot, (57, 219, 165), (62, 255, 255))
        if self.verbose_debug:
            print("gadget green pixels:", green_pixels, "(if > ", self.gadget_pixels_minimum, " then gadget is ready)")
            cv2.imwrite(f"debug_frames/gadget_debug_{green_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))

        if green_pixels > self.gadget_pixels_minimum:
            return True
        return False

    def check_if_super_ready(self, frame):
        wr, hr = self.window_controller.width_ratio, self.window_controller.height_ratio
        x1, y1 = int(super_crop_area[0] * wr), int(super_crop_area[1] * hr)
        x2, y2 = int(super_crop_area[2] * wr), int(super_crop_area[3] * hr)
        screenshot = frame[y1:y2, x1:x2]
        yellow_pixels = count_hsv_pixels(screenshot, (17, 170, 200), (27, 255, 255))
        if self.verbose_debug:
            print("super yellow pixels:", yellow_pixels, "(if > ", self.super_pixels_minimum, " then super is ready)")
            cv2.imwrite(f"debug_frames/super_debug_{yellow_pixels}_{int(time.time())}.png", cv2.cvtColor(screenshot, cv2.COLOR_RGB2BGR))

        if yellow_pixels > self.super_pixels_minimum:
            return True
        return False

    def get_centered_wall_crop(self, frame, player_data=None):
        frame_height, frame_width = frame.shape[:2]
        crop_size = self.centered_wall_crop_size

        if player_data:
            center_x, center_y = self.get_entity_pos(player_data[0])
        else:
            center_x, center_y = frame_width / 2, frame_height / 2

        crop_x1 = int(clamp(round(center_x - crop_size / 2), 0, frame_width - crop_size))
        crop_y1 = int(clamp(round(center_y - crop_size / 2), 0, frame_height - crop_size))
        crop_x2 = crop_x1 + crop_size
        crop_y2 = crop_y1 + crop_size

        return frame[crop_y1:crop_y2, crop_x1:crop_x2], crop_x1, crop_y1

    @staticmethod
    def offset_tile_data(tile_data, offset_x, offset_y):
        if not offset_x and not offset_y:
            return tile_data

        offset_data = {}
        for class_name, boxes in tile_data.items():
            offset_data[class_name] = [
                [box[0] + offset_x, box[1] + offset_y, box[2] + offset_x, box[3] + offset_y]
                for box in boxes
            ]
        return offset_data

    def get_tile_data(self, frame, player_data=None):
        if self.centered_wall_detection and self.Detect_centered_tile_detector is not None:
            crop, offset_x, offset_y = self.get_centered_wall_crop(frame, player_data)
            tile_data = self.Detect_centered_tile_detector.detect_objects(
                crop,
                conf_tresh=self.wall_detection_confidence
            )
            return self.offset_tile_data(tile_data, offset_x, offset_y)

        tile_data = self.Detect_tile_detector.detect_objects(frame, conf_tresh=self.wall_detection_confidence)
        return tile_data

    def process_tile_data(self, tile_data):
        walls = []
        bushes = []
        for class_name, boxes in tile_data.items():
            if 'bush' not in class_name:
                walls.extend(boxes)
            else:
                bushes.extend(boxes)
        return walls, bushes

    def get_movement(self):
        movement, updated_globals = interpret_pyla_code(self.pyla_code, self.context)
        # The playstyle communicates more than a vector now: `sharp_movement`
        # tells the shaper whether to snap or glide.
        self.last_pyla_globals = updated_globals or {}
        control = getattr(self, "runtime_control", None)
        if control is not None and hasattr(control, "note_activity"):
            control.note_activity(self.last_pyla_globals.get("activity"))
        return movement

    def build_advanced_visuals(self, debug_data):
        """Fill in the hit circle, line-of-sight links and joystick sectors.

        The debug viewer already knows how to draw all three - only the code
        that computes them lived in the unavailable early_access module. Every
        input is already on hand here, so this is a straightforward rewrite
        rather than anything clever.
        """
        player_boxes = debug_data.get("player")
        if not player_boxes:
            return

        player_box = player_boxes[0]
        walls = self.last_walls_data
        player_pos = self.get_entity_pos(player_box)

        center, radius = self.get_player_hit_circle(player_box)
        if center is not None:
            debug_data["player_hit_circle"] = [int(center[0]), int(center[1]), int(radius)]

        # A link is drawn only where the shot would actually connect, so the
        # overlay shows reachability rather than mere proximity.
        for key, target in (("enemy_los_lines", "enemy"), ("teammate_los_lines", "teammate")):
            lines = []
            for box in debug_data.get(target) or []:
                position = self.get_entity_pos(box)
                if not self.walls_block_line_of_sight(player_pos, position, walls):
                    lines.append([
                        int(player_pos[0]), int(player_pos[1]),
                        int(position[0]), int(position[1]),
                    ])
            debug_data[key] = lines

        # One sector per candidate direction, coloured by whether the player's
        # hit circle can actually be swept that way.
        sectors = []
        count = 16
        for index in range(count):
            angle = 2.0 * math.pi * index / count
            move = (math.cos(angle) * JOYSTICK_RADIUS, math.sin(angle) * JOYSTICK_RADIUS)
            sectors.append({
                "angle": round(math.degrees(angle), 1),
                "blocked": bool(self.is_path_blocked(player_box, move, walls)),
            })
        debug_data["joystick_directions"] = sectors

    def publish_debug_view(self, frame, data, state, movement=None):
        if not hasattr(self.window_controller, "debug_view"):
            # No debug window on this machine, but the panel may still be
            # watching - and a picture with no overlay is worth far more than
            # no picture.
            try:
                from live_view import publish as publish_live
                publish_live(frame, None)
            except Exception:
                pass
            return

        self.frame = frame
        advanced_visuals = bool(getattr(self.window_controller.debug_view, "advanced_visuals", False))
        debug_data = {
            "state": state,
            "player": [],
            "enemy": [],
            "teammate": [],
            "wall": [],
            "attack_range": 0,
            "super_range": 0,
            "poison_gas": {},
            "movement": None,
            "joystick": [self.window_controller.joystick_x, self.window_controller.joystick_y],
            "advanced_visuals": advanced_visuals,
            "joystick_radius": int(JOYSTICK_RADIUS * (self.window_controller.scale_factor or 1)),
            "joystick_directions": [],
            "enemy_los_lines": [],
            "teammate_los_lines": [],
            "player_hit_circle": None,
            "projectiles": [],
            "dodge": None,
        }

        if self.dodge_service is not None and self.dodge_config and self.dodge_config.debug_overlay:
            horizon = self.dodge_config.horizon
            for projectile in self.dodge_service.get_projectiles():
                end_x, end_y = projectile.position_at(horizon)
                debug_data["projectiles"].append({
                    "x": int(projectile.x),
                    "y": int(projectile.y),
                    "r": int(projectile.radius),
                    "ex": int(end_x),
                    "ey": int(end_y),
                    "c": round(projectile.confidence, 2),
                })
            decision = self.last_dodge_decision or self.dodge_service.get_decision()
            if decision is not None and decision.active and decision.vector:
                debug_data["dodge"] = {
                    "vector": [float(decision.vector[0]), float(decision.vector[1])],
                    "urgency": decision.urgency,
                    "tti": round(decision.time_to_impact or 0.0, 3),
                }

            # A crashing playstyle freezes the bot silently - no movement, no
            # attacks, just a brawler standing still. Put it on the screen.
            playstyle_error = getattr(interpret_pyla_code, "last_error", None)
            if playstyle_error:
                debug_data["playstyle_error"] = playstyle_error

            # Health is a pixel heuristic, so it has to be visible: a number
            # the bot acts on but nobody can check is worse than no number.
            debug_data["health"] = [
                {
                    "bar": [int(v) for v in reading.bar],
                    "pct": round(reading.fraction * 100),
                    "conf": round(reading.confidence, 2),
                    "hostile": key.startswith("e"),
                }
                for key, reading in self.health_readings.items()
                if reading.bar is not None
            ]

            debug_data["hazards"] = [
                h.as_dict() for h in (self.dodge_service.hazards() or [])
            ]

            if self.dodge_config.debug_show_candidates:
                snapshot = self.dodge_service.tracker.debug_snapshot()
                debug_data["candidates"] = snapshot["blobs"]
                debug_data["pending"] = snapshot["pending"]
                debug_data["trails"] = snapshot["trails"]
                debug_data["tracker_stats"] = snapshot["stats"]

            motion = self.dodge_service.motion
            debug_data["motion"] = {
                "boundary": list(motion.boundary),
                "stuck": bool(motion.stuck),
                "efficiency": round(motion.efficiency, 2),
                "drift": [int(motion.drift[0]), int(motion.drift[1])],
            }

            if self.last_aim_solution is not None:
                debug_data["aim"] = {
                    "point": [int(self.last_aim_solution.point[0]),
                              int(self.last_aim_solution.point[1])],
                    "lead": int(self.last_aim_solution.lead_distance),
                    "flight": round(self.last_aim_solution.flight_time, 3),
                }

        if data:
            for key in ["player", "enemy", "teammate", "wall"]:
                debug_data[key] = [[int(v) for v in box[:4]] for box in (data.get(key) or []) if len(box) >= 4]
            try:
                _, attack_range, super_range = self.get_brawler_range(self.current_brawler)
                debug_data["attack_range"] = int(attack_range)
                debug_data["super_range"] = int(super_range)
            except Exception:
                pass
            if debug_data["player"]:
                try:
                    debug_data["poison_gas"] = self.is_there_poison_gas(debug_data["player"][0])
                except Exception:
                    pass
                if advanced_visuals:
                    # Own implementation; works with or without early_access.
                    self.build_advanced_visuals(debug_data)

        if movement is not None:
            debug_data["movement"] = [float(movement[0]), float(movement[1])]

        self.window_controller.debug_view.publish(frame, debug_data)
        # The same picture, for anybody watching the panel instead of sitting
        # at the machine. Costs a reference assignment when nobody is.
        try:
            from live_view import publish as publish_live
            publish_live(frame, debug_data)
        except Exception:
            pass

    def stage(self, name, started):
        """Record how long one stage of the iteration took.

        IPS on its own says the loop is slow but not which part of it is, and
        every stage here is a plausible culprit: three ONNX sessions, a handful
        of pixel counts, the playstyle, and the debug view. Guessing between
        them from a single number wastes far more time than measuring.
        """
        now = time.perf_counter()
        self._stage_times[name] = self._stage_times.get(name, 0.0) + (now - started) * 1000.0
        return now

    def report_stages(self):
        self._stage_iterations += 1
        now = time.perf_counter()
        if now - self._stage_reported < 5.0:
            return
        elapsed = now - self._stage_reported
        count = max(self._stage_iterations, 1)
        parts = sorted(self._stage_times.items(), key=lambda kv: kv[1], reverse=True)
        total = sum(self._stage_times.values()) / count
        line = "  ".join(f"{name} {ms / count:.1f}" for name, ms in parts if ms / count >= 0.05)
        print(f"[loop] {count / elapsed:.1f} IPS  |  {total:.1f} ms/iter  |  {line}")
        self._stage_times = {}
        self._stage_iterations = 0
        self._stage_reported = now

    def main(self, frame, brawler, main):
        current_time = time.time()
        mark = time.perf_counter()
        state = main.get_latest_state()
        self.ensure_dodge_service()
        mark = self.stage("state", mark)
        # Only during a match: in a menu these coordinates are other buttons.
        if state == "match":
            self.send_emote_if_due(current_time)
        data = self.get_main_data(frame)
        mark = self.stage("yolo", mark)
        odometer = self.camera_odometer()
        if current_time - self.time_since_walls_checked > self.walls_treshold:
            tile_data = self.get_tile_data(frame, data.get("player"))
            walls, bushes = self.process_tile_data(tile_data)
            mark = self.stage("walls", mark)
            self.time_since_walls_checked = current_time
            self.last_walls_data = walls
            self.last_bushes_data = bushes
            self.last_walls_odometer = odometer
            data['wall'] = walls
            data['bush'] = bushes
        else:
            # Slide them along with the camera. They are static in the world,
            # so the only thing that changed is where the world is on screen.
            shift = (self.last_walls_odometer[0] - odometer[0],
                     self.last_walls_odometer[1] - odometer[1])
            data['wall'] = self.shift_boxes(self.last_walls_data, shift)
            data['bush'] = self.shift_boxes(self.last_bushes_data, shift)

        data = self.validate_game_data(data)
        self.track_no_detections(data)
        if data:
            self.time_since_player_last_found = time.time()
            if state != "match":
                data = None

        if not data:
            if self.dodge_service is not None:
                # Out of the match, or the player is not visible: drop every
                # track so stale velocities cannot trigger a phantom dodge.
                self.dodge_service.reset()
            if self.movement_shaper is not None:
                self.movement_shaper.reset()
            if current_time - self.time_since_player_last_found > 1.0:
                self.window_controller.release_movement()
            if current_time - self.time_since_last_proceeding > self.no_detection_proceed_delay:
                current_state = get_state(frame)
                if current_state != "match":
                    main.handle_detected_state(current_state)
                    state = current_state
                    self.time_since_last_proceeding = current_time
                else:
                    print("haven't detected the player in a while proceeding")
                    self.window_controller.press("proceed")
                    self.time_since_last_proceeding = time.time()
            self.publish_debug_view(frame, data, state)
            return
        self.time_since_last_proceeding = time.time()
        mark = time.perf_counter()
        if current_time - self.time_since_hypercharge_checked > self.hypercharge_treshold:
            self.is_hypercharge_ready = self.check_if_hypercharge_ready(frame)
            self.time_since_hypercharge_checked = current_time
        if current_time - self.time_since_gadget_checked > self.gadget_treshold:
            self.is_gadget_ready = self.check_if_gadget_ready(frame)
            self.time_since_gadget_checked = current_time
        if current_time - self.time_since_super_checked > self.super_treshold:
            self.is_super_ready = self.check_if_super_ready(frame)
            self.time_since_super_checked = current_time
        mark = self.stage("buttons", mark)
        self.frame = frame
        self.publish_dodge_context(data)
        if self.dodge_service is not None and not self.dodge_service.config.threaded:
            self.dodge_service.process_frame(frame, current_time)
        mark = self.stage("dodge_ctx", mark)
        movement = self.loop(brawler, data, current_time)
        mark = self.stage("playstyle", mark)
        self.publish_debug_view(frame, data, state, movement)
        mark = self.stage("debugview", mark)
        if movement is not None:
            self.do_movement(movement)
        self.stage("move", mark)
        self.report_stages()
