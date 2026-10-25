"""Internal JSON API of the assistant chat (contract section 11, 0.2).

| Route | Body -> Answer |
|---|---|
| ``GET /api/projects/<id>/chats`` | -> ``{"chats": [{id, title, updated_at, messages, running}]}`` |
| ``POST /api/projects/<id>/chats`` | ``{"title"?, "profile_id"?}`` -> 201 the conversation |
| ``GET /api/chats/<cid>`` | -> the conversation with its messages and files |
| ``PUT /api/chats/<cid>`` | ``{"title"?, "profile_id"?}`` -> the conversation |
| ``DELETE /api/chats/<cid>`` | -> ``{"deleted": true}`` |
| ``POST /api/chats/<cid>/files`` | multipart ``file`` -> 201 ``{id, name, mime, kind, size}`` |
| ``GET /api/chats/<cid>/files/<fid>`` | -> the file (images inline, text as plain text) |
| ``POST /api/chats/<cid>/messages`` | ``{"text", "files": [ids], "profile_id", "max_rounds"}`` -> 202 ``{"user", "reply"}`` |
| ``GET /api/chats/<cid>/messages/<mid>`` | -> one message (poll a running reply) |
| ``GET /api/chats/<cid>/messages/<mid>/draft`` | -> the draft a reply proposed |
| ``POST /api/chats/<cid>/messages/<mid>/apply`` | -> saves that draft as the project (a revision keeps the previous one) |

A conversation is its author's: another author gets 404, an administrator
may read it.
"""
from __future__ import annotations

from urllib.parse import quote

from flask import Blueprint, current_app, g, make_response, request

from .. import ai_loop, chat, db, projects, runtime, targets, testdata
from ..profiles import adapter_for
from ..profiles import get as get_profile
from ..security import api_error, audit, login_required
from .api import _fail, _json_body, _project

bp = Blueprint("chat", __name__, url_prefix="/api")


def _chat(cid: int):
    conn = db.get()
    c = conn.execute("SELECT * FROM chats WHERE id = ?", (cid,)).fetchone()
    if c is None or (c["user_id"] != g.user.id and not g.user.is_admin):
        return None, api_error("not_found", "No such conversation.", 404)
    _row, err = _project(c["project_id"])
    if err:
        return None, api_error("not_found", "No such conversation.", 404)
    return c, None


def _profile_id(body, conn):
    raw = body.get("profile_id")
    if raw in (None, ""):
        return None
    try:
        pid = int(raw)
    except (TypeError, ValueError):
        raise chat.ChatError("profile_id must be a number.")
    prof = get_profile(conn, pid)
    if prof is None or not prof["enabled"]:
        raise chat.ChatError("That AI profile does not exist or is disabled.", "no_profile", 404)
    return pid


@bp.get("/projects/<int:pid>/chats")
@login_required
def chats_list(pid):
    row, err = _project(pid)
    if err:
        return err
    rows = db.get().execute(
        "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS n, "
        "(SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id AND m.status = 'running') "
        "AS running FROM chats c WHERE c.project_id = ? AND c.user_id = ? "
        "ORDER BY c.updated_at DESC", (pid, g.user.id)).fetchall()
    return {"chats": [{"id": r["id"], "title": r["title"] or "New conversation",
                       "profile_id": r["profile_id"], "updated_at": r["updated_at"],
                       "messages": r["n"], "running": bool(r["running"])} for r in rows]}


@bp.post("/projects/<int:pid>/chats")
@login_required
def chats_create(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body(required=False)
    if err:
        return err
    conn = db.get()
    try:
        prof = _profile_id(body, conn)
    except chat.ChatError as exc:
        return api_error(exc.code, exc.message, exc.status)
    title = str(body.get("title") or "")[:120]
    t = db.now()
    cid = conn.execute("INSERT INTO chats (project_id, user_id, title, profile_id, created_at, "
                       "updated_at) VALUES (?,?,?,?,?,?)",
                       (pid, g.user.id, title, prof, t, t)).lastrowid
    audit("chat.create", row["slug"], chat=cid)
    c = conn.execute("SELECT * FROM chats WHERE id = ?", (cid,)).fetchone()
    return chat.chat_answer(conn, c), 201


@bp.get("/chats/<int:cid>")
@login_required
def chat_get(cid):
    c, err = _chat(cid)
    if err:
        return err
    return chat.chat_answer(db.get(), c)


@bp.put("/chats/<int:cid>")
@login_required
def chat_put(cid):
    c, err = _chat(cid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    conn = db.get()
    try:
        if "profile_id" in body:
            conn.execute("UPDATE chats SET profile_id = ? WHERE id = ?",
                         (_profile_id(body, conn), cid))
    except chat.ChatError as exc:
        return api_error(exc.code, exc.message, exc.status)
    if "title" in body:
        conn.execute("UPDATE chats SET title = ? WHERE id = ?", (str(body["title"] or "")[:120], cid))
    return chat.chat_answer(conn, conn.execute("SELECT * FROM chats WHERE id = ?", (cid,)).fetchone())


@bp.delete("/chats/<int:cid>")
@login_required
def chat_delete(cid):
    c, err = _chat(cid)
    if err:
        return err
    conn = db.get()
    if conn.execute("SELECT 1 FROM chat_messages WHERE chat_id = ? AND status = 'running'",
                    (cid,)).fetchone():
        return api_error("busy", "A reply is being written: wait for it, then delete.", 409)
    conn.execute("DELETE FROM chats WHERE id = ?", (cid,))
    audit("chat.delete", str(c["project_id"]), chat=cid)
    return {"deleted": True}


@bp.post("/chats/<int:cid>/files")
@login_required
def chat_file_add(cid):
    c, err = _chat(cid)
    if err:
        return err
    up = request.files.get("file")
    if up is None:
        return api_error("bad_request", "Send the file as the multipart field 'file'.", 400)
    data = up.read(chat.MAX_IMAGE + 1)
    try:
        meta = chat.add_file(db.get(), cid, up.filename or "file", data)
    except chat.ChatError as exc:
        return api_error(exc.code, exc.message, exc.status)
    audit("chat.file", str(c["project_id"]), chat=cid, name=meta["name"], kind=meta["kind"],
          size=meta["size"])
    return meta, 201


@bp.get("/chats/<int:cid>/files/<int:fid>")
@login_required
def chat_file_get(cid, fid):
    c, err = _chat(cid)
    if err:
        return err
    f = db.get().execute("SELECT * FROM chat_files WHERE id = ? AND chat_id = ?",
                         (fid, cid)).fetchone()
    if f is None:
        return api_error("not_found", "No such file.", 404)
    resp = make_response(bytes(f["data"]))
    resp.headers["Content-Type"] = f["mime"] if f["kind"] == "image" else "text/plain; charset=utf-8"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if f["kind"] == "text":
        resp.headers["Content-Disposition"] = "inline; filename*=UTF-8''" + quote(f["name"])
    return resp


def _test_schema(conn, row, pack, snap) -> str:
    """The project's test database for the prompt: names, types, counts and
    relations, never a row ("" when the target has no test runtime)."""
    try:
        runtime.check_pack(pack)
        info = runtime.info(pack).get("datasets") or []
    except runtime.RuntimeUnavailable:
        return ""
    data, _ = runtime.load_testdata(conn, row["id"])
    tables = testdata.sql_tables(data, info, testdata.store_defs(projects.to_api(conn, row), snap))
    return testdata.schema_text(testdata.schema(tables, data.get("origin"))) if tables else ""


@bp.post("/chats/<int:cid>/messages")
@login_required
def chat_send(cid):
    c, err = _chat(cid)
    if err:
        return err
    if c["user_id"] != g.user.id:
        return api_error("not_yours", "Only the author of a conversation writes in it.", 403)
    body, err = _json_body()
    if err:
        return err
    text = body.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > chat.MAX_MESSAGE:
        return api_error("bad_request", f"Write a message (1 to {chat.MAX_MESSAGE} characters).", 400)
    ids = body.get("files") or []
    if not isinstance(ids, list) or len(ids) > chat.MAX_FILES_PER_MESSAGE or \
            not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
        return api_error("bad_request", f"files is a list of at most {chat.MAX_FILES_PER_MESSAGE} "
                                        "file ids.", 400)
    conn = db.get()
    known = {r["id"]: r["kind"] for r in conn.execute(
        "SELECT id, kind FROM chat_files WHERE chat_id = ?", (cid,))}
    if any(i not in known for i in ids):
        return api_error("not_found", "A file is not in this conversation.", 404)
    if sum(1 for i in ids if known[i] == "image") > chat.MAX_IMAGES_PER_MESSAGE:
        return api_error("bad_request", f"At most {chat.MAX_IMAGES_PER_MESSAGE} images per "
                                        "message.", 400)
    if conn.execute("SELECT 1 FROM chat_messages WHERE chat_id = ? AND status = 'running'",
                    (cid,)).fetchone():
        return api_error("busy", "The assistant is still answering the last message.", 409)
    try:
        max_rounds = int(body.get("max_rounds", ai_loop.DEFAULT_MAX_ROUNDS))
    except (TypeError, ValueError):
        return api_error("bad_request", "max_rounds must be a number.", 400)
    max_rounds = max(1, min(max_rounds, ai_loop.MAX_ROUNDS_CAP))
    try:
        prof_id = _profile_id(body, conn) if body.get("profile_id") not in (None, "") \
            else c["profile_id"]
    except chat.ChatError as exc:
        return api_error(exc.code, exc.message, exc.status)
    if prof_id is None:
        return api_error("no_profile", "Pick an AI profile.", 400)
    row, err = _project(c["project_id"])
    if err:
        return err
    try:
        prof = get_profile(conn, prof_id)
        adapter = adapter_for(prof)
        t, snap = targets.context_for_project(conn, row, g.user)
        pack = targets.rules_pack(conn, t, g.user.id, snap)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    conn.execute("UPDATE chats SET profile_id = ? WHERE id = ?", (prof_id, cid))
    ids_msg = chat.start_reply(current_app._get_current_object(), chat_id=cid, user_id=g.user.id,
                               text=text, file_ids=ids, adapter=adapter, pack=pack, snap=snap,
                               project=projects.to_api(conn, row),
                               assets=projects.asset_blobs(conn, row["id"]),
                               max_rounds=max_rounds,
                               profile_label=f"{prof['name']} ({prof['model']})",
                               test_schema=_test_schema(conn, row, pack, snap))
    audit("chat.message", row["slug"], chat=cid, profile=prof["name"], files=len(ids))
    return ids_msg, 202


def _message(cid, mid):
    m = db.get().execute("SELECT * FROM chat_messages WHERE id = ? AND chat_id = ?",
                         (mid, cid)).fetchone()
    if m is None:
        return None, api_error("not_found", "No such message.", 404)
    return m, None


@bp.get("/chats/<int:cid>/messages/<int:mid>")
@login_required
def chat_message(cid, mid):
    c, err = _chat(cid)
    if err:
        return err
    m, err = _message(cid, mid)
    if err:
        return err
    files = {r["id"]: chat.file_meta(r) for r in db.get().execute(
        "SELECT id, name, mime, kind, size FROM chat_files WHERE chat_id = ?", (cid,))}
    return chat.message_answer(m, files, ai_loop.load_progress(db.get(), "chat_progress", mid))


@bp.get("/chats/<int:cid>/messages/<int:mid>/draft")
@login_required
def chat_draft(cid, mid):
    c, err = _chat(cid)
    if err:
        return err
    m, err = _message(cid, mid)
    if err:
        return err
    d = chat.draft_of(m)
    if d is None:
        return api_error("no_draft", "This reply proposed no draft.", 404)
    return {"draft": d}


@bp.post("/chats/<int:cid>/messages/<int:mid>/apply")
@login_required
def chat_apply(cid, mid):
    c, err = _chat(cid)
    if err:
        return err
    if c["user_id"] != g.user.id:
        return api_error("not_yours", "Only the author of a conversation applies its drafts.", 403)
    m, err = _message(cid, mid)
    if err:
        return err
    d = chat.draft_of(m)
    if d is None:
        return api_error("no_draft", "This reply proposed no draft.", 404)
    row, err = _project(c["project_id"])
    if err:
        return err
    conn = db.get()
    try:
        patch = projects.validate_patch({k: d[k] for k in ai_loop.RESULT_KEYS if k in d}, conn)
    except projects.ShapeError as exc:
        return api_error(exc.code, exc.message, 400)
    projects.apply_patch(conn, row, patch, g.user.username)
    conn.execute("UPDATE chat_messages SET applied_at = ? WHERE id = ?", (db.now(), mid))
    audit("chat.apply", row["slug"], chat=cid, message=mid)
    fresh = projects.get_row(conn, row["id"])
    return {"project": projects.to_api(conn, fresh)}
