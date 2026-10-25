/* The project being edited (contract section 11, GET /api/projects/<id>).
 * Pure: the save payload, dirty state and the local draft store, with the
 * storage passed in (window.localStorage in the page, a Map in tests).
 *
 * DATA FIDELITY. The editor keeps the object the server sent and edits it
 * in place. It never rebuilds `app` (the app.json body) or any object in it,
 * and it never adds an empty container just by showing a tab: a key is
 * created the first time the author adds something to it (`ensure*`). So
 * the payload of an untouched project equals what was loaded, unknown keys
 * included, at every depth. */
import { clone, deepEqual } from "./util.js";

/* Keys the server owns: never sent back. */
export const READ_ONLY = ["id", "kind", "assets", "updated_at", "last_check", "local_check", "where"];

export function savePayload(project) {
  const out = {};
  Object.keys(project || {}).forEach((k) => { if (!READ_ONLY.includes(k)) out[k] = clone(project[k]); });
  return out;
}

export function isDirty(project, saved) {
  return !deepEqual(savePayload(project), savePayload(saved));
}

/* ensure(obj, key, factory): obj[key], created with factory() when absent. */
export function ensure(obj, key, factory) {
  if (obj[key] === undefined || obj[key] === null) obj[key] = factory();
  return obj[key];
}

export function ensureApp(project) {
  const body = ensure(project, "app", () => ({}));
  ensure(body, "app", () => ({}));
  return body;
}

/* Read helpers: the existing value, or a detached default that is NOT
 * stored (editing it means calling the matching ensure first). */
export function appBody(project) { return (project && project.app) || {}; }
export function appSpec(project) { return appBody(project).app || {}; }
export function list(obj, key) { return (obj && Array.isArray(obj[key])) ? obj[key] : []; }

/* ── local drafts ─────────────────────────────────────────────────────── */
export const DRAFT_PREFIX = "satom-builder:draft:";

export function draftKey(pid, updatedAt) {
  return `${DRAFT_PREFIX}${pid}:${updatedAt || "new"}`;
}

export function writeDraft(storage, project, now) {
  try {
    storage.setItem(draftKey(project.id, project.updated_at), JSON.stringify({
      saved_at: now || new Date().toISOString(), base_updated_at: project.updated_at || "", payload: savePayload(project),
    }));
    return true;
  } catch (e) {
    return false; // quota or private mode: the server copy is still the truth
  }
}

function storageKeys(storage) {
  if (typeof storage.keys === "function") return Array.from(storage.keys());
  const out = [];
  for (let i = 0; i < storage.length; i++) out.push(storage.key(i));
  return out;
}

export function findDrafts(storage, pid) {
  const pre = `${DRAFT_PREFIX}${pid}:`;
  const out = [];
  storageKeys(storage).forEach((k) => {
    if (!k || !k.startsWith(pre)) return;
    try {
      const d = JSON.parse(storage.getItem(k));
      if (d && d.payload) out.push(Object.assign({ key: k }, d));
    } catch (e) { /* a broken entry is ignored */ }
  });
  return out.sort((a, b) => (a.saved_at < b.saved_at ? 1 : -1));
}

export function clearDrafts(storage, pid) {
  findDrafts(storage, pid).forEach((d) => storage.removeItem(d.key));
}

/* The draft worth offering: newer than the server copy and different from it. */
export function restorable(storage, project) {
  const server = savePayload(project);
  const t = Date.parse(project.updated_at || "") || 0;
  return findDrafts(storage, project.id).find((d) => (Date.parse(d.saved_at) || 0) > t && !deepEqual(d.payload, server)) || null;
}

/* Apply a draft payload onto the project (server-owned keys stay). */
export function applyPayload(project, payload) {
  Object.keys(project).forEach((k) => { if (!READ_ONLY.includes(k)) delete project[k]; });
  Object.keys(payload || {}).forEach((k) => { if (!READ_ONLY.includes(k)) project[k] = clone(payload[k]); });
  return project;
}

/* Raw JSON tab: the text is the whole project; only editable keys apply. */
export function parseRaw(text) {
  let obj;
  try { obj = JSON.parse(text); } catch (e) { return { ok: false, error: "not valid JSON: " + e.message }; }
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return { ok: false, error: "the project is a JSON object" };
  if (obj.app !== undefined && (typeof obj.app !== "object" || obj.app === null || Array.isArray(obj.app))) {
    return { ok: false, error: "\"app\" is the app.json body, an object" };
  }
  return { ok: true, value: obj };
}
