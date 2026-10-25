"""Sessions, the current user, roles, CSRF, login rate-limit and the audit log.

CSRF: one random token per session. Every POST/PUT/PATCH/DELETE must carry it,
either as the ``X-CSRF-Token`` header (JSON API, contract section 11; the value
comes from ``<meta name="csrf-token">``) or as the ``csrf_token`` form field.
"""
from __future__ import annotations

import functools
import hmac
import secrets
import time

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from . import db

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# login rate-limit: failures inside the window
WINDOW = 15 * 60
MAX_PER_USER = 5
MAX_PER_IP = 20


# --------------------------------------------------------------------------
# CSRF
# --------------------------------------------------------------------------
def csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = session["csrf"] = secrets.token_urlsafe(32)
    return tok


def upload_limits():
    """before_request hook (runs before csrf_protect parses a form): the
    per-endpoint body limit. The app-wide limit is 16 MB."""
    if request.endpoint == "admin.updates_enqueue":
        mb = int(current_app.config["SATOM_BUILDER_UPDATE_MAX_MB"])
        request.max_content_length = (mb + 1) * 1024 * 1024


def csrf_protect():
    """before_request hook."""
    if request.method not in UNSAFE_METHODS:
        return None
    expected = session.get("csrf") or ""
    sent = request.headers.get("X-CSRF-Token") or ""
    if not sent and request.mimetype in ("application/x-www-form-urlencoded",
                                         "multipart/form-data"):
        sent = request.form.get("csrf_token", "")
    if expected and sent and hmac.compare_digest(expected, sent):
        return None
    if wants_json():
        return api_error("csrf", "The CSRF token is missing or wrong. Reload the page.", 400)
    abort(400, description="The CSRF token is missing or wrong. Reload the page and try again.")


def wants_json() -> bool:
    return request.path.startswith("/api/")


def api_error(code: str, message: str, status: int = 400):
    resp = jsonify({"error": code, "message": message})
    resp.status_code = status
    return resp


# --------------------------------------------------------------------------
# current user
# --------------------------------------------------------------------------
class User:
    __slots__ = ("id", "username", "role", "source", "enabled")

    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.role = row["role"]
        self.source = row["source"]
        self.enabled = bool(row["enabled"])

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_authenticated(self) -> bool:
        return True


def load_user():
    """before_request hook: g.user from the session (or None)."""
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    row = db.get().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None or not row["enabled"] or session.get("role") != row["role"]:
        # deleted, disabled or role changed: the session ends here
        session.clear()
        return
    g.user = User(row)


def login_user(row) -> None:
    session.clear()
    session.permanent = True
    session["uid"] = row["id"]
    session["role"] = row["role"]
    session["at"] = time.time()
    csrf_token()


def logout_user() -> None:
    session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapper(*a, **kw):
        if g.get("user") is None:
            if wants_json():
                return api_error("unauthenticated", "Sign in first.", 401)
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")
                                    if request.method == "GET" else None))
        return view(*a, **kw)
    return wrapper


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapper(*a, **kw):
        if not g.user.is_admin:
            if wants_json():
                return api_error("forbidden", "Administrators only.", 403)
            abort(403)
        return view(*a, **kw)
    return wrapper


# --------------------------------------------------------------------------
# login rate-limit (stored in SQLite so every gunicorn worker shares it)
# --------------------------------------------------------------------------
def _ip() -> str:
    # gunicorn listens on 127.0.0.1 behind nginx: only then are the proxy's
    # headers trusted (X-Real-IP, else the last X-Forwarded-For hop).
    if request.remote_addr in ("127.0.0.1", "::1"):
        real = request.headers.get("X-Real-IP", "").strip()
        if not real:
            real = request.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
        return (real or request.remote_addr or "")[:64]
    return (request.remote_addr or "")[:64]


def client_ip() -> str:
    return _ip()


def login_blocked(username: str) -> int:
    """Seconds until the next attempt is allowed, or 0."""
    conn = db.get()
    since = time.time() - WINDOW
    conn.execute("DELETE FROM login_failures WHERE at < ?", (since,))
    worst = 0
    for key, limit in (("user:" + username.lower(), MAX_PER_USER),
                       ("ip:" + _ip(), MAX_PER_IP)):
        rows = conn.execute("SELECT at FROM login_failures WHERE key = ? AND at >= ? "
                            "ORDER BY at", (key, since)).fetchall()
        if len(rows) >= limit:
            wait = int(rows[len(rows) - limit]["at"] + WINDOW - time.time()) + 1
            worst = max(worst, wait)
    return worst


def record_login_failure(username: str) -> None:
    conn = db.get()
    t = time.time()
    conn.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)",
                 ("user:" + username.lower(), t))
    conn.execute("INSERT INTO login_failures (key, at) VALUES (?, ?)", ("ip:" + _ip(), t))


def clear_login_failures(username: str) -> None:
    db.get().execute("DELETE FROM login_failures WHERE key = ?", ("user:" + username.lower(),))


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def audit(action: str, obj: str = "", conn=None, username: str | None = None, **detail) -> None:
    """Record a security-relevant action. ``detail`` must never hold secrets."""
    from .secrets_store import redact
    if username is None:
        u = g.get("user")
        username = u.username if u else ""
    try:
        ip = _ip()
    except RuntimeError:
        ip = ""
    c = conn or db.get()
    c.execute("INSERT INTO audit (at, username, action, object, detail, ip) "
              "VALUES (?, ?, ?, ?, ?, ?)",
              (time.time(), username or "", action, str(obj)[:200],
               redact(db.dumps(detail))[:4000], ip))


def security_headers(resp):
    nonce = g.get("csp_nonce", "")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data:; "
        "connect-src 'self'; frame-src 'self' blob: data:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'self'")
    if request.path.startswith("/api/") or g.get("user") is not None:
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def new_nonce():
    g.csp_nonce = secrets.token_urlsafe(16)


def is_testing() -> bool:
    return bool(current_app.config.get("TESTING"))
