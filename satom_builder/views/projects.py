"""Project pages: list, new, adopt a package, overview, settings, bump, delete."""
from __future__ import annotations

import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import db, package, profiles, projects, targets
from ..security import audit, login_required

bp = Blueprint("projects", __name__, url_prefix="/projects")


def _own(pid: int):
    row = projects.get_row(db.get(), pid)
    if row is None or not projects.may_access(g.user, row):
        abort(404)
    return row


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "plugin")[:80].strip("-") or "plugin"


@bp.get("/")
@login_required
def index():
    conn = db.get()
    if g.user.is_admin and request.args.get("all") == "1":
        rows = conn.execute("SELECT p.*, u.username AS owner FROM projects p JOIN users u "
                            "ON u.id = p.owner_id ORDER BY p.updated_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT p.*, u.username AS owner FROM projects p JOIN users u "
                            "ON u.id = p.owner_id WHERE p.owner_id = ? "
                            "ORDER BY p.updated_at DESC", (g.user.id,)).fetchall()
    tnames = {t["id"]: t["name"] for t in targets.list_targets(conn)}
    return render_template("projects/index.html", rows=rows, tnames=tnames,
                           show_all=request.args.get("all") == "1")


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    conn = db.get()
    tlist = targets.list_targets(conn)
    form = {"name": "", "kind": "app", "slug": "", "version": "1.0.0", "icon": "bi-puzzle",
            "description": "", "target_id": ""}
    if request.method == "POST":
        form.update({k: (request.form.get(k) or "").strip() for k in form})
        tid = int(form["target_id"]) if form["target_id"].isdigit() else None
        try:
            pid = projects.create(conn, g.user.id, name=form["name"], kind=form["kind"],
                                  slug=form["slug"] or _slugify(form["name"]),
                                  version=form["version"] or "1.0.0",
                                  icon=form["icon"] or "bi-puzzle",
                                  description=form["description"], target_id=tid)
        except projects.ShapeError as exc:
            flash(exc.message, "danger")
        else:
            audit("project.create", form["slug"] or _slugify(form["name"]), id=pid,
                  kind=form["kind"])
            return redirect(url_for("projects.overview", pid=pid))
    return render_template("projects/new.html", form=form, targets=tlist)


@bp.route("/adopt", methods=["GET", "POST"])
@login_required
def adopt():
    conn = db.get()
    tlist = targets.list_targets(conn)
    if request.method == "POST":
        f = request.files.get("package")
        tid = request.form.get("target_id") or ""
        tid = int(tid) if tid.isdigit() else None
        if f is None:
            flash("Choose a .satomplugin file.", "danger")
        else:
            data = f.stream.read(package.MAX_PACKAGE + 1)
            try:
                parsed = package.read_for_adopt(data)
                pid = projects.adopt(conn, g.user.id, parsed, target_id=tid)
            except (package.PackageError, projects.ShapeError) as exc:
                flash(getattr(exc, "message", None) or str(exc), "danger")
            else:
                m = parsed["manifest"]
                audit("project.adopt", m["slug"], id=pid, kind=m["kind"],
                      version=m["version"], was_signed=parsed["signed"])
                flash("Package adopted. " + ("Its signature was dropped: the Builder signs "
                                             "with this installation's key."
                                             if parsed["signed"] else ""), "success")
                return redirect(url_for("projects.overview", pid=pid))
    return render_template("projects/adopt.html", targets=tlist)


@bp.get("/<int:pid>")
@login_required
def overview(pid):
    row = _own(pid)
    conn = db.get()
    target = targets.get_target(conn, row["target_id"])
    has_tok = bool(target) and targets.has_token(conn, g.user.id, target["id"])
    owner = conn.execute("SELECT username FROM users WHERE id = ?",
                         (row["owner_id"],)).fetchone()
    return render_template("projects/overview.html", p=row, target=target, has_token=has_tok,
                           owner=owner["username"] if owner else "?",
                           last_check=db.loads(row["last_check"]),
                           assets=projects.asset_list(conn, pid),
                           profiles=profiles.list_profiles(conn, enabled_only=True),
                           targets=targets.list_targets(conn))


@bp.post("/<int:pid>/settings")
@login_required
def settings(pid):
    row = _own(pid)
    conn = db.get()
    body = {k: (request.form.get(k) or "").strip() for k in
            ("name", "slug", "version", "icon", "description")}
    tid = request.form.get("target_id") or ""
    body["target_id"] = int(tid) if tid.isdigit() else None
    try:
        patch = projects.validate_patch(body, conn)
    except projects.ShapeError as exc:
        flash(exc.message, "danger")
    else:
        projects.apply_patch(conn, row, patch, g.user.username)
        audit("project.update", patch.get("slug", row["slug"]), id=pid)
        flash("Project saved.", "success")
    return redirect(url_for("projects.overview", pid=pid))


@bp.post("/<int:pid>/bump")
@login_required
def bump(pid):
    row = _own(pid)
    part = request.form.get("part") or "patch"
    try:
        v = package.bump(row["version"], part)
    except package.PackageError as exc:
        flash(str(exc), "danger")
    else:
        projects.apply_patch(db.get(), row, {"version": v}, g.user.username)
        audit("project.bump", row["slug"], version=v)
        flash(f"Version is now {v}.", "success")
    return redirect(url_for("projects.overview", pid=pid))


@bp.post("/<int:pid>/delete")
@login_required
def delete(pid):
    row = _own(pid)
    if request.form.get("confirm") != row["slug"]:
        flash("Type the project's slug to confirm the deletion.", "danger")
        return redirect(url_for("projects.overview", pid=pid))
    db.get().execute("DELETE FROM projects WHERE id = ?", (pid,))
    audit("project.delete", row["slug"], id=pid)
    flash(f"Project {row['name']} deleted.", "success")
    return redirect(url_for("projects.index"))
