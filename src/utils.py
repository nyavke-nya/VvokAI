import hashlib
import html
import io
import math
import os
import random
import shutil
import ssl
import threading
import time
from io import BytesIO
import ctypes
import json
from pathlib import Path
import requests
import toml
from PIL import Image
import cv2
from packaging import version
import traceback
import tempfile
from contextlib import contextmanager

try:
    from early_access.early_access import get_brawler_stats, get_player_info
    early_access = True
except (ImportError, ModuleNotFoundError):
    # Supercell's public API covers both of these. Imported lazily because
    # brawl_api imports from this module.
    def get_brawler_stats(player_info, brawler_name):
        from brawl_api import get_brawler_stats as _stats
        return _stats(player_info, brawler_name)

    def get_player_info(tag):
        from brawl_api import get_player_info as _info
        return _info(tag)
    early_access = False

def extract_text_and_positions(image_path):
    results = reader.readtext(image_path)
    text_details = {}
    for (bbox, text, prob) in results:
        top_left, top_right, bottom_right, bottom_left = bbox
        cx = (top_left[0] + top_right[0] + bottom_right[0] + bottom_left[0]) / 4
        cy = (top_left[1] + top_right[1] + bottom_right[1] + bottom_left[1]) / 4
        center = (cx, cy)
        formatted_bbox = {
            'top_left': top_left,
            'top_right': top_right,
            'bottom_right': bottom_right,
            'bottom_left': bottom_left,
            'center': center
        }

        text_details[text.lower()] = formatted_bbox

    return text_details


class DefaultEasyOCR:
    REQUIRED_MODELS = ("craft_mlt_25k.pth", "english_g2.pth")

    def __init__(self):
        self.reader = None
        self.lock = threading.Lock()

    def readtext(self, image_input):
        if self.reader is None:
            with self.lock:
                if self.reader is None:
                    self.reader = self.create_reader()
        return self.reader.readtext(image_input)

    def create_reader(self):
        model_dir = resolve_project_path("models", "easyocr")
        self.validate_model_directory(model_dir)
        try:
            import easyocr
            try:
                return easyocr.Reader(
                    ['en'],
                    model_storage_directory=str(model_dir),
                    download_enabled=False,
                    verbose=False,
                    gpu=False
                )
            except Exception as exc:
                raise EasyOCRInitializationError(f"EasyOCR failed to load bundled models from {model_dir}: {exc}") from exc
        except ssl.SSLCertVerificationError:
            raise EasyOCRInitializationError("EasyOCR initialization failed due to SSL certificate verification error. To fix this, please check https://discord.com/channels/1205263029269438574/1227618442073342002/1499330873538117703 for a solution.")

    def validate_model_directory(self, model_dir):
        missing = [filename for filename in self.REQUIRED_MODELS if not (model_dir / filename).exists()]
        if missing:
            raise EasyOCRInitializationError(f"Missing EasyOCR model file(s) in {model_dir}: {', '.join(missing)}")


class EasyOCRInitializationError(RuntimeError):
    pass


def _get_project_root():
    import sys
    from pathlib import Path
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _get_project_root()

# Multi-instance support. When several Brawl Stars accounts run at once - one
# MuMu window each - every instance points at its own config tree through this
# environment variable, so their settings, API tokens and match history never
# collide. Unset means the single shared cfg/ directory, i.e. behaviour is
# exactly as it was before instances existed.
#
# Only the cfg/ tree is redirected. Models, playstyles and tooling stay shared
# across instances: they are the same for every account and duplicating them
# would waste gigabytes.
_CFG_DIR_ENV = os.environ.get("VVOK_CFG_DIR")

# Per-account state that historically lives at the project root. When an
# instance is scoped to its own config dir, these move into it too, so each
# account keeps its OWN brawler queue instead of all of them fighting over one
# shared file at the root - which is why a target set on one account came back
# changed. Only redirected when an instance scope is active; a single install
# keeps them at the root exactly as before.
_INSTANCE_ROOT_FILES = {"latest_brawler_data.json", "vvokai_log.txt"}

# Settings that belong to the PERSON, not to the Brawl Stars account. Every
# account of one owner uses the same developer-portal key, because a key is
# tied to a developer login and an IP address - not to a player - and one key
# answers questions about any tag. So these are read from, and written to, the
# shared cfg/ even inside an account scoped to its own config dir.
#
# Copying them per account was worse than untidy. brawl_token._reissue revokes
# every key this bot made before creating a new one, so four accounts each
# holding their own copy of the same key would revoke each other's the first
# time an address changed: one recovers, the other three are left holding a key
# that was deleted underneath them.
#
# player_tag is deliberately NOT here. That one really is per account.
_SHARED_CFG_KEYS = {
    "general_config.toml": ("brawl_api_token", "brawl_api_email",
                            "brawl_api_password", "_brawl_api_token_ip"),
    "login.toml": ("key",),
}


def _shared_keys_for(full_path) -> tuple:
    """The keys of this config that live in the shared cfg/, if any."""
    if not _CFG_DIR_ENV:
        return ()
    return _SHARED_CFG_KEYS.get(Path(full_path).name, ())


def clean_player_tag(value) -> str:
    """A player tag with nothing but its own characters, or "" if none.

    "#" alone is the one that mattered. The panel put the prefix back the
    moment the box was emptied, so a tag could never be cleared, and what
    reached the config was a lone "#" - which is not falsy, so everything
    downstream believed a tag was set and asked the API about a player with no
    name, once a match.
    """
    return str(value or "").strip().replace("%23", "").replace("#", "").strip()



def _cfg_root() -> Path:
    if not _CFG_DIR_ENV:
        return PROJECT_ROOT / "cfg"
    candidate = Path(_CFG_DIR_ENV)
    return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)


def resolve_project_path(*parts) -> Path:
    if parts:
        first = str(parts[0])
        if first == "cfg":
            return _cfg_root().joinpath(*[str(p) for p in parts[1:]])
        if _CFG_DIR_ENV and len(parts) == 1 and first in _INSTANCE_ROOT_FILES:
            return _cfg_root().joinpath(first)
    return PROJECT_ROOT.joinpath(*parts)


def _config_full_path(file_path) -> Path:
    """Absolute path for a cfg-relative file, honouring the per-instance dir."""
    if Path(file_path).is_absolute():
        return Path(file_path)
    rel = os.path.normpath(str(file_path)).replace('\\', '/')
    return resolve_project_path(*rel.split('/'))

cached_toml = {}
def load_toml_as_dict(file_path, cache=True):
    full_path = _config_full_path(file_path)
    if str(full_path) in cached_toml and cache:
        return cached_toml[str(full_path)]

    if not full_path.exists():
        example_path = full_path.with_name(full_path.stem + ".example" + full_path.suffix)
        if example_path.exists():
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(example_path, full_path)
            except Exception as e:
                print(f"Could not copy {example_path} to {full_path}: {e}")

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            data = toml.load(f)
    except Exception as e:
        print(f"Error loading {full_path}: {e}")
        return {}

    data = _overlay_shared(full_path, data)
    cached_toml[str(full_path)] = data
    return data


def _shared_path(full_path) -> Path:
    """Where the shared copy of this config lives: the project's own cfg/."""
    return PROJECT_ROOT / "cfg" / Path(full_path).name


def _overlay_shared(full_path, data: dict) -> dict:
    """Put the owner's shared settings over an account's own copy.

    Read-time rather than copy-time, so a token reissued or retyped anywhere
    reaches every account at once instead of the three that were not looking.
    """
    keys = _shared_keys_for(full_path)
    if not keys:
        return data
    shared_path = _shared_path(full_path)
    if shared_path == Path(full_path) or not shared_path.exists():
        return data
    try:
        with open(shared_path, 'r', encoding='utf-8') as f:
            shared = toml.load(f)
    except Exception as e:
        print(f"Error loading shared {shared_path}: {e}")
        return data
    for key in keys:
        if key in shared:
            data[key] = shared[key]
    return data

def invalidate_toml_cache(file_path):
    full_path = _config_full_path(file_path)
    cached_toml.pop(str(full_path), None)


def save_dict_as_toml(data, file_path):
    full_path = _config_full_path(file_path)
    _write_shared(full_path, data)
    full_path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and swapped in, rather than opened for writing
    # in place. open(..., 'w') truncates immediately, so a crash, a power cut or
    # a killed process during the dump left a zero-byte config behind - and the
    # bot then would not start at all. os.replace is atomic on Windows and
    # POSIX alike, so the file on disk is always either the old one or the new.
    fd, name = tempfile.mkstemp(prefix=full_path.name + ".", suffix=".tmp", dir=full_path.parent)
    os.close(fd)
    temp_path = Path(name)
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            toml.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, full_path)
    except Exception:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        raise
    cached_toml[str(full_path)] = data


def _write_shared(full_path, data: dict) -> None:
    """Send the owner's shared settings to the shared cfg/, not the account's.

    Written there as well as here: the account's own copy is harmless once the
    overlay above always wins, and leaving it means an account that is later
    unscoped still has something to fall back on.
    """
    keys = [key for key in _shared_keys_for(full_path) if key in data]
    if not keys:
        return
    shared_path = _shared_path(full_path)
    if shared_path == Path(full_path):
        return
    try:
        shared = {}
        if shared_path.exists():
            with open(shared_path, 'r', encoding='utf-8') as f:
                shared = toml.load(f)
        if all(shared.get(key) == data[key] for key in keys):
            return
        for key in keys:
            shared[key] = data[key]
        save_dict_as_toml(shared, shared_path)
    except Exception as e:
        # Never at the cost of the save that was actually asked for.
        print(f"Could not update the shared config {shared_path}: {e}")


reader = DefaultEasyOCR()
try:
    from early_access.early_access import OFFICIAL_API
    default_api = OFFICIAL_API
except (ImportError, ModuleNotFoundError):
    default_api = "localhost"
cfg_api_base_url = load_toml_as_dict("cfg/general_config.toml").get("api_base_url", "default")
api_base_url = cfg_api_base_url if cfg_api_base_url != "default" else default_api
brawlers_info_file_path = PROJECT_ROOT / "cfg" / "brawlers_info.json"


def count_hsv_pixels(cv_image, low_hsv, high_hsv):
    hsv_image = cv2.cvtColor(cv_image, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv_image, low_hsv, high_hsv)
    return cv2.countNonZero(mask)


def count_mask_pixels(mask, x1, y1, x2, y2):
    height, width = mask.shape[:2]
    x1 = max(0, min(width, int(x1)))
    x2 = max(0, min(width, int(x2)))
    y1 = max(0, min(height, int(y1)))
    y2 = max(0, min(height, int(y2)))
    if x1 >= x2 or y1 >= y2:
        return 0
    return cv2.countNonZero(mask[y1:y2, x1:x2])

@contextmanager
def atomic_text_writer(path, *, newline=None):
    """Publish a complete file, preserving the last version on any failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_brawler_data(data):
    """
    Save the given data to a json file. As a list of dictionaries.
    """
    queue_path = resolve_project_path("latest_brawler_data.json")
    with atomic_text_writer(queue_path) as f:
        json.dump(data, f, indent=4)


def load_brawler_data():
    queue_path = resolve_project_path("latest_brawler_data.json")
    if not queue_path.exists():
        return []
    try:
        with open(queue_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return clean_queue(data) if isinstance(data, list) else []
    except Exception as e:
        traceback.print_exc()
        print(f"Error loading queue data from {queue_path}: {e}")
        return []

def load_all_brawlers_names():
    brawler_names_path = resolve_project_path("cfg", "names.json")
    if not brawler_names_path.exists():
        return {}
    try:
        with open(brawler_names_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        traceback.print_exc()
        print(f"Error loading brawler names from {brawler_names_path}: {e}")
        return {}


def api_update_brawler_data(brawler_data):
    """Refresh the queue's counts from whoever can answer.

    The gate here used to be `early_access` - the paid module - even though
    every other stats call in this file already falls back to Supercell's
    public API when that module is absent. So an install with a perfectly good
    free API token never had its queue refreshed, and the trophy count only
    ever moved by the bot's own estimate.

    What matters is whether anything can answer the question, which is the
    paid module or a configured token, not which of the two.
    """
    if not early_access:
        from brawl_api import is_available
        if not is_available():
            return
    player_tag = clean_player_tag(
        load_toml_as_dict("cfg/general_config.toml").get("player_tag"))
    if not player_tag:
        return
    player_info = get_player_info(player_tag)
    if not player_info:
        return
    for brawler in brawler_data:
        trophies, win_streak = get_brawler_stats(player_info, brawler['brawler'])
        if trophies is not None:
            brawler['trophies'] = trophies
        if win_streak is not None:
            brawler['win_streak'] = win_streak
    save_brawler_data(brawler_data)


def clear_brawler_data():
    queue_path = resolve_project_path("latest_brawler_data.json")
    if queue_path.exists():
        queue_path.unlink()


def clean_queue(data):
    cleaned_data = []
    for brawler_data in data:
        if brawler_data['type'] not in ["trophies", "wins"]:
            brawler_data['type'] = "trophies"
        type_of_push = brawler_data['type']
        if brawler_data[type_of_push] == "":
            brawler_data[type_of_push] = 0

        if brawler_data['push_until'] == "":
            if type_of_push == "wins":
                brawler_data['push_until'] = 300
            elif type_of_push == "trophies":
                brawler_data['push_until'] = 1000
        value = brawler_data[type_of_push]
        if isinstance(value, str):
            try:
                value = int(value)
            except ValueError:
                value = 0
        current_win_streak = brawler_data["win_streak"] if "win_streak" in brawler_data else 0
        if not isinstance(current_win_streak, int):
            try:
                current_win_streak = int(current_win_streak)
            except ValueError:
                current_win_streak = 0
        automatically_pick = brawler_data["automatically_pick"]
        if not isinstance(automatically_pick, bool):
            automatically_pick = str(automatically_pick).strip().lower() in {"1", "true", "yes", "on"}
        current_wins = brawler_data["wins"]
        if not isinstance(current_wins, int):
            try:
                current_wins = int(current_wins)
            except ValueError:
                current_wins = 0
        current_trophies = brawler_data["trophies"]
        if not isinstance(current_trophies, int):
            try:
                current_trophies = int(current_trophies)
            except ValueError:
                current_trophies = 0
        push_until = brawler_data['push_until']
        if not isinstance(push_until, int):
            try:
                push_until = int(push_until)
            except ValueError:
                push_until = 0

        if value < push_until:
            final_brawler_data = {"brawler": brawler_data['brawler'], "type": type_of_push, "trophies": current_trophies, "wins": current_wins, "push_until": push_until, "automatically_pick": automatically_pick, "win_streak": current_win_streak}
            cleaned_data.append(final_brawler_data)
    return cleaned_data


def find_template_center(main_img, template, threshold=0.8):

    main_image_cv = cv2.cvtColor(main_img, cv2.COLOR_RGB2GRAY)
    if len(template.shape) == 3 and template.shape[2] == 3:
        template_cv = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    else:
        template_cv = template
    w, h = template_cv.shape[::-1]

    # Perform template matching
    result = cv2.matchTemplate(main_image_cv, template_cv, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    # Check if the match is found based on a threshold value
    if max_val >= threshold:
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2

        return center_x, center_y
    else:
        return False


def load_brawlers_info():
    if os.path.exists(brawlers_info_file_path):
        with open(brawlers_info_file_path, 'r') as f:
            return json.load(f)
    else:
        return {}


def update_brawlers_info(brawlers_info):
    with open(brawlers_info_file_path, 'w') as f:
        json.dump(brawlers_info, f, indent=4)


def get_brawler_list():
    if api_base_url == "localhost":
        brawler_list = list(load_brawlers_info().keys())
        return brawler_list
    url = f'https://{api_base_url}/get_brawler_list'
    response = requests.post(url)
    if response.status_code == 201:
        data = response.json()
        return list(set(data.get('brawlers', []) + list(load_brawlers_info().keys())))
    else:
        return []


def update_missing_brawlers_info(brawlers):
    brawlers_info = load_brawlers_info()
    for brawler in brawlers:
        if brawler not in brawlers_info:
            brawler_info = get_brawler_info(brawler)
            if brawler_info:
                brawlers_info[brawler] = brawler_info
                update_brawlers_info(brawlers_info)
                print(f"Added info for brawler '{brawler}': {brawler_info}")
                # Download the brawler icon
                save_brawler_icon(brawler)
            else:
                print(f"Could not find info for brawler '{brawler}'")
        if not os.path.exists(PROJECT_ROOT / "assets" / "brawler_icons" / f"{brawler}.png"):
            save_brawler_icon(brawler)


def get_brawler_info(brawler_name):
    url = f'https://{api_base_url}/get_brawler_info'  # Adjust the URL if necessary
    response = requests.post(url, json={'brawler_name': brawler_name})
    if response.status_code == 200:
        data = response.json()
        return data.get('info', [])
    else:
        print(f"Error fetching info for '{brawler_name}': {response.status_code} - {response.text}")
        return None


# Where a brawler portrait comes from, by numeric id. Brawlify's API used to
# hand out these URLs; it is behind a Cloudflare check now and answers every
# request with a 403 and an HTML security page, so that route stopped working
# and new brawlers quietly shipped without an icon. The CDN itself is still
# open - it just wants the id instead of the name, and Supercell publishes ids
# on the same token this bot already uses to read trophies.
BRAWLER_ICON_CDN = "https://cdn.brawlify.com/brawlers/borderless/{id}.png"
BRAWLIFY_API = "https://api.brawlify.com/v1/brawlers"


def _write_brawler_icon(payload, brawler_name_clean):
    image = Image.open(BytesIO(payload))
    safe_name = os.path.basename(brawler_name_clean).replace('/', '').replace(chr(92), '')
    icon_path = PROJECT_ROOT / "assets" / "brawler_icons" / f"{safe_name}.png"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(icon_path))
    return icon_path


def save_brawler_icon(brawler_name):
    brawler_name_clean = normalize_brawler_filename(brawler_name)

    # Imported lazily because brawl_api imports from this module.
    from brawl_api import brawler_ids
    brawler_id = brawler_ids().get(brawler_name_clean)
    if brawler_id:
        try:
            response = requests.get(BRAWLER_ICON_CDN.format(id=brawler_id), timeout=15)
            if response.status_code == 200:
                _write_brawler_icon(response.content, brawler_name_clean)
                print(f"Saved icon for brawler '{brawler_name}'")
                return
        except (requests.RequestException, OSError, ValueError):
            pass

    # The old route, kept because it needs no API token - it is the only way
    # anybody without one gets an icon at all, and if Brawlify ever drops the
    # security check it starts working again on its own.
    try:
        response = requests.get(BRAWLIFY_API, timeout=15)
    except requests.RequestException as exc:
        print(f"Could not reach the icon source for '{brawler_name}': {exc}")
        return
    if response.status_code != 200:
        print(f"Failed to fetch brawlers from API: {response.status_code}")
        return
    try:
        brawlers_data = response.json()['list']
    except (ValueError, KeyError):
        print(f"The icon source did not answer with brawler data for '{brawler_name}'")
        return

    for brawler_obj in brawlers_data:
        if normalize_brawler_filename(brawler_obj['name']) == brawler_name_clean:
            img_response = requests.get(brawler_obj['imageUrl2'], timeout=15)
            if img_response.status_code == 200:
                _write_brawler_icon(img_response.content, brawler_name_clean)
                print(f"Saved icon for brawler '{brawler_name}'")
            else:
                print(f"Failed to download icon for '{brawler_name}'")
            return
    print(f"Icon not found for brawler '{brawler_name}'")


VVOK_VERSION = "0.8.14"


def get_latest_version():
    url = f'https://{api_base_url}/check_version'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('version', '')
    else:
        return None


def check_version():
    if api_base_url != "localhost":
        latest_version = get_latest_version()
        if latest_version:
            if version.parse(VVOK_VERSION) < version.parse(latest_version):
                print(f"Warning: (ignore if you're using early access) You are not using the latest public version of VvokAI. \nCheck the discord for the latest download link.")
        else:
            print("Error, couldn't get the version, please check your internet connection or go ask for help in the discord.")


def format_notification_status(stage_manager) -> str:
    current_brawler_data = stage_manager.brawlers_pick_data[0]
    push_type = current_brawler_data["type"]
    target = current_brawler_data["push_until"]
    trophy_observer = stage_manager.Trophy_observer

    if push_type == "wins":
        current_amount = trophy_observer.current_wins
    else:
        current_amount = trophy_observer.current_trophies

    win_streak = trophy_observer.win_streak
    next_brawler = stage_manager.brawlers_pick_data[1]["brawler"] if len(stage_manager.brawlers_pick_data) > 1 else "None"
    brawlers_left = max(len(stage_manager.brawlers_pick_data) - 1, 0)

    return (
        f"Current brawler: {current_brawler_data['brawler']} \n"
        f"{push_type.capitalize()}: {current_amount}/{target} | "
        f"Win streak: {win_streak} \n"
        f"Next brawler: {next_brawler} | "
        f"Brawlers left: {brawlers_left}"
    )


def notify_user(message_type, screenshot, stage_manager) -> None:
    user_id = load_toml_as_dict("cfg/webhook_config.toml")["discord_id"].strip()
    webhook_url = load_toml_as_dict("cfg/webhook_config.toml")["webhook_url"].strip()
    telegram_token = load_toml_as_dict("cfg/webhook_config.toml")["telegram_token"].strip()
    telegram_chat_id = load_toml_as_dict("cfg/webhook_config.toml")["telegram_chat_id"].strip()
    has_discord = webhook_url
    has_telegram = telegram_token and telegram_chat_id

    if not has_discord and not has_telegram:
        print("Couldn't notify: no Discord webhook or Telegram bot configured.")
        return

    if message_type == "completed":
        status_line = f"VvokAI has completed all its targets!"
    elif message_type == "bot_is_stuck":
        status_line = f"Your bot is currently stuck, attempted to restart brawl stars !"
    elif message_type == "brawler_goal":
        current_brawler = stage_manager.brawlers_pick_data[0]["brawler"]
        status_line = f"VvokAI completed brawler goal for {current_brawler}!"
    elif message_type in ["regular_minutes_ping", "regular_matches_ping"]:
        status_line = "VvokAI is still running."
    elif message_type == "bot_failed_brawler_selection":
        current_brawler = stage_manager.brawlers_pick_data[0]["brawler"]
        status_line = f"VvokAI failed to select the brawler {current_brawler} after multiple attempts, try changing the OCR Scale Down setting or select it manually and restart. Putting it at the end of the queue and skipping it..."
    else:
        status_line = "Notification"

    stage_status = format_notification_status(stage_manager)
    if stage_status:
        status_line = f"{status_line}\n{stage_status}"

    image_buffer = None
    if screenshot is not None:
        try:
            screenshot_pil = Image.fromarray(screenshot)
            image_buffer = io.BytesIO()
            screenshot_pil.save(image_buffer, format="PNG")
            image_buffer.seek(0)
        except Exception as e:
            print(f"Failed to prepare screenshot: {e}")
            image_buffer = None

    if has_discord:
        ping = f"<@{user_id}>" if user_id else ""
        files = {}
        if image_buffer is not None:
            image_buffer.seek(0)
            files["file"] = ("screenshot.png", image_buffer, "image/png")

        embed = {
            "description": status_line
        }

        if files:
            embed["image"] = {"url": "attachment://screenshot.png"}

        payload = {
            "content": ping,
            "username": "VvokAI notifier",
            "embeds": [embed],
        }

        print("Sending Discord webhook...")
        try:
            if files:
                response = requests.post(webhook_url, data={"payload_json": json.dumps(payload)}, files=files, timeout=15)
            else:
                response = requests.post(webhook_url, json=payload, timeout=15)

            if response.status_code not in (200, 204):
                print(f"Failed to send Discord webhook: {response.status_code} {response.text}")

        except Exception as e:
            print(f"Error sending Discord webhook: {e}")

    if has_telegram:
        print("Sending Telegram notification...")
        try:
            safe_text = html.escape(status_line)

            if image_buffer is not None:
                image_buffer.seek(0)
                url = f"https://api.telegram.org/bot{telegram_token}/sendPhoto"
                response = requests.post(url,
                    data={
                        "chat_id": telegram_chat_id,
                        "caption": safe_text,
                        "parse_mode": "HTML",
                    },
                    files={
                        "photo": ("screenshot.png", image_buffer, "image/png")
                    }, timeout=15)

            else:
                url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                response = requests.post(url,
                    data={
                        "chat_id": telegram_chat_id,
                        "text": safe_text,
                        "parse_mode": "HTML",
                    }, timeout=15)

            if response.status_code != 200:
                print(f"Failed to send Telegram notification: {response.status_code} {response.text}")

        except Exception as e:
            print(f"Error sending Telegram notification: {e}")


def get_discord_link():
    if api_base_url == "localhost":
        return "https://discord.gg/xUusk3fw4A"
    url = f'https://{api_base_url}/get_discord_link'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('link', '')
    else:
        return None


def get_online_wall_model_hash():
    url = f'https://{api_base_url}/get_wall_model_hash'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('hash', '')
    else:
        return None


def calculate_sha256(file_path):
    """
    Calculate the SHA-256 hash of a file.
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as file:
        # Read the file in chunks to handle large files
        for chunk in iter(lambda: file.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def current_wall_model_is_latest() -> bool:
    """
    Check if the current wall model is the latest version.
    """
    if not os.path.exists("models/tileDetector.onnx"):
        return False
    local_hash = calculate_sha256("models/tileDetector.onnx")
    online_hash = get_online_wall_model_hash()
    return local_hash == online_hash


def get_latest_wall_model_file():
    #download the new model to replace the current file and also updates the tile list
    url = f'https://{api_base_url}/get_wall_model_file'
    response = requests.get(url)
    if response.status_code == 200:
        with open("./models/tileDetector.onnx", "wb") as file:
            file.write(response.content)
        print("Downloaded the latest wall model.")
    else:
        print(f"Failed to download the latest wall model. Status code: {response.status_code}")


def get_latest_wall_model_classes():
    url = f'https://{api_base_url}/get_wall_model_classes'
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return data.get('classes', [])
    else:
        return None


def update_wall_model_classes():
    classes = get_latest_wall_model_classes()
    current_classes = load_toml_as_dict("cfg/bot_config.toml")["wall_model_classes"]
    if classes:
        if classes != current_classes:
            print("New wall model classes found. Updating...")
            full_config = load_toml_as_dict("cfg/bot_config.toml")
            full_config["wall_model_classes"] = classes
            save_dict_as_toml(full_config, "cfg/bot_config.toml")
            print("Updated the wall model classes.")
    else:
        print("Failed to update the wall model classes, please report this error.")


def cprint(text: str, hex_color: str):
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        print(f"\033[38;2;{r};{g};{b}m{text}\033[0m")
    except Exception:
        print(text)


def mask_secret(value: str | None, keep: int = 4) -> dict:
    value = (value or "").strip()
    if not value:
        return {"configured": False, "masked": ""}
    if len(value) <= keep:
        return {"configured": True, "masked": "•" * len(value)}
    return {
        "configured": True,
        "masked": f"{value[:2]}{'•' * max(len(value) - (keep + 2), 2)}{value[-keep:]}"
    }


def normalize_brawler_filename(brawler_name: str) -> str:
    return str(brawler_name).lower().replace(' ', '').replace('-', '').replace('.', '').replace('&', '')


def get_brawler_icon_path(brawler_name: str) -> Path | None:
    if not brawler_name:
        return None

    normalized = normalize_brawler_filename(brawler_name)
    candidates = [
        resolve_project_path("assets", "brawler_icons", f"{normalized}.png"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_dpi_scale():
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    return int(user32.GetDpiForSystem())


SAFE_GLOBALS = {
    'math': math,
    'random': random,
    'abs': abs,
    'min': min,
    'max': max,
    'sum': sum,
    'round': round,
    'len': len,
    'range': range,
    'zip': zip,
    'map': map,
    'int': int,
    'float': float,
    'str': str,
    'print': print,
    'time_now': lambda: time.time(),
    'random_int': random.randint,
}


import ast

def is_safe_ast(code_str):
    try:
        tree = ast.parse(code_str)
    except SyntaxError as e:
        return False, f"Syntax Error: {e}"

    for node in ast.walk(tree):
        # 1. Block access to any attributes starting with underscore (e.g. __class__)
        if isinstance(node, ast.Attribute):
            if node.attr.startswith('_'):
                return False, f"Access to private/dunder attribute '{node.attr}' is forbidden."
        
        # 2. Block imports of any kind inside the script
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return False, "Imports are not allowed in playstyle scripts."
            
        # 3. Block calling of eval, exec, compile, etc.
        if isinstance(node, ast.Name):
            if node.id in {'exec', 'eval', 'compile', 'getattr', 'setattr', 'delattr', '__import__'}:
                return False, f"Call to '{node.id}' is forbidden."

    return True, None


_compiled_playstyles = {}


def interpret_vvok_code(vvok_code, context):
    safe_globals = SAFE_GLOBALS.copy()
    safe_globals.update(context)
    safe_globals['__builtins__'] = {}

    try:
        if isinstance(vvok_code, str):
            # Compile once, then reuse.
            #
            # This used to run is_safe_ast() - a full ast.parse plus a walk of
            # every node - and then compile(), on every single iteration of the
            # bot loop. That is fixed work on a script that has not changed, and
            # it scales with the script's length: measured in the loop profile
            # it was 17.5 ms per iteration on a 41 KB playstyle, second only to
            # the YOLO pass and more than the wall detector. It is invisible on
            # a small script, which is why nobody noticed until the playstyles
            # grew.
            #
            # Keyed by the source text, so editing a playstyle and reloading it
            # still re-validates and recompiles.
            cached = _compiled_playstyles.get(vvok_code)
            if cached is None:
                is_safe, error_msg = is_safe_ast(vvok_code)
                if not is_safe:
                    print(f"Security/Syntax Validation Failed for playstyle: {error_msg}")
                    return None, safe_globals
                cached = compile(vvok_code, '<string>', 'exec')
                # One entry is the normal case; the cap only matters if a UI
                # edits a playstyle repeatedly within one run.
                if len(_compiled_playstyles) > 8:
                    _compiled_playstyles.clear()
                _compiled_playstyles[vvok_code] = cached
            compiled_code = cached
        else:
            compiled_code = vvok_code

        if compiled_code is not None:
            exec(compiled_code, safe_globals)
    except Exception as e:
        # Record what broke as well as printing it.
        #
        # A crash in here is close to invisible from the outside: movement
        # comes back None, play.py skips the joystick, and the bot simply
        # stands there. It also stops attacking, because the attack calls live
        # further down the same script. From the outside that reads as "the bot
        # stopped shooting" with no error in sight - a traceback scrolling past
        # in a console nobody is watching is not a symptom anyone connects to a
        # brawler standing still in a match.
        #
        # So the last failure is kept here for the debug overlay to draw.
        frames = traceback.extract_tb(e.__traceback__)
        where = ""
        for frame in frames:
            # play.py compiles the script as "<vvok_script>" up front; this
            # module compiles it as "<string>" when handed raw source. Match
            # both, or the line number silently disappears depending on which
            # path loaded the playstyle.
            if frame.filename in ("<string>", "<vvok_script>"):
                where = f" at playstyle line {frame.lineno}"
        interpret_vvok_code.last_error = f"{type(e).__name__}: {e}{where}"
        interpret_vvok_code.error_count = getattr(interpret_vvok_code, "error_count", 0) + 1
        print(f"Error executing .vvok code{where}")
        traceback.print_exc()
        return None, safe_globals

    interpret_vvok_code.last_error = None
    return safe_globals.get('movement', None), safe_globals


interpret_vvok_code.last_error = None
interpret_vvok_code.error_count = 0


# Playstyle files used to carry the .pyla extension. They ship as .vvok now,
# but a config saved before the rename still names a .pyla file, and somebody
# may have written their own playstyle under the old extension. Both are still
# accepted so an update never orphans a working setup.
PLAYSTYLE_EXTS = (".vvok", ".pyla")


def _resolve_playstyle_path(filename):
    """The playstyle file, trying the other extension if the named one is gone.

    A current_playstyle of "unified_dodge.pyla" finds "unified_dodge.vvok"
    after the rename, and vice versa, so neither a stale config nor an old
    shortcut breaks."""
    path = resolve_project_path("playstyles", filename)
    if path.exists():
        return path
    for ext in PLAYSTYLE_EXTS:
        if filename.endswith(ext):
            for other in PLAYSTYLE_EXTS:
                if other == ext:
                    continue
                alt = resolve_project_path("playstyles", filename[: -len(ext)] + other)
                if alt.exists():
                    return alt
            break
    return path


def load_vvok_script(filename):
    script_path = _resolve_playstyle_path(filename)
    try:
        with open(script_path, 'r', encoding='utf-8') as file:
            metadata_header = file.readline().strip()
            metadata = json.loads(metadata_header) if metadata_header else {}
            vvok_script = file.read()
        return metadata, vvok_script
    except FileNotFoundError:
        print(f"Error: The file {script_path} was not found.")
        return "", ""
    except Exception as e:
        print(f"An error occurred while loading the .vvok script: {e}")
        traceback.print_exc()
        return "", ""


def get_playstyles_list():
    playstyles_dir = resolve_project_path("playstyles")
    playstyles = []
    if not playstyles_dir.exists():
        return playstyles

    # Dedupe by name, not by filename. Both .vvok and .pyla are accepted for
    # backward compatibility, so a machine that still has the old unified_dodge
    # .pyla next to the new .vvok would otherwise list every style twice - the
    # "playstyles keep multiplying" bug. One entry per stem, .vvok winning.
    by_stem = {}
    for filename in os.listdir(playstyles_dir):
        if not filename.endswith(PLAYSTYLE_EXTS):
            continue
        stem = filename.rsplit(".", 1)[0]
        chosen = by_stem.get(stem)
        if chosen is None or (filename.endswith(".vvok") and not chosen.endswith(".vvok")):
            by_stem[stem] = filename

    for stem in sorted(by_stem):
        filename = by_stem[stem]
        metadata, _ = load_vvok_script(filename)
        playstyles.append({
            "filename": filename,
            "metadata": metadata
        })
    return playstyles


def load_default_vvok_script():
    config = load_toml_as_dict("cfg/bot_config.toml")
    current_playstyle = config.get("current_playstyle", "unified_dodge.vvok")
    return load_vvok_script(current_playstyle)


def hash_playstyle(playstyle_info):
    return hashlib.sha256(str(playstyle_info).encode('utf-8')).hexdigest()


def config_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)



def clamp(x: int, low: int, high: int) -> int:
    if x < low:
        return low
    if x > high:
        return high
    return x

JOYSTICK_RADIUS = 75

def shutdown_computer(grace_seconds=60):
    """Ask Windows to shut down, after a delay long enough to change your mind.

    The delay is the point. This runs unattended at the end of an overnight
    session, and a bot that powers the machine off the instant it finishes is a
    bot that will one day do it while somebody is sitting at it. Sixty seconds
    is enough to read the message and run `shutdown /a`.

    Never raises: the run has already finished successfully by the time this is
    called, and failing to power off is not a reason to report a failed run.
    """
    import subprocess

    seconds = max(0, int(grace_seconds))
    try:
        subprocess.run(
            ["shutdown", "/s", "/t", str(seconds),
             "/c", "VvokAI has finished. Run 'shutdown /a' to cancel."],
            check=True, capture_output=True,
        )
    except Exception as error:
        print(f"Could not schedule a shutdown: {error}")
        return False
    print(f"Computer will shut down in {seconds} seconds. "
          f"Run 'shutdown /a' in a terminal to cancel.")
    return True
