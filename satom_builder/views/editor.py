"""The structured editor page.

The route and the context passed to ``editor/index.html`` belong to the core;
the template and its JavaScript (``static/js/editor/``) belong to the editor.
Context:

* ``project`` -- the project in the contract section-11 shape (the same
  dict ``GET /api/projects/<id>`` returns), plus ``"owner"`` (user name);
* ``target``  -- ``{"id", "name", "base_url", "has_token"}`` or ``None``;
* ``csrf_token`` -- the session's CSRF token (also in ``<meta name="csrf-token">``
  from ``base.html``; send it as ``X-CSRF-Token``);
* ``api`` -- the section-11 URLs for this project, ready to use.
"""
from __future__ import annotations

from flask import Blueprint, abort, g, render_template, url_for

from .. import db, projects, targets
from ..security import csrf_token, login_required

bp = Blueprint("editor", __name__)


@bp.get("/projects/<int:pid>/edit")
@login_required
def edit(pid):
    conn = db.get()
    row = projects.get_row(conn, pid)
    if row is None or not projects.may_access(g.user, row):
        abort(404)
    project = projects.to_api(conn, row)
    owner = conn.execute("SELECT username FROM users WHERE id = ?",
                         (row["owner_id"],)).fetchone()
    project["owner"] = owner["username"] if owner else ""
    t = targets.get_target(conn, row["target_id"])
    target = None
    if t is not None:
        target = {"id": t["id"], "name": t["name"], "base_url": t["base_url"],
                  "has_token": targets.has_token(conn, g.user.id, t["id"])}
    api = {
        "project": f"/api/projects/{pid}",
        "assets": f"/api/projects/{pid}/assets",
        "check": f"/api/projects/{pid}/check",
        "validate_remote": f"/api/projects/{pid}/validate-remote",
        "rehearse": f"/api/projects/{pid}/rehearse",
        "import": f"/api/projects/{pid}/import",
        "package": f"/api/projects/{pid}/package",
        "generate_preview": f"/api/projects/{pid}/generate/preview",
        "generate": f"/api/projects/{pid}/generate",
        "jobs": "/api/jobs/",
        "schema": f"/api/targets/{t['id']}/schema" if t is not None else None,
        "snapshot": f"/api/targets/{t['id']}/snapshot" if t is not None else None,
        "overview": url_for("projects.overview", pid=pid),
    }
    return render_template("editor/index.html", project=project, target=target,
                           csrf_token=csrf_token(), api=api)
