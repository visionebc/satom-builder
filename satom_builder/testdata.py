"""Test data: what SATOM's runtime reads instead of a database when an author
tests a project in the Builder.

One JSON document per project (table ``test_data``)::

    {"format": "satom-builder.testdata/1",
     "datasets":    {"<dataset key>": {"rows": [{column: value}]}},
     "stores":      {"<store slug>": {"rows": [{column: value}]}},
     "connections": {"<slug>": {"responses": [{"method", "path", "status", "body"}],
                                "default": {"status", "body"},
                                "queries": [{"match", "rows"}]}},
     "steps":       {"<step key>": {"outcome", "value", "detail"}},
     "sources":     {"<satom_select source>": [{"value", "label"}]},
     "origin":      {"datasets:<key>" | "stores:<slug>" | "sources":
                     {"source": "satom"|"generated"|"package", "at", "target",
                      "total", "truncated", "edited"}}}

* **datasets** have SATOM's columns (``plugin_sandbox.dataset_columns`` from
  the rules pack): a view tested here reads the same keys it reads there.
* **stores** start from the package's seed rows, or generated rows shaped by
  the store's columns, and are what a diagram's store steps read and write.
* **connections** answer from canned responses: nothing leaves the Builder.
  An HTTP response matches on method (``*`` = any) and path (``*`` = any
  segment run, ``{x}`` = one segment); the first match wins, else
  ``default``. A PostgreSQL query returns the rows of the first ``match``
  substring found in its SQL.
* **steps** decide what a step that would touch SATOM itself or a device
  (checks, DDI, certificates, FortiWeb) answers in a test run.

Test data starts from the target's own data when its SATOM sends it
(contract section 15, :func:`from_satom`): the rows a view rendered there
shows the author, the data stores' rows and the ``satom_select`` lists, scoped
to the author's token. ``origin`` says where each table came from. Those rows
stay on this server: they are never written to a package, a repository or
a prompt (the assistant gets :func:`schema_text`, names and counts only).

Generated values are synthetic and deterministic for a seed: names like
``fwb-lab-01``, addresses in the documentation ranges (192.0.2.0/24,
198.51.100.0/24, 203.0.113.0/24), ``example.com`` hosts.

The test data is also a database: :func:`sql_tables` lays it out as tables
(a dataset = its key, a store = ``store_<slug>``), :func:`schema` adds the
relations a diagram draws, and :func:`run_sql` answers a read-only SQLite
SELECT over it.
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone

FORMAT = "satom-builder.testdata/1"
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 2000
MAX_RESPONSES = 200
DEFAULT_ROWS = 8
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_ORIGIN_KEY_RE = re.compile(r"^(sources|(datasets|stores):[A-Za-z0-9_.:-]{1,120})$")
ORIGINS = ("satom", "generated", "package")

KINDS = ("fortiweb", "fortigate", "fortiadc", "fortianalyzer", "fortiauthenticator")
_PREFIX = {"fortiweb": "fwb", "fortigate": "fgt", "fortiadc": "adc",
           "fortianalyzer": "faz", "fortiauthenticator": "fac"}
_FIRMWARE = {"fortiweb": ("7.6.8", "8.0.6"), "fortigate": ("7.4.9", "8.0.1"),
             "fortiadc": ("7.6.3", "8.0.3"), "fortianalyzer": ("7.6.4",),
             "fortiauthenticator": ("6.6.6", "8.0.3")}
_STATUS = ("enable", "enable", "enable", "disable")
_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
          "india", "juliet", "kilo", "lima")


class TestDataError(ValueError):
    pass


def empty() -> dict:
    return {"format": FORMAT, "datasets": {}, "stores": {}, "connections": {},
            "steps": {}, "sources": {}, "origin": {}}


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
class _Gen:
    """Deterministic synthetic values by column name (then by type)."""

    def __init__(self, seed: int, n_devices: int):
        self.r = random.Random(seed)
        self.n = max(1, n_devices)
        self.devices = []
        for i in range(self.n):
            kind = KINDS[0] if i < max(1, self.n - 2) else KINDS[(i % (len(KINDS) - 1)) + 1]
            self.devices.append({
                "id": i + 1, "name": f"{_PREFIX[kind]}-lab-{i + 1:02d}", "kind": kind,
                "firmware": self.r.choice(_FIRMWARE[kind]), "host": f"192.0.2.{10 + i}",
                "port": 443})
        self.t0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)

    def device(self, i: int) -> dict:
        return self.devices[i % self.n]

    def ip(self, i: int) -> str:
        return f"198.51.100.{(i * 7) % 250 + 2}"

    def when(self, i: int, ahead: bool = False) -> str:
        d = timedelta(days=self.r.randint(5, 400)) if ahead else -timedelta(hours=i * 3 + 1)
        return (self.t0 + d).isoformat(timespec="seconds")

    def word(self, i: int) -> str:
        return _WORDS[i % len(_WORDS)]

    def value(self, col: str, i: int, ctype: str = "", ctx: dict | None = None):
        c = (col or "").lower()
        dev = self.device((ctx or {}).get("dev", i))
        if c == "id":
            return i + 1
        if c == "appliance_id":
            return dev["id"]
        if c in ("device", "appliance", "appliance_name"):
            return dev["name"]
        if c == "kind":
            return dev["kind"] if (ctx or {}).get("table") == "fleet" else self.r.choice(
                ("inline", "offline"))
        if c in ("firmware", "version"):
            return dev["firmware"]
        if c in ("host", "hostname"):
            return dev["host"] if (ctx or {}).get("table") == "fleet" else f"app{i + 1}.example.com"
        if c == "port":
            return dev["port"] if (ctx or {}).get("table") == "fleet" else self.r.choice((80, 443, 8443))
        if c in ("ip", "address", "vip", "vserver", "ip_address", "src_ip", "dst_ip"):
            return f"203.0.113.{(i * 5) % 250 + 2}" if c in ("vip", "vserver") else self.ip(i)
        if c in ("policy", "name", "server_policy"):
            stem = {"fleet": dev["name"]}.get((ctx or {}).get("table"))
            return stem or f"{(ctx or {}).get('stem', 'policy')}-{self.word(i)}"
        if c in ("server_pool", "pool"):
            return f"pool-{self.word(i)}"
        if c in ("wpp", "web_protection_profile", "signature_rule", "bot_policy"):
            return {"wpp": "inline-standard", "web_protection_profile": "inline-standard",
                    "signature_rule": "standard-signatures", "bot_policy": "bot-default"}[c]
        if c in ("status", "state"):
            return self.r.choice(_STATUS)
        if c in ("traffic_log", "monitor_mode", "enabled", "https_service"):
            if c == "enabled":
                return self.r.choice((True, True, False))
            if c == "https_service":
                return "HTTPS"
            return self.r.choice(("enable", "disable"))
        if c == "deployment_mode":
            return self.r.choice(("server-pool", "http-content-routing"))
        if c in ("service",):
            return "HTTP"
        if c == "comment":
            return f"App ID APP-{1000 + i} owner team-{self.word(i)}"
        if c == "appid":
            return f"APP-{1000 + i}"
        if c in ("type",):
            return self.r.choice(("reverse-proxy", "offline-protection"))
        if c == "protocol":
            return self.r.choice(("HTTP", "HTTPS"))
        if c in ("common_name", "cn", "fqdn", "domain"):
            return f"{self.word(i)}.example.com"
        if c in ("not_after", "expires", "expiry", "valid_to", "next_run"):
            return self.when(i, ahead=True)
        if c in ("timestamp", "created_at", "updated_at", "at", "time", "date", "last_seen"):
            return self.when(i)
        if c in ("username", "user", "owner", "by"):
            return f"user{i % 4 + 1}"
        if c == "action":
            return self.r.choice(("server_policy.update", "login", "backup.run", "plugin.import"))
        if c == "target":
            return dev["name"]
        if c == "product":
            return dev["kind"]
        if c in ("action_key",):
            return self.r.choice(("device_sync", "metrics_scrape", "backup_all"))
        if c == "schedule_kind":
            return self.r.choice(("interval", "daily", "cron"))
        if c in ("devices",):
            return self.n
        if c in ("server_policies", "certificates", "count", "total"):
            return self.r.randint(3, 60)
        if c.endswith("_id"):
            return self.r.randint(1, 20)
        if c.startswith("is_") or c.startswith("has_"):
            return self.r.choice((True, False))
        t = (ctype or "").lower()
        if t == "number":
            return self.r.randint(1, 500)
        if t == "boolean":
            return self.r.choice((True, False))
        if t == "date":
            return self.when(i)[:10]
        if t == "json":
            return {"note": f"test {i + 1}"}
        return f"{c or 'value'}-{i + 1}"


def dataset_rows(key: str, columns: list[str], gen: _Gen, n: int) -> list[dict]:
    if key == "fleet_appliances":
        return [{c: gen.value(c, i, ctx={"table": "fleet", "dev": i}) for c in columns}
                for i in range(gen.n)]
    if key == "fleet_counts":
        return [{c: gen.value(c, 0) for c in columns}]
    stem = {"server_pools": "pool", "web_protection_profiles": "wpp",
            "scheduled_actions": "job"}.get(key, "policy")
    return [{c: gen.value(c, i, ctx={"stem": stem, "dev": i}) for c in columns}
            for i in range(n)]


def store_rows(columns: list[dict], gen: _Gen, n: int) -> list[dict]:
    out = []
    for i in range(n):
        row = {}
        for col in columns:
            name = str(col.get("name") or "")
            if not name or name.startswith("_"):
                continue
            row[name] = gen.value(name, i, str(col.get("type") or ""), {"stem": name})
        out.append(row)
    return out


def _app_body(project: dict) -> dict:
    a = project.get("app")
    return a if isinstance(a, dict) else {}


def store_defs(project: dict, snapshot: dict | None) -> dict:
    """``{slug: {"columns": [...], "seed": [...], "from": "package"|"target"}}``:
    the package's stores win over the target's existing ones."""
    out = {}
    existing = ((snapshot or {}).get("existing") or {}).get("stores") or {}
    for slug, d in existing.items():
        out[slug] = {"columns": list((d or {}).get("columns") or []), "seed": [], "from": "target"}
    for s in _app_body(project).get("stores") or []:
        if isinstance(s, dict) and s.get("slug"):
            out[s["slug"]] = {"columns": list(s.get("columns") or []),
                              "seed": list(s.get("rows") or []), "from": "package"}
    return out


def connection_defs(project: dict, snapshot: dict | None) -> dict:
    out = {}
    existing = ((snapshot or {}).get("existing") or {}).get("connections") or {}
    for slug, d in existing.items():
        out[slug] = dict(d or {}, slug=slug, **{"from": "target"})
    for c in _app_body(project).get("connections") or []:
        if isinstance(c, dict) and c.get("slug"):
            out[c["slug"]] = dict(c, **{"from": "package"})
    return out


def _api_ops(project: dict, conn: dict) -> list[dict]:
    """The operations of the API version a package connection is pinned to
    (``api`` + ``api_version``), when the package defines that version."""
    slug, ver = conn.get("api"), conn.get("api_version")
    if not slug:
        return []
    for a in _app_body(project).get("apis") or []:
        if isinstance(a, dict) and a.get("slug") == slug and (
                not ver or str(a.get("version")) == str(ver)):
            return [o for o in a.get("operations") or [] if isinstance(o, dict)]
    return []


def _used_ops(project: dict, conn: dict, ops: list[dict]) -> list[dict]:
    """The endpoints of *ops* the diagram's api_call steps on *conn* call
    (by endpoint id, or by method + path with templates as wildcards)."""
    steps = [n.get("params") or {} for n in
             ((_app_body(project).get("app") or {}).get("graph") or {}).get("nodes") or []
             if isinstance(n, dict) and n.get("kind") == "api_call"
             and (n.get("params") or {}).get("connection") == conn.get("slug")]
    tpl = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")
    out = []
    for o in ops:
        oseg = [x for x in str(o.get("path") or "").split("/") if x]
        for prm in steps:
            if prm.get("operation") and prm.get("operation") == o.get("id"):
                out.append(o)
                break
            want = [x for x in tpl.sub("\x00", str(prm.get("path") or "").split("?")[0]).split("/") if x]
            if str(prm.get("method") or "GET").upper() == o.get("method") and len(want) == len(oseg) \
                    and all(w == "\x00" or h.startswith("{") or w == h for w, h in zip(want, oseg)):
                out.append(o)
                break
    return out[:MAX_RESPONSES - 2]


#: test answers of a connection pinned to SATOM's API library come in the
#: product's own envelope: an app written against the real device
#: (``resp.results``) must not fail on a bare list (lab, 2026-10-26). Reads
#: measured with GETs on 2026-10-26: fgt02 8.0.1, fortiweb19 8.0.6
#: (``results``), fortiadc11 8.0.4 (``payload``), fac01 8.0.3 (``objects`` +
#: ``meta``), faz02 7.6.7 (JSON-RPC ``result[0]`` with ``status``/``data``,
#: HTTP 200 for every call). Writes are SATOM's device clients' reading.
_ROW = {"name": "item-1", "q_origin_key": "item-1"}


def _sample(kind: str):
    return [] if kind in ("list", "array") else 0 if kind in ("integer", "int", "float") else ""


def library_answer(product: str, method: str, fields: dict | None = None) -> dict:
    """The test answer of one library endpoint; with *fields* (``{name:
    type}``, SATOM's library), a read's row holds every field, so an app that
    reads a field the device does not have fails its test run."""
    read = method == "GET"
    if fields and read:
        row = {k: _sample(t) for k, t in list(fields.items())[:120]}
        row.update({"name": "item-1", "mkey": "item-1"} if product in ("fortiadc", "fortiweb")
                   else {"name": "item-1"})
        base = library_answer(product, method)
        for k in ("results", "payload", "objects"):
            if isinstance(base.get(k), list):
                base[k] = [dict(row, **({"q_origin_key": "item-1"} if product == "fortigate" else
                                        {"id": 1} if product == "fortiauthenticator" else {}))]
        return base
    if product == "fortigate":
        body = {"http_method": method, "status": "success", "http_status": 200,
                "vdom": "root", "path": "test", "name": "test"}
        if read:
            body["results"] = [dict(_ROW)]
        else:
            body["mkey"] = "item-1"
        return body
    if product == "fortiweb":
        return {"results": [dict(_ROW)] if read else {"errcode": 0}}
    if product == "fortiadc":
        return {"payload": [{"mkey": "item-1", "name": "item-1"}] if read else 0}
    if product == "fortiauthenticator":
        return ({"meta": {"limit": 20, "next": None, "offset": 0, "previous": None,
                          "total_count": 1}, "objects": [{"id": 1, "name": "item-1"}]}
                if read else {"id": 1, "name": "item-1"})
    if product == "fortianalyzer":
        # every call is POST /jsonrpc: the answer carries data either way
        return {"id": 1, "result": [{"status": {"code": 0, "message": "OK"}, "url": "test",
                                     "data": [{"name": "item-1"}]}]}
    return [{"id": 1, "name": "item-1"}] if read else {"ok": True}


# --------------------------------------------------------------------------
# a pinned connection as a device that remembers what it is told
# --------------------------------------------------------------------------
#: lab, 2026-10-26 (FortiADC E2E, found by the session reviewing it): an app
#: read the pool's members as ``pool_resp.payload.member | default([])`` --
#: ``payload`` IS the list -- so it never saw the member it had added and would
#: add it again every day. Canned answers cannot show that; a walk on a device
#: that keeps its writes, run twice, does (render_check.make_app). The answers
#: are the ones measured with GETs on 2026-10-26 (missing entry: FortiGate 404,
#: FortiWeb 500 errcode -3, FortiADC 200 ``{"payload": null}``,
#: FortiAuthenticator 404); a write's answer is SATOM's clients' reading.
SIM_PRODUCTS = ("fortigate", "fortiweb", "fortiadc", "fortiauthenticator")


def _canon(v) -> str:
    return json.dumps(v, sort_keys=True, default=str)


class Device:
    """``request(method, path, query, body)`` -> ``{"status", "body"}``.
    ``state`` = ``{collection: {key: row}}``; ``writes`` = every write as
    ``[method, collection, key, canonical body]``."""

    def __init__(self, product: str, state: dict | None = None):
        self.product = product
        self.state = json.loads(json.dumps(state or {}))
        self.writes: list = []
        # ids it hands out continue after the ones it already holds
        held = [int(k) for rows in self.state.values() if isinstance(rows, dict)
                for k in rows if str(k).isdigit()]
        self._n = max(held + [1000])

    def _auto(self) -> str:
        self._n += 1
        return str(self._n)

    def _write(self, method, coll, key, body):
        self.writes.append([method, coll, str(key), _canon(body)])

    def request(self, method: str, path: str, query=None, body=None) -> dict:
        method = (method or "GET").upper()
        q = {str(k): str(v) for k, v in (query or {}).items()} if isinstance(query, dict) else {}
        path, _s, qs = str(path or "").partition("?")
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                q.setdefault(k, v)
        fn = getattr(self, "_" + self.product.replace("forti", ""), None)
        return fn(method, path.rstrip("/") or "/", q, body) if fn else None

    # FortiGate: /api/v2/cmdb/<a>/<b>[/<key>[/<list>[/<item>]]]
    def _gate(self, m, path, q, body):
        segs = [s for s in path.split("/") if s]
        if segs[:3] != ["api", "v2", "cmdb"] or len(segs) < 5:
            return None
        obj, rest = "/".join(segs[3:5]), segs[5:]
        ok = {"http_method": m, "status": "success", "http_status": 200, "vdom": "root"}
        miss = {"status": 404, "body": {"http_method": m, "status": "error", "http_status": 404}}
        if len(rest) in (0, 1):
            coll, key = obj, (rest[0] if rest else None)
        elif len(rest) in (2, 3):
            if obj not in self.state or rest[0] not in self.state[obj]:
                return miss
            coll, key = "%s/%s/%s" % (obj, rest[0], rest[1]), (rest[2] if len(rest) == 3 else None)
        else:
            return None
        rows = self.state.setdefault(coll, {})
        b = body if isinstance(body, dict) else {}
        if m == "GET":
            if key is None:
                return {"status": 200, "body": dict(ok, results=list(rows.values()))}
            return {"status": 200, "body": dict(ok, results=[rows[key]])} if key in rows else miss
        if m == "POST" and key is None:
            name = str(b.get("name") or b.get("q_origin_key") or self._auto())
            self._write(m, coll, name, b)
            if name in rows:
                return {"status": 500, "body": {"http_method": "POST", "status": "error",
                                                "http_status": 500, "error": -5,
                                                "cli_error": "entry already exists"}}
            rows[name] = dict(b, name=name)
            return {"status": 200, "body": dict(ok, mkey=name)}
        if key is None:
            return {"status": 405, "body": {"status": "error", "http_status": 405}}
        self._write(m, coll, key, b)
        if key not in rows:
            return miss
        if m == "DELETE":
            rows.pop(key)
        else:
            rows[key] = dict(rows[key], **b)
        return {"status": 200, "body": dict(ok, mkey=key)}

    # FortiWeb: the key as ?mkey=, a sub-table (…-list) under ?mkey=<parent>&sub_mkey=
    def _web(self, m, path, q, body):
        if not path.startswith("/api/v2.0/"):
            return None
        b = body.get("data") if isinstance(body, dict) and set(body) == {"data"} else body
        b = b if isinstance(b, dict) else {}
        sub = path.endswith("-list") or "sub_mkey" in q
        coll = path + ("?mkey=" + q.get("mkey", "") if sub else "")
        key = q.get("sub_mkey") if sub else q.get("mkey")
        rows = self.state.setdefault(coll, {})
        nf = {"status": 500, "body": {"results": {"errcode": -3,
                                                  "message": "The entry is not found."}}}
        if m == "GET":
            if not key:
                return {"status": 200, "body": {"results": list(rows.values())}}
            return {"status": 200, "body": {"results": rows[key]}} if key in rows else nf
        if m == "POST":
            key = str(b.get("name") or b.get("id") or self._auto())
            self._write(m, coll, key, b)
            if key in rows:
                return {"status": 500, "body": {"results": {"errcode": -5,
                                                            "message": "A duplicate entry "
                                                                       "already exists."}}}
            rows[key] = dict(b, **({"name": key} if not sub else {"id": key}))
            return {"status": 200, "body": {"results": {"errcode": 0}}}
        self._write(m, coll, key, b)
        if not key or key not in rows:
            return nf
        if m == "DELETE":
            rows.pop(key)
        else:
            rows[key] = dict(rows[key], **b)
        return {"status": 200, "body": {"results": {"errcode": 0}}}

    # FortiADC: the key as ?mkey=, a child table under ?pkey=<parent entry>
    def _adc(self, m, path, q, body):
        if not path.startswith("/api/"):
            return None
        coll = path + ("?pkey=" + q["pkey"] if q.get("pkey") else "")
        key = q.get("mkey")
        rows = self.state.setdefault(coll, {})
        b = body if isinstance(body, dict) else {}
        if m == "GET":
            if not key:
                return {"status": 200, "body": {"payload": list(rows.values())}}
            return {"status": 200, "body": {"payload": rows.get(key)}}
        if m == "POST":
            key = str(b.get("mkey") or self._auto())
            self._write(m, coll, key, b)
            if key in rows:
                return {"status": 200, "body": {"payload": -15}}
            rows[key] = dict(b, mkey=key)
            return {"status": 200, "body": {"payload": 0}}
        self._write(m, coll, key, b)
        if not key or key not in rows:
            return {"status": 200, "body": {"payload": -1}}
        if m == "DELETE":
            rows.pop(key)
        else:
            rows[key] = dict(rows[key], **b)
        return {"status": 200, "body": {"payload": 0}}

    # FortiAuthenticator: /api/v1/<resource>/[<id>/]
    def _authenticator(self, m, path, q, body):
        segs = [s for s in path.split("/") if s]
        if segs[:2] != ["api", "v1"] or len(segs) not in (3, 4):
            return None
        coll, key = "/api/v1/%s/" % segs[2], (segs[3] if len(segs) == 4 else None)
        rows = self.state.setdefault(coll, {})
        b = body if isinstance(body, dict) else {}
        if m == "GET":
            if key is None:
                objs = list(rows.values())
                return {"status": 200, "body": {"meta": {"limit": 20, "next": None, "offset": 0,
                                                         "previous": None,
                                                         "total_count": len(objs)},
                                                "objects": objs}}
            return {"status": 200, "body": rows[key]} if key in rows else {"status": 404,
                                                                             "body": None}
        if m == "POST" and key is None:
            key = self._auto()
            self._write(m, coll, key, b)
            rows[key] = dict(b, id=int(key))
            return {"status": 201, "body": rows[key]}
        self._write(m, coll, key, b)
        if key is None or key not in rows:
            return {"status": 404, "body": None}
        if m == "DELETE":
            rows.pop(key)
            return {"status": 204, "body": None}
        rows[key] = dict(rows[key], **b)
        return {"status": 200, "body": rows[key]}


def connection_template(project: dict, conn: dict) -> dict:
    if conn.get("kind") == "postgres":
        return {"queries": [{"match": "SELECT", "rows": [{"id": 1, "name": "row-1"}]}]}
    responses = []
    ops = _api_ops(project, conn)
    if len(ops) > MAX_RESPONSES - 2:
        # a large version (a build of SATOM's API library is ~3,700 endpoints):
        # answer the endpoints this connection's steps call, plus the catch-alls
        ops = _used_ops(project, conn, ops)
    from . import fortinet_apis
    product = conn.get("api") if conn.get("api") in fortinet_apis.PRODUCTS else ""
    for op in ops:
        path = re.sub(r"\{[^}/]+\}", "*", str(op.get("path") or "/"))
        method = str(op.get("method") or "GET").upper()
        flds = (op.get("fields") or {}).get("fields") if isinstance(op.get("fields"), dict) else None
        body = library_answer(product, method, flds) if product else (
            [{"id": 1, "name": "item-1"}] if method == "GET" else {"ok": True})
        responses.append({"method": str(op.get("method") or "GET").upper(), "path": path,
                          "status": 200, "body": body})
    if not responses or len(ops) < len(_api_ops(project, conn)):
        responses += [{"method": "GET", "path": "*", "status": 200,
                       "body": library_answer(product, "GET") if product
                       else {"items": [{"id": 1, "name": "item-1"}]}},
                      {"method": "*", "path": "*", "status": 200,
                       "body": library_answer(product, "POST") if product else {"ok": True}}]
    return {"responses": responses,
            "default": {"status": 404, "body": {"error": "no test response for this request"}}}


def used_datasets(project: dict) -> list[str]:
    return [d for d in project.get("datasets") or [] if isinstance(d, str)]


def generate(project: dict, snapshot: dict | None, dataset_columns: dict,
             *, seed: int = 1, rows: int = DEFAULT_ROWS, current: dict | None = None,
             only: list | None = None, keys: list | None = None) -> dict:
    """Fresh test data for a project. *only* (``["datasets:x", "stores:y", ...]``)
    regenerates those tables and keeps the rest of *current*."""
    rows = max(1, min(int(rows or DEFAULT_ROWS), 200))
    gen = _Gen(int(seed or 1), max(3, min(rows, 12)))
    out = clean(current) if current else empty()
    want = set(only or [])

    def pick(kind, key):
        return not want or f"{kind}:{key}" in want

    for key in (keys if keys is not None else dataset_keys(project, dataset_columns, current)):
        cols = dataset_columns.get(key)
        if cols and pick("datasets", key):
            out["datasets"][key] = {"rows": dataset_rows(key, cols, gen, rows)}
            out["origin"][f"datasets:{key}"] = {"source": "generated", "seed": int(seed or 1)}
    for slug, d in store_defs(project, snapshot).items():
        if not pick("stores", slug):
            continue
        seed_rows = [r for r in d["seed"] if isinstance(r, dict)]
        out["stores"][slug] = {"rows": seed_rows or store_rows(d["columns"], gen, rows)}
        out["origin"][f"stores:{slug}"] = {"source": "package" if seed_rows else "generated",
                                           "seed": int(seed or 1)}
    for slug, c in connection_defs(project, snapshot).items():
        if pick("connections", slug) and (want or slug not in out["connections"]):
            out["connections"][slug] = connection_template(project, c)
    if pick("sources", "*") or not out["sources"]:
        fleet = [gen.device(i) for i in range(gen.n)]
        out["sources"] = {
            "appliances": [{"value": str(d["id"]), "label": d["name"]} for d in fleet],
            "fortiweb": [{"value": str(d["id"]), "label": d["name"]} for d in fleet
                         if d["kind"] == "fortiweb"],
        }
        out["origin"]["sources"] = {"source": "generated", "seed": int(seed or 1)}
    return out




def dataset_keys(project: dict, known, current: dict | None = None,
                 builtin=None, add=None) -> list[str]:
    """The datasets a project's test data holds. A view or a widget: the ones
    it reads first, then SATOM's built-in ones, then the ones the author added
    to explore (already in *current* or in *add*) -- all of them known to the
    target. An app reads none: only the fleet, which answers "Load an
    appliance" and the satom_select sources."""
    if project.get("kind") not in ("view", "widget"):
        return ["fleet_appliances"]
    known = list(known)
    have = set(known)
    out = [k for k in used_datasets(project) if k in have]
    builtin = list(builtin) if builtin is not None else known
    extra = list(((current or {}).get("datasets") or {}).keys()) + list(add or [])
    for k in builtin + extra:
        if k in have and k not in out:
            out.append(k)
    return out


# --------------------------------------------------------------------------
# from the target's SATOM (contract section 15)
# --------------------------------------------------------------------------
def from_satom(current: dict | None, answer: dict, project: dict, snapshot: dict | None,
               target: str, keys: list | None = None) -> dict:
    """*current* with the tables SATOM sent replaced by SATOM's rows.

    Datasets as :func:`dataset_keys` picks them; the data stores the project
    uses that exist on the target (a package-only store keeps its rows); the
    ``satom_select`` lists. Connections and step answers are not SATOM's data
    and stay. A document over :data:`MAX_BYTES` loses rows from its largest
    table first, and says so (``truncated``)."""
    out = clean(current) if current else empty()
    at = str(answer.get("taken_at") or "")[:40]
    target = str(target or "")[:120]
    sent = answer.get("datasets") or {}
    for key in (keys if keys is not None else dataset_keys(project, list(sent))):
        d = sent.get(key)
        if not isinstance(d, dict):
            continue
        rows = [r for r in d.get("rows") or [] if isinstance(r, dict)][:MAX_ROWS]
        out["datasets"][key] = {"rows": rows}
        out["origin"][f"datasets:{key}"] = {
            "source": "satom", "at": at, "target": target,
            "total": int(d.get("total") or len(rows)), "truncated": bool(d.get("truncated"))}
    stores = answer.get("stores") or {}
    for slug in store_defs(project, snapshot):
        d = stores.get(slug)
        if not isinstance(d, dict):
            continue
        rows = [r for r in d.get("rows") or [] if isinstance(r, dict)][:MAX_ROWS]
        out["stores"][slug] = {"rows": rows}
        out["origin"][f"stores:{slug}"] = {
            "source": "satom", "at": at, "target": target,
            "total": int(d.get("total") or len(rows)), "truncated": bool(d.get("truncated"))}
    src = answer.get("sources")
    if isinstance(src, dict) and src:
        out["sources"] = {str(k)[:60]: [o for o in v if isinstance(o, dict)][:500]
                          for k, v in src.items() if isinstance(v, list)}
        out["origin"]["sources"] = {"source": "satom", "at": at, "target": target}
    while _size(out) > MAX_BYTES:
        part, key = max(((p, k) for p in ("datasets", "stores") for k in out[p]),
                        key=lambda pk: len(out[pk[0]][pk[1]]["rows"]), default=(None, None))
        if part is None or not out[part][key]["rows"]:
            break
        rows = out[part][key]["rows"]
        del rows[len(rows) // 2 or 1:]
        out["origin"].setdefault(f"{part}:{key}", {})["truncated"] = True
    return clean(out)


def _size(doc) -> int:
    return len(json.dumps(doc, ensure_ascii=False, default=str).encode("utf-8"))


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------
def _rows(v, where: str) -> list:
    if not isinstance(v, list):
        raise TestDataError(f"{where}: rows must be a list.")
    if len(v) > MAX_ROWS:
        raise TestDataError(f"{where}: at most {MAX_ROWS} rows.")
    for i, r in enumerate(v):
        if not isinstance(r, dict):
            raise TestDataError(f"{where}[{i}]: a row is an object of column: value.")
    return v


def clean(data) -> dict:
    """The document, checked. Raises :class:`TestDataError` with the place."""
    if data in (None, ""):
        return empty()
    if not isinstance(data, dict):
        raise TestDataError("Test data must be a JSON object.")
    raw = json.dumps(data, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise TestDataError("Test data is larger than 4 MB.")
    out = empty()
    for part in ("datasets", "stores"):
        sec = data.get(part, {})
        if sec is None:
            sec = {}
        if not isinstance(sec, dict):
            raise TestDataError(f"{part} must be an object.")
        for k, v in sec.items():
            if not _KEY_RE.match(str(k)):
                raise TestDataError(f"{part}: {k!r} is not a valid name.")
            rows = (v or {}).get("rows", []) if isinstance(v, dict) else v
            out[part][k] = {"rows": _rows(rows, f"{part}.{k}.rows")}
    sec = data.get("connections", {})
    sec = {} if sec is None else sec
    if not isinstance(sec, dict):
        raise TestDataError("connections must be an object.")
    for k, v in sec.items():
        if not _KEY_RE.match(str(k)) or not isinstance(v, dict):
            raise TestDataError(f"connections.{k} must be an object.")
        resp = v.get("responses") or []
        if not isinstance(resp, list) or len(resp) > MAX_RESPONSES:
            raise TestDataError(f"connections.{k}.responses: a list of at most {MAX_RESPONSES}.")
        clean_resp = []
        for i, r in enumerate(resp):
            if not isinstance(r, dict):
                raise TestDataError(f"connections.{k}.responses[{i}] must be an object.")
            try:
                status = int(r.get("status", 200))
            except (TypeError, ValueError):
                raise TestDataError(f"connections.{k}.responses[{i}].status must be a number.")
            if not 100 <= status <= 599:
                raise TestDataError(f"connections.{k}.responses[{i}].status: 100 to 599.")
            clean_resp.append({"method": str(r.get("method") or "*").upper()[:10],
                               "path": str(r.get("path") or "*")[:500], "status": status,
                               "body": r.get("body")})
        d = v.get("default") if isinstance(v.get("default"), dict) else {}
        queries = v.get("queries") or []
        if not isinstance(queries, list):
            raise TestDataError(f"connections.{k}.queries must be a list.")
        out["connections"][k] = {
            "responses": clean_resp,
            "default": {"status": int(d.get("status", 404) or 404), "body": d.get("body")},
            "queries": [{"match": str(q.get("match") or "")[:500],
                         "rows": _rows(q.get("rows") or [], f"connections.{k}.queries[{i}].rows")}
                        for i, q in enumerate(queries) if isinstance(q, dict)]}
    sec = data.get("steps", {})
    sec = {} if sec is None else sec
    if not isinstance(sec, dict):
        raise TestDataError("steps must be an object.")
    for k, v in sec.items():
        if not _KEY_RE.match(str(k)) or not isinstance(v, dict):
            raise TestDataError(f"steps.{k} must be an object.")
        oc = str(v.get("outcome") or "ok")
        if oc not in ("ok", "yes", "no", "error"):
            raise TestDataError(f"steps.{k}.outcome is ok, yes, no or error.")
        out["steps"][k] = {"outcome": oc, "value": v.get("value"),
                           "detail": str(v.get("detail") or "")[:500]}
    sec = data.get("sources", {})
    sec = {} if sec is None else sec
    if not isinstance(sec, dict):
        raise TestDataError("sources must be an object.")
    for k, v in sec.items():
        if not isinstance(v, list):
            raise TestDataError(f"sources.{k} must be a list of {{value, label}}.")
        out["sources"][str(k)[:60]] = [{"value": str(o.get("value", "")),
                                        "label": str(o.get("label", o.get("value", "")))}
                                       for o in v[:500] if isinstance(o, dict)]
    sec = data.get("origin", {})
    sec = sec if isinstance(sec, dict) else {}
    for k, v in list(sec.items())[:500]:
        if not isinstance(v, dict) or not _ORIGIN_KEY_RE.match(str(k)):
            continue
        o = {"source": v.get("source") if v.get("source") in ORIGINS else "edited"}
        for f in ("at", "target"):
            if v.get(f):
                o[f] = str(v[f])[:120]
        for f in ("total", "seed"):
            if isinstance(v.get(f), int) and not isinstance(v.get(f), bool):
                o[f] = v[f]
        for f in ("truncated", "edited"):
            if v.get(f):
                o[f] = True
        out["origin"][str(k)] = o
    return out


def match_path(pattern: str, path: str) -> bool:
    """``*`` alone = any path; ``*`` inside = one or more path characters
    except ``/`` (a segment), ``**`` = anything."""
    pattern = (pattern or "*").strip()
    if pattern in ("*", "**"):
        return True
    p = (path or "/").split("?", 1)[0]
    rx = re.escape(pattern).replace(r"\*\*", ".*").replace(r"\*", "[^/]+")
    return re.fullmatch(rx, p) is not None or re.fullmatch(rx, p.rstrip("/")) is not None


def http_answer(conn: dict, method: str, path: str) -> dict:
    """The canned answer for one request: ``{"status", "body"}``."""
    for r in (conn or {}).get("responses") or []:
        if r.get("method") in ("*", (method or "GET").upper()) and match_path(r.get("path"), path):
            return {"status": r.get("status", 200), "body": r.get("body")}
    d = (conn or {}).get("default") or {}
    return {"status": int(d.get("status", 404) or 404),
            "body": d.get("body") if d.get("body") is not None
            else {"error": f"no test response for {method} {path}"}}


def sql_answer(conn: dict, sql: str) -> list:
    low = (sql or "").lower()
    for q in (conn or {}).get("queries") or []:
        if q.get("match") and q["match"].lower() in low:
            return list(q.get("rows") or [])
    return []


# --------------------------------------------------------------------------
# the test data as a database: tables, relations, read-only SQL
# --------------------------------------------------------------------------
SQL_MAX_ROWS = 500
SQL_MAX_CHARS = 20000
SQL_SECONDS = 3.0
SQL_MAX_VALUE = 10 * 1024 * 1024
#: Known references between SATOM's datasets: (column, table, key column).
REFS = (("appliance_id", "fleet_appliances", "id"), ("device", "fleet_appliances", "name"),
        ("server_pool", "server_pools", "name"), ("wpp", "web_protection_profiles", "name"))
_SQL_TYPE = {"number": "NUMERIC", "boolean": "INTEGER", "date": "TEXT", "json": "TEXT",
             "text": "TEXT"}
_DENY_FUNCTIONS = {"zeroblob", "randomblob", "load_extension", "readfile", "writefile",
                   "edit", "fts3_tokenizer"}
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                    getattr(sqlite3, "SQLITE_FUNCTION", 31), getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


def store_table(slug: str) -> str:
    return "store_" + re.sub(r"[^A-Za-z0-9_]", "_", slug)


def _infer(rows: list, col: str) -> str:
    for r in rows:
        v = r.get(col)
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            return "boolean"
        if isinstance(v, (int, float)):
            return "number"
        if isinstance(v, (dict, list)):
            return "json"
        return "text"
    return "text"


def sql_tables(data: dict, datasets: list[dict], stores: dict) -> list[dict]:
    """``[{name, kind, key, label, columns: [{name, type}], rows, sql}]``.
    *datasets* is the runtime's ``info`` list (key, label, columns, sql),
    *stores* ``{slug: {"columns": [...]}}``."""
    data = data or empty()
    info = {d["key"]: d for d in datasets or []}
    out = []
    for key, d in (data.get("datasets") or {}).items():
        rows = d.get("rows") or []
        known = list((info.get(key) or {}).get("columns") or [])
        names = known + [c for c in dict.fromkeys(k for r in rows for k in r) if c not in known]
        d = info.get(key) or {}
        out.append({"name": key, "kind": "dataset", "key": key,
                    "label": d.get("label") or key, "sql": d.get("sql") or "",
                    "source": d.get("source") or "", "spec": d.get("spec"),
                    "relations": [r for r in d.get("relations") or [] if isinstance(r, dict)],
                    "masked": list(d.get("masked") or []),
                    "columns": [{"name": c, "type": _infer(rows, c)} for c in names],
                    "rows": rows})
    used = {t["name"] for t in out}
    for slug, d in (data.get("stores") or {}).items():
        # a row without _id gets the one a test run gives it (its position)
        rows = [dict(r, _id=r.get("_id") or i + 1) for i, r in enumerate(d.get("rows") or [])]
        decl = [c for c in ((stores or {}).get(slug) or {}).get("columns") or []
                if isinstance(c, dict) and c.get("name")]
        names = ["_id"] + [c["name"] for c in decl]
        extra = [c for c in dict.fromkeys(k for r in rows for k in r)
                 if c not in names and not str(c).startswith("_")]
        types = {c["name"]: str(c.get("type") or "text") for c in decl}
        name = store_table(slug)
        while name in used:
            name += "_"
        used.add(name)
        out.append({"name": name, "kind": "store", "key": slug, "label": slug, "sql": "",
                    "source": "store", "spec": None, "relations": [], "masked": [],
                    "columns": [{"name": "_id", "type": "number"}]
                    + [{"name": c, "type": types.get(c) or _infer(rows, c)} for c in names[1:] + extra],
                    "rows": rows})
    return out


def schema(tables: list[dict], origin: dict | None = None) -> dict:
    """What the database diagram draws: tables (no rows) and relations."""
    by_name = {t["name"]: t for t in tables}
    by_key = {t["key"]: t for t in tables if t["kind"] == "dataset"}
    rels, seen = [], set()

    def add(frm, col, to, to_col, inferred):
        tgt = by_key.get(to) or by_name.get(to)
        if tgt is None or tgt["name"] == frm["name"] or (frm["name"], col) in seen:
            return
        if col not in {c["name"] for c in frm["columns"]} or \
                to_col not in {c["name"] for c in tgt["columns"]}:
            return
        seen.add((frm["name"], col))
        rels.append({"from": frm["name"], "column": col, "to": tgt["name"], "to_column": to_col,
                     "inferred": bool(inferred)})
    for t in tables:
        # what SATOM says first (contract section 16), then the known built-in references
        for r in t.get("relations") or []:
            add(t, str(r.get("column") or ""), str(r.get("to") or ""),
                str(r.get("to_column") or ""), r.get("inferred"))
        for col, ref, key in REFS:
            add(t, col, ref, key, False)
    origin = origin or {}
    return {"tables": [{"name": t["name"], "kind": t["kind"], "key": t["key"], "label": t["label"],
                        "sql": t["sql"], "source": t.get("source") or "", "spec": t.get("spec"),
                        "masked": t.get("masked") or [],
                        "columns": t["columns"], "rows": len(t["rows"]),
                        "origin": origin.get(("datasets:" if t["kind"] == "dataset" else "stores:")
                                             + t["key"]) or {}}
                       for t in tables],
            "relations": rels}


def schema_text(sch: dict) -> str:
    """The schema for a prompt: names, types, counts and relations, never a row."""
    lines = ["Tables of the test database (SQLite; a dataset is named by its key, a data "
             "store store_<slug>; the rows stay in the Builder):"]
    for t in sch.get("tables") or []:
        src = (t.get("origin") or {}).get("source") or "edited"
        cols = ", ".join(f"{c['name']} {c['type']}" for c in t["columns"])
        lines.append(f"- {t['name']} ({t['kind']} {t['key']}, {t['rows']} rows, {src}): {cols}")
    for r in sch.get("relations") or []:
        lines.append(f"- {r['from']}.{r['column']} -> {r['to']}.{r['to_column']}")
    return "\n".join(lines)


def _qi(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _cell(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if v is None or isinstance(v, (int, float, str)):
        return v
    return str(v)


def _authorizer(action, arg1, arg2, _db, _trigger):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_FUNCTION", 31) and str(arg2 or "").lower() in _DENY_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_sql(tables: list[dict], sql: str, *, max_rows: int = SQL_MAX_ROWS,
            seconds: float = SQL_SECONDS) -> dict:
    """One read-only SELECT over the test data, in a private in-memory SQLite:
    ``{"columns", "rows", "truncated", "ms"}``. Only reading is authorised (no
    write, no ATTACH, no PRAGMA, no file function), it stops after *seconds*,
    and a value is at most 10 MB. Raises :class:`TestDataError` with SQLite's
    message."""
    sql = (sql or "").strip()
    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql:
        raise TestDataError("Write a SELECT.")
    if len(sql) > SQL_MAX_CHARS:
        raise TestDataError(f"The query is longer than {SQL_MAX_CHARS} characters.")
    con = sqlite3.connect(":memory:")
    t0 = time.monotonic()
    try:
        for t in tables:
            cols = [c["name"] for c in t["columns"]]
            if not cols:
                continue
            con.execute("CREATE TABLE %s (%s)" % (_qi(t["name"]), ", ".join(
                "%s %s" % (_qi(c["name"]), _SQL_TYPE.get(c["type"], "")) for c in t["columns"])))
            con.executemany("INSERT INTO %s VALUES (%s)" % (_qi(t["name"]), ",".join("?" * len(cols))),
                            [[_cell(r.get(c)) for c in cols] for r in t["rows"]])
        if hasattr(con, "setlimit"):
            con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, SQL_MAX_VALUE)
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        con.set_authorizer(_authorizer)
        deadline = time.monotonic() + seconds
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description or []]
            fetched = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as exc:
            if str(exc) == "interrupted":
                raise TestDataError(f"The query ran longer than {seconds:g} s: stopped.") from None
            raise TestDataError(f"SQL: {exc}") from None
        except (sqlite3.Error, sqlite3.Warning, ValueError, OverflowError) as exc:
            raise TestDataError(f"SQL: {exc}") from None
        if not columns:
            raise TestDataError("Only a SELECT answers rows here.")
    finally:
        con.close()
    rows = [[v.hex() if isinstance(v, bytes) else v for v in r] for r in fetched[:max_rows]]
    return {"columns": columns, "rows": rows, "truncated": len(fetched) > max_rows,
            "ms": int((time.monotonic() - t0) * 1000)}
