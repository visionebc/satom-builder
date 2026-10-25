"""The AI generation loop (design section 5).

* system = the rules pack's ``author_guide(target)`` (fixed and cacheable, so
  it comes FIRST) + the output contract below;
* user = a schema summary of the target + the current draft + the author's
  text (+ on repair rounds, the problems SATOM's rules found);
* the model returns ONE JSON object with the project shape of section 11;
* the Builder builds the package, runs the rules pack's local ``check()`` and
  feeds problems back, up to ``max_rounds`` (default 3). A JSON parse failure
  counts as a round and its error is fed back. Nothing is ever "fixed"
  silently: when problems remain, the draft is shown with its problems.

What is sent never holds data rows or secrets: the target summary is built
from an ALLOW-list of snapshot keys (names, columns, kinds), seed rows are
stripped from the draft, and connection secret fields are blanked. Tests
scan the built request for every stored secret and every row value.
"""
from __future__ import annotations

import json
import re
import threading
import traceback
import uuid

from . import db, package, projects
from .ai_adapters import AIError
from .secrets_store import redact

DEFAULT_MAX_ROUNDS = 3
MAX_ROUNDS_CAP = 6
MAX_PROMPT = 2000
SECRET_FIELDS = ("password", "secret", "token", "api_key", "client_secret")
RESULT_KEYS = ("name", "slug", "version", "icon", "description", "files", "app",
               "datasets", "params")

OUTPUT_CONTRACT = """\
# Output contract (SATOM Builder)

Answer with ONE JSON object and nothing else: no prose, no explanation, no
Markdown around it. The object is the plugin draft, with these keys:

- "name": the plugin name (text, at most 200 characters);
- "slug": lowercase letters, digits and dashes;
- "version": "X.Y.Z";
- "icon": a Bootstrap icon class such as "bi-puzzle";
- "description": at most 500 characters;
- for a view or a widget: "files" = {"body.jinja": ..., "style.css": ...,
  "script.js": ...}, "datasets" = [dataset names], "params" = [param objects];
- for an app: "app" = the app.json body {"app": {...}, "stores": [...],
  "connections": [...], "apis": [...], "scripts": [...]}.

Keys you leave out keep their current value. Never put a password, token,
API key or any other secret in the draft: SATOM asks for secrets when the
package is imported. Seed rows of data stores are not shown to you; return
stores without "rows" unless you add new seed rows on purpose, and the
Builder keeps the rows it already has.
"""


CHAT_CONTRACT = """\
# Conversation (SATOM Builder assistant)

You are in a conversation with the author about this draft. Add the key
"message" to your JSON object: a short note to the author, in the author's
language, saying what you changed and why, or answering their question. When
the author only asks a question and nothing should change, answer with
{"message": "..."} alone. Files the author attached are shown under
"Attached files"; images are attached to this message.
"""


class ParseError(ValueError):
    pass


# --------------------------------------------------------------------------
# what is sent
# --------------------------------------------------------------------------
#: What a view or a widget can use from the target: nothing of the app world
#: (steps, stores, connections...). Sending the whole schema made a view's
#: request ~27k tokens, past a local model's default context (lab run
#: 2026-10-25: the model never saw the output contract).
VIEW_SCHEMA_KEYS = ("datasets", "param_types", "patterns", "limits")


def target_summary(snapshot: dict, kind: str = "app") -> dict:
    """Names and shapes only, from an allow-list of snapshot keys. A view or a
    widget gets only what a view can use."""
    if kind in ("view", "widget"):
        schema = snapshot.get("schema") or {}
        choices = snapshot.get("choices") or {}
        return {"satom_version": str(snapshot.get("satom_version", "")),
                "datasets": [str(d) for d in (choices.get("datasets") or [])],
                "schema": {k: schema[k] for k in VIEW_SCHEMA_KEYS if k in schema}}
    ex = snapshot.get("existing") or {}
    stores = {}
    for slug, s in (ex.get("stores") or {}).items():
        cols = []
        for c in (s or {}).get("columns") or []:
            if isinstance(c, dict):
                cols.append({"name": str(c.get("name", "")), "type": str(c.get("type", ""))})
        stores[str(slug)] = {"columns": cols}
    conns = {str(slug): {"kind": str((c or {}).get("kind", "")),
                         "auth": str((c or {}).get("auth", ""))}
             for slug, c in (ex.get("connections") or {}).items()}
    apps = {str(slug): {"kind": str((a or {}).get("kind", "")),
                        "status": str((a or {}).get("status", "")),
                        "template": bool((a or {}).get("template"))}
            for slug, a in (ex.get("apps") or {}).items()}
    apis = [[str(x) for x in v][:3] for v in (ex.get("api_versions") or [])
            if isinstance(v, (list, tuple))]
    scripts = sorted(str(s) for s in (ex.get("scripts") or {}))
    choices = snapshot.get("choices") or {}
    return {
        "satom_version": str(snapshot.get("satom_version", "")),
        "datasets": [str(d) for d in (choices.get("datasets") or [])],
        "existing": {"apps": apps, "stores": stores, "connections": conns,
                     "api_versions": apis, "scripts": scripts},
        "schema": snapshot.get("schema") or {},
    }


def draft_for_prompt(project: dict) -> dict:
    """The draft without seed rows and without secret field values."""
    d = {k: project.get(k) for k in RESULT_KEYS}
    d["kind"] = project.get("kind")
    app = json.loads(json.dumps(project.get("app") or {}))
    for st in app.get("stores") or []:
        if isinstance(st, dict) and "rows" in st:
            st.pop("rows", None)
    for c in app.get("connections") or []:
        if isinstance(c, dict):
            for k in SECRET_FIELDS:
                if c.get(k):
                    c[k] = ""
    d["app"] = app
    if project.get("kind") == "app":
        d.pop("files", None)
        d.pop("datasets", None)
        d.pop("params", None)
    else:
        d.pop("app", None)
    return d


def build_prompt(author_guide: str, snapshot: dict, project: dict, author_text: str,
                 feedback: str = "", chat: bool = False) -> tuple[str, str]:
    system = author_guide.rstrip() + "\n\n" + OUTPUT_CONTRACT + ("\n" + CHAT_CONTRACT if chat else "")
    compact = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}
    user = ("# Target SATOM (names and shapes only)\n\n```json\n"
            + json.dumps(target_summary(snapshot, str(project.get("kind") or "app")), **compact)
            + "\n```\n\n# Current draft (" + str(project.get("kind")) + ")\n\n```json\n"
            + json.dumps(draft_for_prompt(project), **compact)
            + "\n```\n\n# What the author wants\n\n" + author_text.strip() + "\n")
    if feedback:
        user += "\n# Fix these problems in your previous answer\n\n" + feedback + "\n"
    return system, user


# --------------------------------------------------------------------------
# what comes back
# --------------------------------------------------------------------------
_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n\s*```\s*$", re.S)


def parse_answer(text: str) -> dict:
    return parse_answer_full(text)[0]


def parse_answer_full(text: str, allow_message: bool = False) -> tuple[dict, str]:
    """-> (the draft keys, the "message" when *allow_message*)."""
    s = (text or "").strip()
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    try:
        obj = json.loads(s)
    except ValueError:
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            raise ParseError("The answer holds no JSON object.")
        try:
            obj = json.loads(s[start:end + 1])
        except ValueError as exc:
            raise ParseError(f"The answer is not valid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ParseError("The answer is JSON but not an object.")
    extra = ("kind", "message") if allow_message else ("kind",)
    unknown = [k for k in obj if k not in RESULT_KEYS and k not in extra]
    if unknown:
        raise ParseError(f"Unknown key(s) in the answer: {', '.join(sorted(unknown)[:5])}.")
    message = obj.get("message") if allow_message else ""
    if message is not None and not isinstance(message, str):
        message = json.dumps(message, ensure_ascii=False)
    return {k: v for k, v in obj.items() if k in RESULT_KEYS}, (message or "")[:20000]


def merge(project: dict, patch: dict) -> dict:
    """The candidate draft: the answer's keys replace the current ones; seed
    rows the answer did not restate are kept (they were never shown)."""
    cand = json.loads(json.dumps(project))
    cand.update({k: v for k, v in patch.items()})
    if "app" in patch and isinstance(patch["app"], dict):
        old_rows = {s.get("slug"): s.get("rows") for s in
                    (project.get("app") or {}).get("stores") or []
                    if isinstance(s, dict) and "rows" in s}
        for st in cand["app"].get("stores") or []:
            if isinstance(st, dict) and "rows" not in st and st.get("slug") in old_rows:
                st["rows"] = old_rows[st.get("slug")]
    return cand


def problems_text(result: dict) -> str:
    lines = []
    for p in (result.get("problems") or [])[:60]:
        lines.append(f"- {p.get('where', '?')}: {p.get('message', '')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------
def run(adapter, pack, snapshot: dict, project: dict, author_text: str, assets: list,
        max_rounds: int = DEFAULT_MAX_ROUNDS, on_round=None, min_satom: str = "",
        chat: bool = False, images: list | None = None) -> dict:
    """-> {"ok", "rounds": [...], "project": candidate, "check": last result,
    "message": the model's note (chat)}. Raises AIError (provider) and
    RulesError (pack). In a chat (*chat*) the model may answer with a message
    alone: nothing is checked and the draft is unchanged."""
    guide = pack.author_guide(snapshot)
    max_rounds = max(1, min(int(max_rounds or DEFAULT_MAX_ROUNDS), MAX_ROUNDS_CAP))
    rounds: list[dict] = []
    current = project
    feedback = ""
    last = None
    message = ""
    for n in range(1, max_rounds + 1):
        system, user = build_prompt(guide, snapshot, current, author_text, feedback, chat=chat)
        answer = adapter.complete(system, user, images=images) if images else \
            adapter.complete(system, user)
        try:
            patch, note = parse_answer_full(answer, allow_message=chat)
            message = note or message
            if chat and not patch:
                rounds.append({"n": n, "ok": True, "at": db.now(), "problems": [],
                               "message_only": True})
                if on_round:
                    on_round(rounds)
                break
            shaped = projects.validate_patch(patch)
        except (ParseError, projects.ShapeError) as exc:
            msg = getattr(exc, "message", None) or str(exc)
            rec = {"n": n, "ok": False, "at": db.now(),
                   "problems": [{"where": "answer", "message": msg}]}
            rounds.append(rec)
            if on_round:
                on_round(rounds)
            feedback = (f"- answer: {msg}\nReturn ONE JSON object with the keys of the "
                        "output contract and nothing else.")
            continue
        candidate = merge(current, shaped)
        try:
            _, data = package.build_from_project(candidate, assets, min_satom=min_satom)
            last = pack.check(data, snapshot, "builder")
        except package.PackageError as exc:
            last = {"ok": False, "problems": [{"where": "package", "message": str(exc)}],
                    "warnings": []}
        rec = {"n": n, "ok": bool(last.get("ok")), "at": db.now(),
               "problems": last.get("problems") or [],
               "warnings": last.get("warnings") or []}
        rounds.append(rec)
        current = candidate
        if on_round:
            on_round(rounds)
        if rec["ok"]:
            break
        feedback = problems_text(last)
    ok = bool(rounds and rounds[-1]["ok"])
    return {"ok": ok, "rounds": rounds,
            "project": current if current is not project else None, "check": last,
            "message": message}


# --------------------------------------------------------------------------
# background jobs (state in SQLite, polled via /api/jobs/<id>)
# --------------------------------------------------------------------------
def start_job(app, *, project_id: int, user_id: int, work) -> str:
    """Run ``work(job_id, conn, update)`` in a daemon thread. ``update(rounds)``
    persists progress. -> the job id."""
    job_id = uuid.uuid4().hex
    with db.session(app.config["DB_PATH"]) as conn:
        t = db.now()
        conn.execute("INSERT INTO jobs (id, project_id, user_id, kind, status, rounds, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (job_id, project_id, user_id, "generate", "running", "[]", t, t))

    def target():
        with app.app_context(), db.session(app.config["DB_PATH"]) as conn:
            def update(rounds):
                conn.execute("UPDATE jobs SET rounds = ?, updated_at = ? WHERE id = ?",
                             (db.dumps(rounds), db.now(), job_id))
            try:
                res = work(job_id, conn, update)
                conn.execute("UPDATE jobs SET status = 'done', rounds = ?, result = ?, "
                             "error = ?, updated_at = ? WHERE id = ?",
                             (db.dumps(res["rounds"]), db.dumps(res), res.get("error", ""),
                              db.now(), job_id))
            except Exception as exc:  # noqa: BLE001 - shown to the author, redacted
                msg = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                if not isinstance(exc, AIError) and type(exc).__name__ not in (
                        "RulesError", "SatomError", "NotReady"):
                    app.logger.error("generation job %s failed: %s", job_id,
                                     redact(traceback.format_exc()))
                conn.execute("UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                             "WHERE id = ?", (redact(msg)[:2000], db.now(), job_id))

    th = threading.Thread(target=target, name=f"generate-{job_id[:8]}", daemon=True)
    th.start()
    app.extensions.setdefault("satom_builder_threads", {})[job_id] = th
    return job_id


def job_answer(row) -> dict:
    res = db.loads(row["result"], {}) or {}
    return {"id": row["id"], "status": row["status"],
            "rounds": db.loads(row["rounds"], []) or [],
            "error": row["error"] or "",
            "ok": bool(res.get("ok")) if row["status"] == "done" else False,
            "project": res.get("project") if row["status"] == "done" else None}


def fail_stale_jobs(conn) -> int:
    """At start-up: a job still 'running' was cut off by a restart."""
    cur = conn.execute("UPDATE jobs SET status = 'failed', error = 'The Builder restarted "
                       "while this job ran; start it again.', updated_at = ? "
                       "WHERE status = 'running' AND updated_at < ?",
                       (db.now(), db.now() - 3600))
    return cur.rowcount
