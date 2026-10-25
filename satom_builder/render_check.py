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


def make(pack, snapshot: dict, defs: dict):
    """-> ``check(candidate) -> (problems, note)`` for views and widgets;
    ``([], why)`` when it cannot render."""
    cols = dataset_columns(pack, defs)

    def check(candidate: dict):
        if candidate.get("kind") not in ("view", "widget"):
            return [], ""
        used = testdata.used_datasets(candidate)
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
