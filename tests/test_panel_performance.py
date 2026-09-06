"""History cache behavior, including concurrent requests and file replacement."""
import csv
import os
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from _harness import Failures
from webui.services import WebDataService

r = Failures('panel performance')
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'history.csv'
    def write(result):
        temporary = path.with_suffix('.new')
        with temporary.open('w',newline='',encoding='utf-8') as handle:
            writer = csv.DictWriter(handle,fieldnames=['brawler_name','result','current_trophies','trophy_delta','date_time'])
            writer.writeheader()
            writer.writerow(dict(brawler_name='shelly',result=result,current_trophies=100,trophy_delta=8,date_time='2026-09-06 10:00:00'))
        os.replace(temporary,path)
    write('victory')
    service = WebDataService.__new__(WebDataService)
    with patch('webui.services.resolve_project_path',return_value=path), patch.object(service,'_read_match_history_payload',wraps=service._read_match_history_payload) as read:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _:service.get_match_history_payload(),range(16)))
        r.check('concurrent requests aggregate unchanged file once',read.call_count,1)
        r.check('all callers receive correct totals',all(v['summary']['wins']==1 for v in results),True)
        write('defeat')
        r.check('atomic replacement invalidates cache',service.get_match_history_payload()['summary']['losses'],1)
        r.check('replacement triggers one rebuild',read.call_count,2)
        path.unlink()
        r.check('deletion clears history',service.get_match_history_payload()['summary']['total_matches'],0)
        write('victory')
        r.check('recreation restores history',service.get_match_history_payload()['summary']['wins'],1)
    # A write occurring while the old snapshot is read must not poison the cache.
    other = WebDataService.__new__(WebDataService)
    original = other._read_match_history_payload
    def racing_read():
        response = original()
        write('defeat')
        return response
    with patch('webui.services.resolve_project_path',return_value=path):
        with patch.object(other,'_read_match_history_payload',side_effect=racing_read):
            other.get_match_history_payload()
        r.check('racing writer cannot cache stale totals',other.get_match_history_payload()['summary']['losses'],1)
raise SystemExit(r.finish())
