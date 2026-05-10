# === First thing (required before any other import!) ===
from gevent import monkey
monkey.patch_all()

from datetime import timedelta

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_cors import CORS
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from bootstrap_config import (
    get_app_bind,
    get_bootstrap_config,
    get_cors_allowed_origins,
    get_or_create_secret_key,
    get_socketio_cors_allowed_origins,
    is_installer_mode,
    normalize_cors_origin,
    resolve_bootstrap_config_path,
)


def _init_socketio(app: Flask):
    from extensions import socketio, SOCKET_MAX_HTTP_BUFFER_BYTES

    cors_allowed_origins = get_socketio_cors_allowed_origins(app.config.get("BOOTSTRAP_CONFIG"))
    socketio.init_app(
        app,
        async_mode="gevent",
        cors_allowed_origins=cors_allowed_origins,
        max_http_buffer_size=SOCKET_MAX_HTTP_BUFFER_BYTES,
        logger=False,
        engineio_logger=False,
        ping_timeout=121,
        ping_interval=120,
    )
    app.extensions["llmcontroller_socketio"] = socketio


def _register_origin_mismatch_logging(app: Flask):
    @app.before_request
    def log_disallowed_origin():
        request_origin = (request.headers.get("Origin") or "").strip()
        if not request_origin:
            return None

        allowed_origins = get_cors_allowed_origins(app.config.get("BOOTSTRAP_CONFIG"))
        normalized_origin = normalize_cors_origin(request_origin)
        if normalized_origin and normalized_origin in allowed_origins:
            return None

        app.logger.warning(
            "Origin rejected or not accepted: request_origin=%s normalized_origin=%s accepted_origins=%s path=%s",
            request_origin,
            normalized_origin or "<invalid>",
            ",".join(allowed_origins) if allowed_origins else "<none>",
            request.path,
        )
        return None


def _configure_installer_mode(app: Flask):
    from installer_routes import installer_routes

    _init_socketio(app)
    app.register_blueprint(installer_routes)

    @app.before_request
    def installer_request_gate():
        if app.config.get("SETUP_COMPLETE"):
            return None

        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint == "index" or endpoint.startswith("installer."):
            return None

        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return redirect(url_for("installer.index"))
        return "Setup is required before LLM Controller CE can be used.", 503

    @app.route("/", methods=["GET"])
    def index():
        if not app.config.get("SETUP_COMPLETE"):
            return redirect(url_for("installer.index"))

        return render_template("installer_runtime_handoff.html")

    @app.route("/<path:path>", methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
    def installer_catch_all(path):
        if not app.config.get("SETUP_COMPLETE"):
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return redirect(url_for("installer.index"))
            return "Setup is required before LLM Controller CE can be used.", 503

        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return redirect(url_for("index"))
        return "Setup has finished. Restart the app to load the full route set.", 503


def _configure_normal_mode(app: Flask):
    from app_settings import get_setting
    from admin_routes import admin
    from analytics_routes import analytics_routes
    from auth import auth, ensure_auth_schema_ready, login_required
    from benchmark_routes import benchmark_routes
    from chat_routes import chat_routes
    from helpers import get_password_settings, init_db
    from model_routes import model_routes
    from settings_routes import settings_routes

    cors_allowed_origins = get_cors_allowed_origins(app.config.get("BOOTSTRAP_CONFIG"))
    CORS(
        app,
        origins=cors_allowed_origins,
        supports_credentials=True,
    )

    csrf = CSRFProtect(app)

    _init_socketio(app)

    app.register_blueprint(chat_routes, url_prefix="/chat")
    app.register_blueprint(model_routes, url_prefix="/model")
    app.register_blueprint(settings_routes, url_prefix="/settings")
    app.register_blueprint(analytics_routes, url_prefix="/analytics")
    app.register_blueprint(auth)
    app.register_blueprint(admin)
    app.register_blueprint(benchmark_routes, url_prefix="/benchmark")

    init_db()
    with app.app_context():
        ensure_auth_schema_ready()

    @app.route("/")
    @login_required()
    def index():
        version = str(get_setting("app.version") or "").strip()
        settings = get_password_settings()
        return render_template(
            "index.html",
            version=version,
            role=session.get("role"),
            settings=settings,
            bootstrap_config_path=app.config.get("BOOTSTRAP_CONFIG_PATH"),
        )


def _request_wants_json():
    path = request.path or ""
    return (
        request.is_json
        or request.accept_mimetypes.best == "application/json"
        or path.startswith(('/api/', '/chat/', '/model/', '/settings/', '/analytics/', '/benchmark/'))
    )


def _classify_csrf_error(e: CSRFError):
    detail = str(getattr(e, "description", "") or e or "").strip()
    lowered = detail.lower()

    if "expired" in lowered:
        code = "csrf_expired"
        message = "This page has been open too long. Reload it and try again."
    elif "session token" in lowered and "missing" in lowered:
        code = "session_expired"
        message = "Your session has expired. Reload the page and sign in again."
    else:
        code = "csrf_invalid"
        message = "This page is out of date. Reload it and try again."

    return {
        "status": "error",
        "code": code,
        "error": message,
        "message": message,
        "reload_required": True,
        "reason": "csrf",
    }

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = get_or_create_secret_key()
    app.permanent_session_lifetime = timedelta(days=30)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    bootstrap_config = get_bootstrap_config()
    installer_mode = is_installer_mode(bootstrap_config)

    app.config["BOOTSTRAP_CONFIG"] = bootstrap_config
    app.config["BOOTSTRAP_CONFIG_PATH"] = resolve_bootstrap_config_path()
    app.config["INSTALLER_MODE"] = installer_mode
    app.config["SETUP_COMPLETE"] = bool(bootstrap_config.get("setup_complete"))
    _register_origin_mismatch_logging(app)

    if installer_mode:
        _configure_installer_mode(app)
    else:
        _configure_normal_mode(app)

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if not _request_wants_json():
            return e.get_response()
        return jsonify(_classify_csrf_error(e)), 400

    @app.errorhandler(Exception)
    def handle_exception(e):
        if isinstance(e, HTTPException):
            return e

        app.logger.exception("Unhandled exception while serving %s %s", request.method, request.path)

        if _request_wants_json():
            return jsonify({"status": "error", "error": "Unexpected server error."}), 500
        return "Unexpected server error.", 500

    return app


app = create_app()


if __name__ == "__main__":
    print("LLM Controller app has Started")
    host, port = get_app_bind(app.config.get("BOOTSTRAP_CONFIG"))
    socketio = app.extensions["llmcontroller_socketio"]
    socketio.run(
        app,
        host=host,
        port=port,
        debug=False,
        use_reloader=False,
    )
