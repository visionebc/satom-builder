"""SATOM's API library as API Catalog versions (contract section 19).

A connection that talks to a Fortinet device is pinned to a build of SATOM's
API library: ``"api": "fortigate", "api_version": "8.0.1"``. The endpoint
list of that build (about 3,700 for a FortiGate) is SATOM's, never the
author's or the model's:

* the draft keeps only the pin -- :func:`strip` drops any ``apis`` entry of a
  library product, so a model cannot write its own endpoint list to get an
  invented path through, and a draft stays small;
* :func:`complete` adds SATOM's entry (``GET /builder/library/fortinet/
  <product>/<build>/catalog``) to the project before a package is built, so
  the check, a test run and the package all see exactly what SATOM serves; an
  ``api_call`` whose path is not one of those endpoints is then a problem of
  SATOM's own rules (``... is not an endpoint in API fortigate 8.0.1``);
* :func:`index` turns a build into the compact list the AI reads: per
  section, each object with its key and its child lists.
"""
from __future__ import annotations

import copy
import re

PRODUCTS = ("fortigate", "fortiweb", "fortiadc", "fortiauthenticator", "fortianalyzer")
NAMES = {"fortigate": "FortiGate", "fortiweb": "FortiWeb", "fortiadc": "FortiADC",
         "fortiauthenticator": "FortiAuthenticator", "fortianalyzer": "FortiAnalyzer"}
#: words that name a product in an author's request
ALIASES = {"fortigate": ("fortigate", "fortigates", "fgt", "fortios"),
           "fortiweb": ("fortiweb", "fortiwebs", "fwb"),
           "fortiadc": ("fortiadc", "fortiadcs", "fad", "adc"),
           "fortiauthenticator": ("fortiauthenticator", "fac"),
           "fortianalyzer": ("fortianalyzer", "fortianalyzers", "faz")}
_PH = re.compile(r"\{([^}/]+)\}")
_ENTRY_KEYS = ("slug", "name", "owner", "docs_url", "description", "version", "base_path",
               "operations", "notes", "source")


class PinError(ValueError):
    """A pin SATOM cannot serve; the text says which connection and why."""


def _body(project: dict) -> dict:
    b = project.get("app")
    return b if isinstance(b, dict) else {}


def pins(project: dict) -> list[tuple[str, str, str]]:
    """``[(connection, product, build)]`` of the connections pinned to the
    API library, in the draft's order."""
    out = []
    for c in _body(project).get("connections") or []:
        if isinstance(c, dict) and c.get("api") in PRODUCTS and c.get("api_version"):
            out.append((str(c.get("slug") or ""), str(c["api"]), str(c["api_version"])))
    return out


def strip(project: dict) -> dict:
    """The project without any ``apis`` entry of a library product (the
    draft keeps the pin only)."""
    b = _body(project)
    apis = b.get("apis")
    if not isinstance(apis, list) or not any(
            isinstance(a, dict) and a.get("slug") in PRODUCTS for a in apis):
        return project
    out = dict(project)
    out["app"] = dict(b, apis=[a for a in apis
                               if not (isinstance(a, dict) and a.get("slug") in PRODUCTS)])
    return out


def complete(project: dict, resolve) -> tuple[dict, list[dict]]:
    """``(project with SATOM's entry for every pin, problems)``.
    ``resolve(product, build)`` returns the entry or raises (a SatomError, a
    PinError); a pin that cannot be resolved is a problem naming the
    connection, never a silent pass."""
    if project.get("kind") != "app":
        return project, []
    base = strip(project)
    want = pins(base)
    if not want:
        return base, []
    out = copy.deepcopy(base)
    body = out["app"]
    apis = list(body.get("apis") or [])
    problems, seen = [], set()
    for conn, product, build in want:
        if (product, build) in seen:
            continue
        seen.add((product, build))
        try:
            entry = resolve(product, build)
        except Exception as exc:  # noqa: BLE001 -- reported as the pin's problem
            msg = getattr(exc, "message", None) or str(exc)
            problems.append({"where": "connections[%s]" % conn, "rule": "builder.library_pin",
                             "message": "connection %s is pinned to %s %s of SATOM's API "
                                        "library, which SATOM cannot serve: %s"
                                        % (conn, NAMES[product], build, msg)})
            continue
        apis.append({k: entry[k] for k in _ENTRY_KEYS if k in entry})
    body["apis"] = apis
    return out, problems


# --------------------------------------------------------------------------
# what the AI reads
# --------------------------------------------------------------------------
def wanted(text: str, project: dict) -> list[str]:
    """The library products a request names or the draft is pinned to."""
    words = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    out = [p for p in PRODUCTS if words & set(ALIASES[p])]
    for _c, p, _b in pins(project):
        if p not in out:
            out.append(p)
    return out


def pick_build(builds: list) -> str:
    """The build to pin by default: the newest the fleet runs, else the
    newest measured one SATOM itself measured, else the newest measured."""
    rows = [b for b in builds or [] if isinstance(b, dict) and b.get("measured")]

    def key(b):
        return [(0, int(x)) if x.isdigit() else (1, x)
                for x in re.split(r"[._+-]", str(b.get("version") or ""))]
    for pool in ([b for b in rows if b.get("in_fleet")],
                 [b for b in rows if not b.get("vendor_only")], rows):
        if pool:
            return str(max(pool, key=key)["version"])
    return ""


def _paths(entry: dict) -> list[str]:
    """What an index lists: the endpoints' paths, and for a JSON-RPC
    endpoint (FortiAnalyzer, one ``POST /jsonrpc``) the urls its body may name."""
    out = []
    for o in entry.get("operations") or []:
        rpc = o.get("rpc") if isinstance(o, dict) else None
        out.extend(str(u) for u in rpc) if rpc else out.append(str(o.get("path") or ""))
    return out


def index(entry: dict) -> dict:
    """``{"prefix", "objects": {directory: "obj{key}[list list] obj ..."}}``
    from a catalog entry's endpoints: the path is prefix + directory + "/" +
    object; ``{key}`` = a table (its entry is ``/{key}``), ``[...]`` = its
    child lists (POST adds one item), no braces = a settings object (or a
    FortiWeb/FortiADC object, whose key is ``?mkey=``). For FortiAnalyzer the
    same over the JSON-RPC urls (``"jsonrpc": true``)."""
    objs: dict = {}
    for path in _paths(entry):
        head, sep, rest = path.partition("/{")
        rec = objs.setdefault(head, {"key": "", "children": set()})
        if sep:
            key, _s, tail = rest.partition("}")
            rec["key"] = rec["key"] or key
            child = tail.strip("/").split("/", 1)[0]
            if child and not child.startswith("{"):
                rec["children"].add(child)
    prefix = _common_prefix(list(objs))
    out: dict = {}
    for head in sorted(objs):
        rec = objs[head]
        name = head[len(prefix):].strip("/") or head
        group, _s, short = name.rpartition("/")
        s = short
        if rec["key"]:
            s += "{%s}" % rec["key"]
        if rec["children"]:
            s += "[%s]" % " ".join(sorted(rec["children"]))
        out.setdefault(group or "-", []).append(s)
    res = {"prefix": prefix, "objects": {g: " ".join(v) for g, v in sorted(out.items())}}
    if any(isinstance(o, dict) and o.get("rpc") for o in entry.get("operations") or []):
        res["jsonrpc"] = True
    return res


def _common_prefix(paths: list[str]) -> str:
    if not paths:
        return ""
    parts = [p.strip("/").split("/") for p in paths]
    pre = []
    for seg in zip(*parts):
        if len(set(seg)) != 1:
            break
        pre.append(seg[0])
    if pre and len(pre) == min(len(p) for p in parts):
        pre = pre[:-1]                  # never swallow a whole path
    return "/" + "/".join(pre) + "/" if pre else "/"


PIN_NOTE = (
    "To call a Fortinet device, define an HTTP connection IN THE PACKAGE (app.connections) "
    "pinned to SATOM's API library: \"api\": \"<product>\", \"api_version\": \"<build>\" (the "
    "build below), with the slug \"connection_slug\" below (a server connection with the same "
    "slug would keep its own settings). Do NOT write an \"apis\" entry for it: the Builder adds SATOM's "
    "endpoint list. In each api_call leave \"operation\" empty and write \"method\" and "
    "\"path\": the path must be an endpoint below (prefix + directory/object; a table's "
    "entry is /{key}, a child list /{key}/<list>), or SATOM refuses the package. Put a "
    "template in a path segment for a value "
    "(\"/api/v2/cmdb/firewall/addrgrp/{{ form.group }}/member\"). An app on a schedule runs "
    "again with the same devices, and a POST of an entry that exists fails (HTTP 500): read "
    "first (GET <path>/{key}; a 404 takes the step's error arrow) and create only what is "
    "missing -- the Builder runs it a second time to check. "
    "FortiADC: the key is ?mkey=<key> on the same path, a child table is its own path with "
    "the parent entry in ?pkey= (a pool member: POST /api/load_balance_pool_child_pool_member"
    "?pkey=<pool>), and a missing entry answers HTTP 200 {\"payload\": null} -- not the error "
    "arrow: test the payload; a pool member references an existing real server by name "
    "(POST /api/load_balance_real_server {\"mkey\": <name>, \"address\": <ip>} first, "
    "then the member {\"real_server_id\": <name>, \"port\": ...}), it is not an inline "
    "ip:port. FortiAuthenticator: only the methods listed (a single object has "
    "no /{id}/). FortiAnalyzer (\"jsonrpc\": true below): every call is POST /jsonrpc with "
    "body {\"method\": \"get\", \"params\": [{\"url\": \"<url>\"}]} and each url "
    "must be one listed (any ADOM as adom/<name>); the answer is HTTP 200 also when it failed: "
    "test result[0].status.code (0 = ok, -3 = does not exist).")


def prompt_section(entries: dict, others: dict) -> dict:
    """What an app prompt carries: ``entries`` = {product: catalog entry}
    for the products the request needs, ``others`` = {product: build} that
    exist but were not asked for (named only)."""
    out = {"how": PIN_NOTE, "builds": {}}
    for p, e in entries.items():
        ix = index(e)
        out["builds"][p] = {"api": p, "api_version": str(e.get("version") or ""),
                            "endpoints": len(e.get("operations") or []),
                            "prefix": ix["prefix"], "objects": ix["objects"]}
    if others:
        out["also_in_the_library"] = {p: v for p, v in others.items() if p not in entries}
    return out


# --------------------------------------------------------------------------
# which builds a request gets, and a wrong path's nearest endpoints
# --------------------------------------------------------------------------
#: the endpoint index the prompt may carry, all products together (characters;
#: a FortiGate build is ~19k, a FortiWeb ~13k): one full build. The rest of an
#: app prompt is ~52k characters, and a 32k-token model needs what is left to
#: answer and repair; a product past it is named with its build, and the
#: check's "is not an endpoint" carries the nearest endpoints.
INDEX_BUDGET = 22000


def library_builds(lib: dict) -> dict:
    """``{product: [build rows]}`` of a library answer (contract section 14)."""
    prods = ((lib or {}).get("fortinet") or {}).get("products") or {}
    return {p: rows for p, rows in prods.items() if p in PRODUCTS and isinstance(rows, list)}


def plan(text: str, project: dict, builds: dict) -> dict:
    """``{product: build}`` the prompt carries: every product the request
    names or the draft is pinned to, at the pinned build or the fleet's."""
    pinned = {p: b for _c, p, b in pins(project)}
    out = {}
    # pinned first: the draft already calls them
    for p in sorted(wanted(text, project), key=lambda x: (x not in pinned, PRODUCTS.index(x))):
        b = pinned.get(p) or pick_build(builds.get(p) or [])
        if b:
            out[p] = b
    return out


def free_slug(product: str, build: str, taken) -> str:
    """A connection slug for a pin that no server connection has:
    ``fortigate-801``, then ``fortigate-801-2``..."""
    base = "%s-%s" % (product, re.sub(r"[^0-9a-z]+", "", str(build).lower()))
    taken = set(taken or ())
    slug, n = base, 2
    while slug in taken:
        slug, n = "%s-%d" % (base, n), n + 1
    return slug


def prompt_for(want: dict, builds: dict, resolve, taken=()) -> dict | None:
    """The prompt section for ``want`` ({product: build}); products past
    :data:`INDEX_BUDGET`, or that SATOM cannot serve, are named with their
    build and endpoint count only. None when nothing is wanted and the
    library has no build."""
    others = {p: pick_build(rows) for p, rows in builds.items() if pick_build(rows)}
    if not want and not others:
        return None
    entries, used, skipped = {}, 0, {}
    for p, b in want.items():
        try:
            e = resolve(p, b)
        except Exception as exc:  # noqa: BLE001 -- the model is told, the check repeats it
            skipped[p] = "%s: %s" % (b, getattr(exc, "message", None) or str(exc))
            continue
        size = len(str(index(e)))
        if entries and used + size > INDEX_BUDGET:
            skipped[p] = "%s: %d endpoints (not listed here, for room; the check names a " \
                         "wrong path and its nearest endpoints)" % (b, len(e.get("operations") or []))
            continue
        entries[p] = e
        used += size
    out = prompt_section(entries, {p: v for p, v in others.items() if p not in want})
    for p, b in out["builds"].items():
        # a slug the server does not have: a server connection keeps its own
        # settings on import, so the pin would be lost (lab, 2026-10-26)
        b["connection_slug"] = free_slug(p, b["api_version"], taken)
    if skipped:
        out["not_listed"] = skipped
    return out


# the path may hold a template with spaces ({{ form.group }})
_NOT_EP = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE) ((?:/|\{\{).*?) is not an endpoint in API "
                     r"(\S+) (\S+)")
#: a path built as ONE expression: {{ '/api/v2/cmdb/firewall/address/' + ip }}
_WHOLE = re.compile(r"^\{\{\s*['\"](/[^'\"]*)['\"](.*)\}\}$", re.S)


def literal_path(path: str) -> str:
    """A whole-path expression rewritten with literal segments, or ''."""
    m = _WHOLE.match((path or "").strip())
    if not m:
        return ""
    head, rest = m.group(1), m.group(2)
    parts = [x.strip() for x in rest.split("+")[1:]]
    out = head
    for x in parts:
        lit = re.fullmatch(r"['\"]([^'\"]*)['\"]", x)
        out += lit.group(1) if lit else "{{ %s }}" % x
    return out
_TPL = re.compile(r"\{\{.*?\}\}|\{%.*?%\}")


def _norm(path: str) -> str:
    return _PH.sub("{}", _TPL.sub("{}", path.split("?", 1)[0])).rstrip("/")


def nearest(entry: dict, method: str, path: str, n: int = 5) -> list[str]:
    """The endpoints of ``entry`` closest to a wrong ``method path``."""
    import difflib
    want = _norm(path)
    ops = entry.get("operations") or []
    by = {}
    for o in ops:
        by.setdefault(_norm(str(o.get("path") or "")), []).append(o)
    names = difflib.get_close_matches(want, list(by), n=n, cutoff=0.3)
    segs = [s for s in want.split("/") if s and s != "{}"]
    if not names and segs:              # a known object name anywhere in the path
        names = [k for k in by if any(s in k.split("/") for s in segs[-2:])][:n]
    out = []
    for k in names:
        same = [o for o in by[k] if o.get("method") == method] or by[k]
        for o in same[:2]:
            out.append("%s %s" % (o.get("method"), o.get("path")))
    return out[:n]


def _rpc_known(urls, url: str) -> bool:
    want = [x for x in _TPL.sub("\x00", str(url or "").split("?", 1)[0]).split("/") if x]
    for u in urls or []:
        have = [x for x in str(u).split("/") if x]
        if len(have) == len(want) and all(w == "\x00" or h.startswith("{") or w == h
                                          for w, h in zip(want, have)):
            return True
    return False


def hints(problems: list, project: dict) -> list:
    """``problems`` with, after each "is not an endpoint in API <library
    product> <build>", the nearest endpoints of that build."""
    apis = {(a.get("slug"), str(a.get("version"))): a for a in _body(project).get("apis") or []
            if isinstance(a, dict)}
    out = []
    for p in problems or []:
        m = _NOT_EP.search(str((p or {}).get("message") or "")) if isinstance(p, dict) else None
        e = apis.get((m.group(3), m.group(4))) if m else None
        rpc = next((o for o in (e or {}).get("operations") or [] if isinstance(o, dict)
                    and o.get("rpc")), None) if m else None
        if m and rpc is not None:
            # lab, 2026-10-26: "GET /dvmdb/adom/root/device" written as an HTTP path
            url = m.group(2)
            known = _rpc_known(rpc["rpc"], url)
            p = dict(p, message=p["message"] + (
                " -- %s speaks JSON-RPC: every call is %s %s with body {\"method\": "
                "\"%s\", \"params\": [{\"url\": \"%s\"}]}%s" % (
                    NAMES.get(m.group(3), m.group(3)), rpc.get("method"), rpc.get("path"),
                    "get" if m.group(1) == "GET" else "set|add|update|delete", url,
                    " (%s is one of its urls)" % url if known else
                    "; its urls are in SATOM's API library (the index above)")))
            out.append(p)
            continue
        if m and e is not None and m.group(3) in PRODUCTS:
            lit = literal_path(m.group(2))
            if lit:
                p = dict(p, message=p["message"] + " -- the path is one expression, so it cannot "
                         "be matched to an endpoint: write it literally with a template only in "
                         "a segment, e.g. %s" % lit)
            near = nearest(e, m.group(1), lit or m.group(2))
            if near:
                p = dict(p, message=p["message"] + " -- nearest endpoints of %s %s: %s"
                         % (m.group(3), m.group(4), "; ".join(near)))
        out.append(p)
    return out


# --------------------------------------------------------------------------
# a Fortinet call that SATOM could not check
# --------------------------------------------------------------------------
#: REST paths that only a Fortinet device serves: a call to one on a
#: connection not pinned to SATOM's API library would reach SATOM unchecked.
#: Generic shapes (``/api/v1/...``, ``/jsonrpc``, ``/api/user_x``) count only
#: with what makes them Fortinet's, so another API's app is never flagged:
FORTINET_PATHS = ("/api/v2/cmdb/", "/api/v2/monitor/", "/api/v2.0/")
#: FortiAuthenticator: the 58 resources ``GET /api/v1/`` lists (fac01 8.0.3)
FAC_RESOURCES = frozenset((
    "auth", "csv", "emergencytoken", "faccloudhost", "fgtgroupfilter", "fortiguardmessages",
    "fortitokenmobilelicenses", "fortitokenmobileprovisioning", "fortitokens", "ftpservers",
    "iamaccounts", "iamusers", "idpsessiondata", "ldapusers", "licensing", "localapiadmin",
    "localgroup-memberships", "localusers", "logsettings", "macdevices",
    "macgroup-memberships", "macgroups", "oauth", "offlinehotpupdate", "offlineotp",
    "passwordpolicies", "pushpoll", "radiusclientgroups", "radiusclients",
    "radiusgroupclient", "radiuspolicies", "radiuspolicyclient", "radiuspolicyclientgroup",
    "radiususers", "realmauth", "recovery", "scepreqs", "scheduledbackupsettings",
    "smtpservers", "snmp", "snmpgeneral", "ssoauth", "ssogroup", "syslogservers", "system",
    "systeminfo", "tacplusclients", "tacplusclientsgroups", "tacplusgroupclient",
    "tacpluspolicies", "tacpluspolicyclient", "tacpluspolicygroup", "transfertoken",
    "upgrade", "usercerts", "usergroups", "userfortitokenpolicy", "userlockoutpolicy"))
#: FortiADC ``/api/<cli path with _>``: every name of a1's library (7.6.7,
#: 8.0.4) starts with one of these roots; only the load-balance ones are
#: FortiADC's alone, the rest count with a connection that names FortiADC
_FAD_RX = re.compile(r"^/api/(config|endpoint|firewall|global|link|load|log|router|security|"
                     r"system|user)_[a-z0-9_]+/?$")
_FAD_OWN = re.compile(r"^/api/(global_|link_)?load_balance_")
#: FortiAnalyzer: the url families its JSON-RPC serves
_FAZ_URL = re.compile(r"[\"']/(cli/global|dvmdb|sys|task|logview|eventmgmt|incidentmgmt|report|"
                      r"fazsys|fortiview|ueba|soarmgmt|pm|um)(/|[\"'])")


def path_product(path: str, body="", hint: str = "") -> str:
    """The Fortinet product whose REST API ``path`` is, or ''. ``body`` is the
    step's body (a JSON-RPC call names its url there), ``hint`` the
    connection's slug, name and URL."""
    p = str(path or "").split("?", 1)[0].strip()
    hint = str(hint or "").lower()
    if p.startswith(("/api/v2/cmdb/", "/api/v2/monitor/")):
        return "fortigate"
    if p.startswith("/api/v2.0/"):
        return "fortiweb"
    m = re.match(r"^/api/v1/([a-z0-9-]+)/", p + "/")
    if m and m.group(1) in FAC_RESOURCES:
        return "fortiauthenticator"
    if _FAD_RX.match(p) and (_FAD_OWN.match(p) or re.search(r"fortiadc|(^|[^a-z])(fad|adc)([^a-z]|$)", hint)):
        return "fortiadc"
    if re.match(r"^/jsonrpc/?$", p):
        text = body if isinstance(body, str) else __import__("json").dumps(body or {})
        if _FAZ_URL.search(text or ""):
            return "fortianalyzer"
    return ""


def lint(project: dict, snapshot: dict | None) -> list[dict]:
    """The Builder's own problems about Fortinet calls, after SATOM's check:

    * an ``api_call`` to a Fortinet REST path on a connection that is not
      pinned (in the package, or on the server) -- SATOM checks a path only on
      a pinned connection, so it would pass unchecked;
    * a package connection pinned to the library whose slug already exists on
      the server with another pin or none -- the import keeps the server's
      connection, so the pin would be lost."""
    if project.get("kind") != "app":
        return []
    body = _body(project)
    pkg = {str(c.get("slug")): c for c in body.get("connections") or []
           if isinstance(c, dict) and c.get("slug")}
    ch = (snapshot or {}).get("choices") or {}
    server_pins = {str(k): v for k, v in (ch.get("catalog") or {}).items() if isinstance(v, dict)}
    existing = ((snapshot or {}).get("existing") or {}).get("connections") or {}
    out = []
    for slug, c in pkg.items():
        if c.get("api") not in PRODUCTS or slug not in existing:
            continue
        have = server_pins.get(slug) or {}
        if (have.get("api"), str(have.get("version") or "")) != (c["api"], str(c.get("api_version"))):
            out.append({"where": "connections[%s]" % slug, "rule": "builder.library_pin",
                        "message": "connection %s already exists on SATOM (%s): the import keeps "
                                   "SATOM's, so the pin to %s %s would be lost. Give the "
                                   "package's connection another slug (e.g. %s-%s)."
                                   % (slug, "pinned to %s %s" % (have.get("api"), have.get("version"))
                                      if have else "not pinned", c["api"], c.get("api_version"),
                                      c["api"], str(c.get("api_version")).replace(".", ""))})
    nodes = ((body.get("app") or {}).get("graph") or {}).get("nodes") or []
    for n in nodes:
        if not isinstance(n, dict) or n.get("kind") != "api_call":
            continue
        prm = n.get("params") or {}
        slug, path = str(prm.get("connection") or ""), str(prm.get("path") or "")
        c = pkg.get(slug) or {}
        hint = " ".join(str(c.get(k) or "") for k in ("slug", "name", "base_url")) or slug
        product = path_product(path, prm.get("body") or "", hint)
        if not product:
            continue
        pinned = (pkg[slug].get("api") in PRODUCTS) if slug in pkg else slug in server_pins
        if not pinned:
            out.append({"where": "app.graph", "rule": "builder.library_unpinned",
                        "message": "%s: %s %s is a %s REST path, but connection %s is not "
                                   "pinned to SATOM's API library, so nothing checks it. Use a "
                                   "package connection with \"api\": \"%s\", "
                                   "\"api_version\": \"<build>\" (see SATOM's API library)."
                                   % (n.get("key"), (prm.get("method") or "GET").upper(), path,
                                      NAMES[product], slug or "(none)", product)})
    return out


# --------------------------------------------------------------------------
# the fields of the endpoints a diagram calls (SATOM's API library, contract
# sections 14 and 19.1)
# --------------------------------------------------------------------------
#: lab, 2026-10-26: gpt-oss wrote a FortiADC pool member as
#: {"member_ip", "member_port"} on the right path; nothing checked a field and
#: the test answers held only mkey/name, so the app "passed" and would have
#: failed on the device. The library knows each endpoint's fields where a
#: device showed them: the endpoints a diagram calls carry them in the
#: completed project, a body that sends another field is a problem naming the
#: real ones, and the test answers hold them.
MAX_FIELD_LOOKUPS = 12
#: keys every product accepts beside its fields
_ALWAYS = frozenset(("mkey", "name", "q_origin_key", "id", "_id", "seq", "vdom"))
_KEYSEG = re.compile(r"^\{.*\}$")


def endpoint_of(product: str, path: str, endpoints: list) -> tuple[str, str] | None:
    """``(library endpoint, list field)`` a request path (or a FortiAnalyzer
    url) reaches: the endpoint whose url the path starts with -- a key or a
    template after it is the entry, a word after the key a FortiGate child
    list; a FortiADC child table ``/api/<parent>_child_<t>`` is ``<parent>``'s
    list ``<t>``. None when the library lists no such endpoint."""
    p = _TPL.sub("{}", str(path or "").split("?", 1)[0]).rstrip("/")
    by: dict = {}
    for e in endpoints or []:
        if not isinstance(e, dict) or e.get("verdict") == "absent":
            continue
        name = str(e.get("endpoint") or "")
        urn = str(e.get("urn") or "").rstrip("/")
        if not urn and product == "fortianalyzer" and name:
            urn = "/cli/global/" + name.replace(".", "/")
        if urn:
            by[urn] = name
    m = re.match(r"^/api/(.+?)_child_([a-z0-9_]+)$", p)
    if m and "/api/" + m.group(1) in by:
        return by["/api/" + m.group(1)], m.group(2)
    segs = [s for s in p.split("/") if s]
    for n in range(len(segs), 0, -1):
        urn = "/" + "/".join(segs[:n])
        if urn not in by:
            continue
        # after the url: <key>[/<list>[/<item>]] -- the key may be literal
        # (/addrgrp/FWB-Group/member), so it is read by position
        tail = segs[n:]
        if len(tail) > 3 or (len(tail) >= 2 and _KEYSEG.match(tail[1])):
            return None
        return by[urn], (tail[1] if len(tail) >= 2 else "")
    return None


def field_spec(info: dict, list_field: str) -> dict:
    """``{"status", "fields": {name: type}}`` of a library fields answer;
    for a list field, its children (types unknown)."""
    flds = (info or {}).get("fields") or {}
    status = str((info or {}).get("status") or "")
    if list_field:
        spec = flds.get(list_field)
        kids = spec.get("children") if isinstance(spec, dict) else None
        out = ({"status": status, "fields": {str(c): "" for c in kids}} if kids
               else {"status": "unknown", "fields": {}})
    else:
        out = {"status": status, "fields": {str(k): str((v or {}).get("type") or "")
                                            for k, v in flds.items() if isinstance(v, dict)}}
    create = (info or {}).get("create")
    if isinstance(create, dict) and create.get("fields"):
        out["create"] = create
    return out


def _nodes(project: dict) -> list:
    return [n for n in ((_body(project).get("app") or {}).get("graph") or {}).get("nodes") or []
            if isinstance(n, dict) and n.get("kind") == "api_call"]


def _json_of(raw):
    if isinstance(raw, (dict, list)):
        return raw
    try:
        import json as _j
        return _j.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except ValueError:
        return None


def _rpc_urls(prm: dict) -> list[str]:
    doc = _json_of(prm.get("body"))
    params = doc.get("params") if isinstance(doc, dict) else None
    return [str(p.get("url")) for p in params or [] if isinstance(p, dict) and p.get("url")]


def _op_for(entry: dict, method: str, path: str) -> dict | None:
    want = [s for s in _TPL.sub("\x00", str(path or "").split("?", 1)[0]).split("/") if s]
    for o in entry.get("operations") or []:
        if not isinstance(o, dict) or str(o.get("method") or "").upper() != method:
            continue
        have = [s for s in str(o.get("path") or "").split("/") if s]
        if len(have) == len(want) and all(w == "\x00" or h.startswith("{") or w == h
                                          for w, h in zip(want, have)):
            return o
    return None


def attach_fields(project: dict, endpoints, fields, others=None) -> dict:
    """The (completed) project with, on each library operation its diagram
    calls, ``"fields": {endpoint, list, status, fields, build}`` from SATOM's
    library (a FortiAnalyzer call: ``"rpc_fields"`` per url). ``endpoints(product,
    build)`` -> the build's endpoint list, ``fields(product, build, endpoint)``
    -> its fields answer, ``others(product)`` -> the product's other measured
    builds, newest first: when the pinned build never showed an endpoint's
    fields (fortiadc11 8.0.4 has no pool), the newest build that did speaks
    for it (``build`` says which). Changes the project in place."""
    if project.get("kind") != "app":
        return project
    pinned = {c: (p, b) for c, p, b in pins(project)}
    apis = {(a.get("slug"), str(a.get("version"))): a for a in _body(project).get("apis") or []
            if isinstance(a, dict)}
    seen: dict = {}
    for n in _nodes(project):
        prm = n.get("params") or {}
        conn = str(prm.get("connection") or "")
        if conn not in pinned:
            continue
        product, build = pinned[conn]
        entry = apis.get((product, build))
        if entry is None:
            continue
        method = str(prm.get("method") or "GET").upper()
        op = _op_for(entry, method, prm.get("path"))
        if op is None:
            continue
        targets = _rpc_urls(prm) if op.get("rpc") else [str(prm.get("path") or "")]
        for t in targets:
            hit = endpoint_of(product, t, endpoints(product, build))
            if hit is None:
                continue
            key = (product, build) + hit
            if key not in seen:
                if len(seen) >= MAX_FIELD_LOOKUPS:
                    continue
                seen[key] = _fields_of(product, build, hit, fields, others)
            rec = seen[key]
            if rec is None:
                continue
            if op.get("rpc"):
                op.setdefault("rpc_fields", {})[t] = rec
            else:
                op["fields"] = rec
    return project


def _fields_of(product: str, build: str, hit: tuple, fields, others) -> dict | None:
    """The fields of *hit* on the pinned build or, when it never showed them,
    on the newest build that did; plus what a create needs where SATOM knows
    it (a FortiADC child table: asked by its own name, contract 19.1)."""
    create = None
    if product == "fortiadc" and hit[1]:
        try:
            create = field_spec(fields(product, build, "%s_child_%s" % hit), "").get("create")
        except Exception:  # noqa: BLE001 -- a hint, never a blocker
            create = None
    tried = [build] + [b for b in (others(product) if others else []) if b != build][:3]
    for b in tried:
        try:
            spec = field_spec(fields(product, b, hit[0]), hit[1])
        except Exception:  # noqa: BLE001 -- a field list is a help, never a blocker
            continue
        create = create or spec.get("create")
        if spec["status"] == "measured" and spec["fields"]:
            return dict(spec, endpoint=hit[0], list=hit[1], build=b,
                        **({"create": create} if create else {}))
    if create:
        return {"status": "unknown", "fields": {}, "endpoint": hit[0], "list": hit[1],
                "build": build, "create": create}
    return None


def _sent_keys(product: str, prm: dict, url: str = "") -> list[str]:
    """The field names a write step's body sends (FortiWeb wraps them in
    ``data``, FortiAnalyzer in ``params[].data``); keys built by a template
    are not names."""
    doc = _json_of(prm.get("body"))
    if product == "fortianalyzer":
        for p in (doc.get("params") if isinstance(doc, dict) else None) or []:
            if isinstance(p, dict) and str(p.get("url")) == url:
                doc = p.get("data")
                break
        else:
            doc = None
    elif product == "fortiweb" and isinstance(doc, dict) and set(doc) == {"data"}:
        doc = doc["data"]
    if isinstance(doc, list) and doc and isinstance(doc[0], dict):
        doc = doc[0]
    if not isinstance(doc, dict):
        return []
    return [k for k in doc if isinstance(k, str) and not _TPL.search(k)]


_WRITES = ("POST", "PUT", "PATCH")
_RPC_WRITES = ("set", "add", "update", "replace")


def fields_lint(project: dict) -> list[dict]:
    """``builder.library_fields``: a write step whose body sends a field the
    endpoint does not have, on a build whose fields a device showed."""
    if project.get("kind") != "app":
        return []
    pinned = {c: (p, b) for c, p, b in pins(project)}
    apis = {(a.get("slug"), str(a.get("version"))): a for a in _body(project).get("apis") or []
            if isinstance(a, dict)}
    out = []
    for n in _nodes(project):
        prm = n.get("params") or {}
        conn = str(prm.get("connection") or "")
        if conn not in pinned:
            continue
        product, build = pinned[conn]
        entry = apis.get((product, build)) or {}
        method = str(prm.get("method") or "GET").upper()
        op = _op_for(entry, method, prm.get("path"))
        if op is None:
            continue
        checks = []
        if op.get("rpc"):
            doc = _json_of(prm.get("body"))
            verb = str(doc.get("method") or "").lower() if isinstance(doc, dict) else ""
            if verb in _RPC_WRITES:
                checks = [(u, (op.get("rpc_fields") or {}).get(u)) for u in _rpc_urls(prm)]
        elif method in _WRITES:
            checks = [(str(prm.get("path") or ""), op.get("fields"))]
        for url, rec in checks:
            sent = _sent_keys(product, prm, url)
            if (product == "fortiadc" and method == "POST" and "_child_" not in url
                    and _json_of(prm.get("body")) is not None and "mkey" not in sent):
                # lab, 2026-10-26: {"address": ip} -- the entry got no name, so the
                # next run could not find it and created it again
                out.append({"where": "app.graph", "rule": "builder.library_fields", "message": (
                    "%s: POST %s does not send mkey: a FortiADC entry is created with its name "
                    "in mkey (e.g. \"mkey\": \"fwb-{{ appliance.name }}\") and found again "
                    "with GET ?mkey=<name> (HTTP 200 {\"payload\": null} when it does not "
                    "exist); without a name the next run cannot find what this one created"
                    % (n.get("key"), url[:160]))})
            if not rec:
                continue
            create = rec.get("create") or {}
            need = [f for f in create.get("fields") or [] if f.get("required")]
            missing = [f for f in need if f.get("key") not in sent]
            if method == "POST" and missing and _json_of(prm.get("body")) is not None:
                out.append({"where": "app.graph", "rule": "builder.library_fields", "message": (
                    "%s: POST %s does not send %s, which %s requires to create one (verified "
                    "on %s %s by SATOM): %s%s" % (
                        n.get("key"), url[:160], ", ".join(f["key"] for f in missing),
                        NAMES[product], NAMES[product], create.get("verified_on") or "",
                        "; ".join("%s = %s" % (f["key"], f.get("help") or "required")
                                  for f in missing),
                        (". " + create["hint"]) if create.get("hint") else ""))})
            if rec.get("status") != "measured" or not rec.get("fields"):
                continue
            names = rec["fields"]
            bad = [k for k in sent if k not in names and k not in _ALWAYS]
            if not bad:
                continue
            what = rec["endpoint"] + ((" list " + rec["list"]) if rec.get("list") else "")
            real = sorted(k for k in names if not k.startswith(("_", "q_")))
            seen_on = rec.get("build") or build
            out.append({"where": "app.graph", "rule": "builder.library_fields", "message": (
                "%s: %s %s sends %s, which %s %s does not have (SATOM's API library%s); its "
                "fields: %s" % (n.get("key"), method, url[:160], ", ".join(bad[:10]),
                                NAMES[product], what,
                                ", as a device of %s showed them" % seen_on,
                                ", ".join(real[:40]) + (" ..." if len(real) > 40 else "")))})
    return out
