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

PRODUCTS = ("fortigate", "fortiweb", "fortiadc", "fortiauthenticator")
NAMES = {"fortigate": "FortiGate", "fortiweb": "FortiWeb", "fortiadc": "FortiADC",
         "fortiauthenticator": "FortiAuthenticator"}
#: words that name a product in an author's request
ALIASES = {"fortigate": ("fortigate", "fortigates", "fgt", "fortios"),
           "fortiweb": ("fortiweb", "fortiwebs", "fwb"),
           "fortiadc": ("fortiadc", "fad"),
           "fortiauthenticator": ("fortiauthenticator", "fac")}
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


def index(entry: dict) -> dict:
    """``{"prefix", "objects": {directory: "obj{key}[list list] obj ..."}}``
    from a catalog entry's endpoints: the path is prefix + directory + "/" +
    object; ``{key}`` = a table (its entry is ``/{key}``), ``[...]`` = its
    child lists (POST adds one item), no braces = a settings object (or a
    FortiWeb/FortiADC object, whose key is ``?mkey=``)."""
    objs: dict = {}
    for o in entry.get("operations") or []:
        path = str(o.get("path") or "")
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
    return {"prefix": prefix, "objects": {g: " ".join(v) for g, v in sorted(out.items())}}


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
    "missing -- the Builder runs it a second time to check.")


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


def hints(problems: list, project: dict) -> list:
    """``problems`` with, after each "is not an endpoint in API <library
    product> <build>", the nearest endpoints of that build."""
    apis = {(a.get("slug"), str(a.get("version"))): a for a in _body(project).get("apis") or []
            if isinstance(a, dict)}
    out = []
    for p in problems or []:
        m = _NOT_EP.search(str((p or {}).get("message") or "")) if isinstance(p, dict) else None
        e = apis.get((m.group(3), m.group(4))) if m else None
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
#: REST paths that only a Fortinet device serves (FortiGate cmdb/monitor,
#: FortiWeb): a call to one on a connection not pinned to SATOM's API library
#: would reach SATOM unchecked
FORTINET_PATHS = ("/api/v2/cmdb/", "/api/v2/monitor/", "/api/v2.0/")


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
        if not path.startswith(FORTINET_PATHS):
            continue
        pinned = (pkg[slug].get("api") in PRODUCTS) if slug in pkg else slug in server_pins
        if not pinned:
            out.append({"where": "app.graph", "rule": "builder.library_unpinned",
                        "message": "%s: %s %s is a Fortinet REST path, but connection %s is not "
                                   "pinned to SATOM's API library, so nothing checks it. Use a "
                                   "package connection with \"api\": \"<product>\", "
                                   "\"api_version\": \"<build>\" (see SATOM's API library)."
                                   % (n.get("key"), (prm.get("method") or "GET").upper(), path,
                                      slug or "(none)")})
    return out
