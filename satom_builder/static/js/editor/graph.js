/* The diagram model: nodes {key, kind, label, params, x, y, ...} and arrows
 * {src, dst, branch, ...}, exactly the shape of app.json `app.graph`
 * (SATOM services/studio_flow.py). Pure: no DOM, testable under node.
 *
 * Every operation mutates the graph IN PLACE and never rebuilds a node or an
 * arrow, so keys this editor does not know survive a round trip.
 *
 * The checks here are hints for the author while drawing. The verdict is the
 * rules pack's (studio_flow.validate_graph, run by POST check): this module
 * only mirrors the structural rules a drawing tool must know to offer the
 * right arrows. */
import { pattern, uniqueName } from "./util.js";

/* SATOM studio_checks.YES_NO_KINDS (commit 0b7a6193): steps that answer yes
 * or no. A schema may override it with `yes_no_kinds`. */
export const YES_NO_KINDS = ["ask", "http_check", "tcp_check", "console_read", "appliance_fact"];
export const NODE_W = 190;
export const NODE_H = 58;

export function ensureGraph(g) {
  if (!g || typeof g !== "object" || Array.isArray(g)) g = {};
  if (!Array.isArray(g.nodes)) g.nodes = [];
  if (!Array.isArray(g.edges)) g.edges = [];
  return g;
}

export function nodeByKey(g, key) {
  return (g.nodes || []).find((n) => n && n.key === key) || null;
}

export function kindMap(kinds) {
  const m = {};
  (kinds || []).forEach((k) => { m[k.key] = k; });
  return m;
}

function yesNoKinds(schema) {
  return (schema && Array.isArray(schema.yes_no_kinds)) ? schema.yes_no_kinds : YES_NO_KINDS;
}

/* The arrows a step of this kind may start, in the order a new arrow takes them. */
export function allowedBranches(kindKey, schema) {
  if (kindKey === "end" || kindKey === "fail") return [];
  if (kindKey === "decision") return ["yes", "no", "always"];
  if (yesNoKinds(schema).includes(kindKey)) return ["yes", "no", "error", "always"];
  return ["always", "ok", "error"];
}

export function branchesUsed(g, src) {
  return g.edges.filter((e) => e && e.src === src).map((e) => e.branch || "always");
}

export function addNode(g, kindKey, kindDef, at) {
  ensureGraph(g);
  const base = String(kindKey || "step").replace(/_/g, "-");
  const key = uniqueName(base, (k) => !!nodeByKey(g, k));
  const node = { key, kind: kindKey, label: (kindDef && kindDef.label) || kindKey, params: {} };
  ((kindDef && kindDef.params) || []).forEach((p) => {
    if (p.default !== undefined && p.default !== null && p.default !== "") node.params[p.name] = p.default;
  });
  node.x = Math.round((at && at.x) || 60);
  node.y = Math.round((at && at.y) || 60 + g.nodes.length * 26);
  g.nodes.push(node);
  return node;
}

/* {ok, edge} or {ok:false, error}. Without a branch the first free one the
 * source kind allows is taken (always for most steps, yes then no for a
 * decision or a check). */
export function connect(g, src, dst, branch, schema) {
  ensureGraph(g);
  const a = nodeByKey(g, src), b = nodeByKey(g, dst);
  if (!a || !b) return { ok: false, error: "both steps must exist" };
  if (src === dst) return { ok: false, error: "an arrow cannot point back at its own step" };
  const allowed = allowedBranches(a.kind, schema);
  if (!allowed.length) return { ok: false, error: `${src} ends the run: it cannot have arrows` };
  const used = branchesUsed(g, src);
  if (branch === undefined || branch === null || branch === "") {
    branch = allowed.find((x) => !used.includes(x));
    if (!branch) return { ok: false, error: `${src} already has every arrow it can have` };
  }
  if (used.includes(branch)) return { ok: false, error: `${src} already has a ${branch} arrow` };
  const edge = { src, dst, branch };
  g.edges.push(edge);
  return { ok: true, edge };
}

export function setBranch(g, index, branch) {
  const e = g.edges[index];
  if (!e) return { ok: false, error: "no such arrow" };
  const clash = g.edges.some((o, i) => i !== index && o.src === e.src && (o.branch || "always") === branch);
  if (clash) return { ok: false, error: `${e.src} already has a ${branch} arrow` };
  e.branch = branch;
  return { ok: true };
}

export function deleteNode(g, key) {
  const before = g.nodes.length;
  for (let i = g.nodes.length - 1; i >= 0; i--) if (g.nodes[i] && g.nodes[i].key === key) g.nodes.splice(i, 1);
  for (let i = g.edges.length - 1; i >= 0; i--) {
    const e = g.edges[i];
    if (e && (e.src === key || e.dst === key)) g.edges.splice(i, 1);
  }
  return g.nodes.length !== before;
}

export function deleteEdge(g, index) {
  if (index < 0 || index >= g.edges.length) return false;
  g.edges.splice(index, 1);
  return true;
}

/* Rename a step: arrows follow it. {ok} or {ok:false, error}. */
export function renameNode(g, oldKey, newKey, schema) {
  const n = nodeByKey(g, oldKey);
  const clean = String(newKey || "").trim().toLowerCase();
  if (!n) return { ok: false, error: "no such step" };
  if (clean === oldKey) return { ok: true };
  if (!pattern(schema, "step_key").test(clean)) return { ok: false, error: "lowercase letters, digits, - and _ (up to 64)" };
  if (nodeByKey(g, clean)) return { ok: false, error: `${clean} is already a step` };
  g.edges.forEach((e) => {
    if (e.src === oldKey) e.src = clean;
    if (e.dst === oldKey) e.dst = clean;
  });
  n.key = clean;
  return { ok: true };
}

/* Structural hints, {where, message}, in the `where` grammar of check(). */
export function validate(g, kinds, schema) {
  const out = [];
  const km = kindMap(kinds);
  const add = (where, message) => out.push({ where, message });
  g = ensureGraph(g);
  const keys = new Set();
  const keyRe = pattern(schema, "step_key");
  g.nodes.forEach((n) => {
    const k = String((n && n.key) || "");
    const where = `app.graph.nodes[${k}]`;
    if (!keyRe.test(k)) add(where, `step key ${JSON.stringify(k)}: lowercase letters, digits, - and _`);
    if (keys.has(k)) add(where, `step key ${k} is used twice`);
    keys.add(k);
    const kind = km[n.kind];
    if (!kind) { if (kinds && kinds.length) add(where, `${k}: unknown step kind ${JSON.stringify(n.kind)}`); return; }
    const p = n.params || {};
    (kind.params || []).forEach((prm) => {
      const v = p[prm.name];
      if (prm.required && (v === undefined || v === null || String(v).trim() === "")) add(where, `${k}: ${prm.label} is required`);
    });
  });
  const starts = g.nodes.filter((n) => n && n.kind === "start").length;
  if (starts !== 1) add("app.graph", `a diagram needs exactly one Start step (it has ${starts})`);
  const seen = new Set();
  g.edges.forEach((e, i) => {
    const where = `app.graph.edges[${i}]`;
    const b = e.branch || "always";
    if (!keys.has(e.src) || !keys.has(e.dst)) { add(where, `an arrow points at a step that does not exist (${e.src} → ${e.dst})`); return; }
    const pair = e.src + "\u0000" + b;
    if (seen.has(pair)) add(`app.graph.nodes[${e.src}]`, `${e.src} has two ${b} arrows`);
    seen.add(pair);
    const src = nodeByKey(g, e.src);
    const allowed = allowedBranches(src.kind, schema);
    if (!allowed.length) add(`app.graph.nodes[${e.src}]`, `${e.src} ends the run: it cannot have arrows`);
    else if (!allowed.includes(b)) add(where, `${e.src} → ${e.dst}: a ${src.kind} step has no ${b} arrow (${allowed.join(", ")})`);
  });
  return out;
}

/* Layered layout from Start, top to bottom: each step one row below the
 * deepest step that leads to it along a forward arrow; steps no arrow
 * reaches go in a last row. Positions only; nothing else changes. */
export function autoLayout(g, opts) {
  const o = Object.assign({ dx: NODE_W + 40, dy: NODE_H + 62, x0: 80, y0: 40 }, opts || {});
  ensureGraph(g);
  const keys = g.nodes.map((n) => n.key);
  const out = {};
  keys.forEach((k) => { out[k] = []; });
  g.edges.forEach((e) => { if (out[e.src] && out[e.dst] !== undefined) out[e.src].push(e.dst); });
  const start = (g.nodes.find((n) => n.kind === "start") || g.nodes[0] || {}).key;
  const layer = {};
  if (start !== undefined) {
    // DFS order marks back arrows (loops) so layering terminates.
    const state = {}, order = [], back = new Set();
    const visit = (k) => {
      state[k] = 1;
      out[k].forEach((d) => {
        if (state[d] === 1) back.add(k + "\u0000" + d);
        else if (!state[d]) visit(d);
      });
      state[k] = 2;
      order.push(k);
    };
    visit(start);
    order.reverse().forEach((k) => {
      if (layer[k] === undefined) layer[k] = 0;
      out[k].forEach((d) => {
        if (back.has(k + "\u0000" + d)) return;
        layer[d] = Math.max(layer[d] === undefined ? 0 : layer[d], layer[k] + 1);
      });
    });
  }
  const maxLayer = Math.max(-1, ...Object.values(layer));
  const rows = {};
  g.nodes.forEach((n) => {
    const l = layer[n.key] !== undefined ? layer[n.key] : maxLayer + 1;
    (rows[l] = rows[l] || []).push(n);
  });
  Object.keys(rows).forEach((l) => {
    rows[l].forEach((n, i) => {
      n.x = o.x0 + i * o.dx;
      n.y = o.y0 + Number(l) * o.dy;
    });
  });
  return g;
}

export function needsLayout(g) {
  return (g.nodes || []).some((n) => typeof n.x !== "number" || typeof n.y !== "number");
}
