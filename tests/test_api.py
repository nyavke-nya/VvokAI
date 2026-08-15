"""The API paths everyone else will actually hit, without touching the network.

Covers the ordinary cases: a good token, a wrong tag, an expired key with no
portal credentials, and a stable-IP user whose address changed once. None of
these should be slow, and none should reach the developer portal more than once.
"""
import sys
import time
import types

from _harness import Failures

import brawl_api

report = Failures("brawl stars api")
calls = {"http": 0, "refresh": 0, "slept": 0.0}


class Reply:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body or {}
        self.text = str(self._body)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._body


def install(responder, configured=False, refresh_result=None):
    calls["http"] = 0
    calls["refresh"] = 0
    calls["slept"] = 0.0
    brawl_api.clear_cache()

    def fake_get(url, **kwargs):
        calls["http"] += 1
        return responder(calls["http"])

    def fake_sleep(seconds):
        calls["slept"] += seconds

    brawl_api.requests = types.SimpleNamespace(
        get=fake_get, RequestException=Exception)
    brawl_api.time = types.SimpleNamespace(time=time.time, sleep=fake_sleep)
    brawl_api.get_token = lambda: "token"

    token_module = types.SimpleNamespace(
        is_configured=lambda: configured,
        last_error=lambda: "portal said no",
    )

    def fake_refresh(previous=None, seen_ip=None, force=False):
        calls["refresh"] += 1
        calls["seen_ip"] = seen_ip
        return refresh_result

    token_module.refresh = fake_refresh
    sys.modules["brawl_token"] = token_module


def check(label, got, want):
    report.check(label, got, want)


PLAYER = {"name": "Someone", "trophies": 500}
DENIED = {"reason": "accessDenied.invalidIp",
          "message": "Invalid authorization: API key does not allow access from IP 8.8.8.8"}

report.section("the ordinary case: it just works")
install(lambda n: Reply(200, PLAYER))
check("returns the player", brawl_api.get_player_info("#ABC")["name"], "Someone")
check("one request", calls["http"], 1)
check("nothing slept", calls["slept"], 0.0)

report.section("the same player again is served from cache, not the network")
brawl_api.get_player_info("#ABC")
check("still one request", calls["http"], 1)

report.section("a wrong tag is reported once, not re-asked on every poll")
install(lambda n: Reply(404, {}))
brawl_api.get_player_info("#BAD")
first = calls["http"]
for _ in range(5):
    brawl_api.get_player_info("#BAD")
check("five more polls cost nothing", calls["http"], first)
check("and the reason is still reported",
      "not found" in (brawl_api.last_error() or ""), True)

report.section("expired key, nobody configured the portal: fast and honest")
install(lambda n: Reply(403, DENIED), configured=False)
brawl_api.get_player_info("#ABC")
check("never called the portal", calls["refresh"], 0)
check("told them how to fix it",
      "0.0.0.0/0" in (brawl_api.last_error() or ""), True)

report.section("address changed once, portal configured: one reissue, then it works")
seq = {"n": 0}


def changed_then_fine(n):
    # First call and its two retries are refused; after the reissue it works.
    return Reply(200, PLAYER) if seq["n"] else Reply(403, DENIED)


install(changed_then_fine, configured=True, refresh_result="new-token")


def refresh_then_ok(previous=None, seen_ip=None, force=False):
    calls["refresh"] += 1
    calls["seen_ip"] = seen_ip
    seq["n"] = 1
    return "new-token"


sys.modules["brawl_token"].refresh = refresh_then_ok
result = brawl_api.get_player_info("#ABC")
check("the player comes back", bool(result), True)
check("the portal was used exactly once", calls["refresh"], 1)
check("and it was told the address the API reported", calls.get("seen_ip"), "8.8.8.8")

report.section("a connection that never matches does not become a portal loop")
install(lambda n: Reply(403, DENIED), configured=True, refresh_result=None)
for _ in range(6):
    brawl_api.get_player_info("#ABC")
check("the portal was tried once, not six times", calls["refresh"], 1)
check("and the waiting stayed bounded", calls["slept"] <= 3.0, True)

sys.exit(report.finish())
