"""Regression checks for audit fixes; no devices or external requests."""
import json
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from _harness import Failures
import utils
import telemetry
from dodge.service import DodgeService
from dodge.config import DodgeConfig
from webui.services import WebDataService, SECRET_PLACEHOLDER

r = Failures('audit regressions')
with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / 'queue.json'
    path.write_text('[1]', encoding='utf-8')
    try:
        with utils.atomic_text_writer(path) as out:
            out.write('[2,')
            raise OSError('interrupted write')
    except OSError:
        pass
    r.check('failed write preserves original JSON', json.loads(path.read_text()), [1])
    with patch.object(utils, 'resolve_project_path', return_value=path):
        utils.save_brawler_data([{'brawler':'shelly'}])
    r.check('queue published completely', json.loads(path.read_text()), [{'brawler':'shelly'}])
    r.check('temporary files cleaned', list(Path(folder).glob('*.tmp')), [])
    with utils.atomic_text_writer(path) as first:
        first.write('[3]')
        with utils.atomic_text_writer(path) as second:
            second.write('[4]')
    r.check('overlapping writers do not share temporary files', json.loads(path.read_text()), [3])

cfg = DodgeConfig()
cfg.log_enabled = False
service = DodgeService(SimpleNamespace(scale_factor=1), cfg)
service.update_context([1,2,3,4], [], [], [], player_center=(4,5))
service.set_tactical_intent((1,0), lambda _: True, lambda _: True)
service.solver._committed_vector = (1,0)
service._last_emergency = (1,0)
service.reset()
r.check('reset clears player and tactical data', (service._player_center, service._tactical_vector, service._gas_veto), (None,None,None))
r.check('reset clears solver commitment', service.solver._committed_vector, None)
r.check('reset clears emergency and context', (service._last_emergency, service._context.player_box), (None,None))
# Pause a frame in its tracker while a new context arrives on another thread.
entered, release, published = threading.Event(), threading.Event(), threading.Event()
def track(*_):
    entered.set()
    if not release.wait(2):
        raise RuntimeError('test timeout')
    return [], (7, 8)
service.tracker.update = track
service.log = Mock()
service.config.log_stats = False
worker = threading.Thread(target=lambda: service._process(None, 1, False))
worker.start()
entered.wait(2)
def publish():
    service.update_context(None, [], [], [])
    published.set()
context_worker = threading.Thread(target=publish)
context_worker.start()
# The invariant is that a fresh context does not inherit the pan a frame in
# flight was accumulating. That used to be enforced by making update_context
# WAIT for the whole frame - tracker and solver included - which meant the bot
# loop stalled on every iteration behind a thread whose frame costs 2.9 ms with
# the screen quiet and 10.3 ms once it is full of tracks. The loop must not
# wait; the frame checks a generation counter on the way out instead.
r.check('publishing a context does not wait for the frame', published.wait(2), True)
release.set()
worker.join(2); context_worker.join(2)
r.check('fresh context does not inherit old accumulated pan', service._accumulated_shift, (0,0))
service.stop()

svc = WebDataService.__new__(WebDataService)
for key, value in [('used_threads',0), ('max_ips',-1), ('ocr_scale_down_factor',float('nan')), ('emulator_port',65536)]:
    try:
        svc._apply_updates({}, svc.GENERAL_FIELDS, {key:value})
        rejected = False
    except ValueError:
        rejected = True
    r.check(f'reject invalid {key}', rejected, True)
config = {'brawl_api_token':'dummy-secret','brawl_api_password':'dummy-password'}
with patch.object(svc, '_load_config', return_value=config.copy()):
    payload = svc.get_settings_payload('general')
r.check('API token masked', payload['brawl_api_token'], SECRET_PLACEHOLDER)
with patch.object(svc, '_load_config', side_effect=lambda _: config.copy()), patch.object(svc, '_save_config') as save:
    svc.update_settings('general', {'brawl_api_token':SECRET_PLACEHOLDER})
    r.check('saving placeholder preserves token', save.call_args.args[1]['brawl_api_token'], 'dummy-secret')
with patch('webui.services.get_playstyles_list', return_value=[{'filename':'broken.vvok','metadata':42}]), patch.object(svc,'_load_config',return_value={}):
    r.check('bad metadata does not crash listing', svc.get_playstyles_payload()['items'][0]['name'], 'broken')

entered, release, done = threading.Event(), threading.Event(), threading.Event()
telemetry._in_flight = False
telemetry._retry_after = 0
profile = Mock(return_value={})
def post(_):
    entered.set()
    release.wait(2)
    return False
with patch.object(telemetry,'enabled',return_value=True), patch.object(telemetry,'_due',return_value=True), patch.object(telemetry,'collect',return_value={}), patch.object(telemetry,'_post',side_effect=post):
    r.check('first report starts', telemetry.send(profile=profile), True)
    entered.wait(2)
    r.check('second report suppressed while in flight', telemetry.send(profile=profile), False)
    release.set()
    # Acquire worker by name and wait for its finally block, without polling.
    for thread in threading.enumerate():
        if thread.name == 'vvok-stats': thread.join(2)
    r.check('failed report waits before retry', telemetry.send(profile=profile), False)
    r.check('profile computed only for actual send', profile.call_count, 1)
with patch.object(telemetry,'enabled',return_value=False):
    profile.reset_mock()
    telemetry.send(profile=profile)
    r.check('disabled telemetry never evaluates history', profile.call_count, 0)

# Real Flask request boundary, with no bot started.
from webui.app import create_app
with patch("webui.app.WebDataService"), patch("webui.app.InstanceManager"), patch("webui.app.DiscordBot"), patch("webui.app.TelegramBot"):
    app = create_app(lambda *_: None, start_discord_bot=False)
client = app.test_client()
r.check('foreign Origin rejected', client.post('/api/runtime/stop', headers={'Origin':'https://foreign.example'}).status_code, 403)
r.check('cross-site form rejected', client.post('/api/runtime/stop', headers={'Sec-Fetch-Site':'cross-site'}).status_code, 403)
r.check('same-origin page remains available', client.get('/', headers={'Origin':'http://localhost'}).status_code, 200)
raise SystemExit(r.finish())
