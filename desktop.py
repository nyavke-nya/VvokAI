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

import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# The modules live in src/ rather than loose in the project root. Their names
# are unchanged - this only tells Python where to find them, so every
# `from utils import ...` in the codebase still reads the same.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

# The window title and what the taskbar calls it.
APP_NAME = "VvokAI"

# Everything the bot prints goes here as well as to the console. It printed a
# great deal and all of it was useful - which brawler it picked, why it gave
# up on one, what the state checker saw - and none of that survives being
# started from a window with no console attached. A log nobody can find is the
# same as no log when somebody is asking for help in Discord.
from logging_tee import LOG_NAME, start_logging  # noqa: E402

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


# Addresses that are the panel itself. Everything else a link points at
# belongs in the user's own browser, not in this window.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def is_external_link(scheme, host):
    """Whether this address should be handed to the system browser.

    Split out from the Qt classes below so the rule can be read and tested on
    its own. Only http and https: a link the panel makes to a blob, a data URL
    or a file of its own is not somebody choosing to leave, and handing those
    to the operating system does nothing useful and occasionally something
    strange.
    """
    return scheme in ("http", "https") and host not in LOCAL_HOSTS


def main():
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QDesktopServices, QIcon
    from PySide6.QtWidgets import QApplication, QMainWindow, QLabel
    from PySide6.QtWebEngineCore import QWebEnginePage
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

    # The links out of the panel - Telegram, the donation page - carry
    # target="_blank", which asks Chromium for a second window. A plain
    # QWebEngineView cannot make one and returns nothing, so clicking them did
    # precisely nothing: no window, no browser, not even an error. In a browser
    # tab the same markup works, which is why it went unnoticed.
    #
    # Both halves below exist because a link can leave this window two ways,
    # and only one of them goes through createWindow.

    class ExternalLinkPage(QWebEnginePage):
        """Send anything that is not the panel to the system browser."""

        def acceptNavigationRequest(self, url, kind, is_main_frame):
            if is_main_frame and is_external_link(url.scheme(), url.host()):
                QDesktopServices.openUrl(url)
                # Refused on purpose. Loading it here would replace the panel
                # with a web page and leave no way back - there is no address
                # bar and no back button in this window.
                return False
            return super().acceptNavigationRequest(url, kind, is_main_frame)

    class PanelView(QWebEngineView):
        """A view that can be asked for a new window and answer sensibly."""

        def createWindow(self, _kind):
            # createWindow is not told where the link goes, so this hands back
            # a throwaway view for the sole purpose of being told a moment
            # later, on urlChanged. It is never shown and never loads anything.
            catcher = QWebEngineView(self)
            catcher.hide()

            def opened(url):
                QDesktopServices.openUrl(url)
                catcher.stop()
                catcher.deleteLater()

            catcher.urlChanged.connect(opened)
            return catcher

    view = PanelView()
    view.setPage(ExternalLinkPage(view))
    view.setUrl(QUrl(f"http://127.0.0.1:{port}/"))
    window.setCentralWidget(view)
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
