"""The Library page: what a plugin builds ON, read from the author's SATOM
target with the author's token (contract section 14), plus what the target's
rules pack says about writing one (step kinds, script SDK, author guide).

Structure only, cached per (target, author) like the snapshot: never a row of
the target's data, never a secret.

| Route | Answer |
|---|---|
| ``GET /library`` | the page |
| ``GET /api/targets/<id>/library`` | ``satom.builder-library/1`` (``?refresh=1`` refetches) |
| ``GET /api/targets/<id>/library/fortinet/<product>/<version>`` | a build's endpoints (``?endpoint=`` that endpoint's fields) |
| ``GET /api/targets/<id>/guide`` | ``{"guide", "sdk_doc", "schema"}`` from the rules pack and snapshot |
"""
from __future__ import annotations

from flask import Blueprint, current_app, g, render_template, request

from .. import db, rules, runtime, targets
from ..satom_client import SatomError
from ..security import api_error, login_required

bp = Blueprint("library", __name__)


def _target(tid: int):
    conn = db.get()
    t = targets.get_target(conn, tid)
    if t is None:
        return None, api_error("not_found", "No such SATOM target.", 404)
    if not targets.has_token(conn, g.user.id, tid):
        return None, api_error("no_token", f"You have no SATOM token for {t['name']} yet: "
                                           "add one under My tokens.", 409)
    return t, None


def _cached(t, key: str, fetch, refresh: bool):
    conn = db.get()
    ttl = float(current_app.config["SATOM_BUILDER_SNAPSHOT_TTL"])
    if not refresh:
        row = conn.execute("SELECT fetched_at, data FROM library_cache WHERE target_id = ? AND "
                           "user_id = ? AND key = ?", (t["id"], g.user.id, key)).fetchone()
        if row and db.now() - row["fetched_at"] < ttl:
            data = db.loads(row["data"])
            if isinstance(data, dict):
                data["cached_at"] = row["fetched_at"]
                return data
    with targets.client(conn, t, g.user.id) as c:
        data = fetch(c)
    conn.execute("INSERT INTO library_cache (target_id, user_id, key, fetched_at, data) "
                 "VALUES (?,?,?,?,?) ON CONFLICT(target_id, user_id, key) DO UPDATE SET "
                 "fetched_at = excluded.fetched_at, data = excluded.data",
                 (t["id"], g.user.id, key, db.now(), db.dumps(data)))
    data["cached_at"] = db.now()
    return data


@bp.get("/library")
@login_required
def page():
    conn = db.get()
    mine = [dict(id=t["id"], name=t["name"]) for t in targets.list_targets(conn)
            if targets.has_token(conn, g.user.id, t["id"])]
    return render_template("library/index.html", targets=mine)


@bp.get("/api/targets/<int:tid>/library")
@login_required
def library(tid):
    t, err = _target(tid)
    if err:
        return err
    try:
        return _cached(t, "library", lambda c: c.library(), request.args.get("refresh") == "1")
    except SatomError as exc:
        return api_error(exc.code, exc.message, exc.status)


@bp.get("/api/targets/<int:tid>/library/fortinet/<product>/<version>")
@login_required
def library_build(tid, product, version):
    t, err = _target(tid)
    if err:
        return err
    endpoint = request.args.get("endpoint") or ""
    key = f"fortinet/{product}/{version}" + (f"#{endpoint}" if endpoint else "")
    try:
        return _cached(t, key[:400], lambda c: c.library_build(product, version, endpoint),
                       request.args.get("refresh") == "1")
    except SatomError as exc:
        return api_error(exc.code, exc.message, exc.status)


@bp.get("/api/targets/<int:tid>/guide")
@login_required
def guide(tid):
    t, err = _target(tid)
    if err:
        return err
    conn = db.get()
    try:
        snap = targets.snapshot(conn, t, g.user.id)
        pack = targets.rules_pack(conn, t, g.user.id, snap)
        text = pack.author_guide(snap)
    except SatomError as exc:
        return api_error(exc.code, exc.message, exc.status)
    except rules.RulesError as exc:
        return api_error("rules_pack", str(exc), 409)
    sdk = ""
    try:
        sdk = runtime.info(pack).get("sdk_doc") or ""
    except runtime.RuntimeUnavailable:
        pass
    return {"guide": text, "sdk_doc": sdk, "schema": snap.get("schema") or {},
            "satom_version": snap.get("satom_version") or ""}
