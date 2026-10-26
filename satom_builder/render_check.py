"""A view or widget the AI drafts is RENDERED before the loop calls it done.

Lab, 2026-10-26: asked for "the FortiGate rules that use an IP", gpt-oss wrote
a view reading ``policy.srcaddr``. SATOM's rules passed it (they parse the
template, they do not run it); the first real render failed with "'dict
object' has no attribute 'srcaddr'": a policy's addresses are rows of
``fortigate_firewall_policy__srcaddr`` (``parent_id`` -> the policy's ``id``),
not a field. So, once a draft passes the rules, it is rendered here with SATOM's
own runtime (the rules pack, as a Test run) over generated rows that have
exactly each dataset's columns (StrictUndefined: a column that does not exist
fails), with no input and with sample inputs. A failure is a problem of the
round, and it goes back to the model with the columns and relations of the
datasets the draft reads and of their sub-object datasets.

Never data rows from SATOM: the rows are generated. A runtime that is not
available (no jail, a pack without it) skips the render and says so.
"""
from __future__ import annotations

import json
import re

from . import runtime, testdata

#: rendering attempts per round: no input, then sample inputs
SAMPLE_IP = "192.0.2.10"


def dataset_columns(pack, defs: dict) -> dict:
    """``{key: [columns]}``: the library's datasets, the pack's built-in ones."""
    out = {str(k): list(d.get("columns") or []) for k, d in (defs or {}).items()}
    try:
        out.update(runtime.dataset_columns(pack))
    except runtime.RuntimeUnavailable:
        pass
    return out


def sample_inputs(params) -> dict:
    """A plausible value per input parameter: its default, else by type/name."""
    out = {}
    for p in params or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        name, typ = str(p["name"]), str(p.get("type") or "text")
        if p.get("default") not in (None, ""):
            out[name] = p["default"]
        elif typ in ("number", "int", "integer"):
            out[name] = 1
        elif typ in ("checkbox", "bool", "boolean"):
            out[name] = True
        elif p.get("options"):
            opts = p["options"]
            first = opts.splitlines()[0] if isinstance(opts, str) else (opts[0] if opts else "")
            out[name] = first.get("value") if isinstance(first, dict) else str(first).strip()
        else:
            out[name] = SAMPLE_IP if "ip" in name.lower() or "addr" in name.lower() else "x"
    return out


def columns_help(keys, defs: dict) -> str:
    """The columns and relations of the datasets *keys* and of their
    sub-object datasets (``<key>__<sub>``), for the model."""
    lines = []
    seen = set()
    for k in keys or []:
        family = [k] + sorted(x for x in (defs or {}) if x.startswith(k + "__"))
        for key in family:
            if key in seen or key not in (defs or {}):
                continue
            seen.add(key)
            d = defs[key]
            rels = ["%s -> %s.%s" % (r.get("column"), r.get("to"), r.get("to_column"))
                    for r in d.get("relations") or [] if isinstance(r, dict)]
            if key != k:
                rels.insert(0, "parent_id -> %s.id (one row per item of the parent's list)" % k)
            lines.append("- `%s`: columns %s%s" % (
                key, ", ".join((d.get("columns") or [])[:60]),
                ("; relations: " + "; ".join(rels[:12])) if rels else ""))
    return "\n".join(lines)


def _names(node, kind, out=None):
    """Names assigned by {% set %} (kind "store") or read (kind "load") under
    *node*, with the line of the first occurrence."""
    from jinja2 import nodes as N
    out = {} if out is None else out
    for n in node.find_all(N.Name):
        if n.ctx == kind:
            out.setdefault(n.name, n.lineno)
    return out


def loop_scope_problems(body: str) -> list[dict]:
    """A {% set x %} inside a {% for %} does not change x after that loop
    (Jinja scoping): the template renders, and is silently wrong (lab,
    2026-10-26: `{% set match = true %}` in nested loops, every rule hidden).
    Flags a name set inside a loop and read after it, in the same block."""
    from jinja2 import Environment, TemplateSyntaxError
    from jinja2 import nodes as N
    try:
        tree = Environment().parse(body or "")
    except TemplateSyntaxError:
        return []                       # SATOM's own check reports the syntax
    probs, seen = [], set()

    def walk(seq):
        seq = list(seq or [])
        for i, node in enumerate(seq):
            if isinstance(node, N.For):
                inside = {}
                for a in node.find_all(N.Assign):
                    for t in a.target.find_all(N.Name) if not isinstance(a.target, N.Name) \
                            else [a.target]:
                        inside.setdefault(t.name, a.lineno)
                if inside:
                    later = {}
                    for sib in seq[i + 1:]:
                        _names(sib, "load", later)
                    for name, line in sorted(inside.items()):
                        if name in later and (name, line) not in seen:
                            seen.add((name, line))
                            probs.append({"where": "files[body.jinja]:%d" % line, "message": (
                                "line %d: {%% set %s = ... %%} is inside the {%% for %%} of line %d, "
                                "so after that loop `%s` still has its old value (Jinja scoping; it "
                                "is read at line %d): collect it in a namespace instead -- "
                                "{%% set ns = namespace(%s=...) %%} before the loop, {%% set ns.%s = "
                                "... %%} inside, ns.%s after" % (line, name, node.lineno, name,
                                                                later[name], name, name, name))})
            for attr in ("body", "else_", "elif_"):
                child = getattr(node, attr, None)
                if isinstance(child, list):
                    walk(child)
    walk(tree.body)
    return probs


def make(pack, snapshot: dict, defs: dict):
    """-> ``check(candidate) -> (problems, note)`` for views and widgets;
    ``([], why)`` when it cannot render."""
    cols = dataset_columns(pack, defs)

    def check(candidate: dict):
        if candidate.get("kind") not in ("view", "widget"):
            return [], ""
        used = testdata.used_datasets(candidate)
        scope = loop_scope_problems((candidate.get("files") or {}).get("body.jinja") or "")
        if scope:
            return scope, ""
        test = testdata.generate(candidate, snapshot, cols, keys=used)
        inputs = sample_inputs(candidate.get("params"))
        attempts = [{}] + ([inputs] if inputs else [])
        for inp in attempts:
            req = runtime.build_request("view", candidate, snapshot, test, {"inputs": inp},
                                        dataset_columns=cols)
            try:
                res = runtime.call(pack, req, timeout=60)
            except runtime.RuntimeUnavailable as exc:
                return [], "not rendered: " + exc.message
            if not res.get("ok"):
                where = "files[body.jinja]"
                with_inp = (" with the inputs " + json.dumps(inp, ensure_ascii=False)) if inp else ""
                probs = [{"where": where, "message": "rendering it with test data%s fails: %s"
                          % (with_inp, str(res.get("error") or "")[:600])}]
                helptext = columns_help(used, defs)
                if helptext:
                    probs.append({"where": "datasets", "message": "the datasets it reads have "
                                  "exactly these columns (a field that is not listed does not "
                                  "exist; a list such as a policy's addresses is its own "
                                  "<type>__<list> dataset joined on parent_id):\n" + helptext})
                return probs, ""
        return [], "rendered with test data (%d attempt(s))" % len(attempts)

    return check


# --------------------------------------------------------------------------
# apps: walk the diagram on test data
# --------------------------------------------------------------------------
def app_inputs(candidate: dict) -> dict:
    """The inputs an app runs with: its first automation's (what a scheduled
    run gives), filled with a sample for every other field."""
    app = ((candidate.get("app") or {}).get("app") or {})
    autos = (candidate.get("app") or {}).get("automations") or []
    given = dict((autos[0] or {}).get("inputs") or {}) if autos and isinstance(autos[0], dict) else {}
    out = sample_inputs(app.get("fields"))
    out.update({k: v for k, v in given.items() if v not in (None, "")})
    return out


#: what FortiOS answers a POST of an entry that exists (the second run)
ALREADY_EXISTS = {"method": "POST", "path": "**", "status": 500,
                  "body": {"http_method": "POST", "status": "error", "http_status": 500,
                           "error": -5, "cli_error": "entry already exists"}}


#: JSON-RPC verbs (FortiAnalyzer, every call POST /jsonrpc) that only read
_RPC_READS = ("get",)


def _call_method(prm: dict) -> str:
    """The method an api_call step means: its HTTP method, except a JSON-RPC
    call (POST /jsonrpc) whose body's ``"method": "get"`` is a read."""
    m = str(prm.get("method") or "GET").upper()
    if m == "POST" and str(prm.get("path") or "").split("?", 1)[0].rstrip("/") == "/jsonrpc":
        verb = re.search(r'"method"\s*:\s*"([A-Za-z_]+)"', str(prm.get("body") or ""))
        if verb and verb.group(1).lower() in _RPC_READS:
            return "GET"
    return m


def _second_run(candidate: dict):
    """The connections to answer "already exists" on a second walk, or None:
    an app on a schedule (it runs again with the same devices) that POSTs to
    a connection pinned to SATOM's API library WITHOUT reading anything from
    it first. An app that reads first (an entry, a list, a rule's members)
    decides from what it read, which canned test answers cannot reproduce --
    walking it again would flag a correct app (lab, 2026-10-26); the armed
    runs on the device are its test."""
    body = candidate.get("app") or {}
    if not body.get("automations"):
        return None
    from . import fortinet_apis
    pinned = {c for c, _p, _b in fortinet_apis.pins(candidate)}
    posts, reads = set(), set()
    for n in ((body.get("app") or {}).get("graph") or {}).get("nodes") or []:
        if not isinstance(n, dict) or n.get("kind") != "api_call":
            continue
        prm = n.get("params") or {}
        m = _call_method(prm)
        (posts if m == "POST" else reads if m == "GET" else set()).add(str(prm.get("connection") or ""))
    hit = sorted((pinned & posts) - reads)
    return hit or None


def _stateful(candidate: dict) -> dict:
    """``{connection: product}`` to walk on a device that keeps its writes:
    an app on a schedule that writes to a connection pinned to SATOM's API
    library (FortiAnalyzer's JSON-RPC is answered as before)."""
    body = candidate.get("app") or {}
    if not body.get("automations"):
        return {}
    from . import fortinet_apis
    pinned = {c: p for c, p, _b in fortinet_apis.pins(candidate) if p in testdata.SIM_PRODUCTS}
    writes = set()
    for n in ((body.get("app") or {}).get("graph") or {}).get("nodes") or []:
        if isinstance(n, dict) and n.get("kind") == "api_call":
            prm = n.get("params") or {}
            if _call_method(prm) in ("POST", "PUT", "PATCH", "DELETE"):
                writes.add(str(prm.get("connection") or ""))
    return {c: p for c, p in pinned.items() if c in writes}


#: how each product's list read looks, for the message
_LIST_SHAPE = {"fortigate": '{"results": [rows]}', "fortiweb": '{"results": [rows]}',
               "fortiadc": '{"payload": [rows]} -- payload IS the list',
               "fortiauthenticator": '{"objects": [rows], "meta": {...}}'}


def _twice_on_devices(pack, candidate, snapshot, test, inputs, sims: dict, note: str):
    """Walk the app on devices that start empty and keep what it writes, then
    again on the same devices: tomorrow's run. A POST the second run repeats
    means what the app read did not show what it wrote -- it would add the same
    thing every day. A walk that cannot finish on an empty device (it needs an
    object that exists on the real one) says so in the note and decides nothing."""
    def walk(states):
        t = json.loads(json.dumps(test))
        for slug, product in sims.items():
            t.setdefault("connections", {}).setdefault(slug, {})["device"] = {
                "product": product, "state": states.get(slug) or {}}
        req = runtime.build_request("app", candidate, snapshot, t, {"inputs": inputs})
        return runtime.call(pack, req, timeout=runtime.RUN_TIMEOUT)
    try:
        first = walk({})
    except runtime.RuntimeUnavailable as exc:
        return [], note + "; on a device that keeps its writes: not run (%s)" % exc.message
    if first.get("status") not in ("done", "waiting"):
        return [], note + "; on an empty device it stops (%s): it needs what the real device "                           "has, so the repeat run is not checked" % str(first.get("error") or "")[:160]
    devs = first.get("devices") or {}
    try:
        second = walk({s: (d or {}).get("state") for s, d in devs.items()})
    except runtime.RuntimeUnavailable as exc:
        return [], note + "; the repeat run was not: " + exc.message
    done = {s: {(w[0], w[1], w[3]) for w in (d or {}).get("writes") or [] if w[0] == "POST"}
            for s, d in devs.items()}
    again = []
    for slug, d in (second.get("devices") or {}).items():
        for w in (d or {}).get("writes") or []:
            if w[0] == "POST" and (w[0], w[1], w[3]) in done.get(slug, set()):
                again.append((slug, w))
    if not again and second.get("status") in ("done", "waiting"):
        wrote = sum(len((d or {}).get("writes") or []) for d in devs.values())
        return [], note + ("; on a device that keeps its writes: %d write(s), and the repeat run "
                           "writes nothing it already wrote" % wrote)
    if again:
        slug, w = again[0]
        product = sims.get(slug, "")
        # what the second run decided before it wrote again: the clue
        decided = [x for x in second.get("steps") or [] if x.get("kind") == "decision"
                   or str(x.get("outcome")) in ("yes", "no")][-4:]
        trail = "; ".join("%s: %s" % (x.get("key"), str(x.get("detail") or x.get("outcome"))[:120])
                          for x in decided)
        msg = ("it runs on a schedule, and on a device that keeps what it is told the second "
               "run POSTs again what the first wrote (connection %s: POST %s %s). What it reads "
               "before writing did not show its own write -- on the device it would add the same "
               "thing every day.%s Read the list the device answers (%s), find the entry by the "
               "name it was created with, and decide with a test that can be true: a default([]) "
               "over a path that does not exist, or a list of values that never holds the one "
               "searched, hides it." % (
                   slug, w[1], w[3][:200],
                   (" The second run decided: %s." % trail) if trail else "",
                   _LIST_SHAPE.get(product, "the list")))
    else:
        bad = next((x for x in reversed(second.get("steps") or []) if x.get("outcome") == "error"),
                   None)
        msg = ("it runs on a schedule, and on a device that keeps what it is told the second run "
               "fails%s: %s. It must find what it wrote the day before and skip it."
               % (" at step %s" % bad.get("key") if bad else "",
                  str(second.get("error") or (bad or {}).get("detail") or "")[:300]))
    return [{"where": "app.graph", "rule": "builder.second_run", "message": msg}], ""


def make_app(pack, snapshot: dict):
    """-> ``check(candidate) -> (problems, note)`` for apps: SATOM's rules
    check the diagram's shape, not what it does -- a step that reads a
    variable no step set passes them and fails on its first run (lab,
    2026-10-26: ``{{ ip_list[i] }}`` with the counter named ``counter``). The
    diagram is walked with SATOM's own runtime on generated test data (the
    test fleet, every connection answered from its endpoints), with the
    inputs of its first automation."""
    cols = dataset_columns(pack, {})

    def check(candidate: dict):
        if candidate.get("kind") != "app":
            return [], ""
        if runtime.needs_jail("app", candidate) and not runtime.bwrap_available():
            return [], "not run: its script steps need the jail (bubblewrap) on this Builder"
        test = testdata.generate(candidate, snapshot, cols, keys=["fleet_appliances"])
        inputs = app_inputs(candidate)
        req = runtime.build_request("app", candidate, snapshot, test, {"inputs": inputs})
        try:
            res = runtime.call(pack, req, timeout=runtime.RUN_TIMEOUT)
        except runtime.RuntimeUnavailable as exc:
            return [], "not run: " + exc.message
        status = res.get("status")
        if status in ("done", "waiting"):
            n = len(res.get("steps") or [])
            again = _second_run(candidate)
            if again is None:
                sims = _stateful(candidate)
                if not sims:
                    return [], "ran on test data: %s after %d step(s)" % (status, n)
                return _twice_on_devices(pack, candidate, snapshot, test, inputs, sims,
                                         "ran on test data: %s after %d step(s)" % (status, n))
            test2 = json.loads(json.dumps(test))
            for slug in again:
                c = test2.setdefault("connections", {}).setdefault(slug, {})
                c["responses"] = [dict(ALREADY_EXISTS)] + list(c.get("responses") or [])
            req2 = runtime.build_request("app", candidate, snapshot, test2, {"inputs": inputs})
            try:
                res2 = runtime.call(pack, req2, timeout=runtime.RUN_TIMEOUT)
            except runtime.RuntimeUnavailable as exc:
                return [], "ran once; the second run was not: " + exc.message
            if res2.get("status") in ("done", "waiting"):
                return [], ("ran on test data: %s after %d step(s), and again with every entry "
                            "already on the device" % (status, n))
            bad2 = next((x for x in reversed(res2.get("steps") or [])
                         if x.get("outcome") == "error"), None)
            return [{"where": "app.graph", "rule": "builder.second_run", "message": (
                "it runs on a schedule, so it runs again tomorrow with the same devices -- and "
                "that second run fails%s: %s. On a Fortinet device a POST of an entry that "
                "already exists answers HTTP 500 (FortiOS: entry already exists). Read before "
                "writing: GET <path>/{<key>} answers 404 (the step's error arrow, "
                "last_response.status) when the entry is missing -- create only then; the same "
                "for a list: GET <path>/{<key>}/<list>/{<item>} before POSTing the item."
                % (" at step %s" % bad2.get("key") if bad2 else "",
                   str(res2.get("error") or (bad2 or {}).get("detail") or "")[:300]))}], ""
        steps = res.get("steps") or []
        bad = next((s for s in reversed(steps) if s.get("outcome") == "error"), None)
        trail = " -> ".join("%s(%s)" % (s.get("key"), s.get("outcome")) for s in steps[-8:])
        msg = ("running it on test data with the inputs %s fails%s: %s. Steps: %s. Every "
               "variable a template reads is set by an earlier step (a `set` step's \"name\", "
               "another step's \"save_as\") or is a form field (`form.<field>`)."
               % (json.dumps(inputs, ensure_ascii=False),
                  " at step %s" % bad.get("key") if bad else "",
                  str(res.get("error") or (bad or {}).get("detail") or status)[:600], trail))
        return [{"where": "app.graph", "rule": "builder.test_run", "message": msg}], ""

    return check
