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
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

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


# cloudflared prefers QUIC, which is UDP on port 7844. Plenty of home routers,
# work networks and ISPs drop that, and when they do cloudflared still starts,
# still prints an address, and simply never connects - so the address answers
# with Cloudflare error 1033 and everything local looks fine. http2 goes over
# TCP 443 instead, which gets through nearly anywhere.
PROTOCOLS = ("quic", "http2")

# How long to give it to register with Cloudflare's edge after it prints the
# address. Connections normally come up in two or three seconds.
READY_SECONDS = 20


# cloudflared honours HTTP_PROXY and HTTPS_PROXY, and its edge connections
# are raw TCP and UDP on port 7844 - which an HTTP proxy generally will not
# carry and a UDP one cannot. On a machine with a VPN client exporting those
# variables (sing-box, v2ray and friends all do) the tunnel then reports
# "HTTP/2 connection is blocked or unreachable" and quietly never connects,
# while everything else on the machine works fine.
#
# So the edge hosts are added to NO_PROXY for the child rather than the proxy
# being cleared: somebody behind a restrictive network may genuinely need it
# for everything else, and this only exempts the two names the tunnel dials.
EDGE_HOSTS = "argotunnel.com,.argotunnel.com,cloudflare.com,.cloudflare.com"


def _environment():
    environment = dict(os.environ)
    existing = environment.get("NO_PROXY") or environment.get("no_proxy") or ""
    merged = ",".join(part for part in (existing, EDGE_HOSTS) if part)
    environment["NO_PROXY"] = merged
    environment["no_proxy"] = merged
    return environment


def _free_port():
    """A port for cloudflared's metrics server, so /ready can be polled.

    Left to itself it picks a random one and only mentions it in a log line.
    Asking for a known one is the difference between being able to check
    whether the tunnel works and having to guess.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Tunnel:
    """Runs cloudflared beside the panel and remembers the address it got."""

    def __init__(self, port):
        self.port = port
        self.url = None
        self.error = None
        self.protocol = None
        # The lines cloudflared printed about why it could not get out.
        self.diagnosis = []
        self._process = None
        self._metrics_port = None
        self._ready = threading.Event()

    def start(self):
        program = executable()
        if program is None:
            self.error = INSTALL_HINT
            return False

        for protocol in PROTOCOLS:
            if self._attempt(program, protocol):
                self.protocol = protocol
                return True
            self._shut_down_process()
            if protocol != PROTOCOLS[-1]:
                print(f"Remote access: {protocol} could not reach Cloudflare, "
                      f"trying {PROTOCOLS[PROTOCOLS.index(protocol) + 1]} instead.")
        return False

    def _attempt(self, program, protocol):
        self.url = None
        self._ready = threading.Event()
        self._metrics_port = _free_port()
        try:
            self._process = subprocess.Popen(
                [program, "tunnel", "--no-autoupdate",
                 "--protocol", protocol,
                 "--metrics", f"127.0.0.1:{self._metrics_port}",
                 "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # No console window popping up on Windows every launch.
                env=_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.error = f"Could not start cloudflared: {exc}"
            return False

        _remember(self._process.pid)
        threading.Thread(target=self._watch, daemon=True,
                         name="pyla-tunnel-watch").start()

        if not self._ready.wait(STARTUP_SECONDS) or self.url is None:
            self.error = ("cloudflared did not report an address within "
                          f"{STARTUP_SECONDS}s. The panel is still reachable on "
                          "your own network.")
            return False

        # The address appears before any connection to Cloudflare exists, so
        # this is the check that matters. Skipping it is how a tunnel that
        # never connected got announced as working.
        if not self._wait_until_connected():
            self.error = ("cloudflared started but never connected to "
                          "Cloudflare, so the address would answer with error "
                          "1033. It needs outbound port 7844, which a VPN or a "
                          "proxy on this machine is the usual thing to be "
                          "eating.")
            for line in self.diagnosis[:4]:
                self.error += chr(10) + "  " + line
            return False
        return True

    def _wait_until_connected(self):
        """Poll cloudflared's own /ready until it has a live connection."""
        deadline = time.time() + READY_SECONDS
        while time.time() < deadline:
            if self._process.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self._metrics_port}/ready", timeout=3) as answer:
                    if json.loads(answer.read().decode("utf-8")).get("readyConnections", 0) > 0:
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(1)
        return False

    def _watch(self):
        """Read cloudflared's output for the hostname, then keep draining it.

        Draining matters: a pipe nobody reads fills up, and then the process
        writing to it blocks forever. That would take the tunnel down quietly.
        """
        process = self._process
        for line in process.stderr:
            if self.url is None:
                found = URL_PATTERN.search(line)
                if found:
                    self.url = found.group(0)
                    self._ready.set()
            # cloudflared runs its own connectivity precheck and says exactly
            # which port is not getting out. Throwing that away and reporting
            # "it did not connect" would be discarding the answer.
            if "ERROR:" in line or "Connectivity" in line:
                cleaned = line.strip().strip("|").strip()
                if cleaned and cleaned not in self.diagnosis:
                    self.diagnosis.append(cleaned)
        # stderr closed: cloudflared exited.
        self._ready.set()

    def _shut_down_process(self):
        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - it is going away either way
                pass
        _forget()

    def stop(self):
        self._shut_down_process()
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
    print(f"Remote access: the panel is also at {tunnel.url} (over {tunnel.protocol})")
    # A quick tunnel gets a new name every time, so a link sent yesterday is
    # dead today. Saying so beats somebody debugging a Cloudflare error page.
    print("Remote access: this address changes on every restart - ask /panel "
          "for the current one.")
    atexit.register(tunnel.stop)
    return tunnel
