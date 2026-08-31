"""Anonymous usage figures, so the fork can be developed on evidence.

What this is for: how many people run it, which playstyles they choose, which
brawlers actually win, whether anybody uses TensorRT. Those questions have
been answered by guesswork and by whoever happens to post in Discord, which is
a loud minority of a small number.

Three rules it follows, and they are not decoration.

Nothing identifies anybody. No player tag, no account name, no API token, no
file paths, no IP that we record. The only stable value is a random number
generated on first run, which exists so that one machine reporting twice is
not counted as two people, and which says nothing about who owns it.

It is announced and it can be turned off. A project whose README argues it is
not a virus cannot also collect data quietly - the first person to read the
source would be right to say so, and would be believed. There is a line in the
README, a switch in the config, and this module does nothing at all until the
switch and an endpoint are both set.

It never blocks the bot. Sending happens on a background thread, failures are
swallowed, and nothing in here can make a match go worse.

On the transport. Whatever endpoint the client posts to, its address ships
inside the client, so anybody can find it and post rubbish - that is true of
every option and cannot be engineered away without a server that authenticates
people. What CAN be arranged is that nobody can READ what was collected, and
that is what a write-only sink like a Google Form gives: submissions go to a
sheet only its owner can open, and the form ID grants no read of any kind.
"""

import json
import platform
import threading
import time
import urllib.parse
import urllib.request
import uuid

from utils import (PYLA_VERSION, config_bool, load_toml_as_dict,
                   resolve_project_path)

# Where reports go. Empty means telemetry is off no matter what else is set -
# which is how it ships, so a fork of this fork is not quietly reporting to
# somebody else's spreadsheet.
ENDPOINT = ("https://docs.google.com/forms/d/e/"
            "1FAIpQLSc33ZzqM_Qqw0sY47-aPsBaCk_A7MRPqSazt6n6RqPV2XuorA/formResponse")

# Google Forms wants entry.<id>=<value>; anything else takes JSON. Set this to
# the field id when ENDPOINT is a form, leave it empty otherwise.
FORM_FIELD = "entry.1962452951"

# The identity file. Deliberately not in cfg/, which people copy between
# machines when they move an install - a copied id would merge two people into
# one figure.
INSTALL_ID_FILE = ".vvok_install_id"

# No more often than this. The questions being asked are about weeks, not
# seconds, and a report per match would be both rude and useless.
MIN_INTERVAL = 6 * 3600


def enabled():
    """Whether to report at all. Off unless switched on AND aimed somewhere."""
    if not ENDPOINT:
        return False
    try:
        general = load_toml_as_dict("cfg/general_config.toml")
    except Exception:
        return False
    return config_bool(general.get("send_anonymous_stats"), True)


def install_id():
    """A random id for this install. Created once, never derived from anything.

    Not a hash of the machine, the account or the player tag: those can be
    checked against a guess, and a value that can be checked is not anonymous.
    """
    path = resolve_project_path(INSTALL_ID_FILE)
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if len(existing) >= 8:
            return existing
    except OSError:
        pass

    fresh = uuid.uuid4().hex
    try:
        path.write_text(fresh, encoding="utf-8")
    except OSError:
        pass
    return fresh


# What the running bot measured, kept here so a report can carry it. The
# first five reports that ever arrived all had "ips": null and
# "provider": "auto" - sent at startup, before a model was loaded or a frame
# was processed - which is every number about performance missing from the
# data collected to answer questions about performance.
_measured = {"ips": [], "provider": ""}


def note_ips(rate):
    """One inferences-per-second reading from the loop."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return
    if rate <= 0:
        return
    samples = _measured["ips"]
    samples.append(rate)
    # A bounded window: the interesting figure is what the machine does when
    # it is warm, not an average dragged down by the first seconds of a run.
    if len(samples) > 600:
        del samples[:len(samples) - 600]


def note_provider(name):
    """Which execution provider actually loaded, not which was configured."""
    if name:
        _measured["provider"] = str(name).replace("ExecutionProvider", "").lower()


def measured_ips():
    samples = _measured["ips"]
    if not samples:
        return None
    ordered = sorted(samples)
    # Median, not mean: one stall while the emulator hiccups should not decide
    # what this machine is reported as capable of.
    return round(ordered[len(ordered) // 2], 1)


def measured_provider():
    return _measured["provider"]


VERSION_STAMP_FILE = ".vvok_version"


def _build_stamp():
    """The commit the updater last installed, short, or blank.

    Only exists on an install the auto-updater has written to. A source
    checkout or a freshly unpacked exe has no commit to name, and a made-up
    one would be worse than none.
    """
    try:
        stamp = resolve_project_path(VERSION_STAMP_FILE).read_text(
            encoding="utf-8").strip()
    except OSError:
        return ""
    # The updater writes a full SHA; anything else in there is a marker like
    # "exe:first", which is not a build and should not pretend to be one.
    return stamp[:12] if len(stamp) >= 40 and stamp.isalnum() else ""


def _win_rate(wins, total):
    return round(wins / total * 100, 1) if total else None


def collect(profile=None, provider="", version="", ips=None, build=""):
    """Build the report. Aggregate numbers only - see the module docstring."""
    if profile is None:
        profile = {}

    brawlers = []
    for entry in (profile.get("brawlers") or [])[:20]:
        played = int(entry.get("matches") or 0)
        # Under ten matches a win rate is noise, and the point of sending this
        # is to compare brawlers, not to publish coin flips.
        if played < 10:
            continue
        brawlers.append({
            "brawler": entry.get("name"),
            "matches": played,
            "win_rate": _win_rate(int(entry.get("wins") or 0), played),
            "net_per_match": entry.get("net_per_match"),
        })

    playstyles = []
    for entry in (profile.get("playstyles") or [])[:10]:
        played = int(entry.get("matches") or 0)
        if played < 10:
            continue
        playstyles.append({
            "playstyle": entry.get("name"),
            "matches": played,
            "win_rate": _win_rate(int(entry.get("wins") or 0), played),
        })

    return {
        "id": install_id(),
        # Resolved here, not taken from whoever called. Two callers used to
        # supply this field and they supplied different things: the bot sent
        # the commit the updater last installed, the panel sent the release
        # number. Half the reports read "0.8.14" and half read "8cda3f6c7d40",
        # so the one column the whole point rests on - which version is this -
        # could not be grouped at all. A caller may still override it, but it
        # no longer has to, and by default cannot get it wrong.
        "version": version or PYLA_VERSION,
        # And the exact build alongside it, because a release number cannot
        # tell two commits apart and that is what a bug report needs. Blank
        # for an install the updater has never touched.
        "build": build or _build_stamp(),
        "os": platform.system(),
        "python": platform.python_version(),
        # Measured beats configured: "auto" says what the config asked for,
        # which is not the same as what onnxruntime ended up running.
        "provider": measured_provider() or provider,
        "ips": round(float(ips), 1) if ips else measured_ips(),
        "matches": int(profile.get("matches") or 0),
        "win_rate": profile.get("win_rate"),
        "trophies_net": profile.get("trophies_net"),
        "days_active": profile.get("days_active"),
        "sessions": profile.get("sessions"),
        "brawlers": brawlers,
        "playstyles": playstyles,
    }


def _post(report):
    payload = json.dumps(report, ensure_ascii=False)
    if FORM_FIELD:
        data = urllib.parse.urlencode({FORM_FIELD: payload}).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    else:
        data = payload.encode("utf-8")
        headers = {"Content-Type": "application/json"}
    headers["User-Agent"] = "VvokAI"

    request = urllib.request.Request(ENDPOINT, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=20) as response:
        return 200 <= response.status < 400


def _stamp_path():
    return resolve_project_path(".vvok_stats_sent")


def _due():
    try:
        last = float(_stamp_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return time.time() - last >= MIN_INTERVAL


def _mark_sent():
    try:
        _stamp_path().write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def send(profile=None, provider="", version="", ips=None, force=False,
         build=""):
    """Report, on a thread, if switched on and not too recent. Never raises."""
    if not enabled() or (not force and not _due()):
        return False

    def work():
        try:
            report = collect(profile, provider, version, ips, build)
            if _post(report):
                _mark_sent()
        except Exception:
            # Nothing here is worth a line in somebody's log, let alone a
            # crash. If it did not go, it goes next time or it does not.
            pass

    threading.Thread(target=work, daemon=True, name="vvok-stats").start()
    return True
