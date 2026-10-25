/* Small pure helpers shared by the editor modules. No DOM, no globals:
 * every module that imports this one can run under `node --test`. */

export function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

export function deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!deepEqual(a[i], b[i])) return false;
    return true;
  }
  if (typeof a === "object") {
    if (Array.isArray(b)) return false;
    const ka = Object.keys(a), kb = Object.keys(b);
    if (ka.length !== kb.length) return false;
    for (const k of ka) if (!Object.prototype.hasOwnProperty.call(b, k) || !deepEqual(a[k], b[k])) return false;
    return true;
  }
  return false;
}

/* `base` made unique against `taken` (a Set or a predicate): base, base-2, base-3... */
export function uniqueName(base, taken, sep = "-") {
  const has = typeof taken === "function" ? taken : (k) => taken.has(k);
  if (!has(base)) return base;
  let n = 2;
  while (has(base + sep + n)) n += 1;
  return base + sep + n;
}

export function moveItem(list, index, delta) {
  const j = index + delta;
  if (index < 0 || index >= list.length || j < 0 || j >= list.length) return false;
  const t = list[index];
  list[index] = list[j];
  list[j] = t;
  return true;
}

/* Patterns copied from the target schema when it carries them (schema.patterns);
 * these are the defaults, taken from SATOM 4.0.1 (commit 0b7a6193). They only
 * drive inline hints: the rules pack is what decides. */
export const DEFAULT_PATTERNS = {
  slug: "^[a-z0-9][a-z0-9-]{0,79}$",
  version: "^\\d{1,4}\\.\\d{1,4}\\.\\d{1,4}$",
  icon: "^bi-[a-z0-9-]{1,28}$",
  field: "^[a-z_][a-z0-9_]{0,63}$",
  step_key: "^[a-z0-9][a-z0-9_-]{0,63}$",
  variable: "^[a-z_][a-z0-9_]{0,63}$",
  action_key: "^[a-z0-9][a-z0-9_-]{0,47}$",
  store_slug: "^[a-z0-9][a-z0-9_-]{1,47}$",
  column: "^[a-z_][a-z0-9_]{0,63}$",
  connection_slug: "^[a-z0-9][a-z0-9_-]{1,47}$",
  operation_id: "^[A-Za-z0-9_.:-]{1,128}$",
};

export function pattern(schema, name) {
  const src = (schema && schema.patterns && schema.patterns[name]) || DEFAULT_PATTERNS[name];
  return new RegExp(src);
}

/* A JSON param value as the text box shows it. A string stays as typed; a
 * structured value (an AI draft may hold one) is shown pretty-printed. */
export function jsonText(v) {
  if (v === undefined || v === null) return "";
  if (typeof v === "string") return v;
  return JSON.stringify(v, null, 2);
}

export function jsonProblem(text) {
  if (typeof text !== "string" || !text.trim()) return "";
  try { JSON.parse(text); return ""; } catch (e) { return String(e.message || e); }
}
