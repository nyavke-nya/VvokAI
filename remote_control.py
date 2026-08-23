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

import socket
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


def _rank(address):
    """How likely an address is to be the one a phone can reach.

    A machine has several. This one, while being written, had three: the
    Wi-Fi at 192.168.0.5, a Docker bridge at 172.18.0.1 and a Radmin VPN at
    26.35.219.234. Asking the routing table which interface leaves the machine
    - the usual trick - returned the Docker bridge for every target tried,
    including 8.8.8.8, because the virtual adapter holds the default route.
    So the routing table cannot be trusted to answer this and the addresses
    are ranked instead.

    192.168/16 and 10/8 are where home networks live. 172.16/12 is a real
    private range too, but on Windows it is nearly always Docker, WSL or
    Hyper-V. Anything outside the private ranges is a VPN or worse.
    """
    if address.startswith("192.168."):
        return 0
    if address.startswith("10."):
        return 1
    parts = address.split(".")
    try:
        if parts[0] == "172" and 16 <= int(parts[1]) <= 31:
            return 2
    except (IndexError, ValueError):
        pass
    return 3


def lan_addresses(limit=3):
    """Every address this machine might be reachable at, best guess first.

    Ranked rather than picked, and more than one is offered, because the
    ranking is a heuristic and a confidently wrong link is worse than two to
    try.
    """
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except (socket.gaierror, OSError):
        pass
    # The routing table as well: it can know about an interface that the
    # hostname lookup does not.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("8.8.8.8", 1))
            found.add(probe.getsockname()[0])
    except OSError:
        pass

    usable = [a for a in found
              if not a.startswith("127.") and not a.startswith("169.254.")]
    return sorted(usable, key=lambda a: (_rank(a), a))[:limit]


HELP = [
    ("start", "Start the bot on the current queue"),
    ("stop", "Stop the bot once it reaches the lobby"),
    ("pause", "Pause the bot once it reaches the lobby"),
    ("status", "What the bot is doing right now"),
    ("screenshot", "A screenshot of the game window"),
    ("queue", "The brawlers left to push"),
    ("restart_game", "Restart Brawl Stars"),
    ("panel", "A link to the web interface"),
    ("help", "This list"),
]


class RemoteControl:
    def __init__(self, runtime_manager, data_service):
        self.runtime_manager = runtime_manager
        self.data_service = data_service
        self.window_controller = None
        # main.py fills this in once it knows which port Flask picked.
        self.web_port = None

    def set_web_port(self, port):
        self.web_port = port

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

    def panel(self):
        """Where to open the web interface from.

        LAN addresses only. The panel has a login now, so this is no longer
        the only thing between a stranger and the bot - but a link that cannot
        be reached from outside the house is still worth more than one that
        can, so the reply says which network it needs.
        """
        if not self.web_port:
            return Reply("The web interface has not started yet.")

        addresses = lan_addresses()
        if not addresses:
            return Reply(f"On this machine: http://127.0.0.1:{self.web_port}"
                         + chr(10) +
                         "This machine has no address on a local network, so "
                         "there is no link a phone could open.")

        lines = [f"http://{addresses[0]}:{self.web_port}", ""]
        if len(addresses) > 1:
            lines.append("If that one does not open, this machine also answers at:")
            lines.extend(f"http://{other}:{self.web_port}" for other in addresses[1:])
            lines.append("")
        lines.append("Only from the same Wi-Fi as the PC, and it asks for the "
                     "panel login before it opens anything.")
        return Reply(chr(10).join(lines))

    def help(self):
        return Reply("\n".join(f"/{name} - {what}" for name, what in HELP))


# Message length caps. Discord's hard limit is 2000 characters and Telegram's
# is 4096; both are set a little under so a chunk boundary has room to land on
# a newline rather than mid-word.
DISCORD_LIMIT = 1800
