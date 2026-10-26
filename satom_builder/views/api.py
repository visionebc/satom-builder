"""Internal JSON API, web UI <-> backend (contract section 11).

Session cookie + ``X-CSRF-Token`` on every unsafe method (enforced app-wide in
``security.csrf_protect``); JSON answers; errors as ``{"error", "message"}``.
Every project route checks that the caller owns the project or is an admin.
"""
from __future__ import annotations

import json
import uuid

from flask import Blueprint, current_app, g, make_response, request

from .. import ai_loop, db, fortinet_apis, package, projects, rules, targets
from ..ai_adapters import AIError
from ..profiles import adapter_for
from ..profiles import get as get_profile
from ..satom_client import SatomError
from ..security import api_error, audit, login_required
from ..targets import NotReady

bp = Blueprint("api", __name__, url_prefix="/api")

MAX_JSON_BODY = projects.MAX_BODY
MAX_REHEARSE_INPUTS = 256 * 1024
MAX_SECRETS = 50
MAX_SECRET_LEN = 8192
MAX_PROMPT = 20000


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _json_body(required: bool = True):
    if request.content_length and request.content_length > MAX_JSON_BODY:
        return None, api_error("too_large", "The request body is too large.", 413)
    raw = request.get_data(cache=False)
    if not raw:
        if required:
            return None, api_error("bad_json", "Send a JSON object.", 400)
        return {}, None
    if len(raw) > MAX_JSON_BODY:
        return None, api_error("too_large", "The request body is too large.", 413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, api_error("bad_json", "The body is not valid JSON.", 400)
    if not isinstance(body, dict):
        return None, api_error("bad_json", "The body must be a JSON object.", 400)
    return body, None


def _project(pid: int):
    conn = db.get()
    row = projects.get_row(conn, pid)
    if row is None or not projects.may_access(g.user, row):
        # the same answer for "missing" and "not yours": no enumeration
        return None, api_error("not_found", "No such project.", 404)
    return row, None


def _fail(exc):
    if isinstance(exc, NotReady):
        return api_error(exc.code, exc.message, exc.status)
    if isinstance(exc, SatomError):
        return api_error(exc.code, exc.message, exc.status)
    if isinstance(exc, rules.RulesError):
        return api_error("rules_pack", str(exc), 409)
    if isinstance(exc, package.PackageError):
        return api_error("package", str(exc), 422)
    if isinstance(exc, AIError):
        return api_error(exc.code, exc.message, 502)
    if isinstance(exc, projects.ShapeError):
        return api_error(exc.code, exc.message, 400)
    raise exc


def _private_key():
    return package.load_private_key(current_app.config["SATOM_BUILDER_PUBLISHER_KEY"])


def _build(conn, row, snap: dict | None = None) -> tuple[str, bytes]:
    proj = projects.to_api(conn, row)
    min_satom = str((snap or {}).get("satom_version") or "")
    defs = []
    if proj.get("kind") in ("view", "widget") and row["target_id"] is not None and any(
            str(k).startswith("admin_") for k in proj.get("datasets") or []):
        t = targets.get_target(conn, row["target_id"])
        if t is not None and targets.has_token(conn, g.user.id, t["id"]):
            defs = package.dataset_defs_from(proj.get("datasets"),
                                             targets.dataset_defs(conn, t, g.user.id))
    if proj.get("kind") == "app":
        t = targets.get_target(conn, row["target_id"]) if row["target_id"] is not None else None
        proj, pin_problems = targets.library_completion(conn, t, g.user.id)(proj)
        if pin_problems:
            raise package.PackageError("; ".join(p["message"] for p in pin_problems))
    return package.build_from_project(proj, projects.asset_blobs(conn, row["id"]),
                                      private_key=_private_key(), min_satom=min_satom,
                                      dataset_defs=defs)


def _local_check(conn, row) -> tuple[dict | None, str]:
    """-> (check() result or None, why None)."""
    try:
        t, snap = targets.context_for_project(conn, row, g.user)
        pack = targets.rules_pack(conn, t, g.user.id, snap)
        _, data = _build(conn, row, snap)
    except package.PackageError as exc:
        res = {"ok": False, "problems": [{"where": "package", "message": str(exc)}],
               "warnings": [], "summary": {}}
        return res, ""
    except (NotReady, SatomError, rules.RulesError) as exc:
        return None, getattr(exc, "message", None) or str(exc)
    res = pack.check(data, snap, "builder")
    if row["kind"] == "app":
        built = projects.to_api(conn, row)
        extra = fortinet_apis.lint(built, snap)
        t2 = targets.get_target(conn, row["target_id"]) if row["target_id"] is not None else None
        built, _ = targets.library_completion(conn, t2, g.user.id)(built)
        probs = fortinet_apis.hints(list(res.get("problems") or []), built) + extra
        res = dict(res, problems=probs, ok=bool(res.get("ok")) and not extra)
    _publisher_hint(res)
    return res, ""


def _where(conn, row) -> dict | None:
    """Where the project installs and why (contract section 17), or None when
    it has no target/token yet or the target cannot be reached."""
    if row["target_id"] is None:
        return None
    try:
        return targets.project_adom(conn, row, g.user)
    except (NotReady, SatomError):
        return None


def _publisher_hint(res: dict) -> None:
    """A package signed by THIS installation's key that the target does not
    trust yet: say what to do (register the key), not only what is wrong."""
    try:
        kid = package.publisher_info(current_app.config["SATOM_BUILDER_PUBLISHER_KEY"])["key_id"]
    except Exception:  # noqa: BLE001 -- no key: nothing to hint
        return
    if not kid:
        return
    for p in (res or {}).get("problems") or []:
        msg = str(p.get("message") or "")
        if "does not trust" in msg and kid[:16] in msg and "Publisher page" not in msg:
            p["message"] = (msg + " -- this is this installation's publisher key: register it "
                            "on the target (Publisher page; needs a super-admin token there).")


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------
@bp.get("/projects/<int:pid>")
@login_required
def project_get(pid):
    row, err = _project(pid)
    if err:
        return err
    conn = db.get()
    out = projects.to_api(conn, row)
    out["where"] = _where(conn, row)
    return out


@bp.put("/projects/<int:pid>")
@login_required
def project_put(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    conn = db.get()
    try:
        patch = projects.validate_patch(body, conn)
    except projects.ShapeError as exc:
        return api_error(exc.code, exc.message, 400)
    projects.apply_patch(conn, row, patch, g.user.username)
    row = projects.get_row(conn, pid)
    try:
        result, note = _local_check(conn, row)
    except rules.RulesError as exc:
        result, note = None, str(exc)
    if result is not None:
        projects.set_last_check(conn, pid, "local", result)
        row = projects.get_row(conn, pid)
    out = projects.to_api(conn, row)
    out["where"] = _where(conn, row)
    out["local_check"] = result
    if result is None:
        out["local_check_note"] = note
    return out


@bp.post("/projects/<int:pid>/assets")
@login_required
def project_asset_add(pid):
    row, err = _project(pid)
    if err:
        return err
    f = request.files.get("file")
    if f is None:
        return api_error("no_file", "Send the image as the multipart field 'file'.", 400)
    data = f.stream.read(projects.ASSET_MAX + 1)
    try:
        out = projects.add_asset(db.get(), pid, f.filename or "", data)
    except projects.ShapeError as exc:
        return api_error(exc.code, exc.message, 413 if exc.code == "too_large" else 400)
    audit("project.asset_add", row["slug"], sha256=out["sha256"])
    return out


@bp.delete("/projects/<int:pid>/assets/<sha>")
@login_required
def project_asset_delete(pid, sha):
    row, err = _project(pid)
    if err:
        return err
    cur = db.get().execute("DELETE FROM assets WHERE project_id = ? AND sha256 = ?",
                           (pid, sha))
    if not cur.rowcount:
        return api_error("not_found", "No such image.", 404)
    audit("project.asset_delete", row["slug"], sha256=sha)
    return {"ok": True}


@bp.get("/projects/<int:pid>/assets/<sha>")
@login_required
def project_asset_get(pid, sha):
    row, err = _project(pid)
    if err:
        return err
    a = db.get().execute("SELECT mime, data FROM assets WHERE project_id = ? AND sha256 = ?",
                         (pid, sha)).fetchone()
    if a is None:
        return api_error("not_found", "No such image.", 404)
    resp = make_response(bytes(a["data"]))
    resp.headers["Content-Type"] = a["mime"]
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------
def _target_snapshot(tid: int, refresh: bool):
    conn = db.get()
    t = targets.get_target(conn, tid)
    if t is None:
        return None, api_error("not_found", "No such SATOM target.", 404)
    if not targets.has_token(conn, g.user.id, tid):
        return None, api_error("no_token", f"You have no SATOM token for {t['name']} yet: "
                                           "add one under My tokens.", 409)
    try:
        return targets.snapshot(conn, t, g.user.id, refresh=refresh), None
    except SatomError as exc:
        return None, _fail(exc)


@bp.get("/targets/<int:tid>/schema")
@login_required
def target_schema(tid):
    snap, err = _target_snapshot(tid, False)
    if err:
        return err
    return snap.get("schema") or {}


@bp.get("/targets/<int:tid>/snapshot")
@login_required
def target_snapshot(tid):
    snap, err = _target_snapshot(tid, request.args.get("refresh") == "1")
    if err:
        return err
    return snap


# --------------------------------------------------------------------------
# check / validate / rehearse / import / package
# --------------------------------------------------------------------------
@bp.post("/projects/<int:pid>/check")
@login_required
def project_check(pid):
    row, err = _project(pid)
    if err:
        return err
    conn = db.get()
    try:
        t, snap = targets.context_for_project(conn, row, g.user)
        pack = targets.rules_pack(conn, t, g.user.id, snap)
        try:
            _, data = _build(conn, row, snap)
            res = pack.check(data, snap, "builder")
        except package.PackageError as exc:
            res = {"ok": False, "problems": [{"where": "package", "message": str(exc)}],
                   "warnings": [], "summary": {}}
    except Exception as exc:  # noqa: BLE001 - mapped below
        return _fail(exc)
    projects.set_last_check(conn, pid, "local", res)
    return res


def _remote(pid: int, action: str, call):
    row, err = _project(pid)
    if err:
        return err
    conn = db.get()
    try:
        t, snap = targets.context_for_project(conn, row, g.user)
        _, data = _build(conn, row, snap)
        with targets.client(conn, t, g.user.id, adom=targets.call_adom(snap)) as c:
            res = call(c, data)
    except Exception as exc:  # noqa: BLE001
        audit(f"satom.{action}", row["slug"], ok=False,
              error=getattr(exc, "code", type(exc).__name__))
        return _fail(exc)
    audit(f"satom.{action}", row["slug"], target=t["name"],
          adom=targets.call_adom(snap) or str(snap.get("product") or ""),
          ok=bool(res.get("ok")) if isinstance(res, dict) else None)
    if action == "validate" and isinstance(res, dict):
        projects.set_last_check(conn, pid, "remote", res)
    return res if isinstance(res, dict) else {"ok": False, "answer": res}


@bp.post("/projects/<int:pid>/validate-remote")
@login_required
def project_validate_remote(pid):
    return _remote(pid, "validate", lambda c, data: c.validate(data))


@bp.post("/projects/<int:pid>/rehearse")
@login_required
def project_rehearse(pid):
    body, err = _json_body(required=False)
    if err:
        return err
    inputs = body.get("inputs", {})
    answers = body.get("answers", "yes")
    if not isinstance(inputs, dict):
        return api_error("bad_shape", "inputs must be an object.", 400)
    if len(json.dumps(inputs)) > MAX_REHEARSE_INPUTS:
        return api_error("too_large", "inputs is too large.", 413)
    if answers not in ("yes", "no", "stop"):
        return api_error("bad_shape", "answers must be yes, no or stop.", 400)
    options = {"inputs": inputs, "answers": answers}
    return _remote(pid, "rehearse", lambda c, data: c.rehearse(data, options))


@bp.post("/projects/<int:pid>/import")
@login_required
def project_import(pid):
    body, err = _json_body(required=False)
    if err:
        return err
    allow = body.get("allow_downgrade", False)
    secrets_in = body.get("secrets", {})
    if not isinstance(allow, bool):
        return api_error("bad_shape", "allow_downgrade must be true or false.", 400)
    if not isinstance(secrets_in, dict) or len(secrets_in) > MAX_SECRETS:
        return api_error("bad_shape", f"secrets must be an object of at most "
                                      f"{MAX_SECRETS} connection slugs.", 400)
    for k, v in secrets_in.items():
        if not package.SLUG_RE.match(str(k).replace("_", "-")) or not isinstance(v, str) \
                or len(v) > MAX_SECRET_LEN:
            return api_error("bad_shape", "Each secret is a connection slug and a text "
                                          "value.", 400)
    # relayed to SATOM, never stored and never logged
    options = {"allow_downgrade": allow, "secrets": {k: v for k, v in secrets_in.items() if v}}
    values = [v for v in options["secrets"].values()]

    def call(c, data):
        try:
            return c.import_package(data, options)
        except SatomError as exc:
            from ..secrets_store import redact
            exc.message = redact(exc.message, extra=values)
            raise
    resp = _remote(pid, "import", call)
    return resp


@bp.get("/projects/<int:pid>/package")
@login_required
def project_package(pid):
    row, err = _project(pid)
    if err:
        return err
    conn = db.get()
    snap = None
    if row["target_id"] is not None and targets.has_token(conn, g.user.id, row["target_id"]):
        try:
            _, snap = targets.context_for_project(conn, row, g.user)
        except (NotReady, SatomError):
            snap = None
    try:
        name, data = _build(conn, row, snap)
    except package.PackageError as exc:
        return _fail(exc)
    audit("package.download", row["slug"], version=row["version"],
          signed=_private_key() is not None)
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/zip"
    resp.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    resp.headers["X-Satom-Builder-Signed"] = "yes" if _private_key() is not None else "no"
    return resp


# --------------------------------------------------------------------------
# AI generation
# --------------------------------------------------------------------------
def _generation_context(row, body):
    """-> (profile_row, target, snap, pack, prompt) or raise / error."""
    conn = db.get()
    try:
        prof_id = int(body.get("profile_id"))
    except (TypeError, ValueError):
        raise projects.ShapeError("Pick an AI profile.", "bad_shape")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
        raise projects.ShapeError(f"Describe what you want (1 to {MAX_PROMPT} characters).",
                                  "bad_shape")
    prof = get_profile(conn, prof_id)
    if prof is None or not prof["enabled"]:
        raise NotReady("That AI profile does not exist or is disabled.", "no_profile", 404)
    t, snap = targets.context_for_project(conn, row, g.user, purpose="generate")
    pack = targets.rules_pack(conn, t, g.user.id, snap)
    return prof, t, snap, pack, prompt


def _thread_prompt(row, body, prompt):
    """-> (thread, what the model gets as the author's text, error). A new
    conversation without "thread"; with one, only a thread of this user AND
    project (404 otherwise, the same answer as a missing one), not while a
    turn of it is still working (409)."""
    thread = body.get("thread")
    if thread in (None, ""):
        return uuid.uuid4().hex, prompt, None
    turns = ai_loop.thread_turns(db.get(), thread, g.user.id, row["id"])
    if not turns:
        return None, None, api_error("not_found", "No such conversation.", 404)
    if any(t["status"] == "running" for t in turns):
        return None, None, api_error("busy", "This conversation is still working: wait for its "
                                     "answer, or start a new conversation.", 409)
    return thread, ai_loop.history_text(turns, prompt), None


@bp.post("/projects/<int:pid>/generate/preview")
@login_required
def generate_preview(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    try:
        prof, t, snap, pack, prompt = _generation_context(row, body)
        guide = pack.author_guide(snap)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    thread, author_text, err = _thread_prompt(row, body, prompt)
    if err:
        return err
    proj = projects.to_api(db.get(), row)
    library = targets.library_prompt(db.get(), t, g.user.id, proj, author_text, snap)
    system, user = ai_loop.build_prompt(guide, snap, proj, author_text, request=prompt,
                                        library=library)
    return {"system": system, "user": user,
            "bytes": len(system.encode("utf-8")) + len(user.encode("utf-8")),
            "sends": ["author guide", "schema", "draft", "your text"],
            "never_sends": ["data rows", "secrets"],
            "profile": {"name": prof["name"], "adapter": prof["adapter"],
                        "model": prof["model"]}}


@bp.post("/projects/<int:pid>/generate")
@login_required
def generate(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    try:
        max_rounds = int(body.get("max_rounds", ai_loop.DEFAULT_MAX_ROUNDS))
    except (TypeError, ValueError):
        return api_error("bad_shape", "max_rounds must be a number.", 400)
    if not 1 <= max_rounds <= ai_loop.MAX_ROUNDS_CAP:
        return api_error("bad_shape", f"max_rounds must be 1 to {ai_loop.MAX_ROUNDS_CAP}.",
                         400)
    try:
        prof, t, snap, pack, prompt = _generation_context(row, body)
        adapter = adapter_for(prof)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    thread, author_text, err = _thread_prompt(row, body, prompt)
    if err:
        return err
    conn = db.get()
    proj = projects.to_api(conn, row)
    assets = projects.asset_blobs(conn, pid)
    min_satom = str(snap.get("satom_version") or "")

    from .. import render_check
    rcheck = render_check.make(pack, snap, targets.dataset_defs(conn, t, g.user.id)) \
        if proj.get("kind") in ("view", "widget") else render_check.make_app(pack, snap)

    uid = g.user.id

    def work(job_id, jconn, update, progress):
        library = complete = None
        if proj.get("kind") == "app":
            library = targets.library_prompt(jconn, t, uid, proj, author_text, snap)
            if library and library.get("builds"):
                progress.step("Fortinet REST endpoints for the AI, from SATOM's API library: "
                              + ", ".join("%s %s (%d)" % (p, b["api_version"], b["endpoints"])
                                          for p, b in library["builds"].items()), "ok")
            complete = targets.library_completion(jconn, t, uid)
        return ai_loop.run(adapter, pack, snap, proj, author_text, assets,
                           max_rounds=max_rounds, on_round=update, min_satom=min_satom,
                           progress=progress, request=prompt, render_check=rcheck,
                           library=library, complete=complete)

    job_id = ai_loop.start_job(current_app._get_current_object(), project_id=pid,
                               user_id=g.user.id, work=work,
                               turn={"thread": thread, "prompt": prompt, "profile": prof["name"]})
    audit("generate.start", row["slug"], profile=prof["name"], job=job_id,
          max_rounds=max_rounds, thread=thread)
    return {"job_id": job_id, "thread": thread}, 202


@bp.get("/projects/<int:pid>/ai-history")
@login_required
def ai_history(pid):
    """This user's Generate conversations on this project -- nobody else's,
    an administrator included: a conversation is between one author and the AI."""
    row, err = _project(pid)
    if err:
        return err
    return {"threads": ai_loop.ai_history(db.get(), g.user.id, pid)}


@bp.get("/jobs/<job_id>")
@login_required
def job_get(job_id):
    jr = db.get().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if jr is None or jr["user_id"] != g.user.id:     # the author's alone, as the history
        return api_error("not_found", "No such job.", 404)
    return ai_loop.job_answer(jr, ai_loop.load_progress(db.get(), "job_progress", job_id))


@bp.get("/ai/profiles")
@login_required
def ai_profiles():
    """The enabled AI profiles an author may pick: never the key, not even "set"."""
    from ..profiles import list_profiles
    rows = list_profiles(db.get(), enabled_only=True)
    return [{"id": r["id"], "name": r["name"], "adapter": r["adapter"], "model": r["model"]}
            for r in rows], 200
