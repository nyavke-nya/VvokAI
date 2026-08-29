"""Send everything printed to the console AND to a log file.

The desktop application did this and main.py did not, so which one you
launched decided whether there was anything to send when something went wrong.
Worse, the exe opens a console that is easy to lose behind the app window, and
several people reported never having seen the log at all - it existed, they
just had no way to look at it.

Both entry points call this now, and the panel serves the file back, so the
log is in the same place however the bot was started.
"""

import sys
import time

LOG_NAME = "vvokai_log.txt"


class Tee:
    """Write to the console and to the log at once.

    Both, not one or the other: somebody running from source is watching the
    console and would lose it, and somebody running the exe may have no console
    they can find and would otherwise have nothing at all.
    """

    def __init__(self, stream, handle):
        self.stream = stream
        self.handle = handle

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError):
                pass
        try:
            self.handle.write(text)
            # Unbuffered on purpose. The interesting case is a crash, and a
            # buffered line is one that never reached the file.
            self.handle.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        for target in (self.stream, self.handle):
            try:
                if target is not None:
                    target.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        try:
            return bool(self.stream) and self.stream.isatty()
        except Exception:
            return False


def start_logging():
    """Point stdout and stderr at the log file as well. Returns its path.

    Safe to call twice - the second call does nothing rather than wrapping a
    Tee in another Tee, which would write every line to the file twice.
    """
    from utils import resolve_project_path

    if isinstance(sys.stdout, Tee):
        return resolve_project_path(LOG_NAME)

    path = resolve_project_path(LOG_NAME)
    try:
        handle = open(path, "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None

    handle.write(f"VvokAI log - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    sys.stdout = Tee(sys.stdout, handle)
    sys.stderr = Tee(sys.stderr, handle)
    return path


def read_log(lines=400):
    """The last `lines` lines of the log, oldest first. Never raises."""
    from utils import resolve_project_path

    path = resolve_project_path(LOG_NAME)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().splitlines()[-lines:]
    except OSError:
        return []
