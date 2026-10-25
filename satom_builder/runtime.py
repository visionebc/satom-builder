"""Test runs: a project run by SATOM's own runtime on the Builder's test data.

The runtime travels in the target's rules pack (contract section 13): the
view renderer and frame, the diagram walk, the data-store rules, the script
jail and the script SDK, verbatim. This module only:

* checks that the pack carries it (SATOM 4.2.0 and later);
* assembles the request from the saved project, the target snapshot and the
  project's test data;
* runs ``runtime_child.py`` in a subprocess -- the same isolation as the
  rules (``python -I -B``, ``sys.path`` = the pack + stdlib + site-packages,
  no Builder secret in its environment), plus no network at all;
* keeps every run in ``test_runs`` (status, inputs with passwords masked,
  the full answer), so a run survives closing the page and is listed later.

Scripts run in SATOM's jail (bubblewrap). Without ``bwrap`` on this server a
script run fails saying so; views and diagrams without script steps still run.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from pathlib import Path

from . import db, rules, testdata

RUNTIME_FORMAT = "satom.runtime/1"
NEEDED = tuple("app.services." + m for m in (
    "plugin_frame", "plugin_sandbox", "studio_flow", "studio_apps", "studio_store",
    "studio_connections", "studio_apis", "studio_checks", "studio_satom", "script_library",
    "sandbox", "sandbox_boot", "script_sdk", "integration_sdk", "studio_broker", "studio_http",
    "script_runner"))
CHILD = Path(__file__).resolve().parent / "runtime_child.py"
TESTDATA = Path(__file__).resolve().parent / "testdata.py"
KINDS = ("view", "app", "script")
MAX_STDOUT = 24 * 1024 * 1024
KEEP_RUNS = 50
#: Seconds a test run may take, and the most a script in it may take.
RUN_TIMEOUT = 180
SCRIPT_TIMEOUT = 120
BWRAP_MISSING = ("Scripts run in SATOM's jail, which needs bubblewrap (bwrap) on this "
                 "server: install the bubblewrap package (apt install bubblewrap, "
                 "dnf install bubblewrap, zypper install bubblewrap) or re-run the "
                 "installer. Views and diagrams without script steps run without it.")


class RuntimeUnavailable(Exception):
    def __init__(self, message: str, code: str = "no_runtime", status: int = 409):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def check_pack(pack: rules.RulesPack) -> None:
    rt = (pack.meta or {}).get("runtime") or {}
    if rt.get("format") != RUNTIME_FORMAT:
        raise RuntimeUnavailable(
            f"The target runs SATOM {pack.satom_version}, whose rules pack carries no test "
            "runtime: test runs need SATOM 4.2.0 or later on the target.")
    mods = rt.get("modules") or {}
    missing = [m for m in NEEDED if mods.get(m) != "packed"]
    if missing:
        raise RuntimeUnavailable("The target's rules pack lacks part of the test runtime ("
                                 + ", ".join(m.rsplit(".", 1)[-1] for m in missing) + ").")


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


def call(pack: rules.RulesPack, request: dict, timeout: float = RUN_TIMEOUT) -> dict:
    """One request to the child. -> its result. Raises RuntimeUnavailable."""
    check_pack(pack)
    payload = json.dumps(request, ensure_ascii=False, default=str).encode("utf-8")
    if len(payload) > rules.MAX_STDIN:
        raise RuntimeUnavailable("The test run is too large (project + test data over 24 MB).",
                                 "too_large", 413)
    cmd = [sys.executable, "-I", "-B", str(CHILD), str(pack.dir), str(TESTDATA)] \
        + rules.child_sys_path()
    try:
        proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout,
                              cwd="/", env=dict(rules._SAFE_ENV), close_fds=True)
    except subprocess.TimeoutExpired:
        raise RuntimeUnavailable(f"The test run took longer than {int(timeout)} s: stopped.",
                                 "timeout", 504) from None
    out = proc.stdout
    if len(out) > MAX_STDOUT:
        raise RuntimeUnavailable("The test run answered more than 24 MB.", "too_large", 502)
    try:
        ans = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        tail = proc.stderr.decode("utf-8", "replace")[-600:].strip()
        raise RuntimeUnavailable("The test runtime crashed" + (f": {tail}" if tail else "."),
                                 "runtime_crash", 502)
    if not ans.get("ok"):
        raise RuntimeUnavailable("The test runtime failed: " + str(ans.get("error"))[:800],
                                 "runtime_failed", 502)
    return ans.get("result") or {}


_INFO: dict[str, dict] = {}


def info(pack: rules.RulesPack) -> dict:
    """``{"datasets": [{key, label, description, columns}], "sources": [...]}``
    from the pack, cached per pack."""
    if pack.sha not in _INFO:
        _INFO[pack.sha] = call(pack, {"op": "info"}, timeout=60)
    return _INFO[pack.sha]


def dataset_columns(pack: rules.RulesPack) -> dict:
    return {d["key"]: d["columns"] for d in info(pack).get("datasets") or []}


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------
def _app(project: dict) -> dict:
    a = project.get("app")
    return a if isinstance(a, dict) else {}


def _catalog(project: dict, conn: dict):
    """The pinned API version of a package connection, in the broker's shape."""
    ops = testdata._api_ops(project, conn)
    if not ops:
        return None
    return {"api": conn.get("api"), "version": conn.get("api_version") or "",
            "status": "published", "operations": ops}


def build_request(kind: str, project: dict, snapshot: dict | None, test: dict, body: dict) -> dict:
    if kind == "view":
        files = project.get("files") or {}
        return {"op": "render", "body": files.get("body.jinja") or "",
                "css": files.get("style.css") or "", "js": files.get("script.js") or "",
                "datasets": testdata.used_datasets(project), "params": project.get("params") or [],
                "inputs": body.get("inputs") or {}, "test": test}
    stores = {s: {"columns": d["columns"]} for s, d in testdata.store_defs(project, snapshot).items()}
    conns = {}
    for s, d in testdata.connection_defs(project, snapshot).items():
        conns[s] = {"kind": d.get("kind") or "http", "base_url": d.get("base_url") or "",
                    "auth": d.get("auth") or "none", "catalog": _catalog(project, d)}
    scripts = [s for s in _app(project).get("scripts") or [] if isinstance(s, dict)]
    req = {"op": "walk" if kind == "app" else "script", "test": test, "stores": stores,
           "connections": conns, "scripts": scripts,
           "choices": (snapshot or {}).get("choices") or {}, "inputs": body.get("inputs") or {},
           "max_timeout": SCRIPT_TIMEOUT, "deadline": RUN_TIMEOUT - 20}
    if kind == "app":
        req["app"] = _app(project).get("app") or {}
        req["answers"] = body.get("answers") if body.get("answers") in ("yes", "no", "stop") else "yes"
    else:
        req["script"] = str(body.get("script") or "")
    return req


def needs_jail(kind: str, project: dict) -> bool:
    if kind == "script":
        return True
    if kind != "app":
        return False
    nodes = ((_app(project).get("app") or {}).get("graph") or {}).get("nodes") or []
    return any(isinstance(n, dict) and n.get("kind") == "script" for n in nodes)


def masked_inputs(kind: str, project: dict, body: dict) -> dict:
    inputs = dict(body.get("inputs") or {})
    if kind == "app":
        fields = (_app(project).get("app") or {}).get("fields") or []
    elif kind == "script":
        sc = next((s for s in _app(project).get("scripts") or []
                   if isinstance(s, dict) and s.get("slug") == body.get("script")), {})
        fields = (sc.get("settings") or {}).get("fields") or []
    else:
        fields = []
    for f in fields:
        if isinstance(f, dict) and f.get("type") == "password" and inputs.get(f.get("name")):
            inputs[f["name"]] = "********"
    return inputs


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
def summary(res: dict) -> dict:
    return {"ok": bool(res.get("ok")), "status": res.get("status") or ("ok" if res.get("ok") else "failed"),
            "error": str(res.get("error") or "")[:500]}


def start(app, pack: rules.RulesPack, *, project_id: int, user_id: int, kind: str,
          subject: str, request: dict, inputs_shown: dict) -> str:
    """Run in a thread; the row is the state. -> the run id."""
    rid = uuid.uuid4().hex
    with db.session(app.config["DB_PATH"]) as conn:
        conn.execute("INSERT INTO test_runs (id, project_id, user_id, kind, subject, status, "
                     "inputs, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (rid, project_id, user_id, kind, subject, "running",
                      db.dumps(inputs_shown), db.now()))
        prune(conn, project_id)

    def work():
        try:
            res = call(pack, request)
            status = "done"
        except RuntimeUnavailable as exc:
            res, status = {"ok": False, "status": "failed", "error": exc.message}, "failed"
        except Exception as exc:  # noqa: BLE001 -- recorded on the run, never lost
            app.logger.error("test run %s failed: %s", rid, traceback.format_exc()[-2000:])
            res, status = {"ok": False, "status": "failed",
                           "error": f"{type(exc).__name__}: {exc}"[:500]}, "failed"
        with db.session(app.config["DB_PATH"]) as conn:
            conn.execute("UPDATE test_runs SET status = ?, summary = ?, result = ?, "
                         "finished_at = ? WHERE id = ?",
                         (status, db.dumps(summary(res)), db.dumps(res), db.now(), rid))

    th = threading.Thread(target=work, name=f"test-run-{rid[:8]}", daemon=True)
    th.start()
    app.extensions.setdefault("satom_builder_threads", {})[rid] = th
    return rid


def prune(conn, project_id: int) -> None:
    conn.execute("DELETE FROM test_runs WHERE project_id = ? AND id NOT IN (SELECT id FROM "
                 "test_runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?)",
                 (project_id, project_id, KEEP_RUNS))


def fail_stale(conn) -> int:
    """At start-up: a run still 'running' was cut off by a restart."""
    cur = conn.execute("UPDATE test_runs SET status = 'failed', summary = ?, finished_at = ? "
                       "WHERE status = 'running' AND created_at < ?",
                       (db.dumps({"ok": False, "status": "failed",
                                  "error": "The Builder restarted while this run ran; "
                                           "run it again."}), db.now(), db.now() - RUN_TIMEOUT))
    return cur.rowcount


def row_answer(row, full: bool = True) -> dict:
    out = {"id": row["id"], "project_id": row["project_id"], "kind": row["kind"],
           "subject": row["subject"], "status": row["status"],
           "summary": db.loads(row["summary"], {}) or {},
           "inputs": db.loads(row["inputs"], {}) or {},
           "created_at": row["created_at"], "finished_at": row["finished_at"]}
    if full:
        out["result"] = db.loads(row["result"], None)
    return out


# --------------------------------------------------------------------------
# test data rows
# --------------------------------------------------------------------------
def load_testdata(conn, project_id: int) -> tuple[dict, float | None]:
    row = conn.execute("SELECT data, updated_at FROM test_data WHERE project_id = ?",
                       (project_id,)).fetchone()
    if row is None:
        return testdata.empty(), None
    try:
        return testdata.clean(db.loads(row["data"], {})), row["updated_at"]
    except testdata.TestDataError:
        return testdata.empty(), row["updated_at"]


def save_testdata(conn, project_id: int, data: dict) -> dict:
    clean = testdata.clean(data)
    conn.execute("INSERT INTO test_data (project_id, data, updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(project_id) DO UPDATE SET data = excluded.data, "
                 "updated_at = excluded.updated_at", (project_id, db.dumps(clean), db.now()))
    return clean
