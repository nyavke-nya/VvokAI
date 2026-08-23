"""A public HTTPS address for the panel, for when you are not on the Wi-Fi.

The panel only ever answered on the local network. That is fine at home and
useless on mobile data, which is exactly when you want to know whether the bot
is still going.

Cloudflare's quick tunnels do this without an account, without opening a port
on the router, and without the router even being able to - they dial out, so
they work behind carrier-grade NAT where port forwarding simply cannot. What
comes back is a random https://something.trycloudflare.com that forwards to
the panel on this machine, and it is HTTPS end to end, which matters because a
password goes over it.

Two deliberate limits:

  * It is off unless remote_access is set to "cloudflare" in
    cfg/general_config.toml. Nothing gets exposed because somebody updated.
  * It refuses to start until the panel has an account. Whoever opens a brand
    new panel first gets to own it, and a tunnel makes every remote request
    look local, so starting one before setup would hand the bot to whoever
    found the URL first.

cloudflared is not bundled and not downloaded automatically - a tool that
reaches out to the internet on your behalf should be something you chose to
install. If it is missing, the panel says the one command that installs it.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading

# cloudflared prints the assigned hostname to stderr, in a box, once it is up.
# The negative lookahead stops it matching the prefix of a longer host -
# evil.trycloudflare.com.somewhere-else.net would otherwise read as a
# match. Nothing hostile reaches this today, since it only ever parses
# the output of a process we launched, but a URL pattern that accepts a
# lookalike is a bad thing to leave lying around.
URL_PATTERN = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com(?![-\w.])")

# How long to wait for that line before deciding it is not coming.
STARTUP_SECONDS = 45

INSTALL_HINT = ("cloudflared is not installed. In PowerShell:\n"
                "  winget install --id Cloudflare.cloudflared\n"
                "Then restart the bot.")


def is_available():
    return shutil.which("cloudflared") is not None


class Tunnel:
    """Runs cloudflared beside the panel and remembers the address it got."""

    def __init__(self, port):
        self.port = port
        self.url = None
        self.error = None
        self._process = None
        self._ready = threading.Event()

    def start(self):
        if not is_available():
            self.error = INSTALL_HINT
            return False
        try:
            self._process = subprocess.Popen(
                ["cloudflared", "tunnel", "--no-autoupdate",
                 "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # No console window popping up on Windows every launch.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.error = f"Could not start cloudflared: {exc}"
            return False

        threading.Thread(target=self._watch, daemon=True,
                         name="pyla-tunnel-watch").start()
        if not self._ready.wait(STARTUP_SECONDS):
            self.error = ("cloudflared did not report an address within "
                          f"{STARTUP_SECONDS}s. The panel is still reachable on "
                          "your own network.")
            return False
        return True

    def _watch(self):
        """Read cloudflared's output for the hostname, then keep draining it.

        Draining matters: a pipe nobody reads fills up, and then the process
        writing to it blocks forever. That would take the tunnel down quietly.
        """
        for line in self._process.stderr:
            if self.url is None:
                found = URL_PATTERN.search(line)
                if found:
                    self.url = found.group(0)
                    self._ready.set()
        # stderr closed: cloudflared exited.
        self._ready.set()
        if self.url is not None:
            self.url = None
            self.error = "The tunnel closed. Restart the bot to open a new one."

    def stop(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
        self.url = None


def start_if_enabled(remote, port, setting, account_exists):
    """Bring a tunnel up when asked to, and tell the panel where it is.

    Returns the Tunnel, or None when one was not wanted or could not start.
    Every refusal is explained through remote.set_public_url so /panel can
    repeat it, rather than being a line in a console nobody is looking at.
    """
    if str(setting or "off").strip().lower() != "cloudflare":
        return None
    if not account_exists:
        remote.set_public_url(None, "The tunnel is off until the panel has a "
                                    "login. Open it on this machine first and "
                                    "create one, then restart.")
        print("Remote access: not starting a tunnel, the panel has no account yet.")
        return None

    tunnel = Tunnel(port)
    if not tunnel.start():
        remote.set_public_url(None, tunnel.error)
        print(f"Remote access: {tunnel.error}")
        return None

    remote.set_public_url(tunnel.url, None)
    print(f"Remote access: the panel is also at {tunnel.url}")
    return tunnel
