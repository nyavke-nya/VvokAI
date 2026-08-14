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

  * Supercell's portal accepts a CIDR range when a key is created. Creating one
    by hand for 0.0.0.0/0 makes it work from any address with no credentials
    stored anywhere and no code involved. That is the better answer when the
    portal allows it; this exists for when it does not, and for people who
    would rather not think about it again.

Only keys carrying KEY_MARKER in their description are ever deleted, so a key
made by hand for something else is left alone.
"""

import re
import threading

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

_lock = threading.Lock()
_last_error = None


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


def _create(session, address):
    response = session.post(
        f"{PORTAL}/apikey/create",
        json={
            "name": KEY_NAME,
            "description": f"{KEY_MARKER} - reissued automatically for {address}",
            "cidrRanges": [address],
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


def refresh(force=False):
    """Re-issue the API token for this machine's current address.

    Returns the new token, or None with the reason in last_error(). Safe to
    call from several threads: only one refresh runs at a time, and the others
    simply use whatever it produced.
    """
    global _last_error

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
        address = current_ip()
        if not address:
            _last_error = "Could not determine this machine's public IP address."
            return None

        config = load_toml_as_dict("cfg/general_config.toml")
        if not force and config.get("_brawl_api_token_ip") == address:
            # Another thread already reissued for this address while this one
            # waited on the lock.
            return str(config.get("brawl_api_token", "")).strip() or None

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

            token = _create(session, address)
            if not token:
                _last_error = "The developer portal refused to create a new key."
                return None
        except requests.RequestException as exc:
            _last_error = f"Could not reach the developer portal: {exc}"
            return None
        finally:
            session.close()

        config["brawl_api_token"] = token
        # Remembered so a second 403 from the same address does not send the
        # bot round this loop again - that would be a login storm, not a fix.
        config["_brawl_api_token_ip"] = address
        save_dict_as_toml(config, "cfg/general_config.toml")

        _last_error = None
        print(f"Brawl Stars API token reissued for {address}.")
        return token
