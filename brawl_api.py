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

import re
import threading
import time

import requests

from utils import load_toml_as_dict, normalize_brawler_filename

API_ROOT = "https://api.brawlstars.com/v1"
REQUEST_TIMEOUT = 10

# Extra attempts when the API refuses the key because of the address it saw.
# Small on purpose: this covers a connection that leaves by more than one exit,
# where the next request is likely to arrive from the address the key was made
# for. If several in a row are refused, the key really is for the wrong address
# and reissuing it is the answer.
IP_MISMATCH_RETRIES = 2
IP_MISMATCH_GAP = 1.5

# The API is rate limited and the UI asks for the same player repeatedly (the
# queue view, the player pill, "push all"), so a short cache keeps one click
# from becoming five requests.
CACHE_SECONDS = 45

# Failures are remembered too, and for a good reason: a failing call is far
# more expensive than a succeeding one. It waits out two retries, and may log
# into the developer portal and issue a key. Without this, every poll from the
# open Settings page pays that again - so a wrong tag or an expired key turns
# into a permanently sluggish interface and a stream of portal logins, rather
# than one failure that is reported and left alone for a while.
FAILURE_CACHE_SECONDS = 30

_cache = {}
_failures = {}
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


BRAWLER_IDS = {}
_brawler_ids_tried = False


def brawler_ids():
    """{brawler name, as the bot spells it: numeric id}, from Supercell.

    Only used to name an icon file. Brawlify's API - which is where icons came
    from - sits behind a Cloudflare check now and answers every request with a
    403 and an HTML security page, so new brawlers stopped getting one. Its CDN
    is still open, but the files there are named by the brawler's numeric id,
    and Supercell publishes those on the same token this bot already uses to
    read trophies.

    The list is static between game updates, so it is fetched once per run and
    a failure is remembered as "no" rather than retried on every startup.
    """
    global _brawler_ids_tried
    if _brawler_ids_tried:
        return BRAWLER_IDS
    _brawler_ids_tried = True

    token = get_token()
    if not token:
        return BRAWLER_IDS
    try:
        response = requests.get(
            f"{API_ROOT}/brawlers",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if response.status_code != 200:
            return BRAWLER_IDS
        for item in response.json().get("items", []):
            name = normalize_brawler_filename(item.get("name", ""))
            if name and item.get("id"):
                BRAWLER_IDS[name] = item["id"]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return BRAWLER_IDS
    return BRAWLER_IDS


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


def _rejected_ip(response):
    """The address the API says it saw, out of a 403 body.

    Supercell answers an IP mismatch with "API key does not allow access from
    IP 1.2.3.4". That number is authoritative in a way nothing else here is:
    it is this connection, as the server that refused it sees it.
    """
    try:
        message = str(response.json().get("message", ""))
    except ValueError:
        message = response.text or ""
    found = re.search(r"from IP\s+(\d{1,3}(?:\.\d{1,3}){3})", message)
    return found.group(1) if found else None


def _fetch(tag, token, allow_refresh):
    global _last_error

    now = time.time()
    with _cache_lock:
        cached = _cache.get(tag)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        # Only the outermost call short-circuits on a remembered failure. The
        # retry after a successful reissue passes allow_refresh=False and has
        # to be allowed through, or the new key would never get to prove
        # itself against the failure that was just recorded.
        failed = _failures.get(tag) if allow_refresh else None
        if failed and now - failed[0] < FAILURE_CACHE_SECONDS:
            _last_error = failed[1]
            return None

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
            _failures.pop(tag, None)
        _last_error = None
        return payload

    if response.status_code == 404:
        _last_error = f"Player #{tag} not found."
    elif response.status_code == 403:
        # By far the most common failure. The token is bound to an IP address
        # and the ISP changed it; nothing about the tag or the account is wrong.
        #
        # The refusal carries the address the API saw us arrive from, which is
        # the only address that matters and the one thing no amount of asking
        # elsewhere can establish. A public what-is-my-ip service answers for
        # its own connection, not this one - different route, different egress,
        # and on a provider that rotates within a pool it is a different number
        # by the time the key exists. Taking it from the refusal itself means
        # the key is issued for exactly the address that was just rejected.
        seen_ip = _rejected_ip(response)

        # Before concluding the key is wrong, ask again. A connection can leave
        # by more than one address - a VPN with several exits, or a provider
        # rotating a pool - and then the same key is refused and accepted from
        # one request to the next. Measured on exactly such a connection: the
        # API reported 152.233.35.206 for one call and accepted the very next
        # one, issued seconds later, from 195.181.175.176.
        #
        # Retrying costs two requests. Not retrying means tearing down a
        # perfectly good key and logging into the developer portal to replace
        # it, every time a request happens to leave by the other exit - which
        # is a login storm that fixes nothing, because the new key is bound to
        # whichever exit answered last and will be refused just as often.
        if seen_ip and allow_refresh:
            for _ in range(IP_MISMATCH_RETRIES):
                time.sleep(IP_MISMATCH_GAP)
                try:
                    retry = requests.get(
                        f"{API_ROOT}/players/%23{tag}",
                        headers={"Authorization": f"Bearer {token}",
                                 "Accept": "application/json"},
                        timeout=REQUEST_TIMEOUT,
                    )
                except requests.RequestException:
                    break
                if retry.status_code == 200:
                    payload = retry.json()
                    with _cache_lock:
                        _cache[tag] = (time.time(), payload)
                        _failures.pop(tag, None)
                    _last_error = None
                    return payload
                if retry.status_code != 403:
                    break

        if allow_refresh:
            import brawl_token

            if brawl_token.is_configured():
                # The rejected token goes in so the refresher can tell "somebody
                # else already replaced this" from "this is the dead one".
                fresh = brawl_token.refresh(previous=token, seen_ip=seen_ip)
                if fresh:
                    # One retry only. If the reissued token is refused too, the
                    # problem is not the address and looping would not help.
                    return _fetch(tag, fresh, allow_refresh=False)
                _last_error = brawl_token.last_error() or _last_error
                with _cache_lock:
                    _failures[tag] = (time.time(), _last_error)
                return None

        import brawl_token

        if brawl_token.is_configured():
            # Telling somebody to add credentials they can see they have added
            # sends them off to re-check the one thing that is already right.
            _last_error = (
                "API rejected the token (403) even after reissuing it for this "
                "address. That usually means the connection leaves by a different "
                "address each time - a VPN or mobile connection does this - and "
                "no single-address key can keep up. There is no wildcard key to "
                "fall back on, because the portal refuses ranges. Either use a "
                "connection with a stable address, or route the bot through a "
                "proxy and issue the key for the proxy."
            )
        else:
            _last_error = (
                "API rejected the token (403). Tokens are locked to the IP address "
                "they were created for, and yours has changed. Add the developer "
                "portal email and password in Settings and the bot will reissue "
                "the key for the new address by itself - that is the only way to "
                "keep this working, since the portal will not issue a key for a "
                "range."
            )
    elif response.status_code == 429:
        _last_error = "Rate limited by the Brawl Stars API. Try again shortly."
    else:
        _last_error = f"Brawl Stars API returned {response.status_code}."

    if allow_refresh and _last_error:
        with _cache_lock:
            _failures[tag] = (time.time(), _last_error)
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
        # Remembered failures go too: this is called when the token or the tag
        # has just been changed, which is exactly the moment somebody wants the
        # previous failure retried rather than replayed at them.
        _failures.clear()
