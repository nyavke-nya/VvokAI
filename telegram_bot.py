"""Remote control over Telegram, the same commands Discord already had.

Telegram could only ever send notifications out; there was no way to ask it
for anything back. People running the bot from their phone had to keep a
Discord server around just for that, which is a lot of setup for "is it still
going".

Long polling with the requests that are already a dependency, rather than a
Telegram library: getUpdates is one HTTP call in a loop, and adding a package
to requirements.txt costs everybody an install for eighty lines of code.

It reuses telegram_token and telegram_chat_id from cfg/webhook_config.toml -
the same two values notifications already need - so for anybody who set those
up, the commands simply start working. Anything arriving from a chat other
than telegram_chat_id is ignored: the token is the only secret here, and a bot
token is enough for anyone who has it to message the bot.
"""

import threading
import time

import requests

from remote_control import HELP, Reply, chunk
from utils import load_toml_as_dict

API = "https://api.telegram.org/bot{token}/{method}"

# Telegram's own cap is 4096; a little under it leaves room for the newline a
# chunk boundary can land on.
MESSAGE_LIMIT = 4000

# How long getUpdates is allowed to hold the connection open waiting for
# something to happen. This is the whole point of long polling: 30 seconds of
# waiting is one request, not thirty.
POLL_SECONDS = 30
REQUEST_TIMEOUT = POLL_SECONDS + 15

# After a network failure. Long enough not to hammer a down connection, short
# enough that the bot is controllable again quickly once it comes back.
RETRY_SECONDS = 5

# Between checks when Telegram is not configured at all. Long, because the
# answer only changes when somebody edits the settings page.
IDLE_SECONDS = 60

# What Telegram offers, which is deliberately a fraction of what Discord has.
#
# Everything the other commands did is on the panel, the panel asks for a login
# now, and /panel puts it one tap away on a phone. A second, worse copy of the
# interface in a chat window is not worth keeping in step with the first - and
# a queue read out as forty lines of text was never the good way to look at a
# queue. So Telegram's job is to get you to the panel.
#
# Anything still in HELP but not here answers with a line saying where it went.
COMMANDS = ("panel", "help")

# Matched with and without the @botname suffix Telegram appends in groups.
# /start is in here because it is the first thing Telegram itself sends when
# somebody opens a chat with a bot, and a link is the right answer to it.
ALIASES = {
    "web": "panel",
    "ui": "panel",
    "site": "panel",
    "link": "panel",
    "open": "panel",
    "start": "panel",
}


class TelegramBot:
    def __init__(self, remote):
        self.remote = remote
        self.started = False
        self._offset = None
        self._stop = threading.Event()

    # ── config ───────────────────────────────────────────────────────
    @staticmethod
    def _settings():
        config = load_toml_as_dict("cfg/webhook_config.toml", cache=False)
        return (str(config.get("telegram_token", "")).strip(),
                str(config.get("telegram_chat_id", "")).strip())

    def _call(self, token, method, **payload):
        response = requests.post(API.format(token=token, method=method),
                                 timeout=REQUEST_TIMEOUT, **payload)
        response.raise_for_status()
        return response.json()

    # ── sending ──────────────────────────────────────────────────────
    def _reply(self, token, chat_id, reply):
        if reply.photo is not None:
            self._call(token, "sendPhoto",
                       data={"chat_id": chat_id, "caption": reply.text[:1024]},
                       files={"photo": ("screenshot.png", reply.photo, "image/png")})
            return
        for piece in chunk(reply.text, MESSAGE_LIMIT):
            self._call(token, "sendMessage", json={"chat_id": chat_id, "text": piece})

    def _publish_command_list(self, token):
        """So the commands show up in Telegram's own menu, not just in /help."""
        self._call(token, "setMyCommands", json={"commands": [
            {"command": name, "description": what}
            for name, what in HELP if name in COMMANDS
        ]})

    # ── receiving ────────────────────────────────────────────────────
    @staticmethod
    def _command_in(text):
        """The bare command name in a message, or None.

        Telegram sends "/status@my_bot" in groups and "/status" in a direct
        chat, and people type "/Status" often enough to be worth handling.
        """
        text = str(text or "").strip()
        if not text.startswith("/"):
            return None
        word = text[1:].split()[0] if len(text) > 1 else ""
        word = word.split("@")[0].lower()
        return ALIASES.get(word, word) or None

    def _handle(self, token, chat_id, command):
        if command not in COMMANDS:
            # A command that exists but is not offered here gets an answer
            # that points somewhere, rather than a bare list that leaves
            # somebody wondering whether it broke.
            if command in {name for name, _ in HELP}:
                self._reply(token, chat_id, Reply(
                    f"/{command} lives in the panel now. Send /panel for the link."))
            else:
                self._reply(token, chat_id, self.remote.help(COMMANDS))
            return

        action = getattr(self.remote, command, None)
        if action is None:
            self._reply(token, chat_id, self.remote.help(COMMANDS))
            return
        try:
            self._reply(token, chat_id,
                        self.remote.help(COMMANDS) if command == "help" else action())
        except Exception as exc:  # noqa: BLE001 - a command must not kill the loop
            print(f"Telegram command /{command} failed: {exc}")
            self._call(token, "sendMessage",
                       json={"chat_id": chat_id, "text": f"That failed: {exc}"})

    def _poll_once(self, token, allowed_chat):
        payload = {"timeout": POLL_SECONDS, "allowed_updates": ["message"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        updates = self._call(token, "getUpdates", json=payload).get("result", [])

        for update in updates:
            # Move past it whatever happens, or one unparseable message would
            # be redelivered forever.
            self._offset = update.get("update_id", 0) + 1
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if chat_id != allowed_chat:
                continue
            command = self._command_in(message.get("text"))
            if command:
                self._handle(token, chat_id, command)

    # ── lifecycle ────────────────────────────────────────────────────
    def stop(self):
        self._stop.set()

    def _skip_backlog(self, token):
        """Ignore whatever piled up while the bot was off.

        Acting on a /stop sent yesterday, the moment the bot starts today, is
        not helpful. offset=-1 asks for the last update only; confirming past
        it discards the rest.
        """
        backlog = self._call(token, "getUpdates", json={"timeout": 0, "offset": -1})
        for update in backlog.get("result", []):
            self._offset = update.get("update_id", 0) + 1

    @staticmethod
    def _explain(exc):
        """What went wrong, in words that point at the fix."""
        response = getattr(exc, "response", None)
        if response is not None and response.status_code == 409:
            return ("Telegram: another copy of the bot is already using this token. "
                    "Close the other one - remote control will take over when it exits.")
        if response is not None and response.status_code == 401:
            return ("Telegram: the API rejected the token. Check telegram_token in "
                    "cfg/webhook_config.toml.")
        return f"Telegram: {exc}. Retrying."

    def run_bot(self):
        if self.started:
            return
        self.started = True

        # Nothing here is fatal. Remote control that switches itself off for
        # the rest of the session because one request failed at startup is
        # worse than useless - it looks like it is working. So the token is
        # re-read every pass (a token typed into the settings page starts
        # working within a poll), every failure is retried, and a message is
        # only printed when it changes, so an outage does not fill the console.
        published = False
        skipped = False
        said = None

        def say(message):
            nonlocal said
            if message != said:
                print(message)
                said = message

        try:
            while not self._stop.is_set():
                token, chat_id = self._settings()
                if not token or not chat_id:
                    say("Telegram remote control is off: telegram_token and "
                        "telegram_chat_id are not both set in cfg/webhook_config.toml.")
                    self._stop.wait(IDLE_SECONDS)
                    continue
                try:
                    if not skipped:
                        self._skip_backlog(token)
                        skipped = True
                    if not published:
                        self._publish_command_list(token)
                        published = True
                        say("Telegram remote control is on. Send /help in your "
                            "chat with the bot.")
                    self._poll_once(token, chat_id)
                    said = None
                except (requests.RequestException, ValueError) as exc:
                    say(self._explain(exc))
                    self._stop.wait(RETRY_SECONDS)
        finally:
            self.started = False
