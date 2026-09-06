"""Run several Brawl Stars accounts at once, managed from the panel.

Each account is a full VvokAI process of its own - one MuMu window each - not a
thread inside this one. That is deliberate: the vision loop is CPU-heavy and the
GIL would make threads take turns, so separate processes are what lets every
account run at full speed at the same time ("resources are not shared").

An instance carries three things:

  * its own config tree, instances/<name>/cfg, seeded once from the shared cfg/
    so an account's token, tag, playstyle and history never touch another's;
  * its own emulator, pinned by ADB serial so the instances never fight over
    the connection or restart the shared adb server under each other;
  * its own panel on a fixed port, opened from the Accounts page.

Only the root process supervises. A child (one started with VVOK_CFG_DIR set) is
an account panel, not a controller, so it never offers to spawn more - that is
what stops a fork bomb of panels spawning panels.
"""

from __future__ import annotations

import io
import logging
import os
import re
import signal
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from adbutils import adb

logger = logging.getLogger(__name__)


def _listening_pids() -> dict:
    """{port: pid} for every locally-listening TCP port. One netstat call.

    Used so an account can be stopped by the panel it is serving, which works
    even for a process this supervisor did not start or has lost the handle to -
    the case where "Stop" left the bot happily playing on."""
    result = {}
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    local = parts[1]
                    if ":" in local:
                        try:
                            result[int(local.rsplit(":", 1)[-1])] = int(parts[4])
                        except ValueError:
                            pass
        else:
            out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9 and ":" in parts[8]:
                    try:
                        result[int(parts[8].rsplit(":", 1)[-1])] = int(parts[1])
                    except ValueError:
                        pass
    except Exception:
        pass
    return result


def _adb_screenshot(serial: str):
    """A small JPEG straight off the device via ADB, or None (reason logged).

    Reliable only when the device is idle. While a bot is streaming that same
    emulator over scrcpy, a second screencap here can fail - which is exactly
    why a RUNNING account is previewed from its own process instead (below)."""
    if not serial:
        return None
    try:
        adb.connect(serial)
    except Exception:
        pass
    try:
        image = adb.device(serial=serial).screenshot()
        image.thumbnail((360, 360))
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=60)
        return buffer.getvalue()
    except Exception as exc:
        logger.info("preview: ADB screencap failed for %s: %s", serial, exc)
        return None


def _encode_jpeg(frame, max_size: int = 360, quality: int = 60):
    """A numpy RGB frame (the bot's live scrcpy frame) to JPEG bytes."""
    try:
        import cv2
        img = frame
        height, width = img.shape[:2]
        if max(height, width) > max_size:
            scale = max_size / float(max(height, width))
            img = cv2.resize(img, (int(width * scale), int(height * scale)))
        if img.ndim == 3 and img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        ok, buffer = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        return buffer.tobytes() if ok else None
    except Exception:
        return None


def own_device_screenshot():
    """This process's own device, for the account panel's /api/preview.jpg when
    no bot frame is available yet (process up, bot not started - device idle)."""
    return _adb_screenshot(os.environ.get("VVOK_ADB_SERIAL", ""))


def _process_name(pid: int) -> str:
    """The executable name behind a PID, lowercased, or "" if it cannot be read."""
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            if out.startswith('"'):
                return out.split('","')[0].strip('"').lower()
        else:
            return subprocess.run(["ps", "-p", str(int(pid)), "-o", "comm="],
                                  capture_output=True, text=True,
                                  timeout=5).stdout.strip().lower()
    except Exception:
        pass
    return ""


# Only these are ever killed by port. An account panel is this interpreter or
# the packaged build; anything else listening on that port is somebody else's
# program and must not be touched - the port could have been recycled, and
# "taskkill whatever owns it" is how a stop button ends up killing svchost.
_OUR_PROCESS_NAMES = ("python.exe", "pythonw.exe", "vvokai.exe", "python", "python3")


def _is_our_process(pid: int) -> bool:
    name = _process_name(pid)
    return bool(name) and name in _OUR_PROCESS_NAMES


def _kill_tree(pid: int) -> None:
    """Kill a process and its children. When the bot dies its scrcpy socket
    closes and the emulator releases any held touch, so the character stops."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                os.kill(pid, signal.SIGKILL)
    except Exception:
        pass

from utils import (PROJECT_ROOT, clean_player_tag, load_toml_as_dict, resolve_project_path,
                   save_dict_as_toml, invalidate_toml_cache)

INSTANCES_FILE = "cfg/instances.toml"

# Ports the common emulators expose ADB on, so "Detect" can find windows the
# user never has to look up. MuMu Player numbers its windows 16384, 16416, and
# up in steps of 32; the rest are the usual LDPlayer / Nox / MemU / generic
# defaults. Connecting to one that is not there fails fast and harmlessly.
_SCAN_PORTS = sorted(set(
    [16384 + 32 * i for i in range(16)]          # MuMu windows
    + list(range(5554, 5586))                    # LDPlayer / generic adb
    + [7555, 7556, 7565, 5137, 5635]             # MuMu 12 / misc
    + [62001, 62025, 62026]                      # Nox
    + [21503, 21513, 21523]                      # MemU
))

# Panel ports for auto-added accounts start here and count up past taken ones.
_AUTO_PORT_BASE = 5001

# Not copied when seeding a fresh account: history is per-account and starts
# empty, the templates only belong in the shared cfg/, and the instance list is
# the supervisor's, never a child's.
_SEED_SKIP = {"match_history.csv", "instances.toml", "instances.example.toml"}

# Blanked when a new account's config is seeded from the shared cfg/. This says
# WHOSE Brawl Stars profile the account is, and copying it meant every account
# started life pointing at the first one's: the startup resync then pulled that
# player's trophies over whatever had been typed in (2014 instead of the 1057
# that was entered) and rewrote the queue with them on every restart. Left
# empty, the API sync stays out of the way until the account is given its own
# tag in its own panel.
#
# The developer-portal credentials are NOT here, and used to be. They belong to
# the owner, not to the account - one key answers questions about any tag - so
# they are shared rather than copied, and utils._SHARED_CFG_KEYS is what makes
# every account read and write the one copy in cfg/. Blanking them here made
# each new account demand its own key and its own login, which is not what
# anybody wanted and is not how the portal works.
_IDENTITY_BLANKS = {
    "general_config.toml": ("player_tag",),
}


def _blank_identity(cfg_dir):
    """Empty the per-account identity fields in a freshly seeded config.

    Line-based on purpose: a TOML round-trip would throw away the comments that
    explain every setting in these files.
    """
    for filename, keys in _IDENTITY_BLANKS.items():
        target = cfg_dir / filename
        if not target.exists():
            continue
        try:
            text = io.open(target, encoding="utf-8", newline="").read()
            for key in keys:
                text = re.sub(r"(?m)^(\s*%s\s*=\s*).*$" % re.escape(key),
                              lambda m: m.group(1) + '""', text)
            io.open(target, "w", encoding="utf-8", newline="").write(text)
        except OSError as exc:
            logger.info("could not blank identity in %s: %s", target, exc)


_TAG_LINE = re.compile(r"(?m)^\s*player_tag\s*=\s*[\"'](.*?)[\"']\s*$")


def _tag_in(path):
    """The player tag written in a config file, stripped of its # and spaces."""
    try:
        text = io.open(path, encoding="utf-8", newline="").read()
    except OSError:
        return ""
    found = _TAG_LINE.search(text)
    return clean_player_tag(found.group(1)) if found else ""


def is_supervisor() -> bool:
    """True in the root process, False inside a spawned account panel."""
    return not os.environ.get("VVOK_CFG_DIR")


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in str(name).strip() if c.isalnum() or c in ("-", "_"))
    if not cleaned:
        raise ValueError("An account name must have letters, digits, - or _.")
    return cleaned


class InstanceManager:
    def __init__(self):
        self._lock = threading.RLock()
        # name -> Popen for the accounts this supervisor has started.
        self._procs: dict[str, subprocess.Popen] = {}
        # name -> when it was started, for the "Configure" grace period.
        self._started_at: dict[str, float] = {}
        if is_supervisor():
            # Once, at startup: accounts made before the tag was blanked
            # at seeding time are all still carrying the owner's own tag.
            self._unshare_inherited_tags()

    # ---- persistence ------------------------------------------------------

    def _read(self) -> list[dict]:
        data = load_toml_as_dict(INSTANCES_FILE, cache=False) or {}
        entries = data.get("instance") or []
        return [dict(entry) for entry in entries if entry.get("name")]

    def _write(self, entries: list[dict]) -> None:
        save_dict_as_toml({"instance": entries}, INSTANCES_FILE)
        invalidate_toml_cache(INSTANCES_FILE)

    # ---- config seeding ---------------------------------------------------

    def _cfg_dir(self, name: str):
        return resolve_project_path("instances", name, "cfg")

    def _seed_config(self, name: str) -> None:
        cfg_dir = self._cfg_dir(name)
        if cfg_dir.exists():
            return
        base = resolve_project_path("cfg")
        cfg_dir.mkdir(parents=True, exist_ok=True)
        for item in base.iterdir():
            if item.name in _SEED_SKIP or item.name.endswith(".example.toml"):
                continue
            target = cfg_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
        # A new account is nobody yet: it must not inherit the identity of the
        # account this was seeded from, or the API sync will overwrite its queue
        # with that player's trophies.
        _blank_identity(cfg_dir)

    def _unshare_inherited_tags(self) -> None:
        """Clear a player tag an account only has because it was copied.

        Accounts made before the tag was blanked at seeding time all carry the
        owner's own tag, and the panel could not clear it either - so every one
        of them resyncs against the first account's profile and reports its
        trophies. Two accounts cannot be the same Brawl Stars player, so a tag
        identical to the shared one can only have been copied, and clearing it
        loses nothing that was ever typed in on purpose.

        Only that exact case. A tag somebody actually set for an account is
        different from the shared one and is left alone.
        """
        shared = _tag_in(resolve_project_path("cfg", "general_config.toml"))
        if not shared:
            return
        root = resolve_project_path("instances")
        if not root.is_dir():
            return
        for cfg_dir in sorted(root.glob("*/cfg")):
            if _tag_in(cfg_dir / "general_config.toml") != shared:
                continue
            _blank_identity(cfg_dir)
            logger.info("cleared the inherited player tag in %s", cfg_dir)

    # ---- lifecycle --------------------------------------------------------

    def _port_of_name(self, name: str):
        entry = next((e for e in self._read() if e.get("name") == name), None)
        return int(entry["port"]) if entry and entry.get("port") else None

    def _running(self, name: str, listening=None) -> bool:
        # A tracked process that is still alive, OR anything serving this
        # account's panel port - the latter catches a process this supervisor
        # did not start or lost track of, so status and Stop stay honest.
        proc = self._procs.get(name)
        if proc is not None and proc.poll() is None:
            return True
        port = self._port_of_name(name)
        if port is None:
            return False
        if listening is None:
            listening = _listening_pids()
        return port in listening

    def start(self, name: str) -> dict:
        if not is_supervisor():
            raise ValueError("Accounts can only be started from the main panel.")
        name = _safe_name(name)
        with self._lock:
            entry = next((e for e in self._read() if e["name"] == name), None)
            if entry is None:
                raise FileNotFoundError(f"Account '{name}' is not configured.")
            if self._running(name):
                return {"ok": True, "message": f"{name} is already running."}

            self._seed_config(name)
            env = dict(os.environ)
            env["VVOK_CFG_DIR"] = self._cfg_dir(name).relative_to(PROJECT_ROOT).as_posix()
            env["VVOK_ADB_SERIAL"] = str(entry["adb_serial"])
            env["VVOK_NO_BROWSER"] = "1"
            if entry.get("port"):
                env["VVOK_WEB_PORT"] = str(entry["port"])

            # Below-normal priority so a stack of accounts leaves the machine
            # responsive rather than pinning every core (the "very laggy"
            # report). The vision loop is the heavy part; capping Max IPS in an
            # account's own settings shares the CPU further.
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)

            proc = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "main.py")],
                                    cwd=str(PROJECT_ROOT), env=env,
                                    creationflags=creationflags)
            self._procs[name] = proc
            # When it started, so the panel can hold back "Configure" until the
            # account's own web server is actually listening. Offering it
            # immediately meant a click landed on a port nothing was serving yet
            # and the embedded page showed "127.0.0.1 refused to connect".
            self._started_at[name] = time.time()
            return {"ok": True, "message": f"{name} starting."}

    def stop(self, name: str) -> dict:
        name = _safe_name(name)
        with self._lock:
            proc = self._procs.pop(name, None)
            port = self._port_of_name(name)

        # First the handle we have, if any.
        if proc is not None and proc.poll() is None:
            _kill_tree(proc.pid)

        # Then whatever is still serving this account's panel port - this is
        # what makes Stop actually stop a bot the supervisor is not tracking
        # (started in a previous session, or whose handle was lost). Without it
        # "Stop all" left an account merrily playing on.
        if port is not None:
            pid = _listening_pids().get(port)
            if pid and (proc is None or pid != proc.pid):
                # Verified before killing, not killed blindly: the point is to
                # stop an account this supervisor is not tracking (started in an
                # earlier session, or whose handle was lost), which is the case
                # where "Stop" used to leave a bot happily playing on. Refusing
                # outright brings that bug back; killing whatever owns the port
                # could take out an unrelated program. So: kill it only if it
                # looks like one of ours.
                if _is_our_process(pid):
                    _kill_tree(pid)
                else:
                    return {"ok": False,
                            "message": "That port is held by another program, "
                                       "so it was left alone. Close it yourself."}
        return {"ok": True, "message": f"{name} stopped."}

    def stop_all(self) -> None:
        for name in list(self._procs):
            try:
                self.stop(name)
            except Exception:
                pass

    # ---- editing ----------------------------------------------------------

    def add(self, name: str, adb_serial: str, port=None) -> dict:
        name = _safe_name(name)
        serial = str(adb_serial).strip()
        if not serial:
            raise ValueError("An ADB serial is required, e.g. 127.0.0.1:16384.")
        with self._lock:
            entries = self._read()
            if any(e["name"] == name for e in entries):
                raise ValueError(f"An account named '{name}' already exists.")
            entry = {"name": name, "adb_serial": serial}
            if port:
                entry["port"] = int(port)
            entries.append(entry)
            self._write(entries)
        return {"ok": True}

    def update(self, name: str, adb_serial=None, port=None) -> dict:
        name = _safe_name(name)
        with self._lock:
            entries = self._read()
            entry = next((e for e in entries if e["name"] == name), None)
            if entry is None:
                raise FileNotFoundError(f"Account '{name}' is not configured.")
            if adb_serial is not None:
                serial = str(adb_serial).strip()
                if not serial:
                    raise ValueError("An ADB serial is required.")
                entry["adb_serial"] = serial
            if port is not None:
                entry["port"] = int(port) if port else None
                if entry["port"] is None:
                    entry.pop("port", None)
            self._write(entries)
        return {"ok": True}

    def remove(self, name: str) -> dict:
        name = _safe_name(name)
        self.stop(name)
        with self._lock:
            entries = [e for e in self._read() if e["name"] != name]
            self._write(entries)
        return {"ok": True}

    # ---- discovery --------------------------------------------------------

    @staticmethod
    def _port_of(serial: str):
        if ":" in serial:
            try:
                return int(serial.rsplit(":", 1)[-1])
            except ValueError:
                return None
        return None

    @classmethod
    def _serial_rank(cls, serial: str):
        """Lower is better when several ADB endpoints are the SAME emulator.

        Prefer a MuMu window port (16384, 16416, ... every 32), then any other
        host:port, and last an emulator-NNNN console serial - those are flaky to
        drive. Ties break on the lower port number."""
        port = cls._port_of(serial)
        if port is None:
            return (2, 0)
        if port >= 16384 and (port - 16384) % 32 == 0:
            return (0, port)
        return (1, port)

    @staticmethod
    def _fingerprint(device) -> str:
        """A value that is the SAME across every ADB endpoint of one emulator
        and different between emulators, so mirror ports collapse to one entry.

        android_id is per-instance and identical on all of a window's ports.
        Falls back to the serial (no dedupe) if it cannot be read."""
        try:
            value = device.shell("settings get secure android_id", timeout=3).strip()
            if value and value.lower() != "null":
                return "aid:" + value
        except Exception:
            pass
        return "serial:" + device.serial

    def _scan_serials(self) -> list[str]:
        """Find running emulators, one serial per physical window.

        Connect attempts run in parallel because a dead port takes a moment to
        fail. adb is shared, so this only ADDS to it, never restarts it (which
        would drop running accounts). Each emulator answers on several ports -
        its own plus legacy mirrors like 5555 and emulator-5554 - so devices are
        grouped by a per-instance fingerprint and only the best serial per group
        is kept. Without that, two MuMu windows showed up as six accounts."""
        def _try(port):
            try:
                adb.connect(f"127.0.0.1:{port}")
            except Exception:
                pass

        with ThreadPoolExecutor(max_workers=32) as pool:
            pool.map(_try, _SCAN_PORTS)

        groups: dict[str, list[str]] = {}
        for device in adb.device_list():
            try:
                state = device.get_state() if hasattr(device, "get_state") else "device"
            except Exception:
                continue
            if state != "device":
                continue
            groups.setdefault(self._fingerprint(device), []).append(device.serial)

        chosen = [min(serials, key=self._serial_rank) for serials in groups.values()]
        return sorted(set(chosen))

    def scan_and_add(self) -> dict:
        """Find running emulators and add any new one to the list for the user.

        People do not know their emulator's ADB serial, and should not have to.
        This finds the windows that are actually running and adds each new one
        with a name and a panel port picked automatically - all that is left to
        do is press Start."""
        if not is_supervisor():
            raise ValueError("Accounts can only be managed from the main panel.")
        serials = self._scan_serials()
        with self._lock:
            entries = self._read()
            have = {e.get("adb_serial") for e in entries}
            names = {e.get("name") for e in entries}
            ports = {int(e["port"]) for e in entries if e.get("port")}
            added = []
            next_port = _AUTO_PORT_BASE
            for serial in serials:
                if serial in have:
                    continue
                suffix = serial.rsplit(":", 1)[-1] if ":" in serial else serial
                base = _safe_name(f"acc-{suffix}")
                name, n = base, 2
                while name in names:
                    name, n = f"{base}-{n}", n + 1
                while next_port in ports:
                    next_port += 1
                entries.append({"name": name, "adb_serial": serial, "port": next_port})
                names.add(name)
                ports.add(next_port)
                added.append(name)
                next_port += 1
            if added:
                self._write(entries)
        return {"ok": True, "found": len(serials), "added": added}

    # ---- preview ----------------------------------------------------------

    def screenshot(self, name: str):
        """A small JPEG of what the emulator is showing, or None.

        This is how you tell accounts apart - a name means nothing, but a glance
        at the actual lobby says which window it is.

        A running account is previewed from ITS OWN process, which already has a
        live scrcpy frame - so the preview never fights the running bot for a
        second screencap on the same device (that contention was the 404s while
        accounts were playing). A stopped account is captured directly, since
        its device is idle then."""
        name = _safe_name(name)
        entry = next((e for e in self._read() if e.get("name") == name), None)
        if entry is None:
            raise FileNotFoundError(f"Account '{name}' is not configured.")

        port = entry.get("port")
        if port and self._running(name):
            try:
                import requests
                reply = requests.get(f"http://127.0.0.1:{int(port)}/api/preview.jpg",
                                     timeout=4)
                if reply.ok and reply.content:
                    return reply.content
            except Exception as exc:
                logger.info("preview: account %s panel gave no frame (%s)", name, exc)

        return _adb_screenshot(str(entry.get("adb_serial", "")))

    # ---- status -----------------------------------------------------------

    def list_payload(self) -> dict:
        listening = _listening_pids()
        with self._lock:
            items = []
            for entry in self._read():
                name = entry["name"]
                port = entry.get("port")
                running = self._running(name, listening)
                started = self._started_at.get(name)
                items.append({
                    "name": name,
                    "adb_serial": entry.get("adb_serial", ""),
                    "port": port,
                    "running": running,
                    # Seconds since this supervisor started it, so the panel can
                    # wait for the account's web server before offering
                    # "Configure". None means we did not start it this session,
                    # in which case it has been up long enough already.
                    "uptime": (time.time() - started) if (running and started) else None,
                    "url": f"http://127.0.0.1:{port}" if port else None,
                })
        return {"is_supervisor": is_supervisor(), "items": items}
