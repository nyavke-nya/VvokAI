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

import os
import shutil
import subprocess
import sys
import threading

from utils import (PROJECT_ROOT, load_toml_as_dict, resolve_project_path,
                   save_dict_as_toml, invalidate_toml_cache)

INSTANCES_FILE = "cfg/instances.toml"

# Not copied when seeding a fresh account: history is per-account and starts
# empty, the templates only belong in the shared cfg/, and the instance list is
# the supervisor's, never a child's.
_SEED_SKIP = {"match_history.csv", "instances.toml", "instances.example.toml"}


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

    # ---- lifecycle --------------------------------------------------------

    def _running(self, name: str) -> bool:
        proc = self._procs.get(name)
        return proc is not None and proc.poll() is None

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

            proc = subprocess.Popen([sys.executable, str(PROJECT_ROOT / "main.py")],
                                    cwd=str(PROJECT_ROOT), env=env)
            self._procs[name] = proc
            return {"ok": True, "message": f"{name} starting."}

    def stop(self, name: str) -> dict:
        name = _safe_name(name)
        with self._lock:
            proc = self._procs.get(name)
            if proc is None or proc.poll() is not None:
                self._procs.pop(name, None)
                return {"ok": True, "message": f"{name} is not running."}
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        with self._lock:
            self._procs.pop(name, None)
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

    # ---- status -----------------------------------------------------------

    def list_payload(self) -> dict:
        with self._lock:
            items = []
            for entry in self._read():
                name = entry["name"]
                port = entry.get("port")
                items.append({
                    "name": name,
                    "adb_serial": entry.get("adb_serial", ""),
                    "port": port,
                    "running": self._running(name),
                    "url": f"http://127.0.0.1:{port}" if port else None,
                })
        return {"is_supervisor": is_supervisor(), "items": items}
