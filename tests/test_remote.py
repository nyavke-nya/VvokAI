"""Remote control: the answers, and the Telegram transport around them.

Discord and Telegram now share one set of decisions. That is the point of the
file - somebody on Telegram being told "the bot is not running" where Discord
would have said "it is already pausing" is how a remote control stops being
worth trusting - so every rung of every ladder is checked once here and both
transports inherit it.
"""

import sys

from _harness import Failures

from remote_control import DISCORD_LIMIT, HELP, RemoteControl, chunk
from telegram_bot import ALIASES, TelegramBot

report = Failures("remote control")


class _Runtime:
    def __init__(self, **status):
        self.status = {"is_running": True, "state": "running", "last_error": ""}
        self.status.update(status)
        self.calls = []

    def get_status(self):
        return dict(self.status)

    def stop(self):
        self.calls.append("stop")
        return {"ok": True, "message": "Stopping."}

    def pause(self):
        self.calls.append("pause")
        return {"ok": True, "message": "Pausing."}

    def start_current_queue(self, remote):
        self.calls.append(("start", remote))
        return {"ok": True, "message": "Started."}


class _Data:
    def __init__(self, queue=None, playstyle="unified_dodge.pyla"):
        self._queue = queue if queue is not None else []
        self._playstyle = playstyle

    def get_queue_data(self):
        return self._queue

    def get_playstyles_payload(self):
        return {"current": {"name": self._playstyle}} if self._playstyle else {}


class _Window:
    def __init__(self, frame=None):
        self.frame = frame
        self.restarted = 0

    def screenshot(self):
        return self.frame

    def restart_brawl_stars(self):
        self.restarted += 1


def control(runtime=None, data=None, window=None):
    remote = RemoteControl(runtime or _Runtime(), data or _Data())
    remote.set_window_controller(window)
    return remote


# ── refusals ────────────────────────────────────────────────────────────
report.section("a command that cannot run right now says why, not just no")
for state, action, expected in [
    ("idle", "stop", "The bot is not currently running."),
    ("idle", "pause", "The bot is not currently running."),
    ("stopping", "stop", "The bot is already stopping, please wait."),
    ("pausing", "pause", "The bot is already pausing, please wait."),
    ("paused", "pause", "The bot is already paused."),
]:
    runtime = _Runtime(state=state)
    reply = getattr(control(runtime), action)()
    report.check(f"{action} while {state}", reply.text, expected)
    report.check(f"  and {action} was not actually called", runtime.calls, [])

report.section("an error state is reported with the error in it")
reply = control(_Runtime(state="error", last_error="adb fell over")).stop()
report.check("the reason is included", "adb fell over" in reply.text, True)

report.section("a bot that is not running at all")
report.check("is_running False beats any state",
             control(_Runtime(is_running=False, state="running")).stop().text,
             "The bot is not currently running.")

report.section("stopping and pausing are not interchangeable")
report.check("pausing while stopping is refused, in its own words",
             control(_Runtime(state="stopping")).pause().text,
             "The bot is currently stopping, so that is not available.")
report.check("stopping while pausing is refused too",
             control(_Runtime(state="pausing")).stop().text,
             "The bot is currently pausing, please wait before trying to stop it.")

report.section("stopping a paused bot is allowed - that is how you end a session")
runtime = _Runtime(state="paused")
report.check("it goes through", control(runtime).stop().text, "Success! Stopping.")
report.check("and reached the runtime", runtime.calls, ["stop"])

# ── actions ─────────────────────────────────────────────────────────────
report.section("start hands the runtime something that can hold a window")
runtime = _Runtime(state="idle", is_running=False)
remote = control(runtime)
report.check("start is not gated on the bot already running",
             remote.start().text, "Success! Started.")
handed = runtime.calls[0][1]
report.check("what start_current_queue got is the remote itself", handed is remote, True)
report.check("and pyla_main can put the window controller on it",
             hasattr(handed, "set_window_controller"), True)

report.section("screenshot")
report.check("no window controller yet",
             control(window=None).screenshot().text,
             "Failed to take a screenshot, is the bot running?")
report.check("a capture that came back empty says the same thing",
             control(window=_Window(None)).screenshot().text,
             "Failed to take a screenshot, is the bot running?")

try:
    import numpy as np
    reply = control(window=_Window(np.zeros((4, 4, 3), dtype=np.uint8))).screenshot()
    report.check("a real frame comes back as PNG bytes",
                 reply.photo[:4], b"\x89PNG")
    report.check("with a caption", bool(reply.text), True)
except ImportError:  # pragma: no cover - numpy is a hard dependency of the bot
    print("  (numpy missing, skipped the PNG check)")

report.section("restart_game")
window = _Window()
report.check("refused while idle",
             control(_Runtime(is_running=False, state="idle"), window=window).restart_game().text,
             "The bot is not currently running.")
report.check("and the game was left alone", window.restarted, 0)
report.check("running but no window yet is said plainly",
             control(window=None).restart_game().text,
             "There is no game window to restart yet.")
report.check("running with a window restarts it",
             control(window=window).restart_game().text, "Restarting Brawl Stars!")
report.check("exactly once", window.restarted, 1)

report.section("status")
report.check("not running", control(_Runtime(is_running=False)).status().text,
             "The bot is currently not running.")
text = control(_Runtime(state="paused"), _Data(playstyle="unified_aggro.pyla")).status().text
report.check("the state is named", "Paused" in text, True)
report.check("so is the playstyle", "unified_aggro.pyla" in text, True)
report.check("a playstyle payload with nothing in it does not crash",
             "None" in control(data=_Data(playstyle=None)).status().text, True)

report.section("queue")
report.check("empty", control(data=_Data([])).queue().text, "The queue is currently empty.")
text = control(data=_Data([
    {"brawler": "nori", "type": "trophies", "trophies": 11, "push_until": 1050,
     "automatically_pick": True},
    {"brawler": "grom", "type": "wins", "wins": 3, "push_until": 10},
])).queue().text
report.check("trophies read from the trophies field", "nori: 11/1050 trophies" in text, True)
report.check("wins read from the wins field", "grom: 3/10 wins" in text, True)
report.check("auto-picked is marked", "(automatically picked)" in text, True)
report.check("and a manual one is not", text.count("(automatically picked)"), 1)

# ── chunking ────────────────────────────────────────────────────────────
report.section("long replies are split on line boundaries, not mid-entry")
queue = _Data([{"brawler": f"brawler{i}", "type": "trophies", "trophies": i,
                "push_until": 1000} for i in range(200)])
pieces = chunk(control(data=queue).queue().text, DISCORD_LIMIT)
report.at_least("a 200-brawler queue needs more than one message", len(pieces), 2)
report.check("every piece fits", max(len(p) for p in pieces) <= DISCORD_LIMIT, True)
report.check("nothing was dropped",
             "".join(pieces), control(data=queue).queue().text)
report.check("no piece starts mid-entry",
             all(not p.startswith("brawler") for p in pieces[1:]), True)
report.check("a short reply stays one message", len(chunk("hello", 100)), 1)
report.check("empty text still returns something to send", chunk("", 100), [""])
report.check("a single over-long line is cut rather than lost",
             chunk("x" * 250, 100), ["x" * 100, "x" * 100, "x" * 50])

# ── telegram parsing ────────────────────────────────────────────────────
report.section("Telegram command parsing")
parse = TelegramBot._command_in
report.check("plain", parse("/status"), "status")
report.check("with the bot suffix groups add", parse("/status@my_pyla_bot"), "status")
report.check("with arguments after it", parse("/queue now please"), "queue")
report.check("capitals", parse("/STATUS"), "status")
report.check("an alias people will guess", parse("/restart"), "restart_game")
report.check("the old Discord name still works", parse("/view_queue"), "queue")
report.check("ordinary chat is not a command", parse("how is it going"), None)
report.check("a bare slash is not a command", parse("/"), None)
report.check("empty", parse(""), None)
report.check("None", parse(None), None)

report.section("every command in the help list is something the remote can do")
missing = [name for name, _ in HELP if not hasattr(RemoteControl, name)]
report.check("no help entry points at a method that does not exist", missing, [])
report.check("help lists itself, so /help is never 'unknown'",
             "help" in {name for name, _ in HELP}, True)
report.check("the aliases all resolve to real commands",
             sorted(t for t in ALIASES.values()
                    if t not in {name for name, _ in HELP}), [])


report.section("the README lists the commands that actually exist")
readme = open("README.md", encoding="utf-8").read()
undocumented = [name for name, _ in HELP if f"/{name}" not in readme]
report.check("every command is written down", undocumented, [])


report.section("a bad moment at startup must not switch remote control off for good")
# What it did before: one failed request on the way up printed "could not reach
# the API" and returned, so the thread was gone for the rest of the session and
# nothing said so again. Silence that looks like everything is fine.
import requests as _requests


def _http_error(status):
    response = _requests.Response()
    response.status_code = status
    return _requests.HTTPError(f"{status}", response=response)


explain = TelegramBot._explain
report.check("a second copy of the bot is named as such",
             "another copy" in explain(_http_error(409)), True)
report.check("and the fix is in the message",
             "Close the other one" in explain(_http_error(409)), True)
report.check("a rejected token points at the setting",
             "telegram_token" in explain(_http_error(401)), True)
report.check("anything else says it will retry",
             "Retrying" in explain(_requests.ConnectionError("no route")), True)

source = open("telegram_bot.py", encoding="utf-8").read()
loop = source[source.index("def run_bot("):]
# The old code returned from inside the try; the new one only ever waits and
# loops. Anything after the try that returns would put the thread back in the
# grave this section exists to keep it out of.
report.check("nothing inside the retry block returns",
             "return" in loop.split("try:", 1)[1], False)
report.check("the token is re-read inside the loop, so filling it in later works",
             loop.index("self._settings()") < loop.index("self._poll_once("), True)
report.check("and repeated failures are not printed over and over",
             "if message != said:" in loop, True)


sys.exit(report.finish())
