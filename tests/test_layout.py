"""What happens to somebody who already had VvokAI installed.

The modules moved into src/. For a fresh download that is simply the layout.
For the several thousand installs that already exist it is a migration, and
the updater copies the incoming tree over the old one without ever noticing
that a file has stopped existing - so without help every one of them would
end up with src/play.py AND a stale play.py in the root, forever.

These tests build a directory that looks like an old install, run the real
update over it, and check three things: the leftovers are gone, nothing that
belongs to the person is touched, and the result actually imports and runs.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _harness import Failures

sys.path.insert(0, "tools")
import updater  # noqa: E402

report = Failures("layout migration")

REPO = Path(__file__).resolve().parent.parent

# What an install looked like before the move: modules loose in the root.
OLD_ROOT_MODULES = [
    "brawl_api.py", "brawl_token.py", "debug_view.py", "detect.py",
    "discord_bot.py", "lobby_automation.py", "play.py", "profile_stats.py",
    "remote_control.py", "schedule_control.py", "stage_manager.py",
    "state_finder.py", "telegram_bot.py", "time_management.py",
    "trophy_observer.py", "utils.py", "window_controller.py",
]
OLD_ROOT_PACKAGES = ["dodge", "webui", "scrcpy"]


def build_old_install(root):
    """A directory shaped like an install from before the move."""
    for name in OLD_ROOT_MODULES:
        (root / name).write_text(f"# stale {name}\nMARKER = 'old'\n", encoding="utf-8")
    for name in OLD_ROOT_PACKAGES:
        (root / name).mkdir()
        (root / name / "__init__.py").write_text("MARKER = 'old'\n", encoding="utf-8")
    (root / "api").mkdir()
    (root / "api" / "api.py").write_text("MARKER = 'old'\n", encoding="utf-8")
    (root / "api" / "assets" / "brawler_icons").mkdir(parents=True)
    (root / "api" / "assets" / "brawler_icons" / "shelly.png").write_bytes(b"old icon")
    for name in ("images", "static", "templates"):
        (root / name).mkdir()
        (root / name / "a_file").write_bytes(b"old asset")
    (root / "DODGE.md").write_text("old writeup", encoding="utf-8")
    (root / "build_exe.bat").write_text("old build script", encoding="utf-8")
    (root / "build_nuitka.bat").write_text("old build script", encoding="utf-8")
    (root / "setup.py").write_text("old packaging stub", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "utils.cpython-311.pyc").write_bytes(b"stale bytecode")

    # Things that are the person's, not ours.
    (root / "cfg").mkdir()
    (root / "cfg" / "general_config.toml").write_text(
        'brawl_api_token = "secret"\nplayer_tag = "#ABC123"\n', encoding="utf-8")
    (root / "cfg" / "match_history.csv").write_text("their,match,history\n", encoding="utf-8")
    (root / "latest_brawler_data.json").write_text('[{"brawler": "shelly"}]', encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "mainInGameModel.onnx").write_bytes(b"a 10 MB download")
    (root / "venv").mkdir()
    (root / "venv" / "pyvenv.cfg").write_text("theirs", encoding="utf-8")


def build_incoming(source):
    """The new tree, as the updater would have unpacked it."""
    (source / "src").mkdir()
    for name in OLD_ROOT_MODULES:
        (source / "src" / name).write_text(
            f"# {name}\nMARKER = 'new'\n", encoding="utf-8")
    for name in OLD_ROOT_PACKAGES:
        (source / "src" / name).mkdir()
        (source / "src" / name / "__init__.py").write_text("MARKER = 'new'\n", encoding="utf-8")
    (source / "src" / "api.py").write_text("MARKER = 'new'\n", encoding="utf-8")
    (source / "assets" / "brawler_icons").mkdir(parents=True)
    (source / "assets" / "brawler_icons" / "shelly.png").write_bytes(b"new icon")
    for name in ("images", "static", "templates"):
        (source / "assets" / name).mkdir(parents=True)
        (source / "assets" / name / "a_file").write_bytes(b"new asset")
    (source / "docs").mkdir()
    (source / "docs" / "DODGE.md").write_text("new writeup", encoding="utf-8")
    (source / "main.py").write_text("MARKER = 'new'\n", encoding="utf-8")
    (source / "tools").mkdir()
    (source / "tools" / "installer.py").write_text("MARKER = 'new'\n", encoding="utf-8")
    (source / "tools" / "build_exe.bat").write_text("new build script", encoding="utf-8")
    # The archive carries a default config; the person's own must win.
    (source / "cfg").mkdir()
    (source / "cfg" / "general_config.toml").write_text(
        'brawl_api_token = ""\nplayer_tag = ""\n', encoding="utf-8")


def run_update():
    """One full update over an old install. Returns the root it produced."""
    holding = Path(tempfile.mkdtemp(prefix="vvok-layout-"))
    root, source = holding / "install", holding / "incoming"
    root.mkdir()
    source.mkdir()
    build_old_install(root)
    build_incoming(source)

    updater.ROOT = root
    updater.BACKUP = root / "backup_before_update"
    updater.say = lambda message: None
    updater.apply(source)
    updater.retire()
    return holding, root


_holding, _root = run_update()

report.section("the leftovers go")
_left = [name for name in OLD_ROOT_MODULES if (_root / name).exists()]
report.check("no stale module is left loose in the root", _left, [])
report.check("nor a stale package",
             [n for n in OLD_ROOT_PACKAGES if (_root / n).exists()], [])
report.check("nor the old api module", (_root / "api" / "api.py").exists(), False)
report.check("nor bytecode compiled from any of them",
             (_root / "__pycache__").exists(), False)

report.section("and the new layout is there instead")
report.check("src/ arrived", (_root / "src").is_dir(), True)
report.check("with the modules in it",
             all((_root / "src" / name).exists() for name in OLD_ROOT_MODULES), True)
report.check("and they are the new copies, not the old ones",
             "new" in (_root / "src" / "play.py").read_text(encoding="utf-8"), True)
report.check("the packages came too",
             all((_root / "src" / name / "__init__.py").exists()
                 for name in OLD_ROOT_PACKAGES), True)

report.section("nothing of theirs was touched")
_config = (_root / "cfg" / "general_config.toml").read_text(encoding="utf-8")
report.check("the API token survived the update", 'secret' in _config, True)
report.check("so did the player tag", "#ABC123" in _config, True)
report.check("the match history is untouched",
             (_root / "cfg" / "match_history.csv").read_text(encoding="utf-8"),
             "their,match,history\n")
report.check("so is the queue",
             (_root / "latest_brawler_data.json").read_text(encoding="utf-8"),
             '[{"brawler": "shelly"}]')
report.check("the models were not re-downloaded",
             (_root / "models" / "mainInGameModel.onnx").read_bytes(), b"a 10 MB download")
report.check("the virtual environment is where it was",
             (_root / "venv" / "pyvenv.cfg").exists(), True)
report.check("the brawler icons are at their new address",
             (_root / "assets" / "brawler_icons" / "shelly.png").read_bytes(), b"new icon")
report.check("and the old api/ tree is gone entirely - both halves relocated",
             (_root / "api").exists(), False)
report.check("the other assets moved under assets/ too",
             all((_root / "assets" / name / "a_file").exists()
                 for name in ("images", "static", "templates")), True)
report.check("and none of them was left in the root",
             [n for n in ("images", "static", "templates") if (_root / n).exists()], [])
report.check("the build scripts and the write-up went with their kind",
             [n for n in ("build_exe.bat", "build_nuitka.bat", "setup.py", "DODGE.md")
              if (_root / n).exists()], [])

report.section("and everything removed is recoverable")
_backup = _root / "backup_before_update"
report.check("the stale modules were kept before deletion",
             (_backup / "play.py").read_text(encoding="utf-8").strip().endswith("'old'"), True)
report.check("and so were the stale packages",
             (_backup / "dodge" / "__init__.py").exists(), True)

shutil.rmtree(_holding, ignore_errors=True)


report.section("a second update over an already-migrated install is a no-op")
_holding2 = Path(tempfile.mkdtemp(prefix="vvok-layout2-"))
_root2, _source2 = _holding2 / "install", _holding2 / "incoming"
_root2.mkdir()
_source2.mkdir()
build_incoming(_source2)          # already the new shape, nothing stale
(_root2 / "cfg").mkdir()
(_root2 / "cfg" / "general_config.toml").write_text('player_tag = "#KEEP"\n', encoding="utf-8")
updater.ROOT = _root2
updater.BACKUP = _root2 / "backup_before_update"
updater.apply(_source2)
report.check("there is nothing left to retire", updater.retire(), 0)
report.check("and the config is still theirs",
             "#KEEP" in (_root2 / "cfg" / "general_config.toml").read_text(encoding="utf-8"), True)
shutil.rmtree(_holding2, ignore_errors=True)


report.section("the real tree imports from its new home")
# Not a claim about the file list - actually start Python in a copy of the
# repository and import the modules the bot imports first.
_probe = subprocess.run(
    [sys.executable, "-c",
     "import os, sys; sys.path.insert(0, os.path.join(os.getcwd(), 'src')); "
     "import utils, detect, trophy_observer, profile_stats, brawl_api; "
     "import webui, dodge.config, scrcpy; "
     "print('imported', utils.PROJECT_ROOT.name)"],
    cwd=REPO, capture_output=True, text=True, timeout=180,
)
report.check("every moved module imports by its plain name",
             _probe.returncode, 0)
if _probe.returncode:
    report.check("  (import error)", _probe.stderr.strip().splitlines()[-1:], [])

report.check("the entry points stayed in the root, where the launchers look",
             all((REPO / name).exists()
                 for name in ("main.py", "desktop.py", "launcher.py", "start_pyla.bat")), True)
report.check("and the launcher still recognises a project by them",
             (REPO / "main.py").exists() and (REPO / "tools" / "installer.py").exists(), True)

report.section("every retired path is one we actually moved")
_shipped = {path.name for path in REPO.rglob("*")
            if path.is_file() and ".git" not in path.parts and "venv" not in path.parts}
_unexplained = [rule for rule in updater.RETIRED
                if not rule.endswith("/")
                and Path(rule).name not in _shipped]
report.check("nothing is retired that did not turn up somewhere else",
             _unexplained, [])
# __pycache__ is retired to clear bytecode compiled from the old root modules,
# but Python writes it again the moment anything at the root is imported, so it
# is the one entry that legitimately comes back.
_gone = [rule for rule in updater.RETIRED
         if rule != "__pycache__/" and (REPO / rule.rstrip("/")).exists()]
report.check("and nothing retired is still sitting in this tree", _gone, [])
report.check("and no retired path is also protected",
             [r for r in updater.RETIRED if updater.protected(Path(r.rstrip("/")))], [])


report.section("and if a leftover survives anyway, src still wins")
# retire() can fail on a single file - antivirus holding it open, a permission
# it does not have - and that has to degrade to "cluttered", never to "running
# last month's code". The entry points insert src at position 0, ahead of the
# script's own directory, so a stale root copy is shadowed rather than used.
_shadow = Path(tempfile.mkdtemp(prefix="vvok-shadow-"))
(_shadow / "src").mkdir()
(_shadow / "utils.py").write_text(
    "MARKER = " + repr("stale root copy") + chr(10), encoding="utf-8")
(_shadow / "src" / "utils.py").write_text(
    "MARKER = " + repr("new src copy") + chr(10), encoding="utf-8")
(_shadow / "main.py").write_text(chr(10).join([
    "import os, sys",
    "sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),"
    " 'src'))",
    "import utils",
    "print(utils.MARKER)",
]) + chr(10), encoding="utf-8")

for _label, _cwd in (("from its own folder", _shadow), ("from elsewhere", REPO)):
    _run = subprocess.run([sys.executable, str(_shadow / "main.py")],
                          cwd=_cwd, capture_output=True, text=True, timeout=60)
    report.check("a stale root module is shadowed, " + _label,
                 _run.stdout.strip(), "new src copy")

shutil.rmtree(_shadow, ignore_errors=True)

report.check("which is why the entry points insert src at position 0",
             "sys.path.insert(0," in (REPO / "main.py").read_text(encoding="utf-8"),
             True)


report.section("every asset the panel asks for is where it now lives")
# The move broke /api/assets/support/... and nothing caught it: the suite
# reads source files and checks their text, so a route still pointing at the
# old folder reads perfectly well. Two hundred and fifty-five images on the
# page came back 404 and only opening it showed that.
#
# So: resolve what the UI actually references, on disk.
import re as _re

_app_js = (REPO / "assets" / "static" / "js" / "app.js").read_text(encoding="utf-8")
_support = sorted(set(_re.findall(r"/api/assets/support/([A-Za-z0-9_.-]+)", _app_js)))
report.check("the panel does reference support assets", len(_support) > 0, True)
_missing_support = [name for name in _support
                    if not (REPO / "assets" / "images" / name).exists()]
report.check("and every one of them resolves", _missing_support, [])

# The brawler icons, through the same helper the route uses.
sys.path.insert(0, os.path.join(REPO, "src"))
from utils import get_brawler_icon_path, load_brawlers_info  # noqa: E402

_roster = sorted(load_brawlers_info())
_no_icon = [name for name in _roster if get_brawler_icon_path(name) is None]
report.check("every brawler in the roster has an icon", _no_icon, [])
report.check("and there are as many as the game has", len(_roster) > 100, True)

# The templates and static folder Flask is pointed at.
for _name, _rel in (("the page template", "assets/templates/index.html"),
                    ("the stylesheet", "assets/static/css/vvok.css"),
                    ("the panel script", "assets/static/js/app.js"),
                    ("the translations", "assets/static/js/i18n.js")):
    report.check(_name + " is where Flask is told to look",
                 (REPO / _rel).exists(), True)

# The state templates the bot matches screens against.
from state_finder import states_path  # noqa: E402
_states = REPO / states_path.lstrip(chr(46) + chr(47))
report.check("the state templates moved with the rest",
             _states.is_dir() and any(_states.iterdir()), True)


sys.exit(report.finish())
