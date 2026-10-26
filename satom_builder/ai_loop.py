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
# plugin.json keys the Builder writes itself: an answer that restates them
# (the author guide describes plugin.json) loses them, not the round
BUILDER_KEYS = ("kind", "format", "min_satom")

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
  "connections": [...], "apis": [...], "scripts": [...], "automations": [...]}
  ("automations": when it runs on a schedule, see the author guide).

Keys you leave out keep their current value. The Builder writes the kind,
"format", "min_satom" and the file hashes of plugin.json itself: leave them
out. Never put a password, token,
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


#: Generate (not the chat): what the model built against what was asked. A
#: request it cannot build was swapped for an easier plugin in silence (lab,
#: 2026-10-25: an SPO request came back as an IP owner lookup, 0 problems).
GENERATE_CONTRACT = """\
# Report to the author (SATOM Builder)

Build what the author asks for. Do not replace the request with a smaller or
different plugin because it is easier to get past SATOM's checks. Add the key
"message" to your JSON object: a short note to the author, in the author's
language, saying what you built, which parts of the request it covers, and
which parts you left out and why (for example a step SATOM's rules do not
allow). When the request cannot be built at all, answer with
{"message": "..."} alone and say why: the draft is then not changed.
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


_OBJECT_PRODUCTS = ("fortiweb", "fortigate", "fortiadc", "fortianalyzer", "fortiauthenticator")


def dataset_summary(keys) -> dict:
    """The target's dataset keys for a prompt: the built-in and administrator
    ones by name, the device-object ones (about 600) counted per product with
    a few examples. A view reads a key as ``{{ <key>.rows }}``."""
    named, objects = [], {}
    for k in keys or []:
        k = str(k)
        prod = k.split("_", 1)[0]
        if prod in _OBJECT_PRODUCTS:
            objects.setdefault(prod, []).append(k)
        else:
            named.append(k)
    return {"named": named,
            "device_objects": {p: {"count": len(v), "examples": v[:8]}
                               for p, v in sorted(objects.items())},
            "device_object_keys": "<product>_<object type> (deep layer <product>_deep_<type>, "
                                  "sub-object <type>__<sub>); columns id, appliance_id, device, "
                                  "mkey, parent_id and the object's fields with - read as _"}


#: What an app's prompt says about datasets instead of listing them (lab,
#: 2026-10-25: an app's prompt listed the ~900 dataset keys, no diagram step
#: can read one, and the model answered "SATOM has no FortiGate dataset").
#: Rewritten 2026-10-26: the first version sent EVERY request that touched
#: SATOM's data to a view -- "every day, put each new FortiWeb's IP in a
#: FortiGate address group" came back as "make it a view", which can neither
#: run on its own nor write. Showing data is a view's job; acting on the
#: devices, on a schedule too, is an app's (contract section 18).
APP_DATASET_NOTE = (
    "Showing or searching SATOM's data (FortiGate rules, FortiWeb policies, any device-cache "
    "object or plugin dataset -- counted below, so they DO exist on this server) is a view's "
    "job: no diagram step reads those datasets. When the request ONLY shows or searches that "
    "data (look an IP up in the firewall rules, list objects, a report), answer with only "
    "{\"message\"}: a view does it -- a view with an input parameter (for example the IP) "
    "-- and the author switches this project to a view (Project settings > Kind: View; the "
    "draft is kept). Never say the data does not exist. Everything that ACTS is an app: "
    "create, change, call another system (a FortiGate's REST API through an HTTP "
    "connection), ask for approval, keep something in step. An app reads the devices SATOM "
    "manages with the step satom_appliances (each device's id, name, kind, host, ip...: "
    "\"every FortiWeb\" is product fortiweb) and runs on its own with automations "
    "(app.json \"automations\", see the author guide: e.g. daily at 02:00). For a request "
    "such as \"when a FortiWeb is added, put its IP in a FortiGate address group\" build "
    "the app: satom_appliances -> read the group from the FortiGate -> create what is "
    "missing (an address per IP, then the group's members) -> an automation that runs it "
    "daily; a second run must change nothing. A FortiGate's API is reached through an "
    "HTTP connection (no secret in the draft: SATOM asks for the token on import).")


#: SATOM's plugin sandbox (plugin_sandbox._SANDBOX: ImmutableSandboxedEnvironment,
#: autoescape, StrictUndefined, no extension), for a view's prompt: in the lab
#: gpt-oss wrote {% break %} and unbalanced parentheses three rounds running.
VIEW_JINJA_NOTES = [
    "Plain Jinja2 in an immutable sandbox with StrictUndefined: no {% break %}, "
    "{% continue %} or {% do %} (no extension is loaded); lists and dicts cannot be "
    "changed (no .append): accumulate with {% set ns = namespace(found=false) %} and "
    "{% set ns.found = true %}, or filter with selectattr/rejectattr/map/select.",
    "A row field that is not one of the dataset's columns is an error, not empty: use only "
    "the columns the dataset has; a missing value is none, test it with `is none`/`or ''`.",
    "A list of an object (a FortiGate policy's srcaddr, dstaddr, service...) is NOT a field of "
    "the row: it is the dataset <type>__<list> (fortigate_firewall_policy__srcaddr), one row "
    "per item with parent_id = the parent row's id and the item in `name`. Join it: "
    "{% for a in fortigate_firewall_policy__srcaddr.rows if a.parent_id == p.id %}. "
    "Declare every dataset the body reads.",
    "Objects name each other by NAME, never by id: a srcaddr/dstaddr row's `name` is the "
    "address row's `name` (and its `mkey`) on the same device (same appliance_id); `id` is "
    "SATOM's internal row id, used only by parent_id. Join: "
    "{% for addr in fortigate_firewall_address.rows if addr.name == a.name and "
    "addr.appliance_id == a.appliance_id %}.",
    "Address objects (fortigate_firewall_address) hold `subnet` as 'IP MASK' "
    "('192.0.2.5 255.255.255.255'); compare with (a.subnet or '').split(' ')[0].",
    "A {% set %} inside a {% for %} does NOT change the variable outside that loop (Jinja "
    "scoping): anything collected across loop iterations goes in a namespace "
    "({% set ns.names = ns.names + [a.name] %}), including lists built in nested loops.",
    "Rows carry `device` (the appliance's name) and `appliance_id`; there is no `adom` column: "
    "the ADOM is the dataset's product (every fortigate_* row is ADOM fortigate).",
    "Close every {% if %}/{% for %} and every parenthesis; keep expressions short.",
]


def app_dataset_note(keys) -> dict:
    """For an app's prompt: what is a view's job and what is an app's, and
    how many datasets there are per product for a view (names only for views:
    their prompt lists them)."""
    per: dict = {}
    for k in keys or []:
        prod = str(k).split("_", 1)[0]
        prod = prod if prod in _OBJECT_PRODUCTS else "builtin_or_admin"
        per[prod] = per.get(prod, 0) + 1
    return {"note": APP_DATASET_NOTE, "for_views": dict(sorted(per.items()))}


def adom_summary(snapshot: dict) -> dict | None:
    """Contract section 17, for a view's prompt: which ADOM the datasets
    above belong to, how the plugin's ADOM is decided, and how many datasets
    live in ADOMs this connection does not reach (names are in the author
    guide). None for a SATOM older than section 17."""
    reach = snapshot.get("reach")
    if not isinstance(reach, dict):
        return None
    away: dict = {}
    for a in ((snapshot.get("elsewhere") or {}).get("datasets") or {}).values():
        away[str(a)] = away.get(str(a), 0) + 1
    out = {"datasets_are_of": str(snapshot.get("product") or "global"),
           "rule": "a view installs in the ADOM of the product datasets it reads; reading "
                   "datasets of more than one product makes it install in Global. Pick the "
                   "datasets the request needs, of any product listed: never say a listed "
                   "dataset does not exist."}
    if away:
        out["not_reachable"] = dict(sorted(away.items()))
    return out


def target_summary(snapshot: dict, kind: str = "app") -> dict:
    """Names and shapes only, from an allow-list of snapshot keys. A view or a
    widget gets only what a view can use."""
    if kind in ("view", "widget"):
        schema = snapshot.get("schema") or {}
        choices = snapshot.get("choices") or {}
        out = {"satom_version": str(snapshot.get("satom_version", "")),
               "datasets": dataset_summary(choices.get("datasets")),
               "schema": {k: schema[k] for k in VIEW_SCHEMA_KEYS
                          if k in schema and k != "datasets"}}
        out["jinja"] = VIEW_JINJA_NOTES
        adom = adom_summary(snapshot)
        if adom:
            out["adom"] = adom
        return out
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
        "datasets": app_dataset_note(choices.get("datasets")),
        "existing": {"apps": apps, "stores": stores, "connections": conns,
                     "api_versions": apis, "scripts": scripts},
        # an app reads no dataset: the ~600 keys stay out of its prompt
        "schema": {k: v for k, v in (snapshot.get("schema") or {}).items() if k != "datasets"},
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
                 feedback: str = "", chat: bool = False, request: str = "") -> tuple[str, str]:
    """*request* is the author's own new message (*author_text* may wrap it
    in the conversation so far): a repair round repeats it after the
    problems, so the last thing the model reads is what to build."""
    system = (author_guide.rstrip() + "\n\n" + OUTPUT_CONTRACT + "\n"
              + (CHAT_CONTRACT if chat else GENERATE_CONTRACT))
    compact = {"sort_keys": True, "ensure_ascii": False, "separators": (",", ":")}
    user = ("# Target SATOM (names and shapes only)\n\n```json\n"
            + json.dumps(target_summary(snapshot, str(project.get("kind") or "app")), **compact)
            + "\n```\n\n# Current draft (" + str(project.get("kind")) + ")\n\n```json\n"
            + json.dumps(draft_for_prompt(project), **compact)
            + "\n```\n\n# What the author wants\n\n" + author_text.strip() + "\n")
    if feedback:
        user += "\n# Fix these problems in your previous answer\n\n" + feedback + "\n"
        user += ("\n# Still what the author asked for\n\nFix the problems without dropping "
                 "any part of this request; whatever you cannot build, say in \"message\".\n\n"
                 + (request or author_text).strip() + "\n")
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
    extra = BUILDER_KEYS + (("message",) if allow_message else ())
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
        chat: bool = False, images: list | None = None, progress=None,
        request: str = "", render_check=None) -> dict:
    """-> {"ok", "rounds": [...], "project": candidate, "check": last result,
    "message": the model's note}. Raises AIError (provider) and RulesError
    (pack). The model may answer with a message alone: nothing is checked and
    the draft is unchanged. *request* is the author's own new message
    (Generate): the first step of the activity, and repeated in repair
    rounds. *progress* (a :class:`progress.Progress`) records every step and
    streams the model's thinking and answer as they arrive."""
    pg = progress
    if pg and request.strip():
        pg.step("Your request", "ok", detail=request.strip())
    if pg:
        pg.step("Reading the author guide from SATOM's rules pack "
                f"(SATOM {getattr(pack, 'satom_version', '') or '?'})")
    guide = pack.author_guide(snapshot)
    max_rounds = max(1, min(int(max_rounds or DEFAULT_MAX_ROUNDS), MAX_ROUNDS_CAP))
    model = str((getattr(adapter, "p", None) or {}).get("model") or "the model")
    rounds: list[dict] = []
    current = project
    feedback = ""
    last = None
    message = ""
    for n in range(1, max_rounds + 1):
        system, user = build_prompt(guide, snapshot, current, author_text, feedback, chat=chat,
                                    request=request)
        kw = {"images": images} if images else {}
        if pg:
            size = len(system.encode("utf-8")) + len(user.encode("utf-8"))
            pg.step(f"Round {n}/{max_rounds}: sending the request to {model} "
                    f"({size // 1024} KB, about {size // 4000}k tokens"
                    + (f", {len(images)} image(s)" if images else "") + ")"
                    + (" with the problems to fix" if feedback else ""))
            pg.step(f"{model} is reading the request and writing the answer")
            pg.model(round_no=n, rounds=max_rounds, model=model)
            kw["sink"] = pg
        try:
            answer = adapter.complete(system, user, **kw)
        except Exception as exc:
            if pg:
                pg.model_done()
                pg.end("bad", redact(getattr(exc, "message", None) or str(exc)))
            raise
        if pg:
            pg.model_done()
            pg.step(f"Reading the answer ({len(answer):,} characters)")
        try:
            patch, note = parse_answer_full(answer, allow_message=True)
            message = note or message
            if not patch and (chat or note):
                # after a round with problems, a message changes nothing:
                # those problems are still there
                kept = last if last and not last.get("ok") else None
                rounds.append({"n": n, "ok": kept is None, "at": db.now(),
                               "problems": (kept or {}).get("problems") or [],
                               "message_only": True})
                if pg:
                    pg.step("The answer is a message: the draft is not changed", "ok")
                if on_round:
                    on_round(rounds)
                break
            shaped = projects.validate_patch(patch)
        except (ParseError, projects.ShapeError) as exc:
            msg = getattr(exc, "message", None) or str(exc)
            rec = {"n": n, "ok": False, "at": db.now(),
                   "problems": [{"where": "answer", "message": msg}]}
            rounds.append(rec)
            if pg:
                pg.end("warn", msg)
            if on_round:
                on_round(rounds)
            feedback = (f"- answer: {msg}\nReturn ONE JSON object with the keys of the "
                        "output contract and nothing else.")
            continue
        candidate = merge(current, shaped)
        if pg:
            pg.step("Building the package and checking it with SATOM's rules")
        try:
            _, data = package.build_from_project(candidate, assets, min_satom=min_satom)
            last = pack.check(data, snapshot, "builder")
        except package.PackageError as exc:
            last = {"ok": False, "problems": [{"where": "package", "message": str(exc)}],
                    "warnings": []}
        if last.get("ok") and render_check is not None:
            # SATOM's rules parse a template, they do not run it: render it
            # (render_check.py) before calling the draft done
            if pg:
                pg.step("Rendering the view with test data (SATOM's own runtime)")
            try:
                rprobs, rnote = render_check(candidate)
            except Exception as exc:  # noqa: BLE001 -- a render check never sinks the job
                rprobs, rnote = [], "not rendered: " + str(exc)[:200]
            if rprobs:
                last = dict(last, ok=False, problems=list(last.get("problems") or []) + rprobs)
            elif pg and rnote:
                pg.step("Render: " + rnote, "ok" if rnote.startswith("rendered") else "warn")
        rec = {"n": n, "ok": bool(last.get("ok")), "at": db.now(),
               "problems": last.get("problems") or [],
               "warnings": last.get("warnings") or []}
        rounds.append(rec)
        current = candidate
        if pg:
            probs = rec["problems"]
            if rec["ok"]:
                pg.end("ok", f"No problems ({len(rec['warnings'])} warning(s))")
            else:
                pg.end("warn", f"{len(probs)} problem(s):\n" + problems_text(last))
        if on_round:
            on_round(rounds)
        if rec["ok"]:
            break
        feedback = problems_text(last)
    ok = bool(rounds and rounds[-1]["ok"])
    if pg and message and not chat:
        pg.step("What the AI says it built and left out", "ok", detail=message)
    if pg:
        if ok and rounds[-1].get("message_only"):
            pass
        elif ok:
            pg.step("Done: the draft passes SATOM's rules. Save (or Apply) to keep it.", "ok")
        elif rounds:
            pg.step(f"Stopped after {len(rounds)} round(s) with problems left: they are listed, "
                    "never fixed silently.", "warn")
    return {"ok": ok, "rounds": rounds,
            "project": current if current is not project else None, "check": last,
            "message": message}


# --------------------------------------------------------------------------
# background jobs (state in SQLite, polled via /api/jobs/<id>)
# --------------------------------------------------------------------------
def progress_saver(conn, table: str, ref):
    """``save(snapshot)`` for :class:`progress.Progress`: one row per job
    (``job_progress``) or chat reply (``chat_progress``)."""
    col = {"job_progress": "job_id", "chat_progress": "message_id"}[table]

    def save(snap: dict) -> None:
        conn.execute(f"INSERT INTO {table} ({col}, data, updated_at) VALUES (?,?,?) "
                     f"ON CONFLICT({col}) DO UPDATE SET data = excluded.data, "
                     "updated_at = excluded.updated_at", (ref, db.dumps(snap), db.now()))
    return save


def load_progress(conn, table: str, ref) -> dict | None:
    col = {"job_progress": "job_id", "chat_progress": "message_id"}[table]
    row = conn.execute(f"SELECT data FROM {table} WHERE {col} = ?", (ref,)).fetchone()
    return with_now(db.loads(row["data"], None) if row else None)


def with_now(progress):
    """The server's clock at answer time: the page times the wait from it
    (while the model reads the request nothing streams, so nothing is saved)."""
    if isinstance(progress, dict):
        progress["server_now"] = db.now()
    return progress


# --------------------------------------------------------------------------
# Generate as a conversation (job_turns): history and continuation
# --------------------------------------------------------------------------
THREAD_RE = re.compile(r"^[0-9a-f]{32}$")
HISTORY_TURNS = 12
HISTORY_PROMPT_CHARS = 4000


def thread_turns(conn, thread: str, user_id: int, project_id: int) -> list:
    """The turns of *thread* that belong to this user AND project, oldest
    first ([] for a thread of anybody else: no enumeration)."""
    if not isinstance(thread, str) or not THREAD_RE.match(thread):
        return []
    return conn.execute(
        "SELECT t.job_id, t.thread, t.prompt, t.profile, t.created_at, j.status, j.rounds, "
        "j.error, j.result FROM job_turns t JOIN jobs j ON j.id = t.job_id "
        "WHERE t.thread = ? AND j.user_id = ? AND j.project_id = ? ORDER BY t.created_at, t.rowid",
        (thread, user_id, project_id)).fetchall()


def turn_outcome(row) -> str:
    """One line: what a turn came to (for the history and for the model)."""
    if row["status"] == "running":
        return "still working"
    if row["status"] == "failed":
        return "failed: " + (row["error"] or "no detail")[:300]
    rounds = db.loads(row["rounds"], []) or []
    res = db.loads(row["result"], {}) or {}
    last = rounds[-1] if rounds else {}
    note = (res.get("message") or "").strip()
    if last.get("message_only"):
        return "answered: " + note[:500]
    said = (" -- the AI says: " + note[:500]) if note else ""
    if last.get("ok"):
        return f"a draft that passes SATOM's rules ({len(rounds)} round(s))" + said
    probs = last.get("problems") or []
    return (f"a draft with {len(probs)} problem(s) left: "
            + "; ".join(f"{p.get('where', '?')}: {p.get('message', '')}" for p in probs[:3])[:600]
            + said)


def history_text(turns: list, new_prompt: str) -> str:
    """The author's side of a continued conversation: the earlier requests and
    what each came to (last :data:`HISTORY_TURNS`), then the new one. The
    draft the model gets is the project as saved now -- what the author kept."""
    parts = ["## The conversation so far",
             "Earlier requests of the author in this conversation and what each came "
             "to. The current draft (above) is what the author kept from them.\n"]
    for r in turns[-HISTORY_TURNS:]:
        prompt = (r["prompt"] or "").strip()
        if len(prompt) > HISTORY_PROMPT_CHARS:
            prompt = prompt[:HISTORY_PROMPT_CHARS] + " [...]"
        parts.append(f"**Author:** {prompt}\n**Result:** {turn_outcome(r)}\n")
    parts.append("## The author's new message\n\n" + new_prompt.strip())
    return "\n".join(parts)


def ai_history(conn, user_id: int, project_id: int, limit: int = 50) -> list:
    """This user's Generate conversations on this project, newest first:
    ``[{thread, title, updated_at, running, turns: [...]}]``."""
    rows = conn.execute(
        "SELECT t.job_id, t.thread, t.prompt, t.profile, t.created_at, j.status, j.rounds, "
        "j.error, j.result, j.updated_at FROM job_turns t JOIN jobs j ON j.id = t.job_id "
        "WHERE j.user_id = ? AND j.project_id = ? ORDER BY t.created_at, t.rowid",
        (user_id, project_id)).fetchall()
    threads: dict = {}
    for r in rows:
        rounds = db.loads(r["rounds"], []) or []
        last = rounds[-1] if rounds else {}
        th = threads.setdefault(r["thread"], {"thread": r["thread"], "title": "",
                                              "updated_at": 0, "running": False, "turns": []})
        if not th["title"]:
            lines = (r["prompt"] or "").strip().splitlines()
            th["title"] = lines[0][:80] if lines else ""
        th["updated_at"] = max(th["updated_at"], r["updated_at"] or r["created_at"])
        th["running"] = th["running"] or r["status"] == "running"
        th["turns"].append({"job_id": r["job_id"], "prompt": r["prompt"], "profile": r["profile"],
                            "created_at": r["created_at"], "status": r["status"],
                            "ok": bool(last.get("ok")) if r["status"] == "done" else False,
                            "rounds": len(rounds), "outcome": turn_outcome(r),
                            "has_draft": r["status"] == "done" and bool(
                                (db.loads(r["result"], {}) or {}).get("project"))})
    out = sorted(threads.values(), key=lambda t: t["updated_at"], reverse=True)
    return out[:limit]


def start_job(app, *, project_id: int, user_id: int, work, turn: dict | None = None) -> str:
    """Run ``work(job_id, conn, update, progress)`` in a daemon thread.
    ``update(rounds)`` persists the rounds, ``progress`` (a
    :class:`progress.Progress`) what the job is doing. *turn* ({thread,
    prompt, profile}) makes the job a turn of a Generate conversation.
    -> the job id."""
    from .progress import Progress
    job_id = uuid.uuid4().hex
    with db.session(app.config["DB_PATH"]) as conn:
        t = db.now()
        conn.execute("INSERT INTO jobs (id, project_id, user_id, kind, status, rounds, "
                     "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (job_id, project_id, user_id, "generate", "running", "[]", t, t))
        from .chat import runner_id
        conn.execute("INSERT INTO job_runners (job_id, runner) VALUES (?, ?)", (job_id, runner_id()))
        if turn:
            conn.execute("INSERT INTO job_turns (job_id, thread, prompt, profile, created_at) "
                         "VALUES (?,?,?,?,?)", (job_id, turn["thread"], turn["prompt"],
                                                turn.get("profile") or "", t))

    def target():
        with app.app_context(), db.session(app.config["DB_PATH"]) as conn:
            def update(rounds):
                conn.execute("UPDATE jobs SET rounds = ?, updated_at = ? WHERE id = ?",
                             (db.dumps(rounds), db.now(), job_id))
            pg = Progress(progress_saver(conn, "job_progress", job_id))
            try:
                res = work(job_id, conn, update, pg)
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
                pg.step("Failed: " + redact(msg)[:300], "bad")
                conn.execute("UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                             "WHERE id = ?", (redact(msg)[:2000], db.now(), job_id))

    th = threading.Thread(target=target, name=f"generate-{job_id[:8]}", daemon=True)
    th.start()
    app.extensions.setdefault("satom_builder_threads", {})[job_id] = th
    return job_id


def job_answer(row, progress: dict | None = None) -> dict:
    res = db.loads(row["result"], {}) or {}
    return {"id": row["id"], "status": row["status"], "progress": progress,
            "rounds": db.loads(row["rounds"], []) or [],
            "error": row["error"] or "",
            "ok": bool(res.get("ok")) if row["status"] == "done" else False,
            "message": (res.get("message") or "") if row["status"] == "done" else "",
            "project": res.get("project") if row["status"] == "done" else None}


RESTARTED = "The Builder restarted while this job ran; start it again."
STALE_AFTER = 2 * 3600


def fail_stale_jobs(conn) -> int:
    """At start-up (every gunicorn worker): a job still 'running' whose process
    is gone (``job_runners``, pid:starttime as chat replies) was cut off by a
    restart -- failed at once, with the reason on its activity too. A job of a
    live worker is left alone; one without a runner row (started before
    schema 6) or older than :data:`STALE_AFTER` is failed by age."""
    from .chat import _alive
    n = 0
    now = db.now()
    for r in conn.execute("SELECT j.id, j.updated_at, r.runner FROM jobs j LEFT JOIN job_runners r "
                          "ON r.job_id = j.id WHERE j.status = 'running'").fetchall():
        if r["runner"] is not None:
            if _alive(r["runner"]) and now - r["updated_at"] < STALE_AFTER:
                continue
        elif now - r["updated_at"] < 3600:
            continue
        cur = conn.execute("UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                           "WHERE id = ? AND status = 'running'", (RESTARTED, now, r["id"]))
        if cur.rowcount:
            n += 1
            snap = load_progress(conn, "job_progress", r["id"])
            if snap:
                snap.pop("server_now", None)
                for st in snap.get("steps") or []:
                    if st.get("state") == "run":
                        st["state"] = "bad"
                if snap.get("live") and snap["live"].get("phase") != "done":
                    snap["live"]["phase"], snap["live"]["ended"] = "done", now
                snap.setdefault("steps", []).append({"at": now, "text": "Failed: " + RESTARTED,
                                                     "state": "bad", "detail": ""})
                progress_saver(conn, "job_progress", r["id"])(snap)
    return n
