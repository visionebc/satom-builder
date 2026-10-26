"""Internal JSON API for test data and test runs (contract section 11, 0.2).

| Route | Body -> Answer |
|---|---|
| ``GET /api/projects/<id>/testdata`` | -> ``{"data", "updated_at", "datasets", "stores", "connections", "steps", "sources", "runtime"}`` |
| ``PUT /api/projects/<id>/testdata`` | ``{"data"}`` -> the saved test data |
| ``POST /api/projects/<id>/testdata/generate`` | ``{"seed", "rows", "only"}`` -> the saved test data |
| ``POST /api/projects/<id>/testdata/import`` | ``{"limit"}`` -> the test data with the target SATOM's rows (contract section 15) |
| ``GET /api/projects/<id>/testdata/schema`` | -> ``{"tables", "relations"}``: the test data as a database, no rows |
| ``POST /api/projects/<id>/testdata/sql`` | ``{"sql"}`` -> ``{"columns", "rows", "truncated", "ms"}``: one read-only SELECT |
| ``POST /api/projects/<id>/test-runs`` | ``{"kind": "view|app|script", "script", "inputs", "answers"}`` -> 202 ``{"run_id"}`` |
| ``GET /api/projects/<id>/test-runs`` | -> the last 50 runs, newest first, without their answers |
| ``GET /api/test-runs/<run_id>`` | -> the run with its full answer |
| ``POST /api/test-runs/<run_id>/keep`` | -> applies the run's data-store changes to the test data |

Every route checks that the caller may open the project.
"""
from __future__ import annotations

from flask import Blueprint, current_app, g

from .. import db, fortinet_apis, projects, rules, runtime, targets, testdata
from ..satom_client import SatomError
from ..security import api_error, audit, login_required
from ..targets import NotReady
from .api import _json_body, _project

bp = Blueprint("testing", __name__, url_prefix="/api")


def _pack_for(conn, row):
    """-> (snapshot, pack, None) or (snapshot|None, None, why)."""
    try:
        t, snap = targets.context_for_project(conn, row, g.user)
    except (NotReady, SatomError) as exc:
        return None, None, getattr(exc, "message", None) or str(exc)
    try:
        pack = targets.rules_pack(conn, t, g.user.id, snap)
        runtime.check_pack(pack)
    except (rules.RulesError, SatomError) as exc:
        return snap, None, getattr(exc, "message", None) or str(exc)
    except runtime.RuntimeUnavailable as exc:
        return snap, None, exc.message
    return snap, pack, None


def _with_library(conn, row, proj: dict) -> tuple[dict, list]:
    """The project with SATOM's endpoint list for every connection pinned to
    the API library (contract section 19): what the runtime and the test
    answers see. -> (project, problems)."""
    if proj.get("kind") != "app" or not fortinet_apis.pins(proj):
        return proj, []
    t = targets.get_target(conn, row["target_id"]) if row["target_id"] is not None else None
    return targets.library_completion(conn, t, g.user.id)(proj)


def _graph_steps(project: dict) -> list[dict]:
    """The steps whose answer test data decides: checks and SATOM steps."""
    graph = (((project.get("app") or {}).get("app") or {}).get("graph") or {})
    plain = {"start", "end", "set", "decision", "fail", "output", "store_query",
             "store_write", "sql_query", "api_call", "script", "ask", "activity", "render_names"}
    return [{"key": n.get("key"), "kind": n.get("kind"), "label": n.get("label") or n.get("key")}
            for n in graph.get("nodes") or []
            if isinstance(n, dict) and n.get("kind") not in plain]


_DEF_KEYS = ("key", "label", "description", "columns", "sql", "source", "group", "product",
             "relations", "masked", "spec", "object")


def _defs(conn, row, pack) -> tuple[dict, list[str]]:
    """``({key: dataset}, built-in keys)``: SATOM's built-in datasets from the
    rules pack's runtime (with their SQL), every other one (administrator,
    device objects) from the target's library (contract section 16). Without
    a library (SATOM older than 4.3.0) only the built-in ones."""
    out, builtin = {}, []
    for d in runtime.info(pack).get("datasets") or []:
        out[d["key"]] = dict(d, source="builtin", group="Built-in")
        builtin.append(d["key"])
    t = targets.get_target(conn, row["target_id"]) if row["target_id"] is not None else None
    if t is not None:
        for k, d in targets.dataset_defs(conn, t, g.user.id).items():
            merged = dict(out.get(k) or {})
            merged.update({f: d[f] for f in _DEF_KEYS if f in d})
            out[k] = merged
    return out, builtin


def _keys(proj, defs, builtin, data, add=None) -> list[str]:
    return testdata.dataset_keys(proj, list(defs), data, builtin=builtin, add=add)


def _describe(conn, row, data, updated_at) -> dict:
    proj = projects.to_api(conn, row)
    snap, pack, why = _pack_for(conn, row)
    defs, builtin, sources = {}, [], []
    if pack is not None:
        try:
            defs, builtin = _defs(conn, row, pack)
            sources = runtime.info(pack).get("sources") or []
        except runtime.RuntimeUnavailable as exc:
            pack, why = None, exc.message
    reads = set(testdata.used_datasets(proj))
    datasets = [dict(defs[k], used=k in reads or proj.get("kind") not in ("view", "widget"),
                     builtin=k in builtin)
                for k in _keys(proj, defs, builtin, data)]
    stores = {s: {"columns": d["columns"], "from": d["from"]}
              for s, d in testdata.store_defs(proj, snap).items()}
    conns = {s: {"kind": d.get("kind") or "http", "from": d.get("from"),
                 "base_url": d.get("base_url") or ""}
             for s, d in testdata.connection_defs(proj, snap).items()}
    scripts_need_jail = runtime.needs_jail("app", proj) or bool(
        ((proj.get("app") or {}).get("scripts") or []))
    return {"data": data, "updated_at": updated_at, "datasets": datasets, "stores": stores,
            "catalog": len(defs),
            "connections": conns, "steps": _graph_steps(proj), "sources": sources,
            "runtime": {"ok": pack is not None, "message": why or "",
                        "satom_version": (snap or {}).get("satom_version") or "",
                        "jail": runtime.bwrap_available(),
                        "jail_message": "" if runtime.bwrap_available() or not scripts_need_jail
                        else runtime.BWRAP_MISSING}}


@bp.get("/projects/<int:pid>/testdata")
@login_required
def testdata_get(pid):
    row, err = _project(pid)
    if err:
        return err
    conn = db.get()
    data, at = runtime.load_testdata(conn, pid)
    return _describe(conn, row, data, at)


@bp.put("/projects/<int:pid>/testdata")
@login_required
def testdata_put(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    try:
        clean = runtime.save_testdata(db.get(), pid, body.get("data"))
    except testdata.TestDataError as exc:
        return api_error("bad_testdata", str(exc), 400)
    audit("testdata.save", row["slug"])
    return {"data": clean, "updated_at": db.now()}


@bp.post("/projects/<int:pid>/testdata/generate")
@login_required
def testdata_generate(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body(required=False)
    if err:
        return err
    conn = db.get()
    snap, pack, why = _pack_for(conn, row)
    if pack is None:
        return api_error("no_runtime", why or "No test runtime for this project's target.", 409)
    try:
        seed = int(body.get("seed") or 1)
        n = int(body.get("rows") or testdata.DEFAULT_ROWS)
    except (TypeError, ValueError):
        return api_error("bad_request", "seed and rows are numbers.", 400)
    only = body.get("only") if isinstance(body.get("only"), list) else None
    current, _ = runtime.load_testdata(conn, pid)
    # a pinned connection's test answers follow SATOM's endpoints; a pin SATOM
    # cannot serve keeps the generic answers (the test run then says why)
    proj, _pins = _with_library(conn, row, projects.to_api(conn, row))
    try:
        defs, builtin = _defs(conn, row, pack)
        data = testdata.generate(proj, snap, {k: d.get("columns") or [] for k, d in defs.items()},
                                 seed=seed, rows=n, current=current, only=only,
                                 keys=_keys(proj, defs, builtin, current))
        clean = runtime.save_testdata(conn, pid, data)
    except (testdata.TestDataError, runtime.RuntimeUnavailable) as exc:
        return api_error("bad_testdata", getattr(exc, "message", None) or str(exc), 400)
    audit("testdata.generate", row["slug"], seed=seed, rows=n, only=only or [])
    return _describe(conn, row, clean, db.now())


@bp.post("/projects/<int:pid>/testdata/import")
@login_required
def testdata_import(pid):
    """The target SATOM's own rows into the test data (contract section 15):
    every dataset the project's test data holds, the data stores it uses, the
    satom_select lists. Connections and step answers stay."""
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body(required=False)
    if err:
        return err
    try:
        limit = int(body.get("limit") or 200)
    except (TypeError, ValueError):
        return api_error("bad_request", "limit is a number.", 400)
    limit = max(1, min(limit, 2000))
    conn = db.get()
    snap, pack, why = _pack_for(conn, row)
    if pack is None:
        return api_error("no_runtime", why or "No test runtime for this project's target.", 409)
    proj = projects.to_api(conn, row)
    add = body.get("add") if isinstance(body.get("add"), list) else None
    try:
        t = targets.get_target(conn, row["target_id"])
        defs, builtin = _defs(conn, row, pack)
        current, _ = runtime.load_testdata(conn, pid)
        if add is not None:
            unknown = [str(k) for k in add if str(k) not in defs]
            if unknown or proj.get("kind") not in ("view", "widget"):
                return api_error("bad_request", "Only a view or a widget adds datasets, and only "
                                 "the target's: " + ", ".join(unknown[:5]), 400)
            keys = [str(k) for k in add]
            stores = []
        else:
            keys = _keys(proj, defs, builtin, current)
            stores = list(testdata.store_defs(proj, snap))
        # the project's ADOM (contract section 17); a table added by hand may be
        # of any ADOM the token reaches, so it is read in Global when it can be
        adom = targets.call_adom(snap)
        if add is not None and adom:
            adom = targets.GLOBAL
        with targets.client(conn, t, g.user.id, adom=adom) as c:
            answer = c.data(datasets=keys, stores=stores, limit=limit)
        if add is not None:
            answer = dict(answer, sources={})          # adding a table keeps the lists
        clean = runtime.save_testdata(conn, pid, testdata.from_satom(current, answer, proj, snap,
                                                                     t["name"], keys=keys))
    except SatomError as exc:
        return api_error(exc.code, exc.message, exc.status)
    except (testdata.TestDataError, runtime.RuntimeUnavailable) as exc:
        return api_error("bad_testdata", getattr(exc, "message", None) or str(exc), 400)
    audit("testdata.import", row["slug"], target=t["name"], limit=limit, added=add or [],
          datasets={k: len(v["rows"]) for k, v in clean["datasets"].items()},
          stores={k: len(v["rows"]) for k, v in clean["stores"].items()})
    return _describe(conn, row, clean, db.now())


def _tables(conn, row, pid):
    """-> (tables, data, None) or (None, None, error response)."""
    snap, pack, why = _pack_for(conn, row)
    if pack is None:
        return None, None, api_error("no_runtime", why or "No test runtime for this "
                                     "project's target.", 409)
    try:
        defs, _builtin = _defs(conn, row, pack)
    except runtime.RuntimeUnavailable as exc:
        return None, None, api_error(exc.code, exc.message, exc.status)
    data, _ = runtime.load_testdata(conn, pid)
    stores = testdata.store_defs(projects.to_api(conn, row), snap)
    return testdata.sql_tables(data, list(defs.values()), stores), data, None


@bp.get("/projects/<int:pid>/testdata/schema")
@login_required
def testdata_schema(pid):
    row, err = _project(pid)
    if err:
        return err
    tables, data, err = _tables(db.get(), row, pid)
    if err:
        return err
    return testdata.schema(tables, data.get("origin"))


@bp.post("/projects/<int:pid>/testdata/sql")
@login_required
def testdata_sql(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    sql = body.get("sql")
    if not isinstance(sql, str):
        return api_error("bad_request", "sql is a string.", 400)
    tables, _data, err = _tables(db.get(), row, pid)
    if err:
        return err
    try:
        return testdata.run_sql(tables, sql)
    except testdata.TestDataError as exc:
        return api_error("bad_sql", str(exc), 400)


@bp.post("/projects/<int:pid>/test-runs")
@login_required
def test_run_start(pid):
    row, err = _project(pid)
    if err:
        return err
    body, err = _json_body()
    if err:
        return err
    kind = body.get("kind")
    if kind not in runtime.KINDS:
        return api_error("bad_request", "kind is view, app or script.", 400)
    if body.get("inputs") is not None and not isinstance(body.get("inputs"), dict):
        return api_error("bad_request", "inputs must be an object.", 400)
    conn = db.get()
    proj = projects.to_api(conn, row)
    if kind == "view" and proj.get("kind") not in ("view", "widget"):
        return api_error("bad_request", "Only a view or a widget renders.", 400)
    if kind in ("app", "script") and proj.get("kind") != "app":
        return api_error("bad_request", "Only an app has a diagram and scripts.", 400)
    if kind == "script" and not any(isinstance(s, dict) and s.get("slug") == body.get("script")
                                    for s in (proj.get("app") or {}).get("scripts") or []):
        return api_error("not_found", "No such script in this project.", 404)
    if runtime.needs_jail(kind, proj) and not runtime.bwrap_available():
        return api_error("no_jail", runtime.BWRAP_MISSING, 409)
    snap, pack, why = _pack_for(conn, row)
    if pack is None:
        return api_error("no_runtime", why or "No test runtime for this project's target.", 409)
    test, _ = runtime.load_testdata(conn, pid)
    proj, pin_problems = _with_library(conn, row, proj)
    if pin_problems:
        return api_error("library_pin", "; ".join(p["message"] for p in pin_problems), 409)
    cols = {}
    if kind == "view":
        try:
            defs, _builtin = _defs(conn, row, pack)
            cols = {k: d.get("columns") or [] for k, d in defs.items()}
        except runtime.RuntimeUnavailable:
            cols = {}
    request = runtime.build_request(kind, proj, snap, test, body, dataset_columns=cols)
    rid = runtime.start(current_app._get_current_object(), pack, project_id=pid,
                        user_id=g.user.id, kind=kind, subject=str(body.get("script") or ""),
                        request=request, inputs_shown=runtime.masked_inputs(kind, proj, body))
    audit("test.run", row["slug"], kind=kind, script=str(body.get("script") or ""))
    return {"run_id": rid}, 202


@bp.get("/projects/<int:pid>/test-runs")
@login_required
def test_run_list(pid):
    row, err = _project(pid)
    if err:
        return err
    rows = db.get().execute("SELECT * FROM test_runs WHERE project_id = ? ORDER BY created_at "
                            "DESC LIMIT ?", (pid, runtime.KEEP_RUNS)).fetchall()
    return {"runs": [runtime.row_answer(r, full=False) for r in rows]}


def _run(run_id: str):
    r = db.get().execute("SELECT * FROM test_runs WHERE id = ?", (run_id,)).fetchone()
    if r is None:
        return None, api_error("not_found", "No such test run.", 404)
    _row, err = _project(r["project_id"])
    if err:
        return None, api_error("not_found", "No such test run.", 404)
    return r, None


@bp.get("/test-runs/<run_id>")
@login_required
def test_run_get(run_id):
    r, err = _run(run_id)
    if err:
        return err
    return runtime.row_answer(r)


@bp.post("/test-runs/<run_id>/keep")
@login_required
def test_run_keep(run_id):
    r, err = _run(run_id)
    if err:
        return err
    res = db.loads(r["result"], {}) or {}
    changes = res.get("changes") or {}
    if r["status"] != "done" or not changes:
        return api_error("nothing_to_keep", "This run changed no data store.", 409)
    conn = db.get()
    data, _ = runtime.load_testdata(conn, r["project_id"])
    for slug, ch in changes.items():
        data["stores"][slug] = {"rows": ch.get("rows") or []}
    try:
        clean = runtime.save_testdata(conn, r["project_id"], data)
    except testdata.TestDataError as exc:
        return api_error("bad_testdata", str(exc), 400)
    audit("testdata.keep", str(r["project_id"]), run=run_id, stores=sorted(changes))
    return {"data": clean, "stores": sorted(changes)}
