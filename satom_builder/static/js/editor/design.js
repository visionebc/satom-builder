/* The app design (app.json `app.design`): {theme, page: [blocks], result: [blocks]}.
 * A block is {id, type, props, style, children? | columns:[{w, children}] | tabs:[{label, children}]}
 * (SATOM services/studio_design.py; the vocabulary comes from schema.design).
 * Pure: tree operations and the colour maths. Blocks are edited in place;
 * keys the editor does not know stay where they are. */
import { uniqueName } from "./util.js";

export const SURFACES = ["page", "result"];

export function ensureDesign(d) {
  if (!d || typeof d !== "object" || Array.isArray(d)) d = {};
  if (!d.theme || typeof d.theme !== "object") d.theme = {};
  SURFACES.forEach((s) => { if (!Array.isArray(d[s])) d[s] = []; });
  return d;
}

/* Every child list of a block: [{label, owner, items, list}]. `items` reads
 * (a detached [] when the key is missing); `list` is the stored array,
 * created on first use (adding a block). Reading never adds a key. */
export function childLists(b) {
  const out = [];
  const lazy = (owner, key, label) => ({
    label, owner,
    items: Array.isArray(owner[key]) ? owner[key] : [],
    get list() { if (!Array.isArray(owner[key])) owner[key] = []; return owner[key]; },
  });
  if (Array.isArray(b.children)) out.push(lazy(b, "children", "Inside"));
  (Array.isArray(b.columns) ? b.columns : []).forEach((c, i) => { if (c && typeof c === "object") out.push(lazy(c, "children", `Column ${i + 1} (width ${c.w || "?"})`)); });
  (Array.isArray(b.tabs) ? b.tabs : []).forEach((t, i) => { if (t && typeof t === "object") out.push(lazy(t, "children", `Tab "${t.label || "Tab " + (i + 1)}"`)); });
  return out;
}

/* The design as read-only lists (never adds keys). */
export function designView(d) {
  d = d || {};
  return { theme: d.theme || {}, page: Array.isArray(d.page) ? d.page : [], result: Array.isArray(d.result) ? d.result : [] };
}

export function* walk(blocks, depth = 1) {
  for (const b of blocks || []) {
    if (!b || typeof b !== "object") continue;
    yield { block: b, depth };
    for (const c of childLists(b)) yield* walk(c.items, depth + 1);
  }
}

export function allIds(design) {
  const s = new Set();
  SURFACES.forEach((surf) => { for (const { block } of walk(designView(design)[surf])) if (block.id) s.add(block.id); });
  return s;
}

export function countBlocks(design) {
  let n = 0;
  const v = designView(design);
  SURFACES.forEach((surf) => { for (const _ of walk(v[surf])) n++; }); // eslint-disable-line no-unused-vars
  return n;
}

/* {list, index, block, surface, depth} of the block with this id, or null. */
export function locate(design, id) {
  const find = (list, surface, depth) => {
    for (let i = 0; i < list.length; i++) {
      const b = list[i];
      if (b && b.id === id) return { list, index: i, block: b, surface, depth };
      for (const c of childLists(b || {})) {
        if (!c.items.length) continue;
        const r = find(c.items, surface, depth + 1);
        if (r) return r;
      }
    }
    return null;
  };
  const v = designView(design);
  for (const s of SURFACES) {
    const r = find(v[s], s, 1);
    if (r) return r;
  }
  return null;
}

function newId(seen) {
  let id;
  do { id = Math.random().toString(16).slice(2, 10).padEnd(8, "0"); } while (seen.has(id));
  seen.add(id);
  return id;
}

export function newBlock(type, spec, seen) {
  const b = { id: newId(seen || new Set()), type, props: {}, style: {} };
  ((spec && spec.props) || []).forEach((p) => {
    if (p.default !== undefined && p.default !== "") b.props[p.name] = p.kind === "int" ? Number(p.default) : p.default;
    else if (p.kind === "bool") b.props[p.name] = false;
  });
  const c = spec && spec.container;
  if (c === "children") b.children = [];
  else if (c === "columns") b.columns = [{ w: 6, children: [] }, { w: 6, children: [] }];
  else if (c === "tabs") b.tabs = [{ label: "Tab 1", children: [] }];
  return b;
}

export function addBlock(design, list, type, dschema) {
  const spec = (dschema && dschema.blocks && dschema.blocks[type]) || null;
  const max = (dschema && dschema.limits && dschema.limits.blocks) || 300;
  if (countBlocks(design) >= max) return { ok: false, error: `at most ${max} blocks` };
  const b = newBlock(type, spec, allIds(design));
  list.push(b);
  return { ok: true, block: b };
}

export function removeBlock(design, id) {
  const at = locate(design, id);
  if (!at) return false;
  at.list.splice(at.index, 1);
  return true;
}

export function moveBlock(design, id, delta) {
  const at = locate(design, id);
  if (!at) return false;
  const j = at.index + delta;
  if (j < 0 || j >= at.list.length) return false;
  const t = at.list[at.index];
  at.list[at.index] = at.list[j];
  at.list[j] = t;
  return true;
}

export function addColumn(block, maxCols) {
  if (!Array.isArray(block.columns)) block.columns = [];
  if (block.columns.length >= (maxCols || 4)) return false;
  block.columns.push({ w: Math.max(1, Math.floor(12 / (block.columns.length + 1))), children: [] });
  return true;
}

export function addTab(block, maxTabs) {
  if (!Array.isArray(block.tabs)) block.tabs = [];
  if (block.tabs.length >= (maxTabs || 8)) return false;
  block.tabs.push({ label: uniqueName("Tab " + (block.tabs.length + 1), (l) => block.tabs.some((t) => t.label === l)), children: [] });
  return true;
}

/* Blocks of the palette allowed on a surface, grouped: [[group, [[type, spec]]]] */
export function palette(dschema, surface) {
  const groups = new Map();
  Object.entries((dschema && dschema.blocks) || {}).forEach(([type, spec]) => {
    if (!(spec.surfaces || SURFACES).includes(surface)) return;
    if (!groups.has(spec.group)) groups.set(spec.group, []);
    groups.get(spec.group).push([type, spec]);
  });
  return Array.from(groups.entries());
}

/* ── colours (studio_design.hex6 / luminance / contrast) ── */
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export function hex6(v) {
  let s = String(v || "").trim();
  if (!HEX.test(s)) return "";
  if (s.length === 4) s = "#" + s.slice(1).split("").map((c) => c + c).join("");
  return s.toUpperCase();
}

export function luminance(h) {
  const ch = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  const r = parseInt(h.slice(1, 3), 16), g = parseInt(h.slice(3, 5), 16), b = parseInt(h.slice(5, 7), 16);
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

export function contrast(a, b) {
  const x = hex6(a), y = hex6(b);
  if (!x || !y) return null;
  const la = luminance(x), lb = luminance(y);
  return Math.round(((Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)) * 100) / 100;
}

/* SATOM's defaults for the colours a theme may leave out (studio_design.BASE). */
export const BASE = { page_bg: "#F3F6FC", surface: "#FFFFFF", text: "#0C1B33", accent: "#0A3F9F" };

/* Contrast hints of a theme, the same pairs studio_design.warnings() checks. */
export function themeHints(theme, minContrast) {
  const min = minContrast || 4.5;
  const t = theme || {};
  const pageBg = hex6(t.page_bg) || BASE.page_bg, surface = hex6(t.surface) || BASE.surface, text = hex6(t.text) || BASE.text;
  const out = [];
  if (hex6(t.accent)) {
    const c = contrast(t.accent, "#FFFFFF");
    if (c < 3) out.push(`white button text on the accent ${hex6(t.accent)} has contrast ${c.toFixed(1)}:1 (3:1 at least)`);
  }
  const c1 = contrast(text, pageBg), c2 = contrast(text, surface);
  if (c1 < min) out.push(`text ${text} on the page background ${pageBg} has contrast ${c1.toFixed(1)}:1 (${min}:1 recommended)`);
  if (c2 < min) out.push(`text ${text} on the card surface ${surface} has contrast ${c2.toFixed(1)}:1 (${min}:1 recommended)`);
  return out;
}

/* Field names and Output step keys a design may name (field / output props). */
export function outputKeys(graph) {
  return ((graph && graph.nodes) || []).filter((n) => n && n.kind === "output").map((n) => n.key);
}
