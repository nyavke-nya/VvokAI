"""Environment setup for VvokAI: inspect, install, verify, explain.

start_pyla.bat finds a usable Python and then hands over to this. Batch can
start a program and check an exit code; it cannot retry a flaky download,
recognise why a build failed, or say anything useful about it - and almost
every install problem this project has produced came down to that last part.
The failures were never mysterious once you knew what to look for:

    Microsoft Visual C++ 14.0 or greater is required
        Reads as a missing compiler. Is really a Python too new for one of the
        dependencies, so pip fell back to building it from source.

    ModuleNotFoundError: No module named 'pandas'
        The install half-failed once, the launcher wrote a "setup complete"
        marker anyway, and nothing ever checked again.

    The function is not implemented. Rebuild the library with Windows...
        Two OpenCV distributions unpacked into the same folder and the headless
        one landed last.

Each of those cost hours to diagnose from the raw output. They are all
recognised here and reported in a sentence.

Run directly for a report on the current machine:

    python tools/installer.py --report
"""

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / "venv"
VENV_PYTHON = VENV / "Scripts" / "python.exe"
MARKER = VENV / ".setup_complete"
LOG = ROOT / "install_log.txt"

# Versions the dependencies actually publish Windows wheels for. Outside this
# range pip tries to compile from source and needs a C++ toolchain nobody
# installing a game bot is expected to have.
MIN_PYTHON = (3, 10)
MAX_PYTHON = (3, 12)

# Installed first, together, so pip resolves them as one set.
CORE_PACKAGES = [
    "aiohttp", "numpy", "requests", "toml", "pillow", "discord.py",
    "packaging", "pywin32", "easyocr", "Flask", "pycryptodome",
]

# Pinned because scrcpy's frame handling is sensitive to both.
PINNED_PACKAGES = ["adbutils==2.12.0", "av==12.3.0"]

# module name -> package that provides it, for the final check.
IMPORT_CHECKS = [
    ("cv2", "opencv-python"),
    ("numpy", "numpy"),
    ("requests", "requests"),
    ("toml", "toml"),
    ("PIL", "pillow"),
    ("discord", "discord.py"),
    ("win32api", "pywin32"),
    ("easyocr", "easyocr"),
    ("adbutils", "adbutils"),
    ("av", "av"),
    ("flask", "Flask"),
    ("Crypto", "pycryptodome"),
    ("onnxruntime", "onnxruntime"),
    ("torch", "torch"),
    ("aiohttp", "aiohttp"),
]

REQUIRED_FILES = [
    "main.py", "play.py",
    "models/mainInGameModel.onnx",
    "models/tileDetector.onnx",
    "models/closeTileDetector.onnx",
    "models/easyocr/craft_mlt_25k.pth",
    "cfg/bot_config.toml",
    "playstyles/unified_dodge.pyla",
]

# Failure signatures, most specific first. Each maps raw pip noise onto a
# sentence that says what to do about it.
KNOWN_FAILURES = [
    (r"Microsoft Visual C\+\+ 1[4-9]\.0 or greater is required",
     "A package had no ready-made build for this Python and pip tried to compile it.",
     "Install Python 3.11 and run this again. The compiler is not the real problem."),
    (r"Could not find a version that satisfies the requirement (\S+)",
     "No release of {0} exists for this Python version.",
     "Install Python 3.11 and run this again."),
    (r"No matching distribution found for (\S+)",
     "Nothing published for {0} matches this Python or this platform.",
     "Install Python 3.11 and run this again."),
    (r"\[WinError 5\]|Access is denied|Отказано в доступе",
     "A file is locked, so it could not be replaced.",
     "Close the bot and the debug window, then run this again."),
    (r"\[WinError 32\]|being used by another process",
     "A file is open in another program.",
     "Close the bot and any Explorer window showing this folder, then retry."),
    (r"CERTIFICATE_VERIFY_FAILED|SSLError|SSL: ",
     "The download was rejected on a certificate check.",
     "Usually antivirus or a company network intercepting HTTPS. Try another network."),
    (r"No space left|not enough space on the disk|Недостаточно места",
     "The disk filled up.",
     "About 4 GB is needed. Free some space and run this again."),
    (r"ReadTimeoutError|ConnectionError|Temporary failure in name resolution|"
     r"Failed to establish a new connection|Connection reset",
     "The download did not complete.",
     "A network problem. This retries on its own; if it keeps failing, check the connection."),
    (r"error: subprocess-exited-with-error",
     "A package tried to build itself and failed.",
     "Almost always an unsupported Python version. 3.11 is the safe choice."),
]


def log(message="", also_print=True):
    if also_print:
        print(message)
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except OSError:
        pass


def section(title):
    log("")
    log("-" * 62)
    log(f"  {title}")
    log("-" * 62)


def run(command, capture=True, timeout=3600):
    """Run a command, returning (code, combined output)."""
    try:
        result = subprocess.run(
            command,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(ROOT),
        )
    except FileNotFoundError:
        return 127, f"not found: {command[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    output = ((result.stdout or "") + (result.stderr or "")) if capture else ""
    return result.returncode, output


def explain(output):
    """Turn pip's output into something worth reading. Returns [(what, fix)]."""
    found = []
    for pattern, what, fix in KNOWN_FAILURES:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            groups = match.groups()
            found.append((what.format(*groups) if groups else what, fix))
    return found


# ---------------------------------------------------------------------------
#  System
# ---------------------------------------------------------------------------

def find_pythons():
    """Every Python on the machine, with its version, best candidates first."""
    seen = {}
    candidates = []

    code, output = run(["py", "-0p"])
    if code == 0:
        for line in output.splitlines():
            parts = line.split()
            for part in parts:
                if part.lower().endswith("python.exe"):
                    candidates.append(part.strip('"'))

    candidates.append(shutil.which("python") or "")
    local = os.environ.get("LocalAppData", "")
    program_files = os.environ.get("ProgramFiles", "")
    for version in ("311", "312", "310", "313", "39"):
        candidates.append(fr"{local}\Programs\Python\Python{version}\python.exe")
        candidates.append(fr"{program_files}\Python{version}\python.exe")

    for path in candidates:
        if not path:
            continue
        # Windows paths differ only by case all the time; without this the same
        # interpreter is listed several times.
        path = str(Path(path).resolve()) if Path(path).exists() else ""
        if not path or path.lower() in {key.lower() for key in seen}:
            continue
        code, output = run([path, "-c",
                            "import sys;print('%d.%d' % sys.version_info[:2])"])
        if code != 0:
            continue
        try:
            major, minor = (int(part) for part in output.strip().split("."))
        except ValueError:
            continue
        seen[path] = (major, minor)

    ordered = sorted(
        seen.items(),
        key=lambda item: (
            not (MIN_PYTHON <= item[1] <= MAX_PYTHON),   # supported first
            abs(item[1][1] - 11),                        # then closest to 3.11
        ),
    )
    return ordered


def gpu_info():
    code, output = run(["nvidia-smi", "--query-gpu=name,compute_cap",
                        "--format=csv,noheader"])
    if code == 0 and output.strip():
        name, _, cap = output.strip().splitlines()[0].partition(",")
        clean = name.strip()
        if clean.upper().startswith("NVIDIA "):
            clean = clean[7:]
        return "nvidia", clean, cap.strip()

    code, output = run(["wmic", "path", "win32_VideoController", "get", "name"])
    if code == 0:
        for line in output.splitlines()[1:]:
            if line.strip():
                return "other", line.strip(), ""
    return "unknown", "", ""


def report_system():
    section("This machine")
    log(f"  Windows        {platform.platform()}")
    log(f"  Architecture   {platform.machine()}")

    total, used, free = shutil.disk_usage(str(ROOT))
    log(f"  Free space     {free / 1024**3:.1f} GB   (about 4 GB is needed)")

    vendor, name, cap = gpu_info()
    if vendor == "nvidia":
        log(f"  Graphics       NVIDIA {name} (compute {cap})")
    elif name:
        log(f"  Graphics       {name}")
    else:
        log("  Graphics       could not be determined")

    pythons = find_pythons()
    if not pythons:
        log("  Python         none found")
    for path, version in pythons:
        supported = MIN_PYTHON <= version <= MAX_PYTHON
        mark = "usable" if supported else "too new" if version > MAX_PYTHON else "too old"
        log(f"  Python {version[0]}.{version[1]:<3}    {mark:<8} {path}")

    missing = [name for name in REQUIRED_FILES if not (ROOT / name).exists()]
    if missing:
        log("")
        log("  Files missing from this download:")
        for name in missing:
            log(f"    {name}")
        log("  The download is incomplete - unzip it again, all of it.")
    return free, pythons, vendor, missing


# ---------------------------------------------------------------------------
#  Install
# ---------------------------------------------------------------------------

def pip_install(args, what, attempts=3):
    """Install, retrying what is worth retrying and explaining what is not."""
    command = [str(VENV_PYTHON), "-m", "pip", "install", "--disable-pip-version-check"]
    for attempt in range(1, attempts + 1):
        log(f"  {what}" + (f"   (attempt {attempt} of {attempts})" if attempt > 1 else ""))
        code, output = run(command + args)
        log(output, also_print=False)
        if code == 0:
            return True

        reasons = explain(output)
        transient = any("network" in fix.lower() or "retries on its own" in fix.lower()
                        for _, fix in reasons)
        if reasons and not transient:
            # A cause that will not change on its own; stop wasting the user's
            # time repeating it.
            log("")
            for what_happened, fix in reasons:
                log(f"  [!] {what_happened}")
                log(f"      {fix}")
            return False

        if attempt < attempts:
            wait = attempt * 4
            log(f"      failed, retrying in {wait}s")
            time.sleep(wait)

    log("")
    for what_happened, fix in explain(output) or [("The install failed.",
                                                   "The full output is in install_log.txt.")]:
        log(f"  [!] {what_happened}")
        log(f"      {fix}")
    return False


def ensure_venv(python_path):
    if VENV_PYTHON.exists():
        code, output = run([str(VENV_PYTHON), "-c",
                            "import sys;print('%d.%d' % sys.version_info[:2])"])
        version = None
        if code == 0:
            try:
                version = tuple(int(part) for part in output.strip().split("."))
            except ValueError:
                version = None
        if version and MIN_PYTHON <= version <= MAX_PYTHON:
            log(f"  Reusing the existing environment (Python {version[0]}.{version[1]})")
            return True
        # Installing into a venv built on the wrong Python would fail exactly
        # the way it failed the first time.
        log("  The existing environment uses an unsupported Python. Rebuilding it.")
        shutil.rmtree(VENV, ignore_errors=True)

    log(f"  Creating the environment with {python_path}")
    code, output = run([python_path, "-m", "venv", str(VENV)])
    if code != 0:
        log(output)
        return False
    return True


def install_accelerator(vendor):
    """One ONNX runtime, and a torch that does not fight it.

    Both onnxruntime distributions unpack into the same folder, so having two
    installed means whichever landed last is the one that runs - and the same
    is true of opencv. Every one of these is removed before one is put back.
    """
    log("  Removing any conflicting runtimes first")
    run([str(VENV_PYTHON), "-m", "pip", "uninstall", "-y",
         "onnxruntime", "onnxruntime-gpu", "onnxruntime-directml"])

    if vendor == "nvidia":
        log("  NVIDIA card detected - trying CUDA, which is about 3.7x faster")
        if pip_install(["onnxruntime-gpu[cuda,cudnn]==1.28.0"], "CUDA runtime", attempts=2):
            # The CUDA build of torch ships its own cuDNN, which then fights
            # onnxruntime's. torch is needed for one line here, so the CPU
            # build is both sufficient and the one that works.
            pip_install(["torch", "torchvision", "--index-url",
                         "https://download.pytorch.org/whl/cpu"], "PyTorch (CPU build)")
            return "CUDA"
        log("  CUDA would not install. Falling back to DirectML, which always works.")

    if pip_install(["onnxruntime-directml~=1.24"], "DirectML runtime"):
        pip_install(["torch", "torchvision", "--index-url",
                     "https://download.pytorch.org/whl/cpu"], "PyTorch (CPU build)")
        return "DirectML"
    return None


def install_opencv():
    """Exactly one OpenCV, and it must be the one with a GUI.

    easyocr depends on opencv-python-headless. Both unpack into the same cv2
    folder, so whichever pip installs last wins outright - and when that is the
    headless build, every window call fails with "the function is not
    implemented" and the debug view can never open, with nothing in the log to
    say why.
    """
    run([str(VENV_PYTHON), "-m", "pip", "uninstall", "-y",
         "opencv-python", "opencv-python-headless", "opencv-contrib-python"])
    return pip_install(["opencv-python~=4.11"], "OpenCV (with window support)")


def ensure_configs():
    """Create working configuration files from their examples if missing."""
    cfg_dir = ROOT / "cfg"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for example in cfg_dir.glob("*.example.toml"):
        target_name = example.name.replace(".example.toml", ".toml")
        target = cfg_dir / target_name
        if not target.exists():
            try:
                shutil.copyfile(example, target)
            except OSError:
                pass
    history_file = cfg_dir / "match_history.csv"
    if not history_file.exists():
        try:
            history_file.write_text(
                "date_time,brawler_name,result,current_trophies,trophy_delta,"
                "new_winstreak,playstyle_hash,playstyle_name,playstyle_gamemodes,"
                "playstyle_brawlers,pyla_version,power_level\n",
                encoding="utf-8"
            )
        except OSError:
            pass


# ---------------------------------------------------------------------------
#  Reaching the panel from outside the house
# ---------------------------------------------------------------------------
#
# The panel only answers on the local network, and turning that into a public
# address by hand is three steps that only make sense if you already know what
# a tunnel is. Most people running this fork do not, and should not have to.
#
# So it is one question, asked once, off by default. The answer is remembered
# as remote_access in cfg/general_config.toml - present means answered, so
# nobody gets asked twice, and anybody who changes their mind edits one line.

CLOUDFLARED_URL = ("https://github.com/cloudflare/cloudflared/releases/latest/"
                   "download/cloudflared-windows-amd64.exe")
CLOUDFLARED_LOCAL = ROOT / "tools" / "cloudflared.exe"
GENERAL_CONFIG = ROOT / "cfg" / "general_config.toml"


def cloudflared_present():
    return shutil.which("cloudflared") is not None or CLOUDFLARED_LOCAL.exists()


def install_cloudflared():
    """winget first, then the official binary straight into tools/.

    winget is preferred because it is the machine's own package manager and
    keeps the thing updated. It is missing on older Windows 10 and disabled on
    some managed machines, and "install winget first" is not an answer anybody
    wants, so the fallback fetches the same file winget would.
    """
    if cloudflared_present():
        log("  cloudflared is already installed.")
        return True

    if shutil.which("winget"):
        log("  Installing cloudflared with winget...")
        code, output = run(["winget", "install", "--id", "Cloudflare.cloudflared",
                            "--silent", "--accept-package-agreements",
                            "--accept-source-agreements"], timeout=600)
        if code == 0 and shutil.which("cloudflared"):
            log("  Installed.")
            return True
        log("  winget could not do it, downloading the program directly instead.")

    log(f"  Downloading cloudflared ({CLOUDFLARED_URL.rsplit('/', 1)[-1]}, about 50 MB)...")
    try:
        import urllib.request
        request = urllib.request.Request(CLOUDFLARED_URL,
                                         headers={"User-Agent": "VvokAI-installer"})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
        CLOUDFLARED_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        CLOUDFLARED_LOCAL.write_bytes(payload)
    except Exception as error:  # noqa: BLE001 - any failure here is the same answer
        log(f"  The download failed ({type(error).__name__}: {error}).")
        log("  Remote access is being left off. You can turn it on later by")
        log("  installing cloudflared and setting remote_access = \"cloudflare\".")
        return False

    log(f"  Saved to {CLOUDFLARED_LOCAL.relative_to(ROOT)}.")
    return True


def _set_remote_access(value):
    """Write the answer into general_config.toml, replacing any existing line.

    Hand-edited rather than round-tripped through a TOML writer: this file
    holds the API token and the developer-portal password, and rewriting the
    whole thing to change one line is a good way to lose them to a formatting
    bug.
    """
    line = f'remote_access = "{value}"'
    try:
        text = GENERAL_CONFIG.read_text(encoding="utf-8") if GENERAL_CONFIG.exists() else ""
        lines = text.splitlines()
        for index, existing in enumerate(lines):
            if existing.strip().startswith("remote_access"):
                lines[index] = line
                break
        else:
            lines.append(line)
        GENERAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        GENERAL_CONFIG.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
        return True
    except OSError as error:
        log(f"  Could not write {GENERAL_CONFIG.name} ({error}).")
        return False


def current_remote_access():
    """The setting as it stands, or None when the file has never had it."""
    try:
        text = GENERAL_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.strip().startswith("remote_access"):
            return line.split("=", 1)[-1].strip().strip('"').lower()
    return None


def set_up_remote_access():
    """Install cloudflared and switch remote access on, without asking.

    This used to be a question. It is not one any more: the people running
    this fork should not have to know what a tunnel is to reach their own
    panel from a phone, and "answer yes to the thing you do not recognise" is
    not a better experience than it just working.

    What makes that defensible is the login. The panel cannot be opened
    without an account, the account can only be created from this machine or
    its own network, and guessing the password is rate limited. Without those
    this would be handing every install to the internet.

    An explicit remote_access = "off" is left alone. Somebody who turned it
    off meant it, and setup running again should not undo that.
    """
    section("Reaching the panel from your phone")

    setting = current_remote_access()
    if setting == "off":
        log("  remote_access is set to \"off\" in cfg/general_config.toml, so this")
        log("  is being left alone. Set it to \"cloudflare\" to turn it back on.")
        return

    log("  The panel is where the queue, the settings and the stats live. It")
    log("  will be reachable from anywhere - mobile data, work, a friend's")
    log("  house - over an HTTPS address, not only on this Wi-Fi.")
    log("")
    log("  It asks for a username and password before it opens anything. You")
    log("  will be asked to create those the first time you open it, and that")
    log("  can only be done from this computer or its own network.")
    log("")

    if not install_cloudflared():
        _set_remote_access("off")
        log("")
        log("  Remote access is off for now. Run this again once the download")
        log("  can get through, or install cloudflared yourself:")
        log("    winget install --id Cloudflare.cloudflared")
        return

    if _set_remote_access("cloudflare"):
        log("")
        log("  Done. When the bot starts it will print the address, and the")
        log("  Telegram bot will send it if you ask it for /panel.")
        log("  The address changes every restart, so ask for it rather than")
        log("  reusing an old link.")


# ---------------------------------------------------------------------------
#  Verify
# ---------------------------------------------------------------------------

CHECK_SCRIPT = r"""
import importlib, json, sys
result = {"missing": [], "gui": False, "providers": []}
for module, package in %(checks)s:
    try:
        importlib.import_module(module)
    except Exception as error:
        result["missing"].append([package, type(error).__name__])
try:
    import cv2
    info = cv2.getBuildInformation()
    part = info.split("GUI:", 1)[-1][:400].upper() if "GUI:" in info else ""
    result["gui"] = any(n in part for n in ("WIN32UI", "GTK", "COCOA", "QT"))
except Exception:
    pass
try:
    import onnxruntime
    result["providers"] = onnxruntime.get_available_providers()
except Exception:
    pass
print(json.dumps(result))
"""


def verify():
    section("Checking the result")
    import json

    code, output = run([str(VENV_PYTHON), "-c",
                        CHECK_SCRIPT % {"checks": repr(IMPORT_CHECKS)}])
    try:
        data = json.loads(output.strip().splitlines()[-1])
    except (ValueError, IndexError):
        log("  Could not run the check at all:")
        log(output)
        return False

    if data["missing"]:
        for package, error in data["missing"]:
            log(f"  MISSING  {package}   ({error})")
    else:
        log(f"  All {len(IMPORT_CHECKS)} packages import correctly")

    log(f"  Debug window support: {'yes' if data['gui'] else 'NO - headless OpenCV is in the way'}")
    providers = [p.replace("ExecutionProvider", "") for p in data["providers"]]
    log(f"  Acceleration: {', '.join(providers) if providers else 'none detected'}")

    return not data["missing"] and data["gui"]


def fingerprint():
    import hashlib
    digest = hashlib.sha256()
    for name in ("setup.py", "requirements.txt", "tools/installer.py"):
        path = ROOT / name
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()[:16]


def up_to_date():
    if not VENV_PYTHON.exists() or not MARKER.exists():
        return False
    try:
        return MARKER.read_text(encoding="utf-8").strip() == fingerprint()
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true",
                        help="describe the machine and exit")
    parser.add_argument("--force", action="store_true",
                        help="reinstall even if nothing has changed")
    args = parser.parse_args()

    try:
        LOG.write_text(f"VvokAI install log - {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                       encoding="utf-8")
    except OSError:
        pass

    log("=" * 62)
    log("  VvokAI setup")
    log("=" * 62)

    free, pythons, vendor, missing_files = report_system()
    ensure_configs()

    if args.report:
        return 0

    if missing_files:
        return 2

    if free < 4 * 1024**3:
        section("Not enough disk space")
        log(f"  {free / 1024**3:.1f} GB free, about 4 GB is needed.")
        return 3

    usable = [(path, version) for path, version in pythons
              if MIN_PYTHON <= version <= MAX_PYTHON]
    if not usable:
        section("No usable Python")
        if pythons:
            newest = pythons[0][1]
            log(f"  Found Python {newest[0]}.{newest[1]}, which is outside the")
            log(f"  {MIN_PYTHON[0]}.{MIN_PYTHON[1]} - {MAX_PYTHON[0]}.{MAX_PYTHON[1]} range these dependencies publish builds for.")
            log("  On a newer Python, pip tries to compile them and stops at a")
            log("  missing C++ compiler - which is a misleading way to say")
            log("  'this Python is too new'.")
        log("")
        log("  Install Python 3.11.9 and run this again:")
        log("    https://www.python.org/downloads/release/python-3119/")
        log("  Tick 'Add python.exe to PATH'. Your current Python can stay;")
        log("  several versions coexist happily.")
        return 4

    if VENV_PYTHON.exists() and not args.force:
        # Check the environment before deciding to touch it. The fingerprint
        # says whether the dependency list has moved, but a working install is
        # a working install - if everything imports and the debug window can
        # open, reinstalling only risks breaking something that was fine. A
        # newly added dependency would fail the import check and be caught
        # here, which is the case the fingerprint was really guarding.
        section("Already set up" if up_to_date() else "Checking the existing setup")
        if verify():
            if not up_to_date():
                log("")
                log("  The dependency list changed but everything still works,")
                log("  so nothing is being reinstalled.")
                try:
                    MARKER.parent.mkdir(parents=True, exist_ok=True)
                    MARKER.write_text(fingerprint(), encoding="utf-8")
                except OSError:
                    pass
            log("")
            log("  Ready.")
            set_up_remote_access()
            return 0
        log("")
        log("  Something is missing, so it is being repaired now.")

    section("Installing")
    if not ensure_venv(usable[0][0]):
        log("  Could not create the environment.")
        return 5

    run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade",
         "pip", "setuptools", "wheel"])

    if not pip_install(CORE_PACKAGES, "Core packages"):
        return 6
    if not pip_install(PINNED_PACKAGES, "Pinned packages (scrcpy is fussy about these)"):
        return 6
    if not install_opencv():
        return 6

    section("Graphics acceleration")
    runtime = install_accelerator(vendor)
    if runtime is None:
        log("  No accelerator could be installed; the bot will run on the CPU.")

    ok = verify()

    section("Done" if ok else "Finished with problems")
    if ok:
        try:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(fingerprint(), encoding="utf-8")
        except OSError:
            pass
        log("  Everything installed and checked.")
        set_up_remote_access()
        log("")
        log("  Starting the bot.")
        return 0

    log("  Some of it did not work. The list above says what.")
    log(f"  The full output is in {LOG.name} - send that if you need help.")
    return 7


if __name__ == "__main__":
    sys.exit(main())
