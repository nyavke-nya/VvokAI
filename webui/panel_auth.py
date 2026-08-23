"""The panel's own account: one username, one password, stored hashed.

The web interface used to be open to anyone who could reach it, which was
defensible only while that meant "somebody sitting at this PC". It is not
defensible the moment the address is handed to a phone, and it is dangerous
the moment anybody tunnels it out: the panel starts and stops the bot, edits
the queue, and its settings page hands the Brawl Stars API token straight to
the browser.

So there is a login now, and on a fresh install the first thing the panel asks
for is a username and a password to create.

Deliberately small: one account, no roles, no email, no reset flow. This
guards one person's bot on one machine. A forgotten password is fixed by
deleting cfg/panel_auth.toml and setting it up again, which is the right
amount of ceremony for something whose recovery story is "walk over to the
computer".

The password is never stored. What is stored is PBKDF2-HMAC-SHA256 over it
with a random 16-byte salt, at a work factor high enough that the file being
read does not hand over the password. hashlib is in the standard library, so
this costs nobody an install.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Any

from utils import resolve_project_path

AUTH_FILE = "panel_auth.toml"

# OWASP's floor for PBKDF2-HMAC-SHA256 is 600k as of writing. This is one
# hash on one login, on a desktop, so there is no reason to go under it.
ITERATIONS = 600_000
SALT_BYTES = 16

# Letters, digits and the three separators people actually use. Narrow on
# purpose: it keeps the name safe to write into a flat TOML file without any
# quoting rules to get wrong, and nobody needs punctuation in the name they
# type to log into their own bot.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,32}$")
MIN_PASSWORD = 8


def auth_path() -> Path:
    return resolve_project_path("cfg", AUTH_FILE)


def _read() -> dict[str, str]:
    """The stored account, or {} if there is not one yet.

    Hand-parsed rather than via load_toml_as_dict because that caches, and a
    cached copy of the credentials file is the difference between "the account
    you just created works" and "log in again in a minute".
    """
    path = auth_path()
    if not path.exists():
        return {}
    found: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            found[key.strip()] = value.strip().strip('"')
    except OSError:
        return {}
    return found


def _write(values: dict[str, str]) -> None:
    path = auth_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f'{key} = "{value}"' for key, value in values.items())
    path.write_text(
        "# The panel's login. Delete this file to be asked to set one up again -\n"
        "# that is also how a forgotten password is fixed.\n"
        "#\n"
        "# The password is not in here. 'hash' is PBKDF2-HMAC-SHA256 over it with\n"
        "# the salt below, so reading this file does not give anybody the password.\n"
        f"{body}\n",
        encoding="utf-8",
    )
    # Best effort: on a machine that honours it, only the owner can read the
    # session key. Windows ignores the mode, which is why it is not the thing
    # the password is protected by.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash(password: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations
    ).hex()


def is_configured() -> bool:
    stored = _read()
    return bool(stored.get("username") and stored.get("hash") and stored.get("salt"))


def username() -> str:
    return _read().get("username", "")


def problem_with(name: str, password: str) -> str | None:
    """Why these credentials cannot be used, or None if they can."""
    if not USERNAME_PATTERN.match(str(name or "")):
        return ("The username must be 3 to 32 characters, using letters, digits, "
                "dots, dashes or underscores.")
    if len(str(password or "")) < MIN_PASSWORD:
        return f"The password must be at least {MIN_PASSWORD} characters."
    return None


def create(name: str, password: str) -> dict[str, Any]:
    """Set up the one account. Refuses if there already is one.

    That refusal is the whole security of the setup page: without it, anybody
    who could reach the panel could overwrite the account and lock the owner
    out of their own bot.
    """
    if is_configured():
        return {"ok": False, "message": "An account already exists on this panel."}
    complaint = problem_with(name, password)
    if complaint:
        return {"ok": False, "message": complaint}

    salt = secrets.token_bytes(SALT_BYTES)
    _write({
        "username": name,
        "salt": salt.hex(),
        "hash": _hash(password, salt, ITERATIONS),
        "iterations": str(ITERATIONS),
        # Flask signs the session cookie with this. Kept beside the account so
        # it survives a restart - regenerating it would sign everybody out
        # every time the bot starts, including the phone in your pocket.
        "secret_key": secrets.token_hex(32),
    })
    return {"ok": True, "message": "Account created."}


def verify(name: str, password: str) -> bool:
    stored = _read()
    if not stored.get("hash") or not stored.get("salt"):
        return False
    try:
        salt = bytes.fromhex(stored["salt"])
        iterations = int(stored.get("iterations", ITERATIONS))
    except (ValueError, TypeError):
        return False

    # Both halves compared in constant time, and the hash is computed even
    # when the name is wrong, so a wrong username and a wrong password take
    # the same time to answer.
    candidate = _hash(str(password or ""), salt, iterations)
    name_ok = hmac.compare_digest(str(name or ""), stored.get("username", ""))
    hash_ok = hmac.compare_digest(candidate, stored["hash"])
    return name_ok and hash_ok


def secret_key() -> str:
    """The key Flask signs session cookies with.

    Before an account exists there is nothing to keep signed in, so a
    throwaway key is fine and is deliberately not written to disk.
    """
    return _read().get("secret_key") or secrets.token_hex(32)
