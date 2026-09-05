"""VvokAI.exe - the one file people download, and everything before the window.

Built into a single executable by build_exe.bat. It is deliberately small,
around fifteen megabytes, and contains no part of the bot: torch, opencv and
the detection models are three gigabytes between them, and a three gigabyte
executable that unpacks itself into the temp folder on every launch is both
slow and the exact shape antivirus software treats as suspicious.

Instead this is the thing start_vvok.bat used to be, compiled:

  1. Check whether a newer VvokAI.exe has been published, and replace itself
     with it if so.
  2. Make sure the project files next to it are present and current.
  3. Make sure there is a Python and an environment with the dependencies in
     it, which tools/installer.py already knows how to do.
  4. Open the application window.

Everything it fetches comes from the project's own GitHub releases and the
python.org installer, over HTTPS, and every step says what it is doing on the
console - somebody watching a fresh download run for the first time can see
exactly what it touches. That is worth more against "is this a virus" than any
amount of reassurance in a README.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "nyavke-nya/VvokAI"
BRANCH = "main"

# For asking GitHub a question. Short: these are small answers, and a slow one
# means something is wrong rather than something is large.
TIMEOUT = 30

# For pulling down the project, Python or a new copy of the exe. urlopen's
# timeout is per read rather than for the whole transfer, but on a slow
# connection a single read can stall for a while, and 30 seconds was short
# enough to fail on the project archive here.
DOWNLOAD_TIMEOUT = 300
USER_AGENT = "VvokAI-launcher"

# What the built executable is called, on disk and in a release.
EXE_NAME = "VvokAI.exe"

# Written beside the exe so the next run knows what it already has.
VERSION_FILE = ".vvok_version"

# The Pythons the dependencies publish Windows builds for. On anything newer
# pip falls back to compiling them and stops at a missing C++ compiler, which
# sounds like a toolchain problem and is really a Python that is too new.
PYTHON_RANGE = ((3, 10), (3, 12))
PYTHON_INSTALLER = ("https://www.python.org/ftp/python/3.11.9/"
                    "python-3.11.9-amd64.exe")


def say(message=""):
    print(message, flush=True)


def home():
    """The folder the exe lives in, which is where the project goes.

    sys.executable is the exe when frozen and the interpreter when running
    from source, so this has to ask which it is rather than assume.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def fetch(url, binary=False):
    """A GitHub answer, or a file. Binary means a file, and files take longer."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    limit = DOWNLOAD_TIMEOUT if binary else TIMEOUT
    with urllib.request.urlopen(request, timeout=limit) as response:
        payload = response.read()
    return payload if binary else json.loads(payload.decode("utf-8"))


# ---------------------------------------------------------------------------
#  Step 1: replace itself, if a newer exe has been published
# ---------------------------------------------------------------------------
#
# A running executable on Windows cannot be deleted or written to, so it
# cannot update itself in place. What works is: write the new one beside the
# old one, start it, and exit. The new copy renames itself over the old name
# on its next start, once nothing is holding the file open.


def pending_swap():
    """Finish an update started by the previous run, if there is one."""
    current = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False) or current.name != f"new-{EXE_NAME}":
        return False
    target = current.with_name(EXE_NAME)
    for _ in range(20):
        try:
            if target.exists():
                target.unlink()
            current.replace(target)
            say(f"Updated. Restarting {EXE_NAME}.")
            subprocess.Popen([str(target)], cwd=str(target.parent))
            return True
        except OSError:
            # The old copy is still shutting down. It only takes a moment.
            time.sleep(0.25)
    say("Could not put the update in place; carrying on with this copy.")
    return False


def auto_update_wanted(root):
    """Whether updating is allowed. Missing means yes; see tools/updater.py."""
    try:
        text = (root / "cfg" / "general_config.toml").read_text(encoding="utf-8")
    except OSError:
        return True
    # The exact key, not a prefix. "auto_update" also prefixes
    # auto_update_every_minutes, which sits ABOVE it in the shipped config - so a
    # prefix match read "60", called that true and returned before ever reaching
    # auto_update itself. Setting auto_update = false did nothing at all.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == "auto_update":
            value = value.strip().strip('"').strip("'").lower()
            return value not in ("false", "no", "off", "0")
    return True


def update_self(root):
    """Download a newer VvokAI.exe if the latest release has one."""
    if not getattr(sys, "frozen", False):
        return False  # Running from source; the exe is not what is out of date.
    if not auto_update_wanted(root):
        say("Automatic updates are off in cfg/general_config.toml.")
        return False
    try:
        release = fetch(f"https://api.github.com/repos/{REPO}/releases/latest")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        say(f"Could not check for a newer launcher ({exc}); carrying on.")
        return False

    asset = next((a for a in release.get("assets", [])
                  if a.get("name") == EXE_NAME), None)
    tag = release.get("tag_name", "")
    if not asset or not tag:
        return False

    current = Path(sys.executable).resolve()
    stamp = current.parent / VERSION_FILE
    try:
        if stamp.read_text(encoding="utf-8").strip() == f"exe:{tag}":
            return False
    except OSError:
        pass

    say(f"A newer VvokAI is available ({tag}). Downloading it.")
    try:
        payload = fetch(asset["browser_download_url"], binary=True)
    except (urllib.error.URLError, OSError) as exc:
        say(f"That download failed ({exc}); carrying on with this copy.")
        return False

    beside = current.with_name(f"new-{EXE_NAME}")
    try:
        beside.write_bytes(payload)
        stamp.write_text(f"exe:{tag}", encoding="utf-8")
    except OSError as exc:
        say(f"Could not save the update ({exc}); carrying on.")
        return False

    say("Restarting into the new version.")
    subprocess.Popen([str(beside)], cwd=str(current.parent))
    return True


# ---------------------------------------------------------------------------
#  Step 2: the project files
# ---------------------------------------------------------------------------


# Where the project goes. One fixed path rather than "next to the exe":
# people put an exe on their Desktop, and unpacking sixty files and a dozen
# folders across somebody's Desktop is not a thing to do to them - uninstalling
# would then mean picking our files out of theirs one at a time.
#
# The root of C: is writable without administrator rights on a default Windows,
# checked rather than assumed. A short path also avoids the other thing that
# bites here: some of the dependencies build paths deep enough to run into the
# 260 character limit when the project starts somewhere like
# C:/Users/Somebody/OneDrive/Documents/Downloads/VvokAI-main.
INSTALL_DIR = "C:/VvokAI"

# What the app exits with to ask for a relaunch, matching tools/updater.py and
# src/auto_update.py. One number, one meaning.
RESTART_CODE = 10


def project_present(root):
    return (root / "main.py").exists() and (root / "tools" / "installer.py").exists()


def usable_root(candidate):
    """Whether the project can actually be written there."""
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".vvok_write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def project_root(base):
    """Where the project lives.

    C:/VvokAI, unless the exe was dropped into a checkout that is already
    there - that is how somebody running from source does it, and moving their
    files out from under them would be rude. If C: cannot be written to at
    all, which happens on locked-down machines, it falls back to a folder
    beside the exe rather than refusing to start.
    """
    if project_present(base):
        return base

    fixed = Path(INSTALL_DIR)
    if project_present(fixed) or usable_root(fixed):
        return fixed

    say(f"Could not use {fixed}, so the files go next to the program instead.")
    return base / "VvokAI"


def download_project(root):
    """First run: there is an exe in an empty folder and nothing else."""
    say("Downloading VvokAI. This is a few tens of megabytes.")
    try:
        blob = fetch(f"https://github.com/{REPO}/archive/{BRANCH}.zip", binary=True)
    except (urllib.error.URLError, OSError) as exc:
        say(f"The download failed: {exc}")
        return False

    holding = Path(tempfile.mkdtemp(prefix="vvok-"))
    try:
        archive = holding / "source.zip"
        archive.write_bytes(blob)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(holding)
        roots = [p for p in holding.iterdir() if p.is_dir()]
        if len(roots) != 1:
            say("The archive did not look the way it should.")
            return False
        for item in roots[0].iterdir():
            target = root / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
    except (OSError, zipfile.BadZipFile) as exc:
        say(f"The download could not be unpacked: {exc}")
        return False
    finally:
        shutil.rmtree(holding, ignore_errors=True)

    say("Downloaded.")
    return True


# ---------------------------------------------------------------------------
#  Step 3: a Python to run it with
# ---------------------------------------------------------------------------


def python_version(candidate):
    try:
        result = subprocess.run(
            [*candidate, "-c",
             "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        major, minor = (int(part) for part in result.stdout.strip().split("."))
    except ValueError:
        return None
    return (major, minor)


def find_python():
    """The same search start_vvok.bat does, in a language that can express it.

    Preferred versions first, so a machine with several gets the one the
    dependencies are actually built for.
    """
    candidates = [["py", "-3.11"], ["py", "-3.12"], ["py", "-3.10"], ["python"]]
    for version in ("311", "312", "310"):
        for base in (os.environ.get("LocalAppData", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("SystemDrive", "C:")):
            if not base:
                continue
            candidates.append([str(Path(base) / "Programs" / "Python" / f"Python{version}" / "python.exe")])
            candidates.append([str(Path(base) / f"Python{version}" / "python.exe")])

    for candidate in candidates:
        found = python_version(candidate)
        if found and PYTHON_RANGE[0] <= found <= PYTHON_RANGE[1]:
            return candidate
    return None


def install_python():
    say("No suitable Python found. Downloading Python 3.11.9 (about 25 MB).")
    try:
        payload = fetch(PYTHON_INSTALLER, binary=True)
    except (urllib.error.URLError, OSError) as exc:
        say(f"That download failed: {exc}")
        return None

    installer = Path(tempfile.gettempdir()) / "vvok-python-3.11.9.exe"
    target = Path(os.environ.get("LocalAppData", tempfile.gettempdir()))
    target = target / "Programs" / "Python" / "Python311"
    try:
        installer.write_bytes(payload)
        say(f"Installing to {target}. A permission prompt may appear - accept it.")
        subprocess.run([str(installer), "/quiet", "InstallAllUsers=0",
                        "PrependPath=1", "Include_test=0", "Include_launcher=1",
                        f"TargetDir={target}"], timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"The install did not finish: {exc}")
        return None
    finally:
        try:
            installer.unlink()
        except OSError:
            pass

    direct = [str(target / "python.exe")]
    if python_version(direct):
        return direct
    return find_python()


# ---------------------------------------------------------------------------


def run(command, root):
    try:
        return subprocess.run(command, cwd=str(root)).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"Could not run {command[0]}: {exc}")
        return 1


def main():
    if pending_swap():
        return 0

    base = home()
    root = project_root(base)
    say("=" * 62)
    say("  VvokAI")
    say("=" * 62)
    say(f"  Files: {root}")
    say()

    if update_self(root):
        return 0

    if not project_present(root):
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            say(f"Could not create {root}: {exc}")
            input("Press Enter to close. ")
            return 1
    if not project_present(root) and not download_project(root):
        say()
        say("Nothing to run. Check the connection and start VvokAI again.")
        input("Press Enter to close. ")
        return 1

    python = find_python() or install_python()
    if python is None:
        say()
        say("VvokAI needs Python 3.10, 3.11 or 3.12 and could not install it.")
        say("Install 3.11.9 from python.org, tick 'Add python.exe to PATH',")
        say("then start VvokAI again.")
        input("Press Enter to close. ")
        return 1

    # From here the project's own tooling takes over: it already knows how to
    # update itself, build the environment and explain what went wrong.
    updater = root / "tools" / "updater.py"
    if updater.exists():
        run([*python, str(updater)], root)

    if run([*python, str(root / "tools" / "installer.py")], root) != 0:
        say()
        say("Setup did not finish. The reason is above, and install_log.txt")
        say("has the full output.")
        input("Press Enter to close. ")
        return 1

    # python.exe rather than pythonw.exe, and not detached: the bot prints a
    # lot and all of it is useful, so it goes to this console. desktop.py
    # copies the same thing into vvokai_log.txt for anybody who closed it.
    window = root / "venv" / "Scripts" / "python.exe"
    script = root / "desktop.py"
    if not window.exists() or not script.exists():
        say("The application is missing after setup, which should not happen.")
        input("Press Enter to close. ")
        return 1

    say()
    say("Opening VvokAI. Leave this window open - the log goes here.")
    say()
    # Waited on rather than launched and forgotten, so the console stays with
    # the program it belongs to and closing it closes the bot.
    while True:
        code = subprocess.call([str(window), str(script)], cwd=str(root))
        if code != RESTART_CODE:
            break
        # An update installed itself while the bot was running. A Python
        # process cannot reload the modules it is already executing, so it
        # asks to be started again instead. Only ever after an update that
        # actually installed - a check that finds nothing never gets here.
        say()
        say("Update installed. Restarting VvokAI...")
        say()

    if code != 0:
        # The window died rather than being closed. Without this the console
        # goes with it and takes the reason along - which from the outside is
        # exactly "it starts and then everything disappears".
        say()
        say(f"VvokAI stopped with an error (exit code {code}).")
        say(f"The last lines above say why, and {root / 'vvokai_log.txt'}")
        say("has the whole run.")
        input("Press Enter to close. ")
    return code


if __name__ == "__main__":
    sys.exit(main())
