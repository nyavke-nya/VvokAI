"""What belongs to the account, and what belongs to the person.

The player tag is per account: it says which Brawl Stars profile this one
plays, and two accounts are never the same player. The developer-portal
credentials are the opposite - one key, issued to one developer login for one
IP address, that answers questions about any tag.

Getting it backwards both ways is what these cover. Every account was seeded
with the owner's own tag and then could not be cleared of it, so all of them
resynced against the first account's profile; and the key was copied per
account, which matters more than it looks because reissuing one revokes every
key this bot made - so four copies would delete each other.
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

import toml

from _harness import Failures, read_source

sys.path.insert(0, "src")
import utils  # noqa: E402

report = Failures("account identity")


report.section("a tag box that can actually be emptied")
_app = read_source("static/js/app.js")
report.check("an empty field no longer answers '#'",
             'return cleanTag ? `#${cleanTag}` : "";' in _app, True)
report.check("and neither does the handler bound to typing",
             'if (!text) return "";' in _app, True)
# The input event rewrote the box on every keystroke, so "#" went straight back
# in as the last character was deleted. That is the whole "can't delete it" bug.
report.check("nothing puts the prefix back on an empty box",
             '"#";' in _app.split("function formatPlayerTagInput")[1][:400], False)

report.section("and a tag made of punctuation is no tag")
for value, want in (("#", ""), ("", ""), ("  #  ", ""), ("%23ABC", "ABC"),
                    ("#299JJ2GJ8", "299JJ2GJ8"), (" 299JJ2GJ8 ", "299JJ2GJ8"),
                    (None, "")):
    report.check(f"clean_player_tag({value!r})", utils.clean_player_tag(value), want)

_stage = read_source("stage_manager.py")
report.check("the bot decides it has a tag with the same rule",
             "clean_player_tag(" in _stage, True)
_utils_src = read_source("utils.py")
report.check("so does the startup resync",
             "clean_player_tag(\n        load_toml_as_dict" in _utils_src
             or "clean_player_tag(" in _utils_src.split("def resync")[-1][:400],
             True)


report.section("the API key is the owner's, and there is one of it")
_instances = read_source("webui/instances.py")
_blanks = _instances.split("_IDENTITY_BLANKS = {")[1].split("}")[0]
report.check("a new account is seeded without a player tag",
             "player_tag" in _blanks, True)
for key in ("brawl_api_token", "brawl_api_email", "brawl_api_password"):
    report.check(f"but keeps the shared {key}", key in _blanks, False)
report.check("and is not made to log in again either",
             "login.toml" in _blanks, False)

report.check("the shared keys are declared in one place",
             set(utils._SHARED_CFG_KEYS["general_config.toml"]),
             {"brawl_api_token", "brawl_api_email", "brawl_api_password",
              "_brawl_api_token_ip"})
report.check("the player tag is deliberately not one of them",
             "player_tag" in utils._SHARED_CFG_KEYS["general_config.toml"], False)


report.section("an account reads and writes the one shared copy")


def scoped(body):
    """Run body(root, instance_cfg) with utils scoped to a throwaway account."""
    root = Path(tempfile.mkdtemp(prefix="vvok-identity"))
    saved = (utils.PROJECT_ROOT, utils._CFG_DIR_ENV, dict(utils.cached_toml))
    try:
        shared = root / "cfg"
        shared.mkdir()
        inst = root / "instances" / "acc1" / "cfg"
        inst.mkdir(parents=True)
        for target, data in ((shared / "general_config.toml",
                              {"brawl_api_token": "SHARED", "brawl_api_email": "me@x",
                               "player_tag": "#OWNER", "run_for_minutes": 10}),
                             (inst / "general_config.toml",
                              {"brawl_api_token": "STALE", "brawl_api_email": "stale@x",
                               "player_tag": "", "run_for_minutes": 99}),
                             (shared / "login.toml", {"key": "LICENCE"}),
                             (inst / "login.toml", {"key": ""})):
            with io.open(target, "w", encoding="utf-8") as handle:
                toml.dump(data, handle)

        utils.PROJECT_ROOT = root
        utils._CFG_DIR_ENV = str(inst)
        utils.cached_toml.clear()
        return body(root, inst)
    finally:
        utils.PROJECT_ROOT, utils._CFG_DIR_ENV, cache = saved
        utils.cached_toml.clear()
        utils.cached_toml.update(cache)
        shutil.rmtree(root, ignore_errors=True)


def _read_back(path, key):
    with io.open(path, encoding="utf-8") as handle:
        return toml.load(handle).get(key)


def _sharing(root, inst):
    seen = utils.load_toml_as_dict("cfg/general_config.toml")
    report.check("the token comes from the shared cfg, not the stale copy",
                 seen["brawl_api_token"], "SHARED")
    report.check("so does the portal email", seen["brawl_api_email"], "me@x")
    report.check("the licence key is shared too",
                 utils.load_toml_as_dict("cfg/login.toml")["key"], "LICENCE")
    report.check("but the tag is the account's own", seen["player_tag"], "")
    report.check("and so is everything else", seen["run_for_minutes"], 99)

    # A key reissued inside one account has to reach the others, or the three
    # that were not looking keep a key that has just been revoked.
    seen["brawl_api_token"] = "REISSUED"
    seen["player_tag"] = "#ACC1"
    seen["run_for_minutes"] = 5
    utils.save_dict_as_toml(seen, "cfg/general_config.toml")

    report.check("a reissued token lands in the shared cfg",
                 _read_back(root / "cfg" / "general_config.toml", "brawl_api_token"),
                 "REISSUED")
    report.check("without touching the owner's tag there",
                 _read_back(root / "cfg" / "general_config.toml", "player_tag"),
                 "#OWNER")
    report.check("the account's own tag stays in the account",
                 _read_back(inst / "general_config.toml", "player_tag"), "#ACC1")
    report.check("and so do its own settings",
                 _read_back(inst / "general_config.toml", "run_for_minutes"), 5)

    utils.cached_toml.clear()
    report.check("which is what the account reads next time",
                 utils.load_toml_as_dict("cfg/general_config.toml")["brawl_api_token"],
                 "REISSUED")


scoped(_sharing)


report.section("and a single install is left exactly as it was")
_root = Path(tempfile.mkdtemp(prefix="vvok-single"))
_saved = (utils.PROJECT_ROOT, utils._CFG_DIR_ENV, dict(utils.cached_toml))
try:
    (_root / "cfg").mkdir()
    with io.open(_root / "cfg" / "general_config.toml", "w", encoding="utf-8") as handle:
        toml.dump({"brawl_api_token": "ONLY", "player_tag": "#ME"}, handle)
    utils.PROJECT_ROOT = _root
    utils._CFG_DIR_ENV = None
    utils.cached_toml.clear()
    _cfg = utils.load_toml_as_dict("cfg/general_config.toml")
    _cfg["brawl_api_token"] = "CHANGED"
    utils.save_dict_as_toml(_cfg, "cfg/general_config.toml")
    report.check("no overlay, no second file, one config",
                 _read_back(_root / "cfg" / "general_config.toml", "brawl_api_token"),
                 "CHANGED")
    report.check("and nothing else was created",
                 sorted(p.name for p in (_root / "cfg").iterdir()),
                 ["general_config.toml"])
finally:
    utils.PROJECT_ROOT, utils._CFG_DIR_ENV, _cache = _saved
    utils.cached_toml.clear()
    utils.cached_toml.update(_cache)
    shutil.rmtree(_root, ignore_errors=True)


report.section("accounts that already inherited the tag are freed of it")
import webui.instances as _inst_mod  # noqa: E402

_root = Path(tempfile.mkdtemp(prefix="vvok-migrate"))
_saved = (utils.PROJECT_ROOT, utils._CFG_DIR_ENV, dict(utils.cached_toml))
try:
    (_root / "cfg").mkdir()
    io.open(_root / "cfg" / "general_config.toml", "w", encoding="utf-8").write(
        'player_tag = "#299JJ2GJ8"\nbrawl_api_token = "SHARED"\n')
    for name, tag in (("copied", '"#299JJ2GJ8"'), ("its_own", '"#OTHER1"'),
                      ("already_blank", '""')):
        cfg = _root / "instances" / name / "cfg"
        cfg.mkdir(parents=True)
        io.open(cfg / "general_config.toml", "w", encoding="utf-8").write(
            f'# a comment that must survive\nplayer_tag = {tag}\n'
            f'brawl_api_token = "SHARED"\n')

    utils.PROJECT_ROOT = _root
    utils._CFG_DIR_ENV = None
    utils.cached_toml.clear()
    _inst_mod.InstanceManager()._unshare_inherited_tags()

    def _tag(name):
        return _inst_mod._tag_in(_root / "instances" / name / "cfg" / "general_config.toml")

    report.check("a tag that could only have been copied is cleared",
                 _tag("copied"), "")
    report.check("a tag somebody set for that account is left alone",
                 _tag("its_own"), "OTHER1")
    report.check("one already empty stays empty", _tag("already_blank"), "")
    _text = io.open(_root / "instances" / "copied" / "cfg" / "general_config.toml",
                    encoding="utf-8").read()
    report.check("the shared token is not touched on the way past",
                 'brawl_api_token = "SHARED"' in _text, True)
    report.check("and the comments in the file survive",
                 "# a comment that must survive" in _text, True)
finally:
    utils.PROJECT_ROOT, utils._CFG_DIR_ENV, _cache = _saved
    utils.cached_toml.clear()
    utils.cached_toml.update(_cache)
    shutil.rmtree(_root, ignore_errors=True)


report.section("the panel stores an emptied box as empty")
_root = Path(tempfile.mkdtemp(prefix="vvok-panel"))
_saved = (utils.PROJECT_ROOT, utils._CFG_DIR_ENV, dict(utils.cached_toml))
try:
    shutil.copytree("cfg", _root / "cfg")
    utils.PROJECT_ROOT = _root
    utils._CFG_DIR_ENV = None
    utils.cached_toml.clear()
    from webui.services import WebDataService  # noqa: E402

    service = WebDataService.__new__(WebDataService)
    service.update_settings("general", {"player_tag": "#ABC123"})
    report.check("a real tag is kept, with its prefix",
                 service.get_settings_payload("general")["player_tag"], "#ABC123")

    # What an older panel sends when the box is emptied.
    service.update_settings("general", {"player_tag": "#"})
    report.check("a bare '#' is stored as no tag at all",
                 _read_back(_root / "cfg" / "general_config.toml", "player_tag"), "")
    report.check("and the box comes back empty",
                 service.get_settings_payload("general")["player_tag"], "")

    service.update_settings("general", {"player_tag": "  299JJ2GJ8  "})
    report.check("a tag pasted without its # still saves",
                 _read_back(_root / "cfg" / "general_config.toml", "player_tag"),
                 "#299JJ2GJ8")
    service.update_settings("general", {"player_tag": ""})
    report.check("and clearing it clears it",
                 _read_back(_root / "cfg" / "general_config.toml", "player_tag"), "")
finally:
    utils.PROJECT_ROOT, utils._CFG_DIR_ENV, _cache = _saved
    utils.cached_toml.clear()
    utils.cached_toml.update(_cache)
    shutil.rmtree(_root, ignore_errors=True)

sys.exit(report.finish())
