"""VvokAI as a desktop application rather than a browser tab.

The interface has always been a local web page: the bot serves it on a
localhost port and opens whichever browser is default. That works, and it
looks like what it is - a website - with an address bar, a tab strip, and a
browser that may be doing anything else at the same time.

This is the same interface in a real window. A QMainWindow with the page
inside it, no address bar, no tabs, its own entry in the taskbar, and closing
it closes the program.

Deliberately not a rewrite. The panel is a couple of thousand lines of HTML,
CSS and JavaScript that already work, and hand-porting them to Qt widgets
would take weeks and lose features on the way. Qt hosts the page; every button
on it is the same code that was there before.

The server still runs, on the loopback interface, because that is what the
page talks to. It is just no longer something anybody has to look at.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# The window title and what the taskbar calls it.
APP_NAME = "VvokAI"

# Everything the bot prints goes here as well as to the console. It printed a
# great deal and all of it was useful - which brawler it picked, why it gave
# up on one, what the state checker saw - and none of that survives being
# started from a window with no console attached. A log nobody can find is the
# same as no log when somebody is asking for help in Discord.
LOG_NAME = "vvokai_log.txt"

# Rewritten at each start rather than appended to. One run is what anybody
# ever wants to read, and an append-only file from a bot that prints every
# frame reaches hundreds of megabytes in a weekend.
LOG_BYTES_KEPT = 8 * 1024 * 1024

# Where the window starts. Big enough for the queue and the settings form
# side by side, small enough to fit a 1366x768 laptop.
DEFAULT_SIZE = (1280, 820)
MINIMUM_SIZE = (900, 600)

# How long to wait for Flask to answer before giving up and saying so. It
# imports torch and opencv on the way up, which on a cold disk is not quick.
SERVER_TIMEOUT = 90


class Tee:
    """Write to the console and to the log at once.

    Both, not one or the other: somebody running from source is watching the
    console and would lose it, and somebody running the exe has no console and
    would otherwise have nothing at all.
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
                target.flush()
            except (OSError, ValueError, AttributeError):
                pass

    def isatty(self):
        return getattr(self.stream, "isatty", lambda: False)()


def start_logging():
    """Point stdout and stderr at the log file as well. Returns its path."""
    from utils import resolve_project_path

    path = resolve_project_path(LOG_NAME)
    try:
        handle = open(path, "w", encoding="utf-8", errors="replace", buffering=1)
    except OSError:
        return None

    handle.write(f"VvokAI log - {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    sys.stdout = Tee(sys.stdout, handle)
    sys.stderr = Tee(sys.stderr, handle)
    return path


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(port):
    """Run the existing Flask app on a background thread.

    Bound to 127.0.0.1 rather than 0.0.0.0: in a desktop application there is
    nobody else who should be reaching this, and the panel has no login.
    """
    from main import pyla_main
    from webui import create_app

    app = create_app(pyla_main, start_discord_bot=True)

    def run():
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False,
                threaded=True)

    threading.Thread(target=run, daemon=True, name="vvok-web").start()
    return app


def wait_for_server(port, timeout=SERVER_TIMEOUT):
    """Block until the page is actually servable.

    Loading the window before Flask answers gives a Qt error page, and a user
    looking at "connection refused" has no way to know it will work in ten
    seconds. Waiting is the whole difference.
    """
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            # Anything that answers at all is up; a redirect or a 404 still
            # means Flask is listening.
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def main():
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from utils import resolve_project_path

    log_path = start_logging()
    if log_path:
        print(f"Log: {log_path}")

    port = free_port()
    serve(port)

    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)

    window = QMainWindow()
    window.setWindowTitle(APP_NAME)
    window.resize(*DEFAULT_SIZE)
    window.setMinimumSize(*MINIMUM_SIZE)

    icon = resolve_project_path("images", "vvokai.png")
    if icon.exists():
        window.setWindowIcon(QIcon(str(icon)))

    # Something to look at while torch and opencv load, instead of an empty
    # frame that looks like the program has hung.
    splash = QLabel("Starting VvokAI...")
    splash.setAlignment(Qt.AlignCenter)
    splash.setStyleSheet("background:#050506; color:#9a9aa2; font-size:15px;")
    window.setCentralWidget(splash)
    window.show()
    application.processEvents()

    if not wait_for_server(port):
        splash.setText("The interface did not start.\n\n"
                       f"The reason is in {log_path or LOG_NAME}.")
        return application.exec()

    view = QWebEngineView()
    view.setUrl(QUrl(f"http://127.0.0.1:{port}/"))
    window.setCentralWidget(view)
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
