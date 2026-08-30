"""Anonymous means anonymous, off means off, and failure means nothing.

The value of usage figures is that development stops being guesswork. The cost
of getting them wrong is a project that argues in its own README that it is not
malware while quietly shipping somebody's data somewhere - so the checks that
matter here are about what is NOT in a report, and about the switch really
being a switch.
"""
import json
import sys

from _harness import Failures

sys.path.insert(0, "src")
import telemetry  # noqa: E402

report = Failures("telemetry")

PROFILE = {
    "matches": 2542, "win_rate": 54.5, "trophies_net": 10313,
    "days_active": 16, "sessions": 39,
    "brawlers": [
        {"name": "nori", "matches": 269, "wins": 121, "net_per_match": 3.76},
        {"name": "shelly", "matches": 9, "wins": 9, "net_per_match": 8.0},
    ],
    "playstyles": [
        {"name": "Unified + Dodge", "matches": 2523, "wins": 1370},
        {"name": "Unified Light", "matches": 4, "wins": 4},
    ],
}


report.section("what a report contains")
_r = telemetry.collect(PROFILE, provider="tensorrt", version="0.8.14")
report.check("the totals that answer 'what should I build'",
             (_r["matches"], _r["win_rate"]), (2542, 54.5))
report.check("which provider people actually run", _r["provider"], "tensorrt")
report.check("and the version, so old installs can be told apart",
             _r["version"], "0.8.14")

report.check("per-brawler win rates", _r["brawlers"][0]["brawler"], "nori")
report.check("computed, not copied", _r["brawlers"][0]["win_rate"], 45.0)

# A brawler with nine matches has a win rate that means nothing, and sending it
# would drown the ones that do.
report.check("a brawler under ten matches is left out",
             [b["brawler"] for b in _r["brawlers"]], ["nori"])
report.check("and so is a playstyle under ten",
             [p["playstyle"] for p in _r["playstyles"]], ["Unified + Dodge"])


report.section("what a report must never contain")
_blob = json.dumps(telemetry.collect(PROFILE, provider="cuda", version="1")).lower()
for _label, _secret in (("a player tag", "#299jj2gj8"),
                        ("an api token", "eyjhbgcioijiuzi1niis"),
                        ("a windows path", "c:\\users"),
                        ("a home folder", "/users/")):
    report.check(f"no {_label}", _secret in _blob, False)

report.check("the id is random, not derived from anything checkable",
             len(_r["id"]) == 32 and all(c in "0123456789abcdef" for c in _r["id"]),
             True)
_source = open("src/telemetry.py", encoding="utf-8").read()
report.check("nothing hashes the machine or the account",
             any(word in _source for word in ("getnode", "gethostname", "getlogin",
                                              "md5", "sha1", "sha256")), False)
report.check("the player tag is never read at all",
             "player_tag" in _source, False)


report.section("the switch is a switch")
# The sink is a Google Form: submissions land in a sheet only its owner can
# open, and the address grants no read of any kind. Which is the half that can
# be protected - see the module docstring for the half that cannot.
report.check("it posts to a write-only sink",
             "docs.google.com/forms" in telemetry.ENDPOINT, True)
report.check("as a form submission rather than JSON",
             telemetry.FORM_FIELD.startswith("entry."), True)
report.check("and to formResponse, which accepts, not viewform, which shows",
             telemetry.ENDPOINT.endswith("/formResponse"), True)

_saved = telemetry.ENDPOINT
try:
    telemetry.ENDPOINT = ""
    report.check("no endpoint means off whatever the config says",
                 telemetry.enabled(), False)
    telemetry.ENDPOINT = "https://example.invalid/collect"
    telemetry.load_toml_as_dict = lambda *a, **k: {"send_anonymous_stats": False}
    report.check("off in the config means off", telemetry.enabled(), False)
    telemetry.load_toml_as_dict = lambda *a, **k: {"send_anonymous_stats": True}
    report.check("on in the config and aimed somewhere means on",
                 telemetry.enabled(), True)
    telemetry.load_toml_as_dict = lambda *a, **k: {}
    report.check("a config that never heard of it defaults to on, as documented",
                 telemetry.enabled(), True)
finally:
    telemetry.ENDPOINT = _saved
    from utils import load_toml_as_dict as _real
    telemetry.load_toml_as_dict = _real

report.check("the README says it is happening",
             "send_anonymous_stats" in open("README.md", encoding="utf-8").read(), True)
report.check("and the config template documents it",
             "send_anonymous_stats" in
             open("cfg/general_config.example.toml", encoding="utf-8").read(), True)


report.section("it cannot hurt the bot")
report.check("sending happens on its own thread", "threading.Thread" in _source, True)
report.check("and every failure is swallowed",
             "except Exception:" in _source, True)
report.check("reporting is rate limited rather than per match",
             telemetry.MIN_INTERVAL >= 3600, True)

# The one honest limitation, written down so it is not forgotten: the endpoint
# ships inside the client, so anybody can post to it. What cannot happen is
# anybody READING what was collected.
report.check("the module says the endpoint is public",
             "anybody can find it" in _source, True)


report.section("the numbers that were always null")
# The first five reports that ever arrived all carried "ips": null and
# "provider": "auto". Sent at startup, before a model was loaded or a frame
# processed - so the data collected to find out whether TensorRT helped
# anybody could not answer that question.
telemetry.note_provider("TensorrtExecutionProvider")
for _rate in (41.2, 58.9, 55.1, 12.0, 57.3, 56.8):
    telemetry.note_ips(_rate)

_live = telemetry.collect(PROFILE, provider="auto", version="0.8.14")
report.check("the provider reported is the one that loaded, not the one asked for",
             _live["provider"], "tensorrt")
report.check("and a rate is actually present", _live["ips"] is not None, True)
report.check("as the median, so one stall does not define the machine",
             _live["ips"], 56.8)

telemetry.note_ips(0)
telemetry.note_ips(-5)
telemetry.note_ips("fast")
report.check("junk readings are ignored rather than averaged in",
             telemetry.collect(PROFILE)["ips"], 56.8)

_main_src = open("main.py", encoding="utf-8").read()
report.check("the loop feeds the rate in as it measures it",
             "note_ips_for_stats" in _main_src, True)
report.check("and reports from a bot that is running, not only at startup",
             "send(profile=self.stats_profile()" in _main_src, True)

telemetry._measured["ips"] = []
telemetry._measured["provider"] = ""
report.check("with nothing measured it is null again rather than a guess",
             telemetry.collect(PROFILE)["ips"], None)


sys.exit(report.finish())
