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

# Files that used to ship and no longer do.
#
# apply() walks the INCOMING tree, so it can add and overwrite but has no way
# to notice something that simply stopped existing. Left alone, every install
# that has ever updated accumulates the leftovers of every file that was ever
# moved or renamed - which is exactly the clutter a reorganisation is meant to
# remove, made permanent on everybody else's machine.
#
# Deliberately an explicit list rather than "delete anything not in the
# archive". The archive is what GitHub gives us; a bad download, a partial
# extract or a path the packer skipped would, under that rule, quietly delete
# working files. Naming them means the worst a mistake here can do is remove
# something we put there ourselves, and a copy of it goes to
# backup_before_update first either way.
#
# A trailing slash retires a whole directory. PROTECTED still wins, so nothing
# listed there can be removed by this.
RETIRED = (
    # The loose modules moved into src/ so the project root stops being a wall
    # of forty files. Their names did not change and neither did any import;
    # only where Python looks for them.
    "brawl_api.py",
    "brawl_token.py",
    "debug_view.py",
    "detect.py",
    "discord_bot.py",
    "lobby_automation.py",
    "play.py",
    "profile_stats.py",
    "remote_control.py",
    "schedule_control.py",
    "stage_manager.py",
    "state_finder.py",
    "telegram_bot.py",
    "time_management.py",
    "trophy_observer.py",
    "utils.py",
    "window_controller.py",
    "dodge/",
    "scrcpy/",
    "webui/",
    "api/api.py",
    # Stale bytecode for all of the above. Harmless, but it is the same clutter
    # by another name, and leaving it behind makes the root look untouched.
    "__pycache__/",

    # The assets moved under assets/. api/ went with them: it held the brawler
    # icons and one module nothing imported, and the /api/... URLs the panel
    # uses are Flask routes that never had anything to do with the folder.
    "images/",
    "static/",
    "templates/",
    "api/",

    # Build scripts and the packaging stub belong with the other tooling, and
    # the dodging write-up with the other documentation.
    "build_exe.bat",
    "build_nuitka.bat",
    "setup.py",
    "DODGE.md",

    # The shipped playstyles gained the .vvok extension. The .vvok copies arrive
    # through apply(); these old .pyla ones would otherwise linger and show up
    # twice in the list. A config still naming the .pyla file is fine - the
    # loader falls back to the .vvok one, and re-activating rewrites the config.
    # A playstyle somebody wrote themselves is not listed, so it survives.
    "playstyles/unified_dodge.pyla",
    "playstyles/unified_aggro.pyla",
    "playstyles/unified_light.pyla",

)

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
        "attack_range_multiplier",
        "gadget_pixels_minimum",
        "hypercharge_pixels_minimum",
        "super_pixels_minimum",
        "idle_pixels_minimum",
        # How the invite dialog is found and where its checkbox is are
        # measurements of the game, so a corrected one should reach everyone.
        # Whether invites are declined at all is not - some people want them -
        # so decline_team_invites is absent, and arrives once as the shipped
        # default like any other new key.
        "team_invite_green_minimum",
        "team_invite_mute_x",
        "team_invite_mute_y",
        # stop_at and resume_at are deliberately absent.
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
        "idle_restart_cooldown",
        "team_invite",
    ),
    "cfg/lobby_config.toml": (
        # Where the pink heart that identifies the brawler list is looked for.
        # The update that added the search box pushed the heart left, out of
        # the box entirely, and the bot stopped recognising the list at all -
        # every selection ended in "the list never opened". Correcting it in
        # the shipped file is not enough on its own: cfg/ is protected as a
        # whole, so an install that updates rather than reinstalls keeps its
        # own copy, and this is a measurement of where the game draws a thing
        # rather than anything anyone chose. So it is listed, and overwritten.
        "brawler_menu_heart",
    ),
    # Every value in the dodge config is calibration; there is nothing personal
    # in the file. Listing no keys means "add anything new, change nothing" -
    # deliberately conservative, because anyone who has tuned their dodging has
    # tuned it against their own machine.
    "cfg/dodge_config.toml": (),
}


# The same rule as TUNING, for the two configs that are JSON rather than TOML:
# a name the file has never heard of is added, a name it already has is left
# exactly as it is.
#
# They need it as much as the TOML ones do. Both are keyed by brawler, cfg/ is
# protected wholesale, and neither is in TUNING - so a brawler added here has
# so far reached nobody who installed from a zip. The bot does ask an upstream
# service for brawlers it does not recognise, but a brawler released this week
# is exactly the one that service does not have yet, which is the case this
# exists for.
#
# Adding only, never changing: somebody whose file already has the brawler may
# have tuned its ranges, or picked it up from that upstream service with
# numbers of its own, and neither is ours to overwrite.
JSON_ADDITIONS = (
    "cfg/brawlers_info.json",
    "cfg/names.json",
)


def merge_json(shipped_text, current_text):
    """The user's file with unknown names added. None when nothing is missing.

    Two levels deep, because a brawler can gain a field as well as a file
    gaining a brawler - quick_attack_range arrived on a Nori that some people
    already had - and a record that is missing one of those reads as a brawler
    with no second attack rather than as an error. Lists are unioned rather
    than left alone for the same reason: names.json holds the spellings OCR
    produces for a brawler, and a new one has to reach a name the file already
    knows about.
    """
    try:
        shipped = json.loads(shipped_text)
        current = json.loads(current_text)
    except ValueError:
        return None
    if not isinstance(shipped, dict) or not isinstance(current, dict):
        return None

    changed = False
    for name, record in shipped.items():
        if name not in current:
            current[name] = record
            changed = True
        elif isinstance(record, dict) and isinstance(current[name], dict):
            for field, value in record.items():
                if field not in current[name]:
                    current[name][field] = value
                    changed = True
        elif isinstance(record, list) and isinstance(current[name], list):
            # names.json: the OCR spellings a brawler answers to. Purely
            # additive by nature - a new one is a reading somebody actually saw
            # on screen - so the two lists are unioned, keeping whatever the
            # user added themselves. Without this a brawler already in the file
            # could never gain a spelling, which is exactly the case that broke:
            # the game renders NORI and easyocr reads "norz".
            for value in record:
                if value not in current[name]:
                    current[name].append(value)
                    changed = True

    if not changed:
        return None
    return json.dumps(current, indent=4, ensure_ascii=False) + chr(10)


def auto_update_wanted():
    """Whether updating is allowed at all.

    Somebody whose setup works has a real reason to freeze it, and until now
    the only way was an environment variable nobody could be expected to
    discover. An update that breaks a working machine and cannot be refused is
    worse than no updater: the person who reported that one stopped using the
    project.

    Missing means yes, so nothing changes for anybody who has not asked.
    """
    try:
        text = (ROOT / "cfg" / "general_config.toml").read_text(encoding="utf-8")
    except OSError:
        return True
    for line in text.splitlines():
        if line.strip().startswith("auto_update"):
            value = line.split("=", 1)[-1].strip().strip('"').strip("'").lower()
            return value not in ("false", "no", "off", "0")
    return True


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


def retire():
    """Remove what used to ship and does not any more. Returns how many.

    Each one is copied into backup_before_update before it goes, the same way
    an overwritten file is, so an update never destroys the only copy of
    anything.
    """
    removed = 0
    for rule in RETIRED:
        relative = Path(rule.rstrip("/"))
        if protected(relative):
            # Belt and braces: a path in both lists is a mistake, and the
            # protective answer is the one to take.
            say(f"  refusing to remove protected path {relative.as_posix()}")
            continue

        target = ROOT / relative
        if not target.exists():
            continue

        try:
            keep = BACKUP / relative
            keep.parent.mkdir(parents=True, exist_ok=True)
            if target.is_dir():
                shutil.copytree(target, keep, dirs_exist_ok=True)
                shutil.rmtree(target)
            else:
                shutil.copy2(target, keep)
                target.unlink()
        except OSError as exc:
            say(f"  could not remove {relative.as_posix()}: {exc}")
            continue

        removed += 1
        say(f"  removed {relative.as_posix()} (no longer part of VvokAI)")
    return removed


def apply(source):
    """Copy the new files in. Returns how many actually changed."""
    changed = 0
    for incoming in source.rglob("*"):
        if not incoming.is_file():
            continue
        relative = incoming.relative_to(source)
        target = ROOT / relative

        key = relative.as_posix().lower()
        if key in JSON_ADDITIONS and target.exists():
            try:
                merged = merge_json(incoming.read_text(encoding="utf-8"),
                                    target.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            if merged is None:
                continue
            keep = BACKUP / relative
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, keep)
            target.write_text(merged, encoding="utf-8")
            changed += 1
            say(f"  {relative.as_posix()} (new entries added)")
            continue

        tuning = TUNING.get(key)
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


def main(check_only=False):
    """Update, or with check_only just report whether there is one.

    The check exists so the running bot can ask hourly without rewriting a
    single file. Applying an update under a bot that is mid-match would swap
    the code out from under a game in progress; asking first means the files
    only move once it has finished and stopped.
    """
    if (ROOT / ".git").exists():
        return 0  # Development copy; its working tree IS the published version.
    if not auto_update_wanted():
        if not check_only:
            say("skipped (auto_update is off in cfg/general_config.toml)")
        return 0
    if os.environ.get("VVOK_NO_UPDATE"):
        if not check_only:
            say("skipped (VVOK_NO_UPDATE is set)")
        return 0

    try:
        sha, subject = latest_commit()
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        if not check_only:
            say(f"could not check for updates ({exc}); starting with what is here")
        return 0

    if sha == installed():
        return 0

    if check_only:
        # Same 10 the applying path uses, and for the same reason: the caller
        # has something to do about it.
        say(f"an update is available: {subject}")
        return 10

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
        changed += retire()
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
    sys.exit(main(check_only="--check" in sys.argv[1:]))
