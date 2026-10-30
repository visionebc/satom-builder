"""Project pages: list, new, adopt a package, open from SATOM, overview, settings, bump,
delete."""
from __future__ import annotations

import functools
import re

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template, request,
                   url_for)

from .. import db, kinds, package, profiles, projects, targets
from ..satom_client import SatomError
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


@bp.get("/icons.json")
@login_required
def icons():
    """Every Bootstrap icon the vendored font draws that a plugin may use
    (``package.ICON_RE``), for the icon pickers. Read once from the CSS."""
    resp = jsonify(icon_names())
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@functools.lru_cache(maxsize=1)
def icon_names() -> list[str]:
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "static" / "vendor" / "bootstrap-icons"
           / "bootstrap-icons.min.css").read_text(encoding="utf-8")
    names = sorted(set(re.findall(r"\.(bi-[a-z0-9-]+)::?before", css)))
    return [n for n in names if package.ICON_RE.match(n)]


#: the list's filters (server-side: the page shows only what matches)
CHECKS = ("ok", "problems", "never")


def _check_state(row) -> str:
    lc = db.loads(row["last_check"])
    res = (lc or {}).get("result") if isinstance(lc, dict) else None
    if not isinstance(res, dict):
        return "never"
    return "ok" if res.get("ok") else "problems"


@bp.get("/")
@login_required
def index():
    conn = db.get()
    show_all = g.user.is_admin and request.args.get("all") == "1"
    if show_all:
        rows = conn.execute("SELECT p.*, u.username AS owner FROM projects p JOIN users u "
                            "ON u.id = p.owner_id ORDER BY p.updated_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT p.*, u.username AS owner FROM projects p JOIN users u "
                            "ON u.id = p.owner_id WHERE p.owner_id = ? "
                            "ORDER BY p.updated_at DESC", (g.user.id,)).fetchall()
    tlist = targets.list_targets(conn)
    tnames = {t["id"]: t["name"] for t in tlist}
    f = {"q": (request.args.get("q") or "").strip()[:200],
         "kind": request.args.get("kind") or "", "target": request.args.get("target") or "",
         "adom": request.args.get("adom") or "", "check": request.args.get("check") or ""}
    # where each project installs: one library per target (cached), the rule is pure
    libs: dict = {}
    for t in tlist:
        if targets.has_token(conn, g.user.id, t["id"]):
            libs[t["id"]] = targets.library(conn, t, g.user.id)
    out = []
    for r in rows:
        draft = db.loads(r["draft"], {}) or {}
        where = None
        if r["target_id"] in libs and libs[r["target_id"]]:
            where = targets.install_adom(r["kind"], draft.get("datasets") or [],
                                         str(draft.get("adom") or ""), libs[r["target_id"]])
        adom = (where or {}).get("shown") or str(draft.get("adom") or "")
        item = {"r": r, "where": where, "adom": adom, "check": _check_state(r)}
        hay = " ".join(str(r[k] or "") for k in ("name", "slug", "description")).lower()
        if f["q"] and f["q"].lower() not in hay:
            continue
        if f["kind"] and r["kind"] != f["kind"]:
            continue
        if f["target"] and str(r["target_id"] or "none") != f["target"]:
            continue
        if f["adom"] and adom != f["adom"]:
            continue
        if f["check"] and item["check"] != f["check"]:
            continue
        out.append(item)
    return render_template("projects/index.html", items=out, total=len(rows), tnames=tnames,
                           targets=tlist, f=f, show_all=show_all,
                           adoms=targets.known_adoms(conn, g.user.id),
                           filtered=any(f.values()))


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    conn = db.get()
    tlist = targets.list_targets(conn)
    form = {"name": "", "kind": request.args.get("kind") or "view", "slug": "",
            "version": "1.0.0", "icon": "bi-puzzle", "description": "", "target_id": "",
            "adom": ""}
    if request.method == "POST":
        form.update({k: (request.form.get(k) or "").strip() for k in form})
        tid = int(form["target_id"]) if form["target_id"].isdigit() else None
        try:
            pid = projects.create(conn, g.user.id, name=form["name"], kind=form["kind"],
                                  slug=form["slug"] or _slugify(form["name"]),
                                  version=form["version"] or "1.0.0",
                                  icon=form["icon"] or "bi-puzzle",
                                  description=form["description"], target_id=tid,
                                  adom=form["adom"])
        except projects.ShapeError as exc:
            flash(exc.message, "danger")
        else:
            audit("project.create", form["slug"] or _slugify(form["name"]), id=pid,
                  kind=form["kind"])
            return redirect(url_for("projects.overview", pid=pid))
    return render_template("projects/new.html", form=form, targets=tlist,
                           adoms=targets.known_adoms(conn, g.user.id))


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


def _mine_by_slug(conn, user_id: int) -> dict:
    """``{(kind, slug): project id}`` of this author's projects (the newest wins)."""
    rows = conn.execute("SELECT id, kind, slug FROM projects WHERE owner_id = ? ORDER BY id",
                        (user_id,)).fetchall()
    return {(r["kind"], r["slug"]): r["id"] for r in rows}


def _open_from_satom(conn, t, wanted: list[tuple[str, str]]) -> tuple[list, list]:
    """Adopt each ``(kind, slug)`` from target *t* -> ``(opened, failed)``."""
    opened, failed = [], []
    with targets.client(conn, t, g.user.id) as cli:
        for kind, slug in wanted:
            try:
                parsed = package.read_for_adopt(cli.plugin_package(kind, slug))
                pid = projects.adopt(conn, g.user.id, parsed, target_id=t["id"])
            except SatomError as exc:
                failed.append((slug, exc.message))
                continue
            except (package.PackageError, projects.ShapeError) as exc:
                failed.append((slug, getattr(exc, "message", None) or str(exc)))
                continue
            m = parsed["manifest"]
            audit("project.adopt", m["slug"], id=pid, kind=m["kind"], version=m["version"],
                  source="satom", target=t["id"])
            opened.append((slug, pid))
    return opened, failed


@bp.route("/from-satom", methods=["GET", "POST"])
@login_required
def from_satom():
    """Open what a SATOM target already has (its Plugin Studio apps and Report
    Studio plugins) as projects (contract section 21). Each one is fetched as
    SATOM exports it and adopted like an uploaded package."""
    conn = db.get()
    linked = [t for t in targets.list_targets(conn) if targets.has_token(conn, g.user.id, t["id"])]
    raw = request.values.get("target_id") or ""
    t = next((x for x in linked if str(x["id"]) == raw), linked[0] if linked else None)
    listing, error = None, ""
    if t is not None:
        try:
            with targets.client(conn, t, g.user.id) as cli:
                listing = cli.plugins()
        except SatomError as exc:
            error = exc.message
    mine = _mine_by_slug(conn, g.user.id)
    items = []
    for it in (listing or {}).get("items") or []:
        if not isinstance(it, dict):
            continue
        kind, slug = str(it.get("kind") or ""), str(it.get("slug") or "")
        items.append(dict(it, kind=kind, slug=slug, project=mine.get((kind, slug)),
                          key=f"{kind}:{slug}"))
    if request.method == "POST" and t is not None and listing is not None:
        can = {i["key"] for i in items if i.get("openable") and not i["project"]}
        picked = (sorted(can) if request.form.get("all") else
                  [k for k in request.form.getlist("item") if k in can])
        if not picked:
            flash("Nothing to open: pick what is not in the Builder yet.", "warning")
        else:
            opened, failed = _open_from_satom(conn, t, [tuple(k.split(":", 1)) for k in picked])
            if opened:
                flash(f"Opened {len(opened)} as project(s): "
                      + ", ".join(s for s, _ in opened) + ".", "success")
            for slug, msg in failed:
                flash(f"{slug}: {msg}", "danger")
            if len(opened) == 1 and not failed:
                return redirect(url_for("projects.overview", pid=opened[0][1]))
        return redirect(url_for("projects.from_satom", target_id=t["id"]))
    return render_template("projects/from_satom.html", targets=linked, target=t,
                           items=items, error=error,
                           can_open=sum(1 for i in items if i.get("openable")
                                        and not i["project"]))


@bp.get("/<int:pid>")
@login_required
def overview(pid):
    row = _own(pid)
    conn = db.get()
    target = targets.get_target(conn, row["target_id"])
    has_tok = bool(target) and targets.has_token(conn, g.user.id, target["id"])
    owner = conn.execute("SELECT username FROM users WHERE id = ?",
                         (row["owner_id"],)).fetchone()
    where = None
    if target is not None and has_tok:
        try:
            where = targets.project_adom(conn, row, g.user, target)
        except Exception:  # noqa: BLE001 -- the page shows without it
            where = None
    draft = db.loads(row["draft"], {}) or {}
    return render_template("projects/overview.html", p=row, target=target, has_token=has_tok,
                           where=where, adom=str(draft.get("adom") or ""),
                           adoms=targets.known_adoms(conn, g.user.id),
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
            ("name", "slug", "version", "icon", "description", "kind", "adom")}
    if not body["kind"]:
        body.pop("kind")
    tid = request.form.get("target_id") or ""
    body["target_id"] = int(tid) if tid.isdigit() else None
    try:
        patch = projects.validate_patch(body, conn)
    except projects.ShapeError as exc:
        flash(exc.message, "danger")
    else:
        projects.apply_patch(conn, row, patch, g.user.username)
        switched = patch.get("kind") and patch["kind"] != row["kind"]
        audit("project.update", patch.get("slug", row["slug"]), id=pid,
              **({"kind": f"{row['kind']} -> {patch['kind']}"} if switched else {}))
        flash(("Project saved. It is now a %s: %s" % (patch["kind"], kinds.SWITCH_NOTE))
              if switched else "Project saved.", "success")
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
