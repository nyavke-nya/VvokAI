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

The token is bound to the IP address you registered it from, so a new address
from the ISP silently kills it and the API answers with a bare 403. When
developer-portal credentials are configured, brawl_token re-issues the key for
the current address and the request is retried once; see that module for the
credential-free alternative.
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

    return _fetch(tag, token, allow_refresh=True)


def _fetch(tag, token, allow_refresh):
    global _last_error

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
        # it says nothing about the cause. The token is bound to an IP address
        # and the ISP changed it; nothing about the tag or the account is wrong.
        if allow_refresh:
            import brawl_token

            if brawl_token.is_configured():
                # The rejected token goes in so the refresher can tell "somebody
                # else already replaced this" from "this is the dead one".
                fresh = brawl_token.refresh(previous=token)
                if fresh:
                    # One retry only. If the reissued token is refused too, the
                    # problem is not the address and looping would not help.
                    return _fetch(tag, fresh, allow_refresh=False)
                _last_error = brawl_token.last_error() or _last_error
                return None

        import brawl_token

        if brawl_token.is_configured():
            # Telling somebody to add credentials they can see they have added
            # sends them off to re-check the one thing that is already right.
            _last_error = (
                "API rejected the token (403) even after reissuing it for this "
                "address. If you are on a VPN or mobile connection the address "
                "may be changing faster than the key can be replaced; a key made "
                "by hand at developer.brawlstars.com with Allowed IP Ranges set "
                "to 0.0.0.0/0 works from anywhere and needs no credentials."
            )
        else:
            _last_error = (
                "API rejected the token (403). Tokens are locked to the IP address "
                "they were created for, and yours has changed. Either create a key "
                "at developer.brawlstars.com for the range 0.0.0.0/0, or add "
                "brawl_api_email and brawl_api_password to cfg/general_config.toml "
                "and the bot will reissue it by itself."
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
