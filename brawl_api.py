"""Player stats from the official Brawl Stars API.

Replaces the `get_player_info` / `get_brawler_stats` pair that the paid
early_access module used to provide. Supercell publishes this data themselves
at api.brawlstars.com; a token is free from developer.brawlstars.com.

    cfg/general_config.toml:
        brawl_api_token = "eyJ0eXAi..."
        player_tag      = "#2Y0LQ8CU"

One catch worth knowing before you wonder why win streaks never update: the
official API does not expose per-brawler win streaks at all. It returns
trophies, highest trophies, rank and power. `get_brawler_stats` therefore
returns None for the streak, and callers keep whatever value they already had
rather than overwriting it with a guess.

The token is bound to the IP address you registered it from. If requests start
coming back 403, that is almost always a changed IP rather than a bad token.
"""

import threading
import time

import requests

from utils import load_toml_as_dict, normalize_brawler_filename

API_ROOT = "https://api.brawlstars.com/v1"
REQUEST_TIMEOUT = 10

# The API is rate limited and the UI asks for the same player repeatedly (the
# queue view, the player pill, "push all"), so a short cache keeps one click
# from becoming five requests.
CACHE_SECONDS = 45

_cache = {}
_cache_lock = threading.Lock()
_last_error = None


def get_token():
    token = str(load_toml_as_dict("cfg/general_config.toml").get("brawl_api_token", "")).strip()
    return token or None


def is_available():
    """True when a token is configured, which is what the UI gates on."""
    return get_token() is not None


def last_error():
    return _last_error


def clean_tag(tag):
    """Normalise a player tag into the bare uppercase form the API expects."""
    tag = str(tag or "").strip().upper()
    tag = tag.replace("%23", "").replace("#", "").replace(" ", "")
    # O and 0 are easy to confuse in-game; the API only ever uses zero.
    return tag.replace("O", "0")


def get_player_info(tag):
    """Fetch a player profile, or None if unavailable.

    Returns the raw API payload so callers can read `trophies`, `name`,
    `brawlers` and so on directly.
    """
    global _last_error

    token = get_token()
    if not token:
        _last_error = "No brawl_api_token set in cfg/general_config.toml."
        return None

    tag = clean_tag(tag)
    if not tag:
        _last_error = "Empty player tag."
        return None

    now = time.time()
    with _cache_lock:
        cached = _cache.get(tag)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]

    try:
        response = requests.get(
            f"{API_ROOT}/players/%23{tag}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        _last_error = f"Could not reach the Brawl Stars API: {exc}"
        return None

    if response.status_code == 200:
        payload = response.json()
        with _cache_lock:
            _cache[tag] = (now, payload)
        _last_error = None
        return payload

    if response.status_code == 404:
        _last_error = f"Player #{tag} not found."
    elif response.status_code == 403:
        # By far the most common failure, and the message the API returns for
        # it is unhelpfully generic.
        _last_error = (
            "API rejected the token (403). Tokens are locked to the IP address "
            "they were created for - re-create it at developer.brawlstars.com "
            "if your IP changed."
        )
    elif response.status_code == 429:
        _last_error = "Rate limited by the Brawl Stars API. Try again shortly."
    else:
        _last_error = f"Brawl Stars API returned {response.status_code}."

    return None


def get_brawler_stats(player_info, brawler_name):
    """Return (trophies, win_streak) for one brawler.

    win_streak is always None: the official API does not publish it. Callers
    treat None as "leave the existing value alone".
    """
    if not player_info or not brawler_name:
        return None, None

    wanted = normalize_brawler_filename(brawler_name)
    for brawler in player_info.get("brawlers", []) or []:
        # API names are upper case and spaced ("EL PRIMO", "MR. P", "8-BIT");
        # the same normaliser the icon loader uses maps both sides to the
        # project's flat keys.
        if normalize_brawler_filename(brawler.get("name", "")) == wanted:
            return int(brawler.get("trophies", 0) or 0), None

    return None, None


def get_player_summary(tag):
    """Compact view for the UI pill: name, total trophies, brawler count."""
    info = get_player_info(tag)
    if not info:
        return None
    return {
        "tag": info.get("tag", ""),
        "name": info.get("name", ""),
        "trophies": int(info.get("trophies", 0) or 0),
        "highest_trophies": int(info.get("highestTrophies", 0) or 0),
        "brawlers": len(info.get("brawlers", []) or []),
    }


def clear_cache():
    with _cache_lock:
        _cache.clear()
