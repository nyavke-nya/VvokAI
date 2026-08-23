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

sys.path.insert(0, "tools")

import installer  # noqa: E402
import tunnel
from remote_control import RemoteControl
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

    report.section("the password cannot be guessed at leisure")
    # Unlimited attempts were survivable while only your own network could
    # reach the panel. A public address gets guessed at by machines, all day.
    throttle = panel_auth.LoginThrottle()
    for attempt in range(panel_auth.LoginThrottle.FREE_ATTEMPTS):
        throttle.record_failure("1.2.3.4", now=1000)
    report.check("a few wrong guesses are free - people mistype",
                 throttle.locked_for("1.2.3.4", now=1000), 0.0)
    throttle.record_failure("1.2.3.4", now=1000)
    report.check("then it starts waiting",
                 throttle.locked_for("1.2.3.4", now=1000),
                 panel_auth.LoginThrottle.BASE_LOCK_SECONDS)
    throttle.record_failure("1.2.3.4", now=1000)
    report.check("and the wait doubles",
                 throttle.locked_for("1.2.3.4", now=1000),
                 panel_auth.LoginThrottle.BASE_LOCK_SECONDS * 2)
    for _ in range(20):
        throttle.record_failure("1.2.3.4", now=1000)
    report.check("up to a ceiling, so it cannot run away",
                 throttle.locked_for("1.2.3.4", now=1000),
                 panel_auth.LoginThrottle.MAX_LOCK_SECONDS)
    report.check("the wait runs down with the clock",
                 throttle.locked_for("1.2.3.4", now=1000 + panel_auth.LoginThrottle.MAX_LOCK_SECONDS),
                 0.0)
    report.check("one caller's failures do not lock out another",
                 throttle.locked_for("5.6.7.8", now=1000), 0.0)
    throttle.record_success("1.2.3.4")
    report.check("and getting in clears the record",
                 throttle.locked_for("1.2.3.4", now=1000), 0.0)

    report.section("the throttle is actually wired to the login route")
    guessing = app.test_client()
    codes = []
    for _ in range(panel_auth.LoginThrottle.FREE_ATTEMPTS + 2):
        codes.append(guessing.post("/api/auth/login",
                                   json={"username": "vvok", "password": "wrong"}).status_code)
    report.check("wrong guesses are refused", codes[0], 401)
    report.check("and eventually stop being answered at all", codes[-1], 429)
    report.check("with a wait people can act on",
                 "Try again in" in guessing.post(
                     "/api/auth/login",
                     json={"username": "vvok", "password": "correct horse"}).get_json()["message"],
                 True)

    report.section("the first account has to be created at home")
    # Whoever sets up a brand new panel owns it. On a public address that
    # would be whichever stranger found the URL before the owner finished.
    for address, allowed in [("127.0.0.1", True), ("192.168.0.5", True),
                             ("10.0.0.9", True), ("172.18.0.1", True),
                             ("8.8.8.8", False), ("203.0.113.7", False),
                             ("", False), (None, False)]:
        report.check(f"{address!r} counts as local: {allowed}",
                     panel_auth.is_local_request(address), allowed)

    empty = fresh_app().test_client()
    refused = empty.post("/api/auth/setup",
                         environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
                         json={"username": "stranger", "password": "hunter2222"})
    report.check("a remote setup attempt is refused", refused.status_code, 403)
    report.check("and no account was created", panel_auth.is_configured(), False)
    report.check("the same request from the LAN works",
                 empty.post("/api/auth/setup",
                            environ_overrides={"REMOTE_ADDR": "192.168.0.5"},
                            json={"username": "vvok", "password": "correct horse"}).status_code,
                 200)

    report.section("the tunnel refuses to run before there is a login")
    # A tunnel makes every remote request look local, which would defeat the
    # check above - so the two have to agree.
    class _Remote:
        def __init__(self):
            self.url = self.problem = None

        def set_public_url(self, url, problem=None):
            self.url, self.problem = url, problem

    remote = _Remote()
    report.check("off by default", tunnel.start_if_enabled(remote, 5185, "off", True), None)
    report.check("and an unset value is off too",
                 tunnel.start_if_enabled(remote, 5185, None, True), None)
    remote = _Remote()
    report.check("asked for, but no account yet",
                 tunnel.start_if_enabled(remote, 5185, "cloudflare", False), None)
    report.check("the panel is told why", "login" in (remote.problem or ""), True)
    report.check("and no address was published", remote.url, None)

    report.check("a missing cloudflared is a hint, not a stack trace",
                 "winget install" in tunnel.INSTALL_HINT, True)
    report.check("the address it looks for is the quick-tunnel one",
                 bool(tunnel.URL_PATTERN.search(
                     "|  https://calm-river-1234.trycloudflare.com  |")), True)
    report.check("and a lookalike host is not mistaken for one",
                 bool(tunnel.URL_PATTERN.search("https://a.trycloudflare.com.attacker.net")),
                 False)

    report.section("setup arranges remote access itself, without a question")
    # It used to ask. "Answer yes to the thing you do not recognise" is not a
    # better experience than it working, and the people running this fork
    # should not have to know what a tunnel is to reach their own panel.
    config = sandbox / "general_config.toml"
    installer.GENERAL_CONFIG = config
    starting_config = 'player_tag = "#ABC"\nbrawl_api_token = "secret"\n'

    def set_up(existing=None, cloudflared_ok=True):
        config.write_text(
            starting_config + (f'remote_access = "{existing}"\n' if existing else ""),
            encoding="utf-8")
        installed = installer.install_cloudflared
        installer.install_cloudflared = lambda: cloudflared_ok
        try:
            installer.set_up_remote_access()
        finally:
            installer.install_cloudflared = installed
        return config.read_text(encoding="utf-8")

    report.check("a fresh install gets it turned on",
                 'remote_access = "cloudflare"' in set_up(), True)
    report.check("nothing was asked",
                 "input(" in open("tools/installer.py", encoding="utf-8").read(), False)
    report.check("the rest of the config survives - it holds the API token",
                 'brawl_api_token = "secret"' in set_up(), True)
    report.check("and the setting is written once, not appended every launch",
                 set_up().count("remote_access"), 1)

    report.section("but a deliberate off is left alone")
    # Setup runs on every launch. Undoing somebody's choice each time would
    # make the setting impossible to keep.
    report.check("off stays off", 'remote_access = "off"' in set_up(existing="off"), True)
    report.check("and it is not quietly rewritten to cloudflare",
                 'cloudflare' in set_up(existing="off"), False)
    report.check("an existing cloudflare stays on",
                 'remote_access = "cloudflare"' in set_up(existing="cloudflare"), True)

    report.section("a download that does not get through turns it off, not on")
    # Better a setting that says off than one that says on and fails at every
    # start with nothing to point at.
    text = set_up(cloudflared_ok=False)
    report.check("the setting reflects reality", 'remote_access = "off"' in text, True)

    report.section("reading the setting back")
    for written, expected in [('remote_access = "cloudflare"', "cloudflare"),
                              ('remote_access = "off"', "off"),
                              ("remote_access = 'off'", "'off'"),
                              ("", None)]:
        config.write_text(starting_config + (written + chr(10) if written else ""),
                          encoding="utf-8")
        report.check(f"{written or 'no line at all'} reads as {expected!r}",
                     installer.current_remote_access(), expected)

    report.section("a tunnel left behind by the last run is closed, not left running")
    # Every restart used to start another cloudflared and leave the old one up.
    # They pile up, each holding a tunnel to a panel that is gone, and an
    # address from an earlier message answers with Cloudflare error 1033 -
    # which reads like a broken tunnel rather than a stale link.
    report.check("the pid of the one we started is written down",
                 tunnel.PID_FILE.name, ".cloudflared.pid")
    report.check("start_if_enabled closes the previous one first",
                 open("tunnel.py", encoding="utf-8").read().index("stop_previous()")
                 < open("tunnel.py", encoding="utf-8").read().index("tunnel = Tunnel(port)"),
                 True)
    report.check("and the current one is stopped on the way out",
                 "atexit.register(tunnel.stop)" in open("tunnel.py", encoding="utf-8").read(),
                 True)
    report.check("a pid that is not cloudflared is never killed - pids get reused",
                 tunnel._is_cloudflared(0), False)
    report.check("no leftover pid means nothing to do", tunnel.stop_previous(), False)
    report.check("the cleanup does not import psutil, which is not a dependency "
                 "of this project and would make it silently never run",
                 "import psutil" in open("tunnel.py", encoding="utf-8").read(), False)

    report.section("and the address is advertised as temporary")
    remote_panel = RemoteControl(None, None)
    remote_panel.set_web_port(5185)
    remote_panel.set_public_url("https://example-name.trycloudflare.com")
    panel_text = remote_panel.panel().text
    report.check("the link is the public one",
                 "https://example-name.trycloudflare.com" in panel_text, True)
    report.check("and it says the link expires with the restart",
                 "changes every time" in panel_text, True)

    report.section("the credentials file is not something to commit")
    ignored = open(".gitignore", encoding="utf-8").read()
    report.check("gitignore covers it", "cfg/panel_auth.toml" in ignored, True)
finally:
    shutil.rmtree(sandbox, ignore_errors=True)

sys.exit(report.finish())
