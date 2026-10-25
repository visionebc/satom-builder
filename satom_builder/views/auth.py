"""Sign-in and sign-out: local users first, then the configured directory."""
from __future__ import annotations

import time

from flask import Blueprint, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .. import db, directory, security

bp = Blueprint("auth", __name__)

# A hash to compare against when the user does not exist, so a wrong user
# name costs the same time as a wrong password.
_DUMMY_HASH = generate_password_hash("not-a-real-password-satom-builder")


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("projects.index")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None and request.method == "GET":
        return redirect(url_for("projects.index"))
    conn = db.get()
    dir_mode = directory.load(conn).get("mode", "off")
    error = ""
    username = ""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()[:80]
        password = request.form.get("password") or ""
        wait = security.login_blocked(username) if username else 0
        if not username or not password:
            error = "Enter your user name and password."
        elif wait:
            error = (f"Too many failed sign-ins. Try again in "
                     f"{max(1, (wait + 59) // 60)} minute(s).")
            security.audit("login.blocked", username, username=username)
        else:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            ok, source = False, ""
            if row is not None and row["password_hash"]:
                ok = check_password_hash(row["password_hash"], password)
                source = "local"
            elif row is None or row["source"] != "local":
                check_password_hash(_DUMMY_HASH, password)
                if dir_mode in ("ldap", "ad", "radius"):
                    ok, _detail, source = directory.authenticate(conn, username, password)
                    if ok and row is None:
                        if directory.load(conn).get("auto_create"):
                            conn.execute("INSERT INTO users (username, password_hash, role, "
                                         "source, enabled, created_at) VALUES "
                                         "(?, NULL, 'author', ?, 1, ?)",
                                         (username, source, db.now()))
                            row = conn.execute("SELECT * FROM users WHERE username = ?",
                                               (username,)).fetchone()
                            security.audit("user.create", username, username=username,
                                           source=source, by="directory sign-in")
                        else:
                            ok = False
            else:
                check_password_hash(_DUMMY_HASH, password)
            if ok and row is not None and row["enabled"]:
                security.clear_login_failures(username)
                conn.execute("UPDATE users SET last_login = ? WHERE id = ?",
                             (time.time(), row["id"]))
                security.login_user(row)
                security.audit("login.ok", username, username=row["username"], source=source)
                return redirect(_safe_next(request.form.get("next")))
            security.record_login_failure(username)
            security.audit("login.fail", username, username=username)
            error = "Wrong user name or password, or the account is disabled."
    return render_template("login.html", error=error, username=username,
                           next=request.args.get("next") or request.form.get("next") or "",
                           dir_mode=dir_mode), (401 if error else 200)


@bp.post("/logout")
def logout():
    if g.get("user") is not None:
        security.audit("logout", g.user.username)
    security.logout_user()
    return redirect(url_for("auth.login"))
