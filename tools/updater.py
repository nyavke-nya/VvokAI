"""Bring the folder up to date with the published version, before launching.

Most people run this from a zip, not a clone, so there is no `git pull` to
offer them - the way an update reaches them is that somebody says "download it
again" in Discord and they re-extract the archive over the top, which loses
their config about half the time. This does the same thing properly: it asks
GitHub what the latest commit is, and if it is not the one already on disk it
fetches that archive and copies the files in.

What it will not touch, ever:

  * cfg/ - the settings, the API token, the developer-portal password. This is
    the whole reason re-extracting by hand goes wrong.
  * venv/ and models/ - gigabytes that the archive does not carry anyway.
  * anything the archive does not contain, so a playstyle somebody wrote
    themselves survives an update that ships two new ones.

Files that differ and are about to be replaced are copied into
backup_before_update/ first, so an update that turns out to be worse is a
folder copy away from being undone.

A checkout with a .git folder is skipped outright. That is the development
copy, its working tree is the thing being published, and overwriting it from
the last release would delete work in progress.

Exit codes: 0 to carry on and launch, 10 when files changed and the caller
should restart, so the launcher is not left running a batch script that has
been rewritten underneath it. Anything that goes wrong - no network, GitHub
down, rate limited, a half-written zip - exits 0. Failing to update is not a
reason to fail to start.
"""

import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO = "nyavke-nya/VvokAI"
BRANCH = "main"
TIMEOUT = 20

ROOT = Path(__file__).resolve().parent.parent
STAMP = ROOT / ".vvok_version"
BACKUP = ROOT / "backup_before_update"

# Matched against the path relative to the project root, lowercased, with
# forward slashes. A trailing slash means "this folder and everything in it".
PROTECTED = (
    "cfg/",
    "venv/",
    "models/",
    "debug_frames/",
    "backup_before_update/",
    "install_log.txt",
    "debug_view_worker.log",
    ".vvok_version",
    "addtogitinore.txt",
)

# Nothing in here is ours to overwrite even if the archive carries it.
SKIP_NAMES = {".gitignore", ".gitattributes"}

# Config files are protected as whole files, because they hold API tokens, the
# brawler queue and everything else somebody set for themselves. But some of
# what lives in them is not preference at all - it is calibration. How sure the
# wall model has to be, how big a tile reads on screen, how much of the area
# beside the player has to look like gas. Those are measurements of how the
# game renders, they are the same for everyone, and when they improve here they
# should improve everywhere rather than only for people who reinstall.
#
# So these two rules apply to the files below, and to nothing else:
#
#   * a key listed here is updated to the shipped value
#   * a key the file does not have yet is added, whatever it is
#
# Everything else the file contains is left exactly as it was found. Adding
# missing keys matters as much as updating listed ones: a setting introduced
# after somebody installed would otherwise never reach them, and they would
# silently run on the code default while the config that documents it sits
# unread on someone else's machine.
TUNING = {
    "cfg/bot_config.toml": (
        "perceived_tile_size",
        "player_collision_radius",
        "wall_model_classes",
        "wall_detection_confidence",
        "entity_detection_confidence",
        "centered_wall_detection",
        "poison_gas_fraction",
        "gadget_pixels_minimum",
        "hypercharge_pixels_minimum",
        "super_pixels_minimum",
        "idle_pixels_minimum",
        # stop_at, resume_at and max_session_minutes are deliberately absent.
        # When somebody may run their bot is their decision, not calibration,
        # and pushing this machine's hours onto theirs would be rude. They
        # still ARRIVE on an install that has never seen them, because a key
        # the file does not have yet is always added - it just arrives empty,
        # which is off.
    ),
    "cfg/time_tresholds.toml": (
        "state_check",
        "wall_detection",
        "no_detections",
        "no_detection_proceed",
        "check_if_brawl_stars_crashed",
        "gadget",
        "hypercharge",
        "super",
        "idle",
    ),
    # Every value in the dodge config is calibration; there is nothing personal
    # in the file. Listing no keys means "add anything new, change nothing" -
    # deliberately conservative, because anyone who has tuned their dodging has
    # tuned it against their own machine.
    "cfg/dodge_config.toml": (),
}


def say(message):
    print(f"[update] {message}")


def protected(relative):
    key = relative.as_posix().lower()
    if Path(key).name in SKIP_NAMES:
        return True
    for rule in PROTECTED:
        if rule.endswith("/"):
            if key.startswith(rule):
                return True
        elif key == rule:
            return True
    return False


def fetch(url, binary=False):
    # GitHub rejects requests without a User-Agent outright.
    request = urllib.request.Request(url, headers={"User-Agent": "VvokAI-updater"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = response.read()
    return payload if binary else json.loads(payload.decode("utf-8"))


def latest_commit():
    data = fetch(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}")
    return data["sha"], (data.get("commit", {}).get("message", "") or "").splitlines()[0]


def installed():
    try:
        return STAMP.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def download(sha):
    url = f"https://github.com/{REPO}/archive/{sha}.zip"
    blob = fetch(url, binary=True)
    holding = Path(tempfile.mkdtemp(prefix="vvok-update-"))
    archive = holding / "update.zip"
    archive.write_bytes(blob)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(holding)
    # The archive wraps everything in one folder named for the commit.
    roots = [p for p in holding.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError("the archive did not look the way it should")
    return holding, roots[0]


KEY_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _keys_in(text):
    """{key: line} for a flat TOML file, in the order they appear.

    Line-based on purpose. This runs on whatever Python is on the machine,
    before the venv exists, so there is no TOML writer available - and none is
    needed: every file this touches is a flat list of key = value. Anything
    with a [section] header is left alone rather than guessed at.
    """
    found = {}
    for line in text.splitlines():
        if line.lstrip().startswith("["):
            return None
        match = KEY_LINE.match(line)
        if match:
            found[match.group(1)] = line
    return found


def merge_settings(shipped_text, current_text, tuning_keys):
    """The user's file, with calibration updated and new keys added.

    Returns None when nothing needs to change, so an update that touches no
    settings does not rewrite the file or take a backup of it.
    """
    shipped = _keys_in(shipped_text)
    current = _keys_in(current_text)
    if shipped is None or current is None:
        return None

    lines = current_text.splitlines()
    changed = False

    # Update in place, so the file keeps the order and the spacing it had.
    for index, line in enumerate(lines):
        match = KEY_LINE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in tuning_keys and key in shipped and shipped[key] != line:
            lines[index] = shipped[key]
            changed = True

    # Then anything the file has never heard of, in the order the shipped file
    # lists it, so a new setting arrives rather than waiting for a reinstall.
    for key, line in shipped.items():
        if key not in current:
            lines.append(line)
            changed = True

    if not changed:
        return None
    return "\n".join(lines) + "\n"


def apply(source):
    """Copy the new files in. Returns how many actually changed."""
    changed = 0
    for incoming in source.rglob("*"):
        if not incoming.is_file():
            continue
        relative = incoming.relative_to(source)
        target = ROOT / relative

        tuning = TUNING.get(relative.as_posix().lower())
        if tuning is not None:
            # A config with calibration in it: merged key by key rather than
            # replaced, so the token, the queue and the chosen playstyle stay
            # exactly as they were.
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(incoming, target)
                changed += 1
                say(f"  {relative.as_posix()} (new)")
                continue
            try:
                merged = merge_settings(
                    incoming.read_text(encoding="utf-8"),
                    target.read_text(encoding="utf-8"),
                    set(tuning),
                )
            except (OSError, UnicodeDecodeError):
                continue
            if merged is None:
                continue
            keep = BACKUP / relative
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, keep)
            target.write_text(merged, encoding="utf-8")
            changed += 1
            say(f"  {relative.as_posix()} (settings merged)")
            continue

        if protected(relative):
            continue

        if target.exists() and target.read_bytes() == incoming.read_bytes():
            continue

        if target.exists():
            keep = BACKUP / relative
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, keep)

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(incoming, target)
        changed += 1
        say(f"  {relative.as_posix()}")
    return changed


def main():
    if (ROOT / ".git").exists():
        return 0  # Development copy; its working tree IS the published version.
    if os.environ.get("VVOK_NO_UPDATE"):
        say("skipped (VVOK_NO_UPDATE is set)")
        return 0

    try:
        sha, subject = latest_commit()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        say(f"could not check for updates ({exc}); starting with what is here")
        return 0

    if sha == installed():
        return 0

    if not installed():
        # First run after unzipping by hand. There is no way to tell how old
        # the folder is, so take the update: at worst it rewrites files with
        # identical ones, which apply() skips anyway.
        say("checking this copy against the published version")
    else:
        say(f"a newer version is available: {subject}")

    holding = None
    try:
        holding, source = download(sha)
        changed = apply(source)
    except (urllib.error.URLError, OSError, ValueError, RuntimeError,
            zipfile.BadZipFile) as exc:
        say(f"the update could not be applied ({exc}); starting with what is here")
        return 0
    finally:
        if holding is not None:
            shutil.rmtree(holding, ignore_errors=True)

    try:
        STAMP.write_text(sha, encoding="utf-8")
    except OSError:
        pass  # Worst case it checks again next time and finds nothing to do.

    if not changed:
        return 0

    say(f"{changed} file(s) updated; the previous ones are in "
        f"{BACKUP.name}/ if you want them back")
    return 10


if __name__ == "__main__":
    sys.exit(main())
