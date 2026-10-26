"""SATOM Builder: author, validate, rehearse, sign and import SATOM plugins.

Flask factory per contract section 10: ``satom_builder:create_app()``.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from flask import Flask, g, jsonify, render_template, request

from . import config as config_mod
from . import db, security
from .secrets_store import install_log_filter

__version__ = config_mod.version()


def create_app(overrides: dict | None = None) -> Flask:
    cfg = config_mod.load(overrides)
    app = Flask(__name__)
    app.config.update(cfg)
    if not cfg.get("SECRET_KEY") or not cfg.get("FERNET_KEY"):
        raise RuntimeError("SECRET_KEY and FERNET_KEY must be set (see "
                           f"{cfg.get('SATOM_BUILDER_ENV')}).")
    data = Path(cfg["SATOM_BUILDER_DATA"])
    data.mkdir(parents=True, exist_ok=True)
    app.config.update(
        VERSION=__version__,
        DB_PATH=str(db.path_in(data)),
        SESSION_COOKIE_NAME="satom_builder_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=str(cfg.get("SATOM_BUILDER_COOKIE_SECURE", "1")).lower()
        in ("1", "true", "yes"),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        JSON_SORT_KEYS=False,
        TEMPLATES_AUTO_RELOAD=False,
    )
    if overrides:
        # tests may override the derived values too
        for k in ("SESSION_COOKIE_SECURE", "MAX_CONTENT_LENGTH", "TESTING"):
            if k in overrides:
                app.config[k] = overrides[k]
    install_log_filter()

    db.migrate(app.config["DB_PATH"])
    from .ai_loop import fail_stale_jobs
    from .chat import fail_stale as fail_stale_replies
    from .runtime import fail_stale as fail_stale_runs
    with db.session(app.config["DB_PATH"]) as conn:
        fail_stale_jobs(conn)
        fail_stale_runs(conn)
        fail_stale_replies(conn)

    app.teardown_appcontext(db.close_request_db)
    app.before_request(security.new_nonce)
    app.before_request(security.upload_limits)
    app.before_request(security.load_user)
    app.before_request(security.csrf_protect)
    app.after_request(security.security_headers)

    @app.context_processor
    def _globals():
        # csrf_value is a string; templates use it. ``csrf_token`` stays callable for
        # pages that call it, and a view may pass ``csrf_token`` as a string.
        from . import kinds
        return {"csrf_token": security.csrf_token, "csrf_value": security.csrf_token(),
                "current_user": g.get("user"),
                "csp_nonce": g.get("csp_nonce", ""), "app_version": __version__,
                "KINDS": kinds}

    @app.template_filter("datetime")
    def _datetime(ts):
        import datetime as _dt
        try:
            return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime(
                "%Y-%m-%d %H:%M UTC")
        except (TypeError, ValueError, OverflowError):
            return ""

    @app.template_filter("fromjson")
    def _fromjson(text):
        return db.loads(text)

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok", "version": __version__})

    from .views import register
    register(app)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(405)
    @app.errorhandler(413)
    def _http_error(err):
        code = getattr(err, "code", 500) or 500
        desc = getattr(err, "description", "") or ""
        if request.path.startswith("/api/"):
            names = {400: "bad_request", 403: "forbidden", 404: "not_found",
                     405: "method_not_allowed", 413: "too_large"}
            msg = desc if code in (400, 413) else {
                403: "You may not do that.", 404: "Not found.",
                405: "Method not allowed."}.get(code, desc)
            return security.api_error(names.get(code, "error"), msg, code)
        return render_template("error.html", code=code, message=desc), code

    @app.errorhandler(500)
    def _server_error(err):
        if request.path.startswith("/api/"):
            return security.api_error("server_error", "Internal error; see the log.", 500)
        return render_template("error.html", code=500,
                               message="Internal error; see the log."), 500

    return app
