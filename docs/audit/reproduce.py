"""Read-only audit probes; fake devices/network, temporary files only.

Run from repository root: venv\Scripts\python.exe docs\audit\reproduce.py
REPRODUCED means a defect is present, not that the product test passed.
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

import utils
import stage_manager as stages
import trophy_observer as trophies
import webui.services as services
import webui.runtime as runtime
import webui.instances as instances
import webui.app as app_module
import schedule_control
import telemetry
import brawl_token
from dodge.service import DodgeService
from dodge.config import DodgeConfig
from dodge.tracker import FrameContext
from werkzeug.datastructures import FileStorage

observations = []


def report(name, reproduced, detail):
    observations.append({"probe": name, "reproduced": bool(reproduced), "detail": detail})
    print(f"{'REPRODUCED' if reproduced else 'NOT REPRODUCED'} {name}: {detail}")


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_main_class():
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "vvok_main")
    cls = next(n for n in func.body if isinstance(n, ast.ClassDef))
    namespace = {}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "<audit-main>", "exec"), namespace)
    return namespace["Main"], namespace


def main():
    with tempfile.TemporaryDirectory(prefix="vvok-audit-") as folder:
        scratch = Path(folder)

        # 1. No config is opened: compare the actual path resolver's results.
        with patch.object(utils, "_CFG_DIR_ENV", str(scratch / "account")):
            normal = utils._config_full_path("cfg/general_config.toml")
            dotted = utils._config_full_path("./cfg/general_config.toml")
            report("config_scope", normal.resolve() != dotted.resolve(),
                   f"cfg uses account={normal.parent == scratch / 'account'}; ./cfg uses root={dotted.parent.resolve() == ROOT / 'cfg'}")

        # 2. Two independent emergencies, after the controller released priority.
        svc = object.__new__(DodgeService)
        mover = Mock()
        svc.window_controller = SimpleNamespace(move_with_priority=mover)
        svc._last_emergency = None
        svc._emergency_hold = 0.14
        svc._apply_emergency((100, 0))
        svc.window_controller.joystick_priority_until = 0
        svc._apply_emergency((100, 0))
        report("repeated_emergency", mover.call_count == 1,
               f"two emergency requests produced {mover.call_count} joystick command")

        # 3. Reset leaves live context/intent and last emergency intact.
        svc.tracker = Mock()
        svc.enemy_tracker = Mock()
        svc.teammate_tracker = Mock()
        svc._lock = threading.Lock()
        svc._context = FrameContext(player_box=[1, 2, 3, 4], stamp=1)
        svc._player_center = (2, 3)
        svc._player_radius = 30.0
        svc._tactical_vector = (100, 0)
        svc.reset()
        report("reset_keeps_context", svc._context.player_box is not None and svc._player_center is not None,
               f"player={svc._player_center}; intent={svc._tactical_vector}; emergency={svc._last_emergency}")

        # 4. Re-enter update_context while a frame is being processed.
        # This deterministic interleaving is possible because update releases _lock.
        svc.config = DodgeConfig({})
        svc.config.log_stats = False
        svc.config.aim_enabled = False
        svc._accumulated_shift = (5, 0)
        svc._is_blocked = None
        svc._gas_veto = None
        svc.log = Mock()
        svc.solver = Mock(solve=Mock(return_value=None))
        def tracker_update(*args):
            svc.update_context([1, 2, 3, 4], [], [], [], player_center=(2, 3))
            return [], (2, 0)
        svc.tracker.update = tracker_update
        svc._process(None, 10, emergency=False)
        report("camera_shift_race", svc._accumulated_shift == (7, 0),
               f"published fresh context cleared pan, old frame restored {svc._accumulated_shift}")

        # 5. Timer sees an explicit manual pause/stop as a reason to restart.
        for state in ("paused", "idle"):
            manager = runtime.RuntimeManager(lambda *a, **k: None)
            manager.get_status = lambda state=state: {"state": state, "is_running": state == "paused"}
            calls = []
            def restart(_remote):
                calls.append(state)
                manager._watch_for_resume(None)
                return {"ok": True}
            manager.start_current_queue = restart
            fake_schedule = SimpleNamespace(active=True, resume_at=480, in_quiet_hours=lambda now: False)
            with patch.object(schedule_control.Schedule, "from_config", return_value=fake_schedule), \
                 patch.object(utils, "load_toml_as_dict", return_value={}), \
                 patch.object(utils, "invalidate_toml_cache"), \
                 patch.object(runtime.time, "sleep", return_value=None):
                manager._watch_for_resume(None)
                watcher = manager._resume_thread
                watcher.join(timeout=2)
            report("schedule_overrides_" + state, calls == [state],
                   f"resume calls={len(calls)}, replacement watcher={manager._resume_thread is not watcher}")
            if state == "idle":
                report("schedule_not_rearmed", manager._resume_thread is watcher and not watcher.is_alive(),
                       "auto-resume tried to arm next watcher while current watcher was still alive")

        # 6. The API response belongs to A, but writes to the new queue head B.
        stage = object.__new__(stages.StageManager)
        stage.player_tag = "AUDIT_DUMMY"
        stage.brawlers_pick_data = [{"brawler": "A", "type": "trophies", "trophies": 100}]
        stage.Trophy_observer = SimpleNamespace(current_trophies=100, win_streak=0)
        def delayed_response(_tag):
            stage.brawlers_pick_data[0] = {"brawler": "B", "type": "trophies", "trophies": 800}
            stage.Trophy_observer.current_trophies = 800
            return {"fake": True}
        with patch.object(stages, "get_player_info", side_effect=delayed_response), \
             patch.object(stages, "get_brawler_stats", return_value=(123, None)), \
             patch.object(stages, "save_brawler_data"):
            stage.resync_from_api("audit")
        report("api_resync_wrong_brawler", stage.brawlers_pick_data[0]["trophies"] == 123,
               f"response for A overwrote B's 800 with {stage.brawlers_pick_data[0]['trophies']}")

        # 7. Format performs attribute traversal invisible to the AST check.
        # The function has only fabricated globals, never real config/secrets.
        dummy_globals = {"audit_only": "DUMMY_SECRET"}
        exec("def f(): return 0", dummy_globals)
        source = 'movement = "{0.__globals__[audit_only]}".format(time_now)'
        accepted, _ = utils.is_safe_ast(source)
        movement, _ = utils.interpret_vvok_code(source, {"time_now": dummy_globals["f"]})
        report("sandbox_private_read", accepted and movement == "DUMMY_SECRET",
               f"AST accepted={accepted}; private global read={movement == 'DUMMY_SECRET'}")
        accepted_loop, _ = utils.is_safe_ast("while True:\n    pass")
        report("sandbox_unbounded_loop", accepted_loop, "infinite loop accepted; deliberately NOT executed")

        # 8. Import accepts non-object metadata and leaves a file breaking listing.
        (scratch / "playstyles").mkdir()
        def path(*parts):
            return scratch.joinpath(*parts)
        service = object.__new__(services.WebDataService)
        service._load_config = lambda _: {}
        listing_error = None
        with patch.object(services, "resolve_project_path", side_effect=path), \
             patch.object(utils, "resolve_project_path", side_effect=path):
            try:
                service.import_playstyle(FileStorage(stream=io.BytesIO(b'42\nmovement = None\n'), filename="bad.vvok"))
            except Exception as exc:
                listing_error = type(exc).__name__
        report("metadata_breaks_listing", listing_error == "AttributeError" and (scratch / "playstyles" / "bad.vvok").exists(),
               f"file persisted=True; listing error={listing_error}")

        # 9. Port number alone is treated as authority to terminate a process.
        manager = instances.InstanceManager()
        manager._read = lambda: [{"name": "audit", "port": 54321}]
        with patch.object(instances, "_listening_pids", return_value={54321: 999999}), \
             patch.object(instances, "_kill_tree") as kill:
            manager.stop("audit")
        report("kill_unverified_port_owner", kill.call_count == 1,
               f"untracked PID={kill.call_args.args[0] if kill.called else None}; no real process killed")

        # 10. Trophy floor and recorded delta disagree.
        observer = object.__new__(trophies.TrophyObserver)
        with patch.object(trophies, "load_toml_as_dict", return_value={"trophies_multiplier": 1}), \
             patch.object(trophies.TrophyObserver, "load_history", return_value=[]):
            observer.__init__()
        observer.current_trophies = 1000
        observer.current_wins = 0
        observer.save_history = lambda: None
        observer.send_results_to_api = lambda: None
        meta = {"name": "audit", "gamemodes": ["all"], "brawlers": ["all"]}
        result = trophies.ParsedGameResult(trophies.GameMode.CLASSIC, trophies.MatchResult.DEFEAT)
        observer.add_trophies(result, "audit", meta)
        delta = observer.match_history[-1]["trophy_delta"]
        report("floor_delta_disagrees", observer.current_trophies == 1000 and delta != 0,
               f"actual=0, recorded={delta}")

        # 11. Re-entry on the same end screen counts it again after 35 seconds.
        stage = object.__new__(stages.StageManager)
        stage.window_controller = Mock(screenshot=Mock(return_value=None))
        stage.Trophy_observer = Mock(current_trophies=100, current_wins=0, win_streak=0)
        stage.Trophy_observer.parse_game_result.return_value = result
        stage.brawlers_pick_data = [{"brawler": "audit", "type": "trophies", "trophies": 100, "push_until": 1000}]
        stage.playstyle_info = meta
        stage.read_power_level = lambda _: None
        stage.resync_from_api = Mock()
        stage._last_result_recorded_at = 1
        stage.play_again_on_win = False
        stage.runtime_control = None
        fake_time = SimpleNamespace(value=100.0)
        def now():
            return fake_time.value
        def sleep(seconds):
            fake_time.value += seconds
        with patch.object(stages, "get_state", return_value="end_defeat"), \
             patch.object(stages.time, "time", side_effect=now), \
             patch.object(stages.time, "sleep", side_effect=sleep), \
             patch.object(stages, "save_brawler_data"):
            stage.end_game()
            stage.end_game()
        report("end_screen_counted_twice", stage.Trophy_observer.add_trophies.call_count == 2,
               f"one unchanged end screen recorded {stage.Trophy_observer.add_trophies.call_count} times")

        # A winning target has no rematch requested, but still enters rematch wait.
        stage.play_again_on_win = True
        stage._target_reached = lambda: True
        stage._last_result_recorded_at = 0
        stage.Trophy_observer.parse_game_result.return_value = trophies.ParsedGameResult(
            trophies.GameMode.CLASSIC, trophies.MatchResult.VICTORY)
        stage.window_controller.reset_mock()
        states = iter(['end_victory', 'lobby'])
        fake_time.value = 500.0
        with patch.object(stages, "get_state", side_effect=lambda _: next(states, 'lobby')), \
             patch.object(stages.time, "time", side_effect=now), \
             patch.object(stages.time, "sleep", side_effect=sleep), \
             patch.object(stages, "save_brawler_data"):
            stage.end_game()
        report("target_victory_waits_for_unrequested_rematch",
               stage.window_controller.restart_brawl_stars.called,
               f"already in lobby, target complete, yet restart called={stage.window_controller.restart_brawl_stars.called}")

        # 12. .tmp filename collision: the second save consumes the first writer's file.
        target = scratch / "race.toml"
        original_replace = os.replace
        nested = False
        def replace_interleaved(src, dst):
            nonlocal nested
            if not nested:
                nested = True
                utils.save_dict_as_toml({"value": 2}, str(target))
            return original_replace(src, dst)
        failure = None
        with patch.object(utils.os, "replace", side_effect=replace_interleaved), \
             patch.object(utils, "_config_full_path", return_value=target):
            try:
                utils.save_dict_as_toml({"value": 1}, str(target))
            except OSError as exc:
                failure = type(exc).__name__
        report("config_tmp_collision", failure == "FileNotFoundError", f"first request ends in {failure}")

        # 13. Multiple telemetry workers launch before any one finishes sending.
        with patch.object(telemetry, "enabled", return_value=True), \
             patch.object(telemetry, "_due", return_value=True), \
             patch.object(telemetry.threading, "Thread") as worker:
            for _ in range(10):
                telemetry.send({})
        report("telemetry_inflight_unbounded", worker.call_count == 10,
               f"{worker.call_count} workers for 10 calls; no requests sent")

        # 14. Stop on target completion returns past the common Main cleanup.
        Main, namespace = extract_main_class()
        stage = object.__new__(stages.StageManager)
        stage.runtime_control = None
        stage.player_tag = ""
        stage.brawler_needs_selecting = False
        stage.brawlers_pick_data = [{"brawler": "audit", "type": "wins", "push_until": 1}]
        stage.Trophy_observer = SimpleNamespace(current_trophies=0, current_wins=1)
        stage.window_controller = Mock()
        with patch.object(stages, "notify_user"), patch.object(stages, "load_toml_as_dict", return_value={}):
            try:
                stage.start_game()
            except SystemExit:
                report("target_completion_systemexit", True,
                       "StageManager exited directly; vvok_main has no finally to stop its watchers")

        # 15. Play's display activity reader has no assigned RuntimeControl.
        play_tree = ast.parse((ROOT / 'src/play.py').read_text(encoding='utf-8'))
        play_cls = next(n for n in play_tree.body if isinstance(n, ast.ClassDef) and n.name == 'Play')
        constructor = next(n for n in play_cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
        assigned = [ast.unparse(n) for n in ast.walk(constructor) if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)]
        main_text = (ROOT / 'main.py').read_text(encoding='utf-8')
        report("activity_control_not_wired", 'self.runtime_control' not in assigned and 'self.Play.runtime_control' not in main_text,
               "Play.__init__ and Main never assign Play.runtime_control")

        # 16. A hostile Origin/Host is accepted for a state-changing endpoint.
        class FakeRuntime:
            def __init__(self, _): pass
            def configure_start_gate(self, *args): pass
            def stop(self): return {"ok": True}
            def get_status(self): return {"state": "idle"}
        fake_data = SimpleNamespace(get_queue_data=lambda: [], get_auth_state=lambda: {})
        with patch.object(app_module, "RuntimeManager", FakeRuntime), \
             patch.object(app_module, "WebDataService", return_value=fake_data), \
             patch.object(app_module, "DiscordBot"), patch.object(app_module, "TelegramBot"):
            app = app_module.create_app(lambda *a, **k: None)
            response = app.test_client().post('/api/runtime/stop', base_url='http://untrusted.invalid',
                                             headers={"Origin": "http://untrusted.invalid"},
                                             content_type='application/x-www-form-urlencoded')
        report("panel_accepts_foreign_origin_host", response.status_code == 200,
               f"form POST status={response.status_code}; fake runtime only, browser policy not simulated")

        # 17. Update failures leave earlier replacements in place, returning success-like 0.
        updater = load_tool("updater")
        update_root = scratch / 'install'
        source = scratch / 'incoming'
        update_root.mkdir()
        source.mkdir()
        for name in ("a.py", "b.py"):
            (source / name).write_text('new', encoding='utf-8')
            (update_root / name).write_text('old', encoding='utf-8')
        copy2 = updater.shutil.copy2
        copied = []
        def failing_copy(src, dst, *args, **kwargs):
            if Path(src).parent == source:
                copied.append(Path(src).name)
                if len(copied) == 2:
                    raise OSError('audit simulated disk failure')
            return copy2(src, dst, *args, **kwargs)
        holding = scratch / 'holding'
        holding.mkdir()
        with patch.multiple(updater, ROOT=update_root, BACKUP=update_root/'backup', STAMP=update_root/'.vvok_version'), \
             patch.object(updater, "latest_commit", return_value=('audit_sha', 'audit')), \
             patch.object(updater, "download", return_value=(holding, source)), \
             patch.object(updater.shutil, "copy2", side_effect=failing_copy):
            code = updater.main()
        contents = sorted(p.read_text(encoding='utf-8') for p in update_root.glob('*.py'))
        report("update_partial_no_rollback", code == 0 and contents == ['new', 'old'],
               f"exit={code}, installed file contents={contents}")

        # 18. Common marker revokes keys belonging to other Vvok installations.
        dummy_config = {'brawl_api_token': 'audit_old'}
        with patch.object(brawl_token, '_quiet_until', 0), \
             patch.object(brawl_token, 'credentials', return_value=('dummy', 'dummy')), \
             patch.object(brawl_token, 'load_toml_as_dict', return_value=dummy_config), \
             patch.object(brawl_token.requests, 'Session'), \
             patch.object(brawl_token, '_login', return_value=(True, None)), \
             patch.object(brawl_token, '_list_keys', return_value=[
                 {'id': 'install_A', 'description': brawl_token.KEY_MARKER},
                 {'id': 'install_B', 'description': brawl_token.KEY_MARKER}]), \
             patch.object(brawl_token, '_revoke') as revoke, \
             patch.object(brawl_token, '_create', return_value='audit_new'), \
             patch.object(brawl_token, '_works', return_value=True), \
             patch.object(brawl_token, 'save_dict_as_toml'):
            brawl_token.refresh(previous='audit_old', seen_ip='192.0.2.1')
        report('token_refresh_revokes_other_install', revoke.call_count == 2,
               f"revoked {[call.args[1] for call in revoke.call_args_list]}; mocked portal only")

        # 19. Full profile is computed even when sending is explicitly disabled.
        main_obj = object.__new__(Main)
        main_obj.Play = None
        main_obj.stats_profile = Mock(return_value={})
        with patch.object(telemetry, 'enabled', return_value=False), \
             patch.object(telemetry, 'note_ips'):
            main_obj.note_ips_for_stats(30)
        report('disabled_telemetry_still_builds_profile', main_obj.stats_profile.called,
               f"full history/profile requested={main_obj.stats_profile.called}")

        stage = object.__new__(stages.StageManager)
        stage.window_controller = Mock(width_ratio=0.5)
        with patch.object(stages.time, 'sleep', return_value=None):
            stage.click_nano_noodles()
        actual_x = [c.args[0] * 0.5 for c in stage.window_controller.click.call_args_list]
        wanted_x = [480.0, 645.0, 315.0]
        report('reward_double_scaling', actual_x != wanted_x,
               f"960px frame x={actual_x}, expected={wanted_x}")

        service = object.__new__(services.WebDataService)
        values = service._apply_updates({}, service.GENERAL_FIELDS, {'max_ips': -1})
        timers = service._apply_updates({}, service.TIMER_FIELDS, {'wall_detection': 'nan'})
        report('invalid_numeric_settings_accepted', values['max_ips'] == -1 and math.isnan(timers['wall_detection']),
               'max_ips=-1 and wall_detection=NaN accepted; no files written')

    print(json.dumps({"probes": len(observations), "reproduced": sum(o['reproduced'] for o in observations)}, indent=2))
    return 0 if all(o['reproduced'] for o in observations) else 1


if __name__ == "__main__":
    sys.exit(main())
