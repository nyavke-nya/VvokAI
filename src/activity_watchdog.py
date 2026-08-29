"""Notice when nothing is happening at all, and do something about it.

The bot already restarts the game when it stops seeing players, and reconnects
scrcpy when the feed goes stale. Neither covers the case people actually hit:
the picture is arriving, the models are running, and the screen has simply
stopped changing - Brawl Stars wedged on a loading screen, a connection dialog
behind the game, the emulator's renderer dead.

What makes that case awkward is that the bot's own opinion is no help. It can
be perfectly convinced a match is in progress, because the last frame it saw
looked like one and that frame is still what it is being handed. So this does
not ask the bot what is going on. It compares the pixels.

Two steps, because they have different costs. Three minutes of a frozen screen
is Brawl Stars, and restarting the app is cheap. Ten minutes means the restart
did not help, which points at the emulator underneath it, and that is a much
heavier thing to do - so it needs to have been given the cheap answer first.
"""

import os
import subprocess
import time

import cv2
import numpy as np

# The picture is compared at this size. Small enough that the comparison costs
# nothing, big enough that a health bar moving still registers.
SIGNATURE_SIZE = (48, 27)

# Mean absolute difference, 0-255, above which two frames are "different".
# Video compression alone moves a still image by well under one level; a game
# that is actually running moves it by tens.
CHANGE_THRESHOLD = 1.5

DEFAULT_GAME_AFTER = 180.0
DEFAULT_EMULATOR_AFTER = 600.0

# Only these are ever killed, and only when we already know how to start them
# again. An emulator we cannot relaunch is one to leave alone: a bot that
# cannot play is a bad afternoon, and a machine with the emulator killed and no
# way back is a worse one.
KNOWN_EMULATORS = {
    "MuMuPlayer.exe", "MuMuNxDevice.exe", "MuMuVMMHeadless.exe",
    "HD-Player.exe", "Bluestacks.exe", "BlueStacks.exe",
    "dnplayer.exe", "LdVBoxHeadless.exe",
    "Nox.exe", "NoxVMHandle.exe",
    "MEmu.exe", "MEmuHeadless.exe",
}


def frame_signature(frame):
    """A tiny grayscale thumbnail of the frame, for comparing against the last."""
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    small = cv2.resize(frame, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA)
    if small.ndim == 3:
        small = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    return small.astype(np.float32)


def frames_differ(a, b, threshold=CHANGE_THRESHOLD):
    if a is None or b is None or a.shape != b.shape:
        return True
    return float(np.abs(a - b).mean()) >= threshold


def running_emulators():
    """(name, pid, command line) for every emulator process we recognise.

    Windows only, and deliberately not psutil - it is not a dependency of this
    project, and adding one for a watchdog would mean the watchdog silently
    not existing on every install that has not reinstalled since.
    """
    if os.name != "nt":
        return []

    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object Name,ProcessId,CommandLine | "
        "ForEach-Object { \"$($_.Name)`t$($_.ProcessId)`t$($_.CommandLine)\" }"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    found = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        name, pid, command = parts[0].strip(), parts[1].strip(), "\t".join(parts[2:]).strip()
        if name in KNOWN_EMULATORS and pid.isdigit():
            found.append((name, int(pid), command))
    return found


class ActivityWatchdog:
    """Watches for a screen that has stopped changing."""

    def __init__(self, restart_game, restart_emulator=None,
                 game_after=DEFAULT_GAME_AFTER,
                 emulator_after=DEFAULT_EMULATOR_AFTER,
                 threshold=CHANGE_THRESHOLD, now=time.time):
        self.restart_game = restart_game
        self.restart_emulator = restart_emulator or restart_emulator_process
        self.game_after = float(game_after)
        self.emulator_after = float(emulator_after)
        self.threshold = float(threshold)
        self._now = now

        self._signature = None
        self._last_change = now()
        self._game_restarted_at = 0.0

    def reset(self, reason=""):
        """Start the clock again. Called after anything that changes the screen."""
        self._last_change = self._now()
        self._signature = None
        if reason:
            print(f"Activity watchdog: clock reset ({reason}).")

    def still_for(self):
        return self._now() - self._last_change

    def note(self, frame):
        """Feed the current frame. Returns what it did, if anything.

        "" when the screen is moving or the clocks have not run out, which is
        almost always.
        """
        signature = frame_signature(frame)
        if signature is None:
            return ""

        if frames_differ(self._signature, signature, self.threshold):
            self._signature = signature
            self._last_change = self._now()
            return ""

        still = self.still_for()

        # The heavy answer first, so that ten minutes does not merely restart
        # the game for the third time.
        if self.emulator_after and still >= self.emulator_after:
            print(f"Nothing on screen has changed for {still / 60:.0f} minutes, "
                  f"and restarting Brawl Stars did not help. Restarting the "
                  f"emulator.")
            self.reset()
            self._game_restarted_at = 0.0
            return "emulator" if self.restart_emulator() else "emulator-failed"

        if still >= self.game_after and (
                self._now() - self._game_restarted_at >= self.game_after):
            print(f"Nothing on screen has changed for {still / 60:.1f} minutes. "
                  f"Restarting Brawl Stars.")
            self._game_restarted_at = self._now()
            # Deliberately not resetting the stillness clock: if the restart
            # does not unfreeze anything, the ten minute rule has to keep
            # counting from when the screen actually stopped, not from here.
            self.restart_game()
            return "game"

        return ""


def restart_emulator_process():
    """Kill the emulator and start it again exactly as it was running.

    Its command line is read first, and nothing is killed unless that read
    worked: an emulator that has been shut down and cannot be started again is
    strictly worse than a frozen one, because at least a frozen one can be
    fixed by the person sitting there.
    """
    emulators = running_emulators()
    if not emulators:
        print("Activity watchdog: no emulator process recognised, so there is "
              "nothing to restart. Leaving it alone.")
        return False

    # The one with a command line, since that is the one we can start again.
    startable = [e for e in emulators if e[2]]
    if not startable:
        print("Activity watchdog: found the emulator but not how it was "
              "started, so it is not being killed.")
        return False

    name, pid, command = startable[0]
    print(f"Activity watchdog: restarting {name} (pid {pid}).")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Activity watchdog: could not stop {name} ({exc}).")
        return False

    time.sleep(5)
    try:
        subprocess.Popen(command, shell=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Activity watchdog: {name} was stopped but would not start "
              f"again ({exc}). Start it by hand.")
        return False

    print(f"Activity watchdog: {name} started again. Waiting for it to boot.")
    return True
