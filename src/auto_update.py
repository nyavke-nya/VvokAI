"""Pick up updates while the bot is running, without losing a match.

The updater has always run once, at startup, from start_pyla.bat or the exe.
That is fine for somebody who restarts often and no use at all to somebody
pushing trophies for two days straight - which is what the bot is for.

Four things make this safe rather than merely automatic.

It asks before it acts. `updater.py --check` compares one commit hash and
touches nothing, so the hourly poll cannot leave a half-written tree behind if
the connection drops, and a poll that finds nothing costs the bot nothing at
all - no stop, no restart, no interruption of any kind.

It stops the way the Stop button stops. The bot's loop only considers stopping
when it is in the lobby, so requesting a stop finishes the match in progress
rather than abandoning it. That behaviour already existed for the schedule;
this reuses it instead of inventing a second kind of stop.

It restarts through the launcher. A Python process cannot reload the modules
it is already running, so the update is picked up by exiting with the code
start_pyla.bat and launcher.py already understand as "start me again".

And it puts the bot back to work. There is no auto-start in this project - the
panel waits for somebody to press Start - so an update that restarted the
process would otherwise leave a machine that was farming overnight sitting
idle in the lobby until morning. A marker written before exiting says "you
were pushing, carry on", and is deleted as soon as it is read so a deliberate
stop is never overridden.
"""

import os
import subprocess
import sys
import threading
import time

from utils import load_toml_as_dict, resolve_project_path

# What the updater returns when it has something for us, and what this process
# exits with to ask the launcher for a restart. One number, one meaning.
RESTART_CODE = 10

DEFAULT_EVERY_MINUTES = 60

# Written before an update restart, read once on the way back up.
RESUME_MARKER = ".vvok_resume"

# How long to wait for a match to finish once a stop has been requested. Long,
# because the thing being waited for is a game of Brawl Stars and the cost of
# giving up early is abandoning it. If it somehow has not stopped by then the
# update simply waits for the next poll.
STOP_TIMEOUT = 20 * 60


def every_minutes():
    """How often to look, in minutes. 0 or less means never."""
    try:
        raw = load_toml_as_dict("cfg/general_config.toml").get(
            "auto_update_every_minutes", DEFAULT_EVERY_MINUTES)
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_EVERY_MINUTES


def _run_updater(*args):
    """Run tools/updater.py and return its exit code, or None if it could not."""
    script = resolve_project_path("tools", "updater.py")
    if not script.exists():
        return None
    try:
        return subprocess.call([sys.executable, str(script), *args],
                               cwd=str(resolve_project_path()))
    except OSError:
        return None


def update_available():
    return _run_updater("--check") == RESTART_CODE


def apply_update():
    """Fetch and write the update. True when something actually changed."""
    return _run_updater() == RESTART_CODE


def mark_resume():
    try:
        resolve_project_path(RESUME_MARKER).write_text("1", encoding="utf-8")
    except OSError:
        pass  # Worst case the bot comes back up idle, which is recoverable.


def take_resume_marker():
    """Whether this start follows an update restart. Consumes the marker."""
    path = resolve_project_path(RESUME_MARKER)
    try:
        if not path.exists():
            return False
        path.unlink()
        return True
    except OSError:
        return False


class AutoUpdater:
    """Polls for updates and restarts the process when one arrives."""

    def __init__(self, runtime_manager, discord_bot=None, restart=None):
        self.runtime_manager = runtime_manager
        self.discord_bot = discord_bot
        # Injected so the tests can watch it rather than end their own process.
        self.restart = restart or self._exit_for_restart
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        minutes = every_minutes()
        if minutes <= 0:
            print("Auto-update while running is off (auto_update_every_minutes = 0).")
            return None
        self.thread = threading.Thread(target=self._loop, args=(minutes * 60,),
                                       daemon=True, name="vvok-auto-update")
        self.thread.start()
        print(f"Auto-update: checking every {minutes:g} minutes.")
        return self.thread

    def stop(self):
        self.stop_event.set()

    def _loop(self, interval):
        while not self.stop_event.wait(interval):
            try:
                self.tick()
            except Exception as exc:
                # An update that fails must never take the bot down with it.
                print(f"Auto-update check failed ({exc}); trying again later.")

    def tick(self):
        """One poll. True when an update was applied and a restart asked for.

        Nothing at all happens without an update: the bot is not stopped, the
        process is not restarted, and no file is touched.
        """
        if not update_available():
            return False

        print("Auto-update: a new version is available.")
        was_running = bool(self.runtime_manager.get_status().get("is_running"))

        if not self._settle():
            print("Auto-update: the bot is still busy, leaving it for now.")
            return False

        if not apply_update():
            # Nothing was written after all - a dropped connection, a bad
            # archive. The bot was stopped to make room for it, so put it back
            # rather than leaving a farming machine idle over a failed update.
            print("Auto-update: nothing was written after all.")
            if was_running:
                self._resume_now()
            return False

        if was_running:
            mark_resume()
        print("Auto-update: installed. Restarting to pick it up.")
        self.restart()
        return True

    def _settle(self):
        """Stop the bot if it is running, and wait for the match to finish."""
        status = self.runtime_manager.get_status()
        if not status.get("is_running"):
            return True

        print("Auto-update: finishing the current match before restarting.")
        try:
            self.runtime_manager.stop()
        except Exception as exc:
            print(f"Auto-update: could not ask the bot to stop ({exc}).")
            return False

        deadline = time.time() + STOP_TIMEOUT
        while time.time() < deadline:
            if self.stop_event.wait(2):
                return False
            if not self.runtime_manager.get_status().get("is_running"):
                return True
        return False

    def _resume_now(self):
        """Put the bot back to work in this process, no restart involved."""
        try:
            self.runtime_manager.start_current_queue(self.discord_bot)
            print("Auto-update: the bot has been started again.")
        except Exception as exc:
            print(f"Auto-update: could not start the bot again ({exc}).")

    @staticmethod
    def _exit_for_restart():
        # os._exit rather than sys.exit: this runs on a daemon thread, where a
        # SystemExit would be swallowed and the process would carry on with the
        # bot stopped and an update half-adopted. Everything worth flushing has
        # already been written - the queue and the history are saved as they
        # change, not at shutdown.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(RESTART_CODE)
