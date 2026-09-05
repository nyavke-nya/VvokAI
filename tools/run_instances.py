"""Run several Brawl Stars accounts at once, one emulator window each.

Reads cfg/instances.toml (see cfg/instances.example.toml) and launches one full
VvokAI panel per account as its own process. Nothing is shared or time-sliced:
every instance is a separate interpreter with its own config tree, its own
pinned emulator and its own panel port, so each account runs at full speed.

    venv\\Scripts\\python.exe tools\\run_instances.py

Ctrl+C stops every instance. Each account's settings live in
instances/<name>/cfg and are edited from that account's own panel - this only
seeds them from the shared cfg/ the first time, so an existing account is never
overwritten.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - older interpreters
    # Fall back to `toml`, which IS in requirements.txt. The obvious choice here
    # is tomli, but it is not a dependency of this project, so on 3.10 this
    # script died at import with ModuleNotFoundError before doing anything.
    import toml

    class tomllib:  # noqa: N801 - stands in for the stdlib module's API
        @staticmethod
        def load(handle):
            return toml.loads(handle.read().decode("utf-8"))

ROOT = Path(__file__).resolve().parent.parent

# Not copied when seeding a new account: match history is per-account and must
# start empty, and the *.example templates only belong in the shared cfg/.
_SEED_SKIP_NAMES = {"match_history.csv", "instances.toml", "instances.example.toml"}


def _seed_config(cfg_dir: Path) -> None:
    """Give a new account its own copy of the shared cfg/, once."""
    if cfg_dir.exists():
        return
    base = ROOT / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for item in base.iterdir():
        if item.name in _SEED_SKIP_NAMES or item.name.endswith(".example.toml"):
            continue
        target = cfg_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    print(f"  seeded {cfg_dir.relative_to(ROOT).as_posix()} from cfg/")


def _load_instances():
    path = ROOT / "cfg" / "instances.toml"
    if not path.exists():
        print("cfg/instances.toml not found. Copy cfg/instances.example.toml to "
              "cfg/instances.toml and edit it first.")
        sys.exit(1)
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    instances = data.get("instance") or []
    if not instances:
        print("cfg/instances.toml has no [[instance]] entries.")
        sys.exit(1)
    return instances


def _launch(instance) -> subprocess.Popen:
    name = str(instance["name"])
    serial = str(instance["adb_serial"])
    cfg_dir = ROOT / "instances" / name / "cfg"
    _seed_config(cfg_dir)

    env = dict(os.environ)
    env["VVOK_CFG_DIR"] = cfg_dir.relative_to(ROOT).as_posix()
    env["VVOK_ADB_SERIAL"] = serial
    env["VVOK_NO_BROWSER"] = "1"
    if instance.get("port"):
        env["VVOK_WEB_PORT"] = str(instance["port"])

    where = f"http://127.0.0.1:{instance['port']}" if instance.get("port") else "(auto port)"
    print(f"[{name}] starting on {serial} -> panel {where}")
    return subprocess.Popen([sys.executable, str(ROOT / "main.py")],
                            cwd=str(ROOT), env=env)


def main() -> int:
    instances = _load_instances()
    procs = []
    try:
        for instance in instances:
            procs.append((instance["name"], _launch(instance)))
            # A small stagger so the shared adb server settles between the first
            # connections rather than a dozen arriving in the same millisecond.
            time.sleep(2.0)

        print(f"\n{len(procs)} instance(s) running. Open each panel and press "
              f"Start when ready. Ctrl+C here stops them all.\n")

        while True:
            for name, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"[{name}] exited with code {code}.")
            if all(proc.poll() is not None for _, proc in procs):
                print("All instances have exited.")
                return 0
            time.sleep(2.0)
    except KeyboardInterrupt:
        print("\nStopping all instances...")
    finally:
        for name, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 10
        for name, proc in procs:
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
