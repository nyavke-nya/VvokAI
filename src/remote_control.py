"""What a remote command does, with nothing about how it was delivered.

Discord and Telegram ask for exactly the same seven things - start, stop,
pause, status, a screenshot, a game restart, the queue - and the interesting
part of each is a ladder of state checks. Being told "the bot is not running"
on one transport where the other would have said "it is already pausing" is
how a remote control stops being worth trusting, so the ladders live here
once rather than once per transport.

Every action returns a Reply: text, and for a screenshot the PNG bytes. What
to do with that - embed it, chunk it, mark it up - is the transport's problem.

This is also the object handed to pyla_main, because it is the one thing both
bots can see: the run puts its WindowController here when it starts and clears
it when it stops, and a screenshot asked for from either side finds it.
"""

from io import BytesIO

from PIL import Image


class Reply:
    """Text, and optionally an image to go with it."""

    __slots__ = ("text", "photo")

    def __init__(self, text, photo=None):
        self.text = text
        self.photo = photo

    def __repr__(self):
        return f"Reply({self.text!r}, photo={'yes' if self.photo else 'no'})"


def chunk(text, limit):
    """Split on line boundaries so a message never lands mid-entry.

    Both transports cap message length - Discord at 2000 characters, Telegram
    at 4096 - and a queue of seventy brawlers is well past either.
    """
    lines = text.splitlines(keepends=True)
    out = []
    current = ""
    for line in lines:
        if current and len(current) + len(line) > limit:
            out.append(current)
            current = ""
        # A single line longer than the limit still has to go somewhere.
        while len(line) > limit:
            out.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        out.append(current)
    return out or [""]


HELP = [
    ("start", "Start the bot on the current queue"),
    ("stop", "Stop the bot once it reaches the lobby"),
    ("pause", "Pause the bot once it reaches the lobby"),
    ("status", "What the bot is doing right now"),
    ("screenshot", "A screenshot of the game window"),
    ("queue", "The brawlers left to push"),
    ("restart_game", "Restart Brawl Stars"),
    ("help", "This list"),
]


class RemoteControl:
    def __init__(self, runtime_manager, data_service):
        self.runtime_manager = runtime_manager
        self.data_service = data_service
        self.window_controller = None

    # pyla_main calls this when a run starts, and again with None when it ends.
    def set_window_controller(self, window_controller):
        self.window_controller = window_controller

    # ── the ladders ──────────────────────────────────────────────────
    def _blocked(self, status, action):
        """The shared "you cannot do that right now" answers, or None."""
        state = status.get("state")
        if state == "idle" or not status.get("is_running"):
            return "The bot is not currently running."
        if state == "error":
            return (f"The bot is in an error state:\n{status.get('last_error', '')}\n"
                    "Please wait a few seconds or check the logs.")
        if state == "stopping":
            if action == "stop":
                return "The bot is already stopping, please wait."
            return "The bot is currently stopping, so that is not available."
        if state == "pausing":
            if action == "pause":
                return "The bot is already pausing, please wait."
            return "The bot is currently pausing, please wait before trying to stop it."
        if state == "paused" and action == "pause":
            return "The bot is already paused."
        return None

    @staticmethod
    def _outcome(result):
        return f"{'Success' if result.get('ok') else 'Failed'}! {result.get('message', '')}".strip()

    # ── actions ──────────────────────────────────────────────────────
    def start(self):
        return Reply(self._outcome(self.runtime_manager.start_current_queue(self)))

    def stop(self):
        status = self.runtime_manager.get_status()
        refusal = self._blocked(status, "stop")
        if refusal:
            return Reply(refusal)
        return Reply(self._outcome(self.runtime_manager.stop()))

    def pause(self):
        status = self.runtime_manager.get_status()
        refusal = self._blocked(status, "pause")
        if refusal:
            return Reply(refusal)
        return Reply(self._outcome(self.runtime_manager.pause()))

    def status(self):
        status = self.runtime_manager.get_status()
        if not status.get("is_running"):
            return Reply("The bot is currently not running.")

        message = f"The bot is currently {status.get('state', 'unknown').capitalize()}."
        if status.get("last_error"):
            message += f"\nLast error: {status['last_error']}"

        playstyle = (self.data_service.get_playstyles_payload() or {}).get("current")
        message += f"\nPlaystyle: {(playstyle or {}).get('name') or 'None'}"
        message += "\nQueue: ask for the queue to see what is left."
        return Reply(message)

    def restart_game(self):
        status = self.runtime_manager.get_status()
        if status.get("state") == "idle" or not status.get("is_running"):
            return Reply("The bot is not currently running.")
        if self.window_controller is None:
            return Reply("There is no game window to restart yet.")
        self.window_controller.restart_brawl_stars()
        return Reply("Restarting Brawl Stars!")

    def screenshot(self):
        # Deliberately one message for both failures. From the outside "no
        # window controller" and "the capture came back empty" are the same
        # thing: there is nothing to look at because nothing is running.
        if self.window_controller is None:
            return Reply("Failed to take a screenshot, is the bot running?")
        frame = self.window_controller.screenshot()
        if frame is None:
            return Reply("Failed to take a screenshot, is the bot running?")

        buffer = BytesIO()
        Image.fromarray(frame).save(buffer, format="PNG")
        return Reply("Here's a screenshot of the current game window:", buffer.getvalue())

    def queue(self):
        queue = self.data_service.get_queue_data()
        if not queue:
            return Reply("The queue is currently empty.")

        lines = ["Current queue:"]
        for item in queue:
            push_type = item.get("type", "Unknown")
            current = item.get("trophies") if push_type == "trophies" else item.get("wins")
            picked = " (automatically picked)" if item.get("automatically_pick") else ""
            lines.append(f"- {item.get('brawler', 'Unknown')}: {current}/"
                         f"{item.get('push_until', 'Unknown')} {push_type}{picked}")
        return Reply("\n".join(lines))

    def help(self):
        return Reply("\n".join(f"/{name} - {what}" for name, what in HELP))


# Message length caps. Discord's hard limit is 2000 characters and Telegram's
# is 4096; both are set a little under so a chunk boundary has room to land on
# a newline rather than mid-word.
DISCORD_LIMIT = 1800
