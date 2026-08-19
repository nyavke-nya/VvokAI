"""resync_from_api, exercised without a device, a network or an API key."""
import sys

from _harness import Failures

import stage_manager as sm

report = Failures("trophy resync")


def check(label, got, want):
    report.check(label, got, want)

class Observer:
    def __init__(self): self.current_trophies, self.win_streak = 500, 3

def make(stats, info={"x": 1}, tag="#ABC", ptype="trophies"):
    mgr = object.__new__(sm.StageManager)
    mgr.player_tag = tag
    mgr.Trophy_observer = Observer()
    mgr.brawlers_pick_data = [{"brawler": "shelly", "type": ptype, "trophies": 500}]
    sm.get_player_info = lambda t: info
    sm.get_brawler_stats = lambda i, b, *a, **k: stats
    sm.save_brawler_data = lambda d: saved.append(dict(d[0]))
    return mgr

report.section("the API knows the trophies but never the streak - the real case")
saved = []
m = make((812, None))
m.resync_from_api("test")
check("trophies taken from the API", m.Trophy_observer.current_trophies, 812)
check("streak left alone", m.Trophy_observer.win_streak, 3)
check("brawler data written", saved[-1]["trophies"], 812)

report.section("a streak, when something can supply one, is used")
saved = []
m = make((900, 7))
m.resync_from_api("test")
check("trophies", m.Trophy_observer.current_trophies, 900)
check("streak", m.Trophy_observer.win_streak, 7)

report.section("nothing from the API changes nothing")
saved = []
m = make((None, None))
m.resync_from_api("test")
check("trophies untouched", m.Trophy_observer.current_trophies, 500)
check("streak untouched", m.Trophy_observer.win_streak, 3)
check("nothing written", saved, [])

report.section("an unreachable API is survivable")
saved = []
m = make((812, None), info=None)
m.resync_from_api("test")
check("trophies untouched", m.Trophy_observer.current_trophies, 500)

report.section("no player tag configured: it simply does not run")
saved = []
m = make((812, None), tag="")
m.resync_from_api("test")
check("trophies untouched", m.Trophy_observer.current_trophies, 500)

report.section("a wins-target brawler keeps its own counter")
saved = []
m = make((812, None), ptype="wins")
m.resync_from_api("test")
check("observer still corrected", m.Trophy_observer.current_trophies, 812)
check("but the wins row is not overwritten with trophies", saved, [])

sys.exit(report.finish())
