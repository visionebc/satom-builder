"""The child process of a test run: SATOM's runtime, from the rules pack, fed
with the Builder's test data instead of a database.

Started by ``satom_builder.runtime`` as::

    python -I -B runtime_child.py <pack dir> <testdata.py> <sys.path entries...>

with ONE JSON request on stdin and ONE JSON answer on stdout. Nothing of the
Builder is importable here (``-I``, ``sys.path`` = the pack + the standard
library + site-packages); ``testdata.py`` is loaded by path for its matching
rules. What runs is SATOM's code, verbatim from the pack:

* ``render`` -- ``plugin_sandbox.safe_render`` + ``plugin_frame.frame_document``;
* ``walk``   -- ``studio_flow.walk`` ARMED, because every place it would
  touch something real is replaced first by the test data:
  ``studio_store`` (get/query/insert/update/delete_row: rows in memory,
  SATOM's own ``clean_row``/``_match``/``upsert``/``delete_where`` on top),
  ``studio_connections``/``studio_http`` (canned answers), the SATOM and
  check steps (the ``steps`` answers), ``satom_select`` sources, and the
  Script Library (a temporary library on disk, run by SATOM's own
  ``script_runner`` in SATOM's own jail);
* ``script`` -- one script, the same way;
* ``info``   -- the datasets, their columns and the query SATOM fills them with.

There is NO network in this process: before anything else runs, every
AF_INET/AF_INET6 connect and name lookup raises. A replacement this file
forgot therefore fails a test run loudly instead of reaching a real system.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import sys
import tempfile
import time
import traceback

_OUT = sys.stdout
NO_NET = "no network in a SATOM Builder test run"


def _no_network() -> None:
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError(NO_NET)
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise OSError(NO_NET)
        return real_connect_ex(self, address)

    def getaddrinfo(*a, **k):
        raise OSError(NO_NET)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
    socket.gethostbyname = getaddrinfo
    socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(OSError(NO_NET))


def _load_testdata(path: str):
    spec = importlib.util.spec_from_file_location("builder_testdata", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# info / render
# --------------------------------------------------------------------------
def op_info(pack: str, req: dict) -> dict:
    from satom_rules.app.services import plugin_sandbox as PS
    from satom_rules.app.services import script_sdk as SDK
    from satom_rules.app.services import studio_satom as S
    return {"datasets": [{"key": k, "label": v[0], "description": v[1],
                          "columns": PS.dataset_columns(k), "sql": v[2]}
                         for k, v in PS.DATASETS.items()],
            "sources": [{"key": k, "label": lab} for k, lab in S.SOURCES],
            "sdk_doc": SDK.__doc__ or ""}


def op_render(pack: str, req: dict) -> dict:
    from satom_rules.app.services import plugin_frame as PF
    from satom_rules.app.services import plugin_sandbox as PS
    rt = json.load(open(os.path.join(pack, "rules.json"), encoding="utf-8"))["runtime"]
    tokens = open(os.path.join(pack, rt["tokens_css"]), encoding="utf-8").read()
    kit = PF.kit_css_from(open(os.path.join(pack, rt["kit_css"]), encoding="utf-8").read())
    test = req.get("test") or {}
    data = {}
    for key in req.get("datasets") or []:
        if key not in PS.DATASETS:
            continue
        cols = PS.dataset_columns(key)
        rows = ((test.get("datasets") or {}).get(key) or {}).get("rows") or []
        data[key] = {"columns": cols, "rows": [{c: r.get(c) for c in cols} for r in rows]}
    pdefs = PS.clean_param_defs(req.get("params") or [])
    params = PS.resolve_params(pdefs, PS.param_args(req.get("inputs") or {}))
    html, err = PS.safe_render(req.get("body") or "", data, params)
    files = type("P", (), {"css": req.get("css") or "", "js": req.get("js") or ""})
    doc = PF.frame_document(files(), html, data, params, tokens=tokens, kit=kit)
    return {"ok": err is None, "rendered": doc, "error": err or "",
            "problems": [{"where": "body.jinja", "message": err}] if err else [],
            "datasets": {k: len(v["rows"]) for k, v in data.items()}, "params": params}


# --------------------------------------------------------------------------
# the replacements for a walk / script run
# --------------------------------------------------------------------------
class _World:
    """Everything a run reads and writes instead of SATOM's database."""

    def __init__(self, req: dict, TD):
        from satom_rules.app.services import studio_store as SS
        self.TD = TD
        self.test = req.get("test") or {}
        self.user = "builder-test"
        self.log: list[str] = []
        self.stores: dict = {}
        for slug, d in (req.get("stores") or {}).items():
            try:
                cols = SS.clean_columns(d.get("columns") or [])
            except Exception:  # noqa: BLE001 -- a target store described by name/type only
                cols = [{"name": c.get("name"), "label": c.get("name"), "type": c.get("type") or "text",
                         "required": False} for c in d.get("columns") or [] if c.get("name")]
            st = type("Store", (), {})()
            st.slug, st.name, st.columns, st.id = slug, slug, cols, len(self.stores) + 1
            rows = ((self.test.get("stores") or {}).get(slug) or {}).get("rows") or []
            st.rows = []
            for i, r in enumerate(rows):
                row = {k: v for k, v in r.items() if not str(k).startswith("_")}
                row.update({"_id": int(r.get("_id") or i + 1),
                            "_created_at": str(r.get("_created_at") or "2026-10-01T08:00:00"),
                            "_updated_at": str(r.get("_updated_at") or "2026-10-01T08:00:00"),
                            "_by": str(r.get("_by") or "test-data")})
                st.rows.append(row)
            st.before = json.loads(json.dumps(st.rows, default=str))
            st.next_id = max([r["_id"] for r in st.rows] or [0]) + 1
            self.stores[slug] = st
        self.connections: dict = {}
        for slug, d in (req.get("connections") or {}).items():
            c = type("Conn", (), {})()
            c.slug, c.kind = slug, d.get("kind") or "http"
            c.base_url = d.get("base_url") or "https://test.invalid"
            c.auth, c.verify_tls = d.get("auth") or "none", True
            c.catalog = d.get("catalog")
            self.connections[slug] = c

    def note(self, line: str) -> None:
        if len(self.log) < 500:
            self.log.append(line)

    def changes(self) -> dict:
        out = {}
        for slug, st in self.stores.items():
            before = {r["_id"]: r for r in st.before}
            after = {r["_id"]: r for r in st.rows}
            ins = [after[i] for i in after if i not in before]
            dele = [before[i] for i in before if i not in after]
            upd = [after[i] for i in after if i in before and json.dumps(after[i], sort_keys=True,
                                                                          default=str)
                   != json.dumps(before[i], sort_keys=True, default=str)]
            if ins or dele or upd:
                out[slug] = {"inserted": ins, "updated": upd, "deleted": dele,
                             "rows": [{k: v for k, v in r.items() if not k.startswith("_")}
                                      for r in st.rows]}
        return out


def _install(world: _World, req: dict, libdir: str) -> None:
    """Point SATOM's data layer at the test data. ``FM_APP_DIR`` (*libdir*)
    was set by :func:`main` before anything of the pack was imported: the
    Script Library reads it once, at import."""
    from satom_rules.app.services import script_library as SL
    from satom_rules.app.services import script_runner as SR
    from satom_rules.app.services import studio_apis as SAPI
    from satom_rules.app.services import studio_apps as SA
    from satom_rules.app.services import studio_checks as C
    from satom_rules.app.services import studio_connections as SC
    from satom_rules.app.services import studio_flow as F
    from satom_rules.app.services import studio_http as H
    from satom_rules.app.services import studio_satom as S
    from satom_rules.app.services import studio_store as SS
    TD = world.TD

    # -- data stores: rows in memory; SATOM's clean_row/_match on top ----------
    def get(slug):
        return world.stores.get(str(slug))

    def query(store, flt=None, *, limit=500, order_by="", desc=False):
        flt = flt or {}
        if not isinstance(flt, dict):
            raise SS.StoreError("a filter is an object of column: value")
        known = {c["name"] for c in store.columns} | set(SS.SYSTEM_COLUMNS)
        bad = [k for k in flt if k not in known]
        if bad:
            raise SS.StoreError("unknown column(s) in the filter: %s" % ", ".join(sorted(bad)))
        limit = max(1, min(int(limit or 500), SS.MAX_QUERY_LIMIT))
        rows = [dict(r) for r in sorted(store.rows, key=lambda r: r["_id"]) if SS._match(r, flt)]
        if order_by:
            if order_by not in known:
                raise SS.StoreError("unknown column %r to order by" % order_by)
            rows.sort(key=lambda r: (r.get(order_by) is None, str(r.get(order_by))
                                     if not isinstance(r.get(order_by), (int, float))
                                     else r.get(order_by)), reverse=desc)
        return rows[:limit]

    def now():
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    def insert(store, values, by):
        data = SS.clean_row(store.columns, values)
        SS._encoded(data)
        if len(store.rows) + 1 > SS.MAX_ROWS:
            raise SS.StoreError("%s is full (%d rows)" % (store.slug, SS.MAX_ROWS))
        row = dict(data, _id=store.next_id, _created_at=now(), _updated_at=now(), _by=by)
        store.next_id += 1
        store.rows.append(row)
        world.note("store %s insert #%s" % (store.slug, row["_id"]))
        return dict(row)

    def update(store, row_id, values, by):
        row = next((r for r in store.rows if r["_id"] == int(row_id)), None)
        if row is None:
            raise SS.StoreError("row %s not found in %s" % (row_id, store.slug))
        data = {k: v for k, v in row.items() if not k.startswith("_")}
        data.update(SS.clean_row(store.columns, values, partial=True))
        for col in store.columns:
            v = data.get(col["name"])
            if col.get("required") and (v is None or (isinstance(v, str) and not v.strip())):
                raise SS.StoreError("%s is required" % col["name"])
        SS._encoded(data)
        row.clear()
        row.update(data)
        row.update({"_id": int(row_id), "_updated_at": now(), "_by": by,
                    "_created_at": row.get("_created_at") or now()})
        world.note("store %s update #%s" % (store.slug, row_id))
        return dict(row)

    def delete_row(store, row_id):
        n = len(store.rows)
        store.rows[:] = [r for r in store.rows if r["_id"] != int(row_id)]
        world.note("store %s delete #%s" % (store.slug, row_id))
        return len(store.rows) < n

    SS.get, SS.query, SS.insert, SS.update, SS.delete_row = get, query, insert, update, delete_row
    SS.count = lambda store: len(store.rows)

    # -- connections: canned answers, never the network ------------------------
    def conn_test(slug):
        return (world.test.get("connections") or {}).get(slug) or {}

    def http_request(d, secret, method="GET", path="", *, query=None, body=None, headers=None,
                     **_kw):
        if d.get("kind") != "http":
            raise H.ConnectionError_("%s is not an HTTP connection" % d.get("slug"))
        ans = TD.http_answer(conn_test(d.get("slug")), method, path)
        st = int(ans["status"])
        world.note("connection %s %s %s -> %s (test data)" % (d.get("slug"), method,
                                                              path or "/", st))
        out = {"status": st, "ok": 200 <= st < 300, "ms": 1,
               "url": (d.get("base_url") or "").rstrip("/") + "/" + (path or "").lstrip("/"),
               "content_type": "application/json"}
        b = ans.get("body")
        if isinstance(b, str):
            out["text"], out["content_type"] = b, "text/plain"
        else:
            out["json"] = b
        return out

    def pg_request(d, secret, sql, params=None, *, write=False, limit=1000, **_kw):
        rows = TD.sql_answer(conn_test(d.get("slug")), sql)[:int(limit or 1000)]
        world.note("connection %s sql %s -> %d row(s) (test data)" % (
            d.get("slug"), "write" if write else "read", len(rows)))
        cols = list(rows[0]) if rows else []
        return {"columns": cols, "rows": rows, "rowcount": len(rows), "ms": 1}

    H.http_request, H.pg_request = http_request, pg_request
    SC.get = lambda slug: world.connections.get(str(slug))
    SC.secret_state = lambda slug: ("test-data-secret", "")
    SC._verify = lambda c: True
    SAPI.catalog_of = lambda c: getattr(c, "catalog", None)

    def definition(c):
        return {"slug": c.slug, "kind": c.kind, "base_url": c.base_url, "host": "",
                "port": None, "database": "", "auth": c.auth, "username": "",
                "header_name": "", "token_url": "", "scope": "", "verify_tls": True,
                "timeout": 10, "catalog": getattr(c, "catalog", None)}
    SC.definition = definition

    def for_runner(slugs):
        entries, missing = {}, []
        for s in slugs or []:
            c = world.connections.get(s)
            if c is None:
                missing.append(s)
            else:
                entries[s] = {"def": definition(c)}
        return entries, missing
    SC.for_runner = for_runner

    # -- what SATOM itself or a device would answer: the "steps" test data -----
    steps = world.test.get("steps") or {}
    fleet = ((world.test.get("datasets") or {}).get("fleet_appliances") or {}).get("rows") or []

    def answer(node, default_outcome, default_value, what):
        key = node.get("key") or ""
        t = steps.get(key) or {}
        outcome = t.get("outcome") or default_outcome
        if outcome == "error":
            raise F.FlowError(t.get("detail") or "test data: %s answers error" % key)
        value = t["value"] if t.get("value") is not None else default_value
        detail = t.get("detail") or "test data: %s simulated" % what
        world.note("step %s (%s) -> %s (test data)" % (key, node.get("kind"), outcome))
        return outcome, value, detail

    appliance = dict(fleet[0]) if fleet else {"id": 1, "name": "fwb-lab-01", "kind": "fortiweb",
                                              "host": "192.0.2.10", "port": 443,
                                              "firmware": "7.6.8"}
    real_check = C.run

    def check_run(node, ctx):
        kind = node.get("kind")
        if kind in ("ask", "activity"):
            return real_check(node, ctx)
        if kind == "appliance_load":
            return answer(node, "ok", {k: appliance.get(k) for k in C.APPLIANCE_FIELDS},
                          "Load an appliance")
        if kind == "collect_diagnostics":
            return answer(node, "ok", {"file": "diag-test.tar.gz", "ran": 0, "skipped": 0},
                          "diagnostics")
        return answer(node, "yes", None, kind)
    C.run = check_run

    real_satom_run = S.run

    def satom_run(node, ctx):
        if node.get("kind") == "render_names":
            return real_satom_run(node, ctx)        # pure: SATOM's own naming rules
        from satom_rules.app.services.studio_flow import render
        plan = {k: v for k, v in (node.get("params") or {}).items() if k != "save_as"}
        try:
            would = render(plan, ctx.scope())
        except Exception:  # noqa: BLE001 -- an unrenderable plan is still shown
            would = plan
        return answer(node, "ok", {"simulated": True, "would": would}, node.get("kind"))
    S.run = satom_run
    S.rehearse = satom_run

    sources = world.test.get("sources") or {}
    S.source_options = lambda source: (list(sources.get(source) or []),
                                       "" if source in sources else
                                       "no test data for source %s" % source)
    SA.script_disabled = lambda slug: False

    # -- scripts: a library on disk, SATOM's runner and jail -------------------
    SR.PYTHON = sys.executable
    for sc in req.get("scripts") or []:
        slug = sc["slug"]
        SL.create(slug, sc.get("name") or slug, world.user)
        settings = dict(sc.get("settings") or {})
        settings.update({"network": "none", "credentials": []})
        settings["timeout"] = max(SL.TIMEOUT_MIN, min(int(settings.get("timeout") or 60),
                                                      int(req.get("max_timeout") or 120)))
        SL.save_draft(slug, sc.get("files") or {}, settings, world.user)
        SL.publish(slug, "test data", world.user)

    real_enqueue = F._enqueue_script

    def enqueue(slug, version, inputs, ctx):
        st = real_enqueue(slug, version, inputs, ctx)
        path = SL.REQ_DIR / (st["request_id"] + ".json")
        res = SR.process_request_file(path) or {}
        world.note("script %s -> %s" % (slug, res.get("status")))
        world.scripts.append({"slug": slug, "status": res.get("status"),
                              "output": res.get("output") or "", "result": res.get("result"),
                              "error": res.get("error") or "",
                              "duration_ms": res.get("duration_ms"),
                              "connections_log": res.get("connections_log") or []})
        return st
    F._enqueue_script = enqueue
    F.SCRIPT_POLL_S = 0.05
    world.scripts = []


def _choices(req: dict) -> dict:
    ch = dict(req.get("choices") or {})
    stores = list(dict.fromkeys(list(ch.get("stores") or []) + list(req.get("stores") or {})))
    http = [s for s, d in (req.get("connections") or {}).items() if (d.get("kind") or "http") == "http"]
    pg = [s for s, d in (req.get("connections") or {}).items() if d.get("kind") == "postgres"]
    ch["stores"] = stores
    ch["http"] = list(dict.fromkeys(list(ch.get("http") or []) + http))
    ch["pg"] = list(dict.fromkeys(list(ch.get("pg") or []) + pg))
    ch["scripts"] = list(dict.fromkeys(list(ch.get("scripts") or [])
                                       + [s["slug"] for s in req.get("scripts") or []]))
    return ch


def op_walk(pack: str, req: dict, TD) -> dict:
    from satom_rules.app.services import studio_apps as SA
    world = _World(req, TD)
    _install(world, req, os.environ["FM_APP_DIR"])
    from satom_rules.app.services import studio_flow as F
    ch = _choices(req)
    SA.choices = lambda: ch
    spec = req.get("app") or {}
    try:
        fields = SA.clean_fields(spec.get("fields") or [], ch)
        values = SA.resolve_inputs(fields, req.get("inputs") or {})
    except SA.AppError as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "steps": [], "outputs": [],
                "problems": [{"where": "app.fields", "message": str(exc)}], "log": world.log}
    passwords = [str(values[f["name"]]) for f in fields
                 if f.get("type") == "password" and values.get(f["name"])]
    secrets = SA._spellings([p for p in passwords if len(p) >= 3])
    graph = spec.get("graph") or {}
    answers = req.get("answers") or "yes"
    ctx = F.Ctx(form=values, armed=True, user=world.user, user_obj=None, run_id=0,
                scripts=True, deadline=time.time() + float(req.get("deadline") or 120))
    res = F.walk(graph, ctx)
    rounds = 0
    while res.get("status") == "waiting" and answers in ("yes", "no") and rounds < 20:
        rounds += 1
        res = F.walk(graph, ctx, resume={"steps": res.get("steps") or [],
                                          "key": res.get("waiting"), "answer": answers,
                                          "note": "answered by the test run"})
    waiting = None
    if res.get("status") == "waiting":
        key = res.get("waiting")
        node = next((n for n in graph.get("nodes") or [] if n.get("key") == key), {})
        step = next((s for s in reversed(res.get("steps") or []) if s.get("key") == key), {})
        waiting = {"key": key, "label": node.get("label") or key, "prompt": step.get("detail") or ""}
    status = res.get("status") or "failed"
    return {"ok": status == "done", "status": status,
            "steps": SA._mask(res.get("steps") or [], secrets),
            "outputs": json.loads(json.dumps(SA._mask(ctx.outputs, secrets), default=str)),
            "vars": json.loads(json.dumps(SA._mask(ctx.vars, secrets), default=str)),
            "waiting": SA._mask(waiting, secrets), "error": SA._mask(res.get("error") or "", secrets),
            "problems": [], "changes": world.changes(), "scripts": world.scripts,
            "log": world.log}


def op_script(pack: str, req: dict, TD) -> dict:
    world = _World(req, TD)
    _install(world, req, os.environ["FM_APP_DIR"])
    from satom_rules.app.services import script_library as SL
    from satom_rules.app.services import script_runner as SR
    slug = req.get("script") or ""
    if not any(s.get("slug") == slug for s in req.get("scripts") or []):
        return {"ok": False, "status": "failed", "error": "no script %r in this project" % slug}
    try:
        st = SL.enqueue_run(slug, version_ref="draft", raw_inputs=req.get("inputs") or {},
                            by=world.user, trigger="test", user=None)
    except (SL.ScriptError, ValueError) as exc:
        return {"ok": False, "status": "failed", "error": str(exc), "log": world.log}
    res = SR.process_request_file(SL.REQ_DIR / (st["request_id"] + ".json")) or {}
    keep = ("status", "exit_code", "output", "result", "error", "duration_ms", "egress",
            "connections_log")
    out = {k: res.get(k) for k in keep}
    out["ok"] = res.get("status") == "ok"
    out["inputs"] = res.get("inputs") or st.get("inputs")
    out["log"] = world.log
    return out


OPS = {"info": op_info, "render": op_render}


def main() -> int:
    pack, td_path, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
    sys.path[:] = [pack] + extra
    sys.stdout = io.StringIO()          # a print() anywhere must not corrupt the answer
    try:
        _no_network()
        req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        op = req.get("op")
        if op in OPS:
            res = OPS[op](pack, req)
        elif op in ("walk", "script"):
            # Before ANY import of the pack: script_library reads it at import.
            os.environ["FM_APP_DIR"] = tempfile.mkdtemp(prefix="satom-builder-run-")
            TD = _load_testdata(td_path)
            res = (op_walk if op == "walk" else op_script)(pack, req, TD)
        else:
            raise ValueError("unknown op %r" % (op,))
        _OUT.write(json.dumps({"ok": True, "result": res}, ensure_ascii=False, default=str))
    except BaseException as exc:  # noqa: BLE001 -- the parent shows it
        tb = traceback.format_exc()[-3000:]
        _OUT.write(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                               "trace": tb}))
    finally:
        lib = os.environ.get("FM_APP_DIR", "")
        if lib.startswith(tempfile.gettempdir()) and "satom-builder-run-" in lib:
            import shutil
            shutil.rmtree(lib, ignore_errors=True)
    _OUT.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
