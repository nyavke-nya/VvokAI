from __future__ import annotations

import logging
import threading

from datetime import timedelta

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_file, session)
from werkzeug.exceptions import HTTPException

from discord_bot import DiscordBot
from remote_control import RemoteControl
from telegram_bot import TelegramBot
from utils import get_brawler_icon_path, resolve_project_path
from . import panel_auth
from .runtime import RuntimeManager
from .services import WebDataService


class _SuppressRuntimeStatusPolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            '"GET /api/queue ' in message
            and ' 200 -' in message
        )

class _SuppressQueuePolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            '"GET /api/runtime/status ' in message
            and ' 200 -' in message
        )

class _SuppressAssetsGetting(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            'GET /api/assets' in message
            and '304 -' in message
        )

class _SupressHistoryPolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            'GET /api/history ' in message
            and ' 200 -' in message
        )

def _configure_request_logging():
    werkzeug_logger = logging.getLogger("werkzeug")
    if not any(isinstance(log_filter, _SuppressRuntimeStatusPolling) for log_filter in werkzeug_logger.filters):
        werkzeug_logger.addFilter(_SuppressRuntimeStatusPolling())
    if not any(isinstance(log_filter, _SuppressQueuePolling) for log_filter in werkzeug_logger.filters):
        werkzeug_logger.addFilter(_SuppressQueuePolling())
    if not any(isinstance(log_filter, _SuppressAssetsGetting) for log_filter in werkzeug_logger.filters):
        werkzeug_logger.addFilter(_SuppressAssetsGetting())
    if not any(isinstance(log_filter, _SupressHistoryPolling) for log_filter in werkzeug_logger.filters):
        werkzeug_logger.addFilter(_SupressHistoryPolling())


def _start_discord_bot_thread(app: Flask):
    discord_bot = app.config["discord_bot"]
    with app.config["discord_bot_lock"]:
        discord_thread = app.config.get("discord_bot_thread")
        if discord_thread and discord_thread.is_alive():
            return

        discord_thread = threading.Thread(
            target=discord_bot.run_bot,
            daemon=True,
            name="pyla-discord-bot",
        )
        app.config["discord_bot_thread"] = discord_thread
        discord_thread.start()


def _start_telegram_bot_thread(app: Flask):
    telegram_bot = app.config["telegram_bot"]
    thread = app.config.get("telegram_bot_thread")
    if thread and thread.is_alive():
        return
    thread = threading.Thread(
        target=telegram_bot.run_bot,
        daemon=True,
        name="pyla-telegram-bot",
    )
    app.config["telegram_bot_thread"] = thread
    thread.start()


def create_app(pyla_main, start_discord_bot=False):
    app = Flask(
        __name__,
        template_folder=str(resolve_project_path("templates")),
        static_folder=str(resolve_project_path("static")),
    )

    runtime_manager = RuntimeManager(pyla_main)
    data_service = WebDataService(runtime_manager)
    # One object behind both transports, so a command means the same thing
    # wherever it came from - and so the run's WindowController has one home.
    remote = RemoteControl(runtime_manager, data_service)
    discord_bot = DiscordBot(remote)
    telegram_bot = TelegramBot(remote)
    runtime_manager.configure_start_gate(data_service.get_queue_data, data_service.get_auth_state)
    app.config["runtime_manager"] = runtime_manager
    app.config["data_service"] = data_service
    app.config["remote_control"] = remote
    app.config["discord_bot"] = discord_bot
    app.config["discord_bot_thread"] = None
    app.config["discord_bot_lock"] = threading.Lock()
    app.config["telegram_bot"] = telegram_bot
    app.config["telegram_bot_thread"] = None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    _configure_request_logging()

    # ── the login gate ───────────────────────────────────────────────
    #
    # Everything below this point used to be open to anyone who could reach
    # the port, which was only ever defensible while that meant "somebody at
    # this PC". The panel starts and stops the bot, rewrites the queue, and
    # its settings page hands the Brawl Stars API token to the browser - so
    # the moment the address goes to a phone, or through a tunnel, it needs an
    # account.
    app.secret_key = panel_auth.secret_key()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Long enough that the phone in your pocket stays signed in between
        # sessions; the cookie is signed with a key that survives restarts.
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )

    # Reachable without being signed in: the login page itself, the three
    # calls it makes, and the static files and logo it is built from.
    OPEN_ENDPOINTS = {"login_page", "auth_state", "auth_setup", "auth_login",
                      "static", "support_asset"}

    login_throttle = panel_auth.LoginThrottle()
    app.config["login_throttle"] = login_throttle

    @app.before_request
    def require_login():
        if request.endpoint in OPEN_ENDPOINTS:
            return None
        if session.get("panel_user") and panel_auth.is_configured():
            return None
        # Deliberately no exemption for 127.0.0.1. Tunnels connect to the
        # loopback interface, so requests arriving through one look local -
        # a loopback exemption would hand the panel to the whole internet the
        # first time somebody forwarded the port.
        if request.path.startswith("/api/"):
            return jsonify({
                "ok": False,
                "error": "Not signed in.",
                "code": "LOGIN_REQUIRED",
            }), 401
        return redirect("/login")

    @app.get("/login")
    def login_page():
        if session.get("panel_user") and panel_auth.is_configured():
            return redirect("/")
        return render_template("login.html")

    @app.get("/api/auth/state")
    def auth_state():
        return jsonify({
            "configured": panel_auth.is_configured(),
            "authenticated": bool(session.get("panel_user")),
            "username": session.get("panel_user", ""),
        })

    @app.post("/api/auth/setup")
    def auth_setup():
        # Whoever sets up a brand new panel owns it, so this has to happen at
        # the machine rather than over a tunnel - otherwise the first stranger
        # to find the address gets the bot. tunnel.py refuses to start before
        # an account exists, which keeps the two halves of that consistent.
        if not panel_auth.is_local_request(request.remote_addr):
            return jsonify({
                "ok": False,
                "message": "The first account has to be created on the computer "
                           "running the bot, or on its own network.",
            }), 403
        payload = request.get_json(silent=True) or {}
        result = panel_auth.create(str(payload.get("username", "")).strip(),
                                   str(payload.get("password", "")))
        if not result.get("ok"):
            return jsonify(result), 400
        # The key only becomes real once there is an account to sign in to.
        app.secret_key = panel_auth.secret_key()
        session.permanent = True
        session["panel_user"] = panel_auth.username()
        return jsonify(result)

    @app.post("/api/auth/login")
    def auth_login():
        payload = request.get_json(silent=True) or {}
        name = str(payload.get("username", "")).strip()
        caller = request.remote_addr or "unknown"

        # A login page on a public address gets guessed at by machines, all
        # day. Unlimited attempts were fine when only your own network could
        # reach this; they are not now.
        waiting = login_throttle.locked_for(caller)
        if waiting > 0:
            return jsonify({
                "ok": False,
                "message": f"Too many attempts. Try again in {int(waiting) + 1} seconds.",
            }), 429

        if not panel_auth.is_configured():
            return jsonify({"ok": False, "message": "No account has been set up yet."}), 400
        if not panel_auth.verify(name, str(payload.get("password", ""))):
            login_throttle.record_failure(caller)
            # One message for both halves, so it cannot be used to find out
            # which usernames exist.
            return jsonify({"ok": False, "message": "Wrong username or password."}), 401

        login_throttle.record_success(caller)
        session.permanent = True
        session["panel_user"] = panel_auth.username()
        return jsonify({"ok": True, "message": "Signed in."})

    @app.post("/api/auth/logout")
    def auth_logout():
        session.pop("panel_user", None)
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        return jsonify(data_service.get_bootstrap_payload())

    @app.errorhandler(KeyError)
    @app.errorhandler(FileNotFoundError)
    @app.errorhandler(ValueError)
    def handle_known_errors(error):
        app.logger.warning("Handled request error at %s: %s", request.path, error)
        return jsonify({"ok": False, "message": str(error)}), 400

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            return error
        app.logger.exception("Unhandled request error at %s", request.path)
        return jsonify({"ok": False, "message": str(error)}), 500

    @app.post("/api/login/validate")
    def validate_login():
        payload = request.get_json(silent=True) or {}
        result = data_service.validate_login(payload.get("api_key", ""))
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/player-info")
    def player_info():
        result = data_service.get_player_info_payload(request.args.get("tag", ""))
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/queue")
    def get_queue():
        return jsonify({"items": data_service.get_queue_data()})

    @app.post("/api/queue")
    def add_queue():
        payload = request.get_json(silent=True) or {}
        items = data_service.add_or_update_queue_item(payload)
        return jsonify({"ok": True, "items": items})

    @app.post("/api/queue/import")
    def import_queue():
        uploaded_file = request.files.get("file")
        items = data_service.import_queue_file(uploaded_file)
        return jsonify({"ok": True, "items": items})

    @app.put("/api/queue/<path:brawler_name>")
    def update_queue_item(brawler_name: str):
        payload = request.get_json(silent=True) or {}
        payload["brawler"] = brawler_name
        items = data_service.add_or_update_queue_item(payload)
        return jsonify({"ok": True, "items": items})

    @app.post("/api/queue/reorder")
    def reorder_queue():
        payload = request.get_json(silent=True) or {}
        items = data_service.reorder_queue(payload.get("order", []))
        return jsonify({"ok": True, "items": items})

    @app.post("/api/queue/push-all-to-target")
    def push_all_to_target():
        result = data_service.push_all_to_default_target()
        return jsonify({"ok": True, **result})

    @app.delete("/api/queue")
    def clear_queue():
        items = data_service.clear_queue()
        return jsonify({"ok": True, "items": items})

    @app.delete("/api/queue/<path:brawler_name>")
    def delete_queue_item(brawler_name: str):
        items = data_service.delete_queue_item(brawler_name)
        return jsonify({"ok": True, "items": items})

    @app.get("/api/playstyles")
    def get_playstyles():
        return jsonify(data_service.get_playstyles_payload())

    @app.post("/api/playstyles/import")
    def import_playstyle():
        uploaded_file = request.files.get("file")
        result = data_service.import_playstyle(uploaded_file)
        return jsonify(result)
    @app.delete("/api/playstyles/<path:filename>")
    def delete_playstyle(filename: str):
        result = data_service.delete_playstyle(filename)
        return jsonify(result)

    @app.put("/api/playstyles/active")
    def activate_playstyle():
        payload = request.get_json(silent=True) or {}
        result = data_service.activate_playstyle(payload.get("filename", ""))
        return jsonify(result)

    @app.get("/api/settings/<section>")
    def get_settings(section: str):
        return jsonify(data_service.get_settings_payload(section))

    @app.put("/api/settings/<section>")
    def update_settings(section: str):
        payload = request.get_json(silent=True) or {}
        return jsonify(data_service.update_settings(section, payload))

    @app.post("/api/settings/<section>/reset")
    def reset_settings(section: str):
        return jsonify(data_service.reset_settings(section))

    @app.post("/api/runtime/start")
    def runtime_start():
        result = runtime_manager.start_current_queue(remote)
        if result.get("ok"):
            status_code = 200
        elif result.get("code") == "EMPTY_QUEUE":
            status_code = 400
        elif "auth" in result:
            status_code = 403
        else:
            status_code = 409
        return jsonify({**result, "runtime": runtime_manager.get_status()}), status_code

    @app.get("/api/runtime/status")
    def runtime_status():
        return jsonify({"ok": True, "runtime": runtime_manager.get_status()})

    @app.post("/api/runtime/pause")
    def runtime_pause():
        result = runtime_manager.pause()
        status_code = 200 if result.get("ok") else 409
        return jsonify({**result, "runtime": runtime_manager.get_status()}), status_code

    @app.post("/api/runtime/stop")
    def runtime_stop():
        result = runtime_manager.stop()
        status_code = 200 if result.get("ok") else 409
        return jsonify({**result, "runtime": runtime_manager.get_status()}), status_code

    @app.get("/api/history")
    def history():
        return jsonify(data_service.get_match_history_payload())

    @app.get("/api/assets/brawlers/<path:brawler_name>")
    def brawler_icon(brawler_name: str):
        icon_path = get_brawler_icon_path(brawler_name)
        if icon_path is None:
            return ("", 404)
        return send_file(icon_path)

    @app.get("/api/assets/support/<path:filename>")
    def support_asset(filename: str):
        target = resolve_project_path("images", filename)
        if not target.exists():
            return ("", 404)
        return send_file(target)

    if start_discord_bot:
        _start_discord_bot_thread(app)
        _start_telegram_bot_thread(app)

    return app
