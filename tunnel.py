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

import atexit
import os
import pathlib
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

INSTALL_HINT = ("cloudflared is not installed. Run start_pyla.bat and answer "
                "yes when it offers to set up remote access, or install it "
                "yourself: winget install --id Cloudflare.cloudflared")


# Where the installer puts it when winget is unavailable or refused. Checked
# as well as PATH, because a copy downloaded straight into the project is
# never on PATH and would otherwise look like "not installed".
LOCAL_COPY = pathlib.Path(__file__).resolve().parent / "tools" / "cloudflared.exe"

# The pid of the cloudflared we started last, so the next run can clear it up.
#
# Every restart used to leave the previous one running. They pile up, each
# holding a tunnel to a panel that is no longer there, and the address from an
# earlier /panel message answers with Cloudflare error 1033 - which reads like
# the tunnel is broken rather than like it belongs to a bot that has since
# been restarted.
#
# A file rather than only stopping it on the way out, because the way out is
# often a closed console window, and nothing runs then.
PID_FILE = pathlib.Path(__file__).resolve().parent / "tools" / ".cloudflared.pid"


def executable():
    """The cloudflared to run, or None."""
    found = shutil.which("cloudflared")
    if found:
        return found
    return str(LOCAL_COPY) if LOCAL_COPY.exists() else None


def is_available():
    return executable() is not None


def _remember(pid):
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass


def _forget():
    try:
        PID_FILE.unlink()
    except OSError:
        pass


def _is_cloudflared(pid):
    """Whether that pid is really our program, before anything gets killed.

    Pids are reused, so after a reboot the number in the file could belong to
    anything at all. psutil would answer this in one line and is not a
    dependency of this project; tasklist ships with Windows.
    """
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "cloudflared" in result.stdout.lower()


def stop_previous():
    """Close the tunnel this bot started last time, if it is still up.

    Only that one pid, never every cloudflared on the machine: somebody may be
    running their own tunnel for something else, and killing it because it
    shares a name would be its own bug report.
    """
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False

    if not _is_cloudflared(pid):
        _forget()
        return False

    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print(f"Remote access: closed the tunnel left over from the last run (pid {pid}).")
    except (OSError, subprocess.SubprocessError):
        pass
    _forget()
    return True


class Tunnel:
    """Runs cloudflared beside the panel and remembers the address it got."""

    def __init__(self, port):
        self.port = port
        self.url = None
        self.error = None
        self._process = None
        self._ready = threading.Event()

    def start(self):
        program = executable()
        if program is None:
            self.error = INSTALL_HINT
            return False
        try:
            self._process = subprocess.Popen(
                [program, "tunnel", "--no-autoupdate",
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

        _remember(self._process.pid)
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
            try:
                self._process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - it is going away either way
                pass
        _forget()
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

    # Before starting a new one, so restarts do not leave a trail of tunnels
    # pointing at panels that are no longer running.
    stop_previous()

    tunnel = Tunnel(port)
    if not tunnel.start():
        remote.set_public_url(None, tunnel.error)
        print(f"Remote access: {tunnel.error}")
        return None

    remote.set_public_url(tunnel.url, None)
    print(f"Remote access: the panel is also at {tunnel.url}")
    # A quick tunnel gets a new name every time, so a link sent yesterday is
    # dead today. Saying so beats somebody debugging a Cloudflare error page.
    print("Remote access: this address changes on every restart - ask /panel "
          "for the current one.")
    atexit.register(tunnel.stop)
    return tunnel
