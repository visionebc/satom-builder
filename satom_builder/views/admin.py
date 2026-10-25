"""Administrator pages: SATOM targets, users, directory sign-in, AI profiles,
updates and the audit log."""
from __future__ import annotations

import time

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)
from werkzeug.security import generate_password_hash

from .. import db, directory, profiles, updates
from ..ai_adapters import EFFORTS, AIError, is_first_party, test_prompt
from ..satom_client import check_base_url, check_ca_pem
from ..security import admin_required, audit

bp = Blueprint("admin", __name__, url_prefix="/admin")

USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@-\\")


# --------------------------------------------------------------------------
# SATOM targets
# --------------------------------------------------------------------------
@bp.get("/targets")
@admin_required
def targets_page():
    conn = db.get()
    rows = conn.execute("SELECT t.*, (SELECT COUNT(*) FROM tokens k WHERE k.target_id = t.id) "
                        "AS ntokens, (SELECT COUNT(*) FROM projects p WHERE p.target_id = t.id)"
                        " AS nprojects FROM targets t ORDER BY name").fetchall()
    edit = None
    if request.args.get("edit", "").isdigit():
        edit = conn.execute("SELECT * FROM targets WHERE id = ?",
                            (int(request.args["edit"]),)).fetchone()
    return render_template("admin/targets.html", rows=rows, edit=edit)


@bp.post("/targets")
@admin_required
def targets_save():
    conn = db.get()
    tid = request.form.get("id") or ""
    name = (request.form.get("name") or "").strip()
    try:
        if not name or len(name) > 80:
            raise ValueError("The name must be 1 to 80 characters.")
        url = check_base_url(request.form.get("base_url") or "")
        ca = check_ca_pem(request.form.get("ca_pem") or "")
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.targets_page", edit=tid or None))
    verify = 1 if request.form.get("verify_tls") else 0
    try:
        if tid.isdigit():
            conn.execute("UPDATE targets SET name = ?, base_url = ?, ca_pem = ?, verify_tls = ? "
                         "WHERE id = ?", (name, url, ca, verify, int(tid)))
            conn.execute("DELETE FROM snapshots WHERE target_id = ?", (int(tid),))
            audit("target.update", name, url=url, verify_tls=bool(verify), ca=bool(ca))
        else:
            conn.execute("INSERT INTO targets (name, base_url, ca_pem, verify_tls, created_at) "
                         "VALUES (?,?,?,?,?)", (name, url, ca, verify, db.now()))
            audit("target.create", name, url=url, verify_tls=bool(verify), ca=bool(ca))
    except Exception as exc:  # sqlite IntegrityError: duplicate name
        if "UNIQUE" in str(exc):
            flash("Another target already has that name.", "danger")
            return redirect(url_for("admin.targets_page"))
        raise
    if not verify:
        flash("TLS verification is OFF for this target: use it only on a lab network.",
              "warning")
    flash("Target saved.", "success")
    return redirect(url_for("admin.targets_page"))


@bp.post("/targets/<int:tid>/delete")
@admin_required
def targets_delete(tid):
    conn = db.get()
    row = conn.execute("SELECT name FROM targets WHERE id = ?", (tid,)).fetchone()
    if row:
        conn.execute("DELETE FROM targets WHERE id = ?", (tid,))
        audit("target.delete", row["name"])
        flash(f"Target {row['name']} deleted; its tokens are gone and its projects have no "
              "target now.", "success")
    return redirect(url_for("admin.targets_page"))


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------
def _admins(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1"
                        ).fetchone()[0]


@bp.get("/users")
@admin_required
def users_page():
    rows = db.get().execute("SELECT * FROM users ORDER BY username").fetchall()
    return render_template("admin/users.html", rows=rows)


@bp.post("/users")
@admin_required
def users_create():
    conn = db.get()
    username = (request.form.get("username") or "").strip()
    role = request.form.get("role") or "author"
    source = request.form.get("source") or "local"
    password = request.form.get("password") or ""
    if not username or len(username) > 80 or not set(username) <= USERNAME_CHARS:
        flash("User names are 1 to 80 letters, digits and . _ @ - \\.", "danger")
    elif role not in ("admin", "author"):
        flash("Unknown role.", "danger")
    elif source not in ("local", "ldap", "ad", "radius"):
        flash("Unknown source.", "danger")
    elif source == "local" and len(password) < 12:
        flash("A local user needs a password of at least 12 characters.", "danger")
    elif conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        flash("That user exists already.", "danger")
    else:
        conn.execute("INSERT INTO users (username, password_hash, role, source, enabled, "
                     "created_at) VALUES (?,?,?,?,1,?)",
                     (username, generate_password_hash(password) if source == "local" else None,
                      role, source, db.now()))
        audit("user.create", username, role=role, source=source)
        flash(f"User {username} created.", "success")
    return redirect(url_for("admin.users_page"))


@bp.post("/users/<int:uid>")
@admin_required
def users_update(uid):
    conn = db.get()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        abort(404)
    action = request.form.get("action")
    is_last_admin = row["role"] == "admin" and row["enabled"] and _admins(conn) <= 1
    if action == "role":
        role = request.form.get("role")
        if role not in ("admin", "author"):
            flash("Unknown role.", "danger")
        elif role != "admin" and is_last_admin:
            flash("This is the last enabled administrator: it keeps its role.", "danger")
        else:
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
            audit("user.role", row["username"], role=role)
            flash(f"{row['username']} is now {role}.", "success")
    elif action in ("enable", "disable"):
        if action == "disable" and (uid == g.user.id or is_last_admin):
            flash("You cannot disable yourself or the last administrator.", "danger")
        else:
            conn.execute("UPDATE users SET enabled = ? WHERE id = ?",
                         (1 if action == "enable" else 0, uid))
            audit(f"user.{action}", row["username"])
            flash(f"{row['username']} {action}d.", "success")
    elif action == "password":
        pw = request.form.get("password") or ""
        if row["source"] != "local":
            flash("Directory users sign in with their directory password.", "danger")
        elif len(pw) < 12:
            flash("The password must be at least 12 characters.", "danger")
        else:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                         (generate_password_hash(pw), uid))
            audit("user.password_reset", row["username"])
            flash(f"Password of {row['username']} reset.", "success")
    elif action == "delete":
        if uid == g.user.id or is_last_admin:
            flash("You cannot delete yourself or the last administrator.", "danger")
        else:
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            audit("user.delete", row["username"])
            flash(f"User {row['username']} deleted with their projects and tokens.",
                  "success")
    else:
        flash("Unknown action.", "danger")
    return redirect(url_for("admin.users_page"))


# --------------------------------------------------------------------------
# directory sign-in
# --------------------------------------------------------------------------
@bp.get("/directory")
@admin_required
def directory_page():
    cfg = directory.load(db.get())
    return render_template("admin/directory.html", cfg=cfg,
                           bind_set=bool(cfg.get("bind_password_enc")),
                           secret_set=bool(cfg.get("secret_enc")))


@bp.post("/directory")
@admin_required
def directory_save():
    conn = db.get()
    cfg = directory.load(conn)
    f = request.form
    mode = f.get("mode") or "off"
    if mode not in directory.MODES:
        flash("Unknown mode.", "danger")
        return redirect(url_for("admin.directory_page"))
    port = (f.get("port") or "").strip()
    if port and (not port.isdigit() or not 0 < int(port) < 65536):
        flash("The port must be a number from 1 to 65535.", "danger")
        return redirect(url_for("admin.directory_page"))
    try:
        ca = check_ca_pem(f.get("ca_pem") or "") if (f.get("ca_pem") or "").strip() else ""
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.directory_page"))
    try:
        timeout = max(1, min(60, int(f.get("timeout") or 8)))
    except ValueError:
        timeout = 8
    cfg.update({
        "mode": mode, "host": (f.get("host") or "").strip()[:255], "port": port,
        "use_ssl": bool(f.get("use_ssl")), "start_tls": bool(f.get("start_tls")),
        "tls_verify": bool(f.get("tls_verify")),
        "ca_pem": ca.strip() if ca else (cfg.get("ca_pem", "") if not f.get("clear_ca")
                                         else ""),
        "base_dn": (f.get("base_dn") or "").strip()[:500],
        "bind_dn": (f.get("bind_dn") or "").strip()[:500],
        "user_attr": (f.get("user_attr") or "").strip()[:80],
        "user_filter": (f.get("user_filter") or "").strip()[:500],
        "ad_domain": (f.get("ad_domain") or "").strip()[:255],
        "timeout": timeout, "nas_id": (f.get("nas_id") or "satom-builder").strip()[:64],
        "auto_create": bool(f.get("auto_create")),
    })
    directory.set_secret_fields(cfg, f)
    directory.save(conn, cfg)
    audit("directory.update", mode, host=cfg["host"], tls_verify=cfg["tls_verify"])
    flash("Directory settings saved.", "success")
    return redirect(url_for("admin.directory_page"))


@bp.post("/directory/test")
@admin_required
def directory_test():
    conn = db.get()
    ok, detail = directory.test(conn, (request.form.get("test_username") or "").strip(),
                                request.form.get("test_password") or "")
    audit("directory.test", directory.load(conn).get("mode", ""), ok=ok)
    flash(("Test passed: " if ok else "Test failed: ") + detail, "success" if ok else "danger")
    return redirect(url_for("admin.directory_page"))


# --------------------------------------------------------------------------
# AI profiles
# --------------------------------------------------------------------------
@bp.get("/ai")
@admin_required
def ai_page():
    conn = db.get()
    rows = [profiles.as_dict(r) for r in profiles.list_profiles(conn)]
    edit = None
    if request.args.get("edit", "").isdigit():
        r = profiles.get(conn, int(request.args["edit"]))
        edit = profiles.as_dict(r) if r else None
    for r in rows + ([edit] if edit else []):
        r["fallbacks_effective"] = (r["adapter"] == "anthropic" and r["fallbacks"]
                                    and is_first_party(r["base_url"]))
    return render_template("admin/ai.html", rows=rows, edit=edit, efforts=EFFORTS)


@bp.post("/ai")
@admin_required
def ai_save():
    conn = db.get()
    pid = request.form.get("id") or ""
    existing = profiles.get(conn, int(pid)) if pid.isdigit() else None
    try:
        values = profiles.clean_form(request.form, existing)
        profiles.save(conn, values, existing["id"] if existing else None)
    except profiles.ProfileError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.ai_page", edit=pid or None))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            flash("Another profile already has that name.", "danger")
            return redirect(url_for("admin.ai_page"))
        raise
    audit("ai_profile." + ("update" if existing else "create"), values["name"],
          adapter=values["adapter"], model=values["model"],
          key="changed" if "key_enc" in values and values["key_enc"] else
          ("cleared" if "key_enc" in values else "kept"))
    flash("AI profile saved.", "success")
    return redirect(url_for("admin.ai_page"))


@bp.post("/ai/<int:pid>/delete")
@admin_required
def ai_delete(pid):
    conn = db.get()
    row = profiles.get(conn, pid)
    if row:
        conn.execute("DELETE FROM ai_profiles WHERE id = ?", (pid,))
        audit("ai_profile.delete", row["name"])
        flash(f"Profile {row['name']} deleted.", "success")
    return redirect(url_for("admin.ai_page"))


@bp.post("/ai/<int:pid>/test")
@admin_required
def ai_test(pid):
    conn = db.get()
    row = profiles.get(conn, pid)
    if row is None:
        abort(404)
    t0 = time.time()
    try:
        text = test_prompt(profiles.adapter_for(row))
    except AIError as exc:
        audit("ai_profile.test", row["name"], ok=False, error=exc.code)
        flash(f"Test of {row['name']} failed: {exc.message}", "danger")
    else:
        audit("ai_profile.test", row["name"], ok=True)
        flash(f"Test of {row['name']} passed in {time.time() - t0:.1f} s; the model "
              f"answered: {text.strip()[:80]!r}", "success")
    return redirect(url_for("admin.ai_page"))


# --------------------------------------------------------------------------
# updates (contract section 9)
# --------------------------------------------------------------------------
@bp.get("/updates")
@admin_required
def updates_page():
    data = current_app.config["SATOM_BUILDER_DATA"]
    return render_template("admin/updates.html", pending=updates.pending(data),
                           statuses=updates.statuses(data),
                           max_mb=int(current_app.config["SATOM_BUILDER_UPDATE_MAX_MB"]))


@bp.post("/updates")
@admin_required
def updates_enqueue():
    cfg = current_app.config
    data = cfg["SATOM_BUILDER_DATA"]
    kind = request.form.get("kind") or "package"
    max_bytes = int(cfg["SATOM_BUILDER_UPDATE_MAX_MB"]) * 1024 * 1024
    try:
        upload = ""
        if kind == "package":
            f = request.files.get("package")
            if f is None or not f.filename:
                raise ValueError("Choose an update package file.")
            upload = updates.save_upload(data, f.filename, f.stream, max_bytes)
        req = updates.enqueue(data, kind=kind, requested_by=g.user.username,
                              upload_name=upload,
                              allow_downgrade=bool(request.form.get("allow_downgrade")))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("admin.updates_page"))
    audit("update.enqueue", req["id"], kind=kind, path=req["path"],
          allow_downgrade=req["allow_downgrade"])
    flash(f"Request {req['id'][:8]} queued. The updater runs it as root and reports "
          "below.", "success")
    return redirect(url_for("admin.updates_page"))


# --------------------------------------------------------------------------
# audit log
# --------------------------------------------------------------------------
@bp.get("/audit")
@admin_required
def audit_page():
    conn = db.get()
    q = (request.args.get("q") or "").strip()[:80]
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    per = 100
    where, args = "", []
    if q:
        where = "WHERE username LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' " \
                "OR object LIKE ? ESCAPE '\\'"
        like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args = [like, like, like]
    total = conn.execute(f"SELECT COUNT(*) FROM audit {where}", args).fetchone()[0]
    rows = conn.execute(f"SELECT * FROM audit {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                        args + [per, (page - 1) * per]).fetchall()
    return render_template("admin/audit.html", rows=rows, q=q, page=page,
                           pages=max(1, (total + per - 1) // per), total=total)
