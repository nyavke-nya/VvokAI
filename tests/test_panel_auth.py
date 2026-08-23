"""The panel's login: setting one up, using it, and not getting past it.

The web interface was open to anyone who could reach the port. That was fine
while reaching it meant sitting at the PC, and stopped being fine the moment
the address went to a phone - it starts and stops the bot, rewrites the queue,
and its settings page hands the Brawl Stars API token to the browser.

Every check here runs against a throwaway credentials file, so running the
suite never touches the account on this machine.
"""

import shutil
import sys
import tempfile
from pathlib import Path

from _harness import Failures

from webui import create_app, panel_auth

report = Failures("panel login")

sandbox = Path(tempfile.mkdtemp(prefix="vvok-auth-"))
panel_auth.auth_path = lambda: sandbox / "panel_auth.toml"


def fresh_app():
    """An app with no account yet, like a first run."""
    if panel_auth.auth_path().exists():
        panel_auth.auth_path().unlink()
    return create_app(lambda *args, **kwargs: None)


try:
    # ── before there is an account ───────────────────────────────────
    report.section("a fresh install asks for an account before it opens")
    app = fresh_app()
    client = app.test_client()
    report.check("the panel redirects instead of rendering",
                 client.get("/").status_code, 302)
    report.check("and it redirects to the login page",
                 client.get("/").headers["Location"].endswith("/login"), True)
    report.check("the login page itself is reachable", client.get("/login").status_code, 200)
    report.check("the page is told there is no account yet",
                 client.get("/api/auth/state").get_json()["configured"], False)

    report.section("nothing behind the gate answers without a session")
    for path in ["/api/queue", "/api/settings/general", "/api/runtime/status",
                 "/api/history", "/api/bootstrap"]:
        report.check(f"{path} is refused", client.get(path).status_code, 401)
    report.check("and says why, so the page can react",
                 client.get("/api/queue").get_json()["code"], "LOGIN_REQUIRED")
    report.check("starting the bot is refused too",
                 client.post("/api/runtime/start").status_code, 401)

    # A tunnel connects to the loopback interface, so requests arriving
    # through one look local. An exemption for 127.0.0.1 would have handed the
    # panel to the internet the first time somebody forwarded the port.
    report.check("being local is not a way past the gate",
                 client.get("/api/queue", environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code,
                 401)

    # ── creating it ──────────────────────────────────────────────────
    report.section("credentials that would not protect anything are refused")
    for name, password, why in [
        ("ab", "longenough1", "too short a username"),
        ("has spaces", "longenough1", "a space in the username"),
        ("bad/slash", "longenough1", "a slash in the username"),
        ("", "longenough1", "no username"),
        ("someone", "short", "too short a password"),
        ("someone", "", "no password"),
    ]:
        response = client.post("/api/auth/setup", json={"username": name, "password": password})
        report.check(f"{why} is rejected", response.status_code, 400)
    report.check("and none of that created an account",
                 panel_auth.is_configured(), False)

    report.section("setting one up signs you straight in")
    response = client.post("/api/auth/setup",
                           json={"username": "vvok", "password": "correct horse"})
    report.check("it is accepted", response.status_code, 200)
    report.check("an account now exists", panel_auth.is_configured(), True)
    report.check("the panel opens", client.get("/").status_code, 200)
    report.check("and so does the data behind it", client.get("/api/queue").status_code, 200)
    report.check("the session knows who you are",
                 client.get("/api/auth/state").get_json()["username"], "vvok")

    report.section("the password is not written down anywhere")
    stored = panel_auth.auth_path().read_text(encoding="utf-8")
    report.check("not in the credentials file", "correct horse" in stored, False)
    report.check("a salt was generated", 'salt = "' in stored, True)
    report.check("and the work factor is recorded with the hash",
                 f'iterations = "{panel_auth.ITERATIONS}"' in stored, True)
    report.at_least("at or above the OWASP floor for PBKDF2-SHA256",
                    panel_auth.ITERATIONS, 600_000)
    report.check("two accounts with the same password do not share a hash",
                 panel_auth._hash("correct horse", b"salt-one", 1000)
                 != panel_auth._hash("correct horse", b"salt-two", 1000), True)

    report.section("the setup page cannot be used to take the account over")
    # Without this, anybody who reached the panel could overwrite the account
    # and lock its owner out of their own bot.
    response = app.test_client().post("/api/auth/setup",
                                      json={"username": "someone_else", "password": "hunter2222"})
    report.check("a second setup is refused", response.status_code, 400)
    report.check("with the reason", "already exists" in response.get_json()["message"], True)
    report.check("and the original account is untouched", panel_auth.username(), "vvok")

    # ── using it ─────────────────────────────────────────────────────
    report.section("signing in")
    stranger = app.test_client()
    report.check("a stranger is still shut out", stranger.get("/api/queue").status_code, 401)
    report.check("wrong password", stranger.post(
        "/api/auth/login", json={"username": "vvok", "password": "nope"}).status_code, 401)
    report.check("wrong username", stranger.post(
        "/api/auth/login", json={"username": "someone", "password": "correct horse"}).status_code, 401)
    report.check("the two failures are worded the same, so the reply does not "
                 "say which usernames exist",
                 stranger.post("/api/auth/login",
                               json={"username": "vvok", "password": "nope"}).get_json()["message"],
                 stranger.post("/api/auth/login",
                               json={"username": "ghost", "password": "nope"}).get_json()["message"])
    report.check("the right pair works", stranger.post(
        "/api/auth/login", json={"username": "vvok", "password": "correct horse"}).status_code, 200)
    report.check("and the panel opens for them", stranger.get("/api/queue").status_code, 200)
    report.check("verify() agrees", panel_auth.verify("vvok", "correct horse"), True)
    report.check("and rejects a near miss", panel_auth.verify("vvok", "correct horsE"), False)

    report.section("signing out")
    report.check("logout is accepted", stranger.post("/api/auth/logout").status_code, 200)
    report.check("and the gate closes again", stranger.get("/api/queue").status_code, 401)

    report.section("the login survives a restart")
    # A session key regenerated at startup would sign out every device on
    # every launch, including the phone in your pocket.
    key = panel_auth.secret_key()
    report.check("the key is stored, not made up each time",
                 panel_auth.secret_key(), key)
    restarted = create_app(lambda *args, **kwargs: None)
    report.check("a new app picks up the same key", restarted.secret_key, key)
    report.check("so a cookie from before it still works",
                 client.get("/api/queue").status_code, 200)

    report.section("the credentials file is not something to commit")
    ignored = open(".gitignore", encoding="utf-8").read()
    report.check("gitignore covers it", "cfg/panel_auth.toml" in ignored, True)
finally:
    shutil.rmtree(sandbox, ignore_errors=True)

sys.exit(report.finish())
