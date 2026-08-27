"""Serve just the panel on a fixed port, for looking at it.

The real entry points pick a free port and open a browser; that is right for
using the bot and wrong for driving the page from outside, which needs to know
where it will be.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import pyla_main
from webui import create_app

app = create_app(pyla_main, start_discord_bot=False)
app.run(host="127.0.0.1", port=5051, debug=False, use_reloader=False, threaded=True)
