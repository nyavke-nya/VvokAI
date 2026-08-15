"""Keeping the Brawl Stars API token valid on a changing IP address.

Supercell binds every API token to the addresses it was created for. That is
their design and there is no header, scope or setting that turns it off - a
token issued at home simply stops working the moment the ISP hands out a new
address, and the API answers with a bare 403 that says nothing about why.

So the token is not made permanent; it is made disposable. When a request comes
back 403, this logs in to developer.brawlstars.com the same way the website
does, deletes the key it previously created, issues a new one for whatever
address the machine has now, and writes it back to the config. The bot then
retries once and carries on. A changed IP becomes a two-second pause instead of
a dead feature.

Two things worth knowing before enabling it:

  * It needs the developer-portal email and password in cfg/general_config.toml.
    That file is git-ignored precisely because it holds credentials, but a
    password in a plain file is a password in a plain file - if that is not
    acceptable, leave this off and use the wildcard approach below instead.

  * The portal's WEB FORM takes a CIDR range, and a key made there by hand for
    0.0.0.0/0 works from any address with no credentials stored anywhere and no
    code involved. That is still the best answer for anyone willing to do it
    once. Its API does not: every range tried through the endpoint used here -
    a /24, a /16, 0.0.0.0/0 itself - is refused with HTTP 500
    "ip-validation-failure", and only a bare address is accepted. So this
    module cannot create the wildcard key on anyone's behalf; it can only keep
    a single-address key pointed at the right address.

    Which address that is comes from the API's own refusal, not from a
    what-is-my-ip service. Those answer for their own connection and, on a
    provider that rotates within a pool, answer with a number that is already
    stale by the time the key exists: a key issued for 152.233.35.206 was wrong
    at 152.233.35.233 a minute later. The 403 says "does not allow access from
    IP x.x.x.x", and that is the address that actually needs a key.

Only keys carrying KEY_MARKER in their description are ever deleted, so a key
made by hand for something else is left alone.
"""

import re
import threading
import time

import requests

from utils import load_toml_as_dict, save_dict_as_toml

PORTAL = "https://developer.brawlstars.com/api"
TIMEOUT = 15

# Stamped into the description of every key this module creates, and the only
# thing that marks a key as ours to delete. Without it an automatic cleanup
# could revoke a key somebody made by hand for another tool.
KEY_MARKER = "managed-by-vvokai"
KEY_NAME = "VvokAI"

# Supercell allows a limited number of keys per account, so the old one is
# removed before a new one is made rather than accumulating.
MAX_KEYS = 10

# Reissues that failed in a row before the module stops trying, and how long it
# then waits. A connection that leaves by a different address on every request
# cannot be served by a key bound to one address: each reissue is correct for
# the request that triggered it and wrong for the next one. Without this the
# bot logs into the developer portal on every single API call, revoking and
# creating keys forever, and the account is rate limited or locked for a
# problem no amount of key-making can fix.
GIVE_UP_AFTER = 2
GIVE_UP_FOR = 1800

_lock = threading.Lock()
_last_error = None
_failures = 0
_quiet_until = 0.0


def last_error():
    return _last_error


def credentials():
    config = load_toml_as_dict("cfg/general_config.toml")
    email = str(config.get("brawl_api_email", "")).strip()
    password = str(config.get("brawl_api_password", "")).strip()
    return (email, password) if email and password else (None, None)


def is_configured():
    email, password = credentials()
    return bool(email and password)


def current_ip():
    """This machine's public address, as the portal will see it."""
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip",
                "https://icanhazip.com"):
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code == 200:
                address = response.text.strip()
                # Cheap sanity check; these services occasionally return an
                # error page with a 200.
                if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", address):
                    return address
        except requests.RequestException:
            continue
    return None


def _login(session, email, password):
    response = session.post(
        f"{PORTAL}/login",
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        return False, f"Developer portal rejected the login ({response.status_code})."
    try:
        payload = response.json()
    except ValueError:
        return False, "Developer portal returned something unreadable at login."
    # The portal reports failures in the body with a 200 status.
    if str(payload.get("status", {}).get("message", "")).lower() not in ("", "ok"):
        return False, "Developer portal rejected the email or password."
    return True, None


def _list_keys(session):
    response = session.post(f"{PORTAL}/apikey/list", json={}, timeout=TIMEOUT)
    if response.status_code != 200:
        return None
    try:
        return response.json().get("keys") or []
    except ValueError:
        return None


def _revoke(session, key_id):
    session.post(f"{PORTAL}/apikey/revoke", json={"id": key_id}, timeout=TIMEOUT)


def network_for(address, bits):
    """The address as a CIDR range, widened by `bits`.

    Kept, and left at a single address by default, because the portal's API
    will not take anything wider. Every range tried - the surrounding /24, a
    /16, even the 0.0.0.0/0 that the portal's own web form accepts - comes back
    HTTP 500 "ip-validation-failure". Only a bare address is accepted there.

    Which makes getting that one address right the whole job, and it is not
    ipify's answer: that reports the address ipify was contacted from, which on
    a provider that rotates within a pool is a different number by the time the
    key exists. The address in the API's own refusal is the one to use.
    """
    bits = max(0, min(32, int(bits)))
    if bits >= 32:
        return address
    if bits == 0:
        return "0.0.0.0/0"
    try:
        octets = [int(part) for part in address.split(".")]
    except ValueError:
        return address
    if len(octets) != 4:
        return address
    value = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
    masked = value & ((0xFFFFFFFF << (32 - bits)) & 0xFFFFFFFF)
    return (f"{(masked >> 24) & 255}.{(masked >> 16) & 255}."
            f"{(masked >> 8) & 255}.{masked & 255}/{bits}")


def _create(session, address, cidr_bits):
    scope = network_for(address, cidr_bits)
    response = session.post(
        f"{PORTAL}/apikey/create",
        json={
            "name": KEY_NAME,
            "description": f"{KEY_MARKER} - reissued automatically for {scope}",
            "cidrRanges": [scope],
            "scopes": ["brawlstars"],
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        return None
    try:
        return (response.json().get("key") or {}).get("key")
    except ValueError:
        return None


_GIVE_UP_MESSAGE = "\n\n".join((
    "Automatic key reissue has been stopped: this connection does not keep one "
    "address. Every request leaves by a different one - a VPN or proxy that "
    "rotates its exits does this - so a key bound to a single address is "
    "already wrong for the next request, and making more of them only floods "
    "the developer portal.",

    "The fix takes two minutes and is permanent: go to "
    "developer.brawlstars.com, delete the VvokAI keys, create one with Allowed "
    "IP Ranges set to 0.0.0.0/0, and paste it into Settings. That key works "
    "from any address, so nothing has to chase it. The website accepts "
    "0.0.0.0/0 even though the portal API this bot uses refuses it, which is "
    "why this cannot be done for you.",

    "You can also clear the developer-portal email and password in Settings - "
    "with a 0.0.0.0/0 key they are not needed.",
))


# A key does not start working the instant the portal hands it over - it has to
# reach the API servers first, which takes a little under a minute. Checking it
# straight away therefore says "broken" about a key that is perfectly good, so
# the check waits it out rather than believing the first answer.
VERIFY_ATTEMPTS = 6
VERIFY_GAP = 8


def _works(token, attempts=VERIFY_ATTEMPTS):
    """Whether the API accepts this token from this machine.

    /brawlers needs no player tag and is refused the same way when the address
    does not match, so it is the cheapest thing to ask.

    The retries are the point. A key read back immediately after being created
    is routinely refused for the first half-minute or so while it propagates,
    and the first version of this check took that at face value: it declared a
    correct key broken, refused to save it, and left the dead one in the config
    for the bot to fail with again next time.
    """
    for attempt in range(max(1, attempts)):
        try:
            response = requests.get(
                "https://api.brawlstars.com/v1/brawlers",
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
        except requests.RequestException:
            return True  # Network trouble is not the token's fault; do not discard it.
        if response.status_code == 200:
            return True
        if response.status_code != 403:
            # Rate limiting or an outage: says nothing about this key.
            return True
        if attempt + 1 < max(1, attempts):
            print(f"New API key not active yet, waiting "
                  f"({attempt + 1}/{attempts})...")
            time.sleep(VERIFY_GAP)
    return False


def refresh(previous=None, force=False, seen_ip=None):
    """Re-issue the API token for this machine's current address.

    Pass the token that was just rejected as `previous`. Returns the new token,
    or None with the reason in last_error(). Safe to call from several threads:
    only one refresh runs at a time, and the others simply use whatever it
    produced.
    """
    global _last_error, _failures, _quiet_until

    if time.time() < _quiet_until:
        # Already established that key-making cannot fix this connection.
        # Repeating it would only hammer the portal.
        _last_error = _GIVE_UP_MESSAGE
        return None

    email, password = credentials()
    if not email or not password:
        _last_error = (
            "Automatic token refresh is off. Add brawl_api_email and "
            "brawl_api_password to cfg/general_config.toml, or create a key at "
            "developer.brawlstars.com for the range 0.0.0.0/0 so it works from "
            "any address."
        )
        return None

    with _lock:
        # seen_ip comes from the API's own refusal - "does not allow access
        # from IP x.x.x.x" - and is therefore the address that actually needs a
        # key, as observed by the server doing the refusing. Asking a
        # what-is-my-ip service is the fallback, not the primary: it answers
        # for its own connection, and on a rotating address it answers late.
        address = seen_ip or current_ip()
        if not address:
            _last_error = "Could not determine this machine's public IP address."
            return None

        config = load_toml_as_dict("cfg/general_config.toml")
        stored = str(config.get("brawl_api_token", "")).strip()
        # Only skip the work if somebody else genuinely replaced the token while
        # this call waited on the lock. Deciding that by address instead - "the
        # last reissue was for this same IP, so nothing to do" - hands back the
        # very token that was just rejected, the retry fails identically, and
        # the bot reports that automatic refresh is not set up when it is.
        if not force and stored and previous is not None and stored != previous:
            return stored

        session = requests.Session()
        try:
            ok, error = _login(session, email, password)
            if not ok:
                _last_error = error
                return None

            keys = _list_keys(session)
            if keys is None:
                _last_error = "Could not read the key list from the developer portal."
                return None

            # Remove only what this module made. Anything created by hand or by
            # another tool is left exactly where it is.
            ours = [k for k in keys if KEY_MARKER in str(k.get("description", ""))]
            for key in ours:
                _revoke(session, key.get("id"))

            remaining = len(keys) - len(ours)
            if remaining >= MAX_KEYS:
                _last_error = (
                    f"The developer account already holds {remaining} keys that "
                    "were not created here, which is the limit. Delete one at "
                    "developer.brawlstars.com."
                )
                return None

            # A single address: the portal rejects every wider range. Left
            # configurable so it costs nothing to widen if Supercell ever
            # starts accepting ranges here.
            cidr_bits = config.get("brawl_api_cidr_bits", 32)
            token = _create(session, address, cidr_bits)
            if not token:
                _last_error = "The developer portal refused to create a new key."
                return None
            accepted = _works(token)
        except requests.RequestException as exc:
            _last_error = f"Could not reach the developer portal: {exc}"
            return None
        finally:
            session.close()

        # Saved whether or not the API has started accepting it yet. The key is
        # freshly issued for this exact address, which makes it strictly better
        # than whatever it replaces; throwing it away on a failed check left the
        # dead token in place and guaranteed the same failure next time. If it
        # is merely still propagating it will simply start working.
        config["brawl_api_token"] = token
        # Remembered so a second 403 from the same address does not send the
        # bot round this loop again - that would be a login storm, not a fix.
        config["_brawl_api_token_ip"] = address
        save_dict_as_toml(config, "cfg/general_config.toml")

        if accepted:
            _failures = 0
        else:
            _failures += 1
            if _failures >= GIVE_UP_AFTER:
                _quiet_until = time.time() + GIVE_UP_FOR
                _last_error = _GIVE_UP_MESSAGE
                print("Brawl Stars API: giving up on automatic key reissue - "
                      "this connection needs a 0.0.0.0/0 key made by hand.")
                return None

        if not accepted:
            # Deliberately not compared against current_ip() here. On the
            # connection this was debugged on, the API saw 195.181.175.176
            # while a what-is-my-ip service reported 152.233.35.233 at the same
            # moment - different destinations, different exits. Reporting that
            # as "your address changed" blamed the wrong thing entirely.
            _last_error = (
                f"A new key was issued for {address} and saved, but the API is "
                "not accepting it yet. Usually that is a new key still "
                "propagating and it starts working within a minute or two on "
                "its own. It also happens on a VPN whose traffic leaves by "
                "several addresses, where a key can only ever match some of "
                "the requests. The permanent fix for that is a key made by "
                "hand at developer.brawlstars.com with Allowed IP Ranges set "
                "to 0.0.0.0/0 - the website accepts it even though the portal "
                "API this bot uses will not, so it cannot be done for you."
            )
            print(f"Brawl Stars API key reissued for {address}, not active yet.")
            return None

        _last_error = None
        print(f"Brawl Stars API token reissued for {address}.")
        return token
