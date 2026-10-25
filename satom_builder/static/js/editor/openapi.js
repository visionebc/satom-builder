/* OpenAPI 3 / Swagger 2 (JSON) -> API Catalog operations, client side.
 *
 * A line-by-line port of SATOM services/studio_api_spec.py (parse_openapi,
 * clean_operation, clean_operations; commit 0b7a6193) so the operations land
 * in exactly the shape SATOM stores:
 *   {id, method, path, summary, params:[{name,in,required,type}], body, tags, deprecated}
 * The Builder backend has no OpenAPI parser; whatever this produces is
 * re-checked by the rules pack on Check. YAML is not parsed here (no
 * dependency, no build step): paste the JSON form of the document. */

export const METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"];
export const PARAM_IN = ["path", "query", "header", "body"];
export const MAX_OPERATIONS = 3000;
export const MAX_SPEC_BYTES = 8 * 1024 * 1024;

const ID_RE = /^[A-Za-z0-9_.:-]{1,128}$/;
const CTRL = /[\x00-\x1f\x7f]/;
const CTRL_G = /[\x00-\x1f\x7f]/g;
const PLACEHOLDER = /\{([A-Za-z0-9_.-]{1,64})\}/g;

export class SpecError extends Error {}

/* Python truthiness, `a or b`, and iterating whatever `or []` produced: the
 * Python code treats an empty dict or list as false, JS does not. */
export function pyBool(v) {
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === "object") return Object.keys(v).length > 0;
  return !!v;
}
const or = (a, b) => (pyBool(a) ? a : b);
function iter(v) {
  if (Array.isArray(v)) return v;
  if (typeof v === "string") return Array.from(v);
  if (v && typeof v === "object") return Object.keys(v);
  return [];
}
const isDict = (v) => !!v && typeof v === "object" && !Array.isArray(v);

/* Python's repr() of a str, so messages read exactly like SATOM's (%r). */
export function repr(s) {
  s = String(s);
  const q = s.includes("'") && !s.includes('"') ? '"' : "'";
  return q + s.replace(/\\/g, "\\\\").replace(new RegExp(q, "g"), "\\" + q).replace(/\n/g, "\\n").replace(/\t/g, "\\t") + q;
}

function placeholders(path) {
  return Array.from(path.matchAll(PLACEHOLDER), (m) => m[1]);
}

export function cleanPath(path) {
  path = String(path || "").trim();
  if (!path.startsWith("/")) path = "/" + path;
  if (CTRL.test(path) || path.length > 500) throw new SpecError(`path ${repr(path.slice(0, 80))} has control characters or is too long`);
  if (path.includes("://") || path.startsWith("//") || path.includes("\\") || path.includes("?") || path.includes("#")) {
    throw new SpecError(`path ${repr(path.slice(0, 80))} must be a plain path under the base URL`);
  }
  if (path.split("/").some((s) => s === ".." || s === ".")) throw new SpecError(`path ${repr(path.slice(0, 80))} cannot hold . or .. segments`);
  return path;
}

export function autoId(method, path) {
  const slug = path.replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "") || "root";
  return (method.toLowerCase() + "_" + slug).slice(0, 128);
}

export function cleanOperation(raw) {
  if (!isDict(raw)) throw new SpecError("an endpoint is an object");
  const method = String(or(raw.method, "GET")).trim().toUpperCase();
  if (!METHODS.includes(method)) throw new SpecError(`method ${repr(method)}: one of ${METHODS.join(", ")}`);
  const path = cleanPath(or(raw.path, ""));
  const oid = String(or(raw.id, "")).trim() || autoId(method, path);
  if (!ID_RE.test(oid)) throw new SpecError(`endpoint id ${repr(oid.slice(0, 80))}: letters, digits and _ . : -, up to 128`);
  const params = [], seen = new Set();
  const ph = new Set(placeholders(path));
  iter(or(raw.params, [])).forEach((p) => {
    if (!isDict(p) || !String(or(p.name, "")).trim()) return;
    const name = String(p.name).trim().slice(0, 64);
    const where = String(or(p.in, "query"));
    const key = name + "\u0000" + where;
    if (!PARAM_IN.includes(where) || seen.has(key)) return;
    if (where === "path" && !ph.has(name)) return;
    seen.add(key);
    params.push({ name, in: where, required: pyBool(p.required) || where === "path", type: String(or(p.type, "string")).slice(0, 32) });
  });
  placeholders(path).forEach((name) => {
    const key = name + "\u0000path";
    if (!seen.has(key)) {
      params.push({ name, in: "path", required: true, type: "string" });
      seen.add(key);
    }
  });
  return {
    id: oid, method, path,
    summary: String(or(raw.summary, "")).replace(CTRL_G, " ").trim().slice(0, 300),
    params, body: pyBool(raw.body),
    tags: iter(or(raw.tags, [])).filter((t) => String(t).trim()).map((t) => String(t).slice(0, 64)).slice(0, 10),
    deprecated: pyBool(raw.deprecated),
  };
}

export function cleanOperations(rawList) {
  if (!Array.isArray(rawList)) throw new SpecError("the endpoints are a list");
  if (rawList.length > MAX_OPERATIONS) throw new SpecError(`more than ${MAX_OPERATIONS} endpoints in one version`);
  const out = [], ids = new Set(), pairs = new Set();
  rawList.forEach((raw, i) => {
    let op;
    try { op = cleanOperation(raw); } catch (e) { throw new SpecError(`endpoint ${i + 1}: ${e.message}`); }
    if (ids.has(op.id)) throw new SpecError(`endpoint id ${op.id} is used twice`);
    const pair = op.method + " " + op.path;
    if (pairs.has(pair)) throw new SpecError(`${op.method} ${op.path} is listed twice`);
    ids.add(op.id);
    pairs.add(pair);
    out.push(op);
  });
  // Python sorts by (path, METHODS.index): plain code-unit order on the path.
  return out.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : METHODS.indexOf(a.method) - METHODS.indexOf(b.method)));
}

function localRef(doc, obj) {
  if (!isDict(obj) || !("$ref" in obj)) return obj;
  const ref = String(obj.$ref);
  if (!ref.startsWith("#/")) return {};
  let cur = doc;
  for (let part of ref.slice(2).split("/")) {
    part = part.replace(/~1/g, "/").replace(/~0/g, "~");
    cur = isDict(cur) ? cur[part] : undefined;
    if (cur === undefined || cur === null) return {};
  }
  return isDict(cur) ? cur : {};
}

function param(doc, raw) {
  const p = localRef(doc, raw);
  if (!isDict(p) || !pyBool(p.name)) return null;
  const schema = localRef(doc, or(p.schema, {}));
  const typ = or(or(p.type, isDict(schema) ? schema.type : ""), "string");
  return { name: p.name, in: or(p.in, "query"), required: pyBool(p.required), type: String(typ) };
}

export function loadDocument(text) {
  if (new TextEncoder().encode(text).length > MAX_SPEC_BYTES) throw new SpecError(`the document is larger than ${MAX_SPEC_BYTES / 1024 / 1024} MiB`);
  text = text.replace(/^﻿/, "");
  let doc;
  try { doc = JSON.parse(text); } catch (e) {
    throw new SpecError("not JSON (" + String(e.message).slice(0, 120) + "). YAML is not read in the browser: paste the JSON form of the document.");
  }
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) throw new SpecError("the document is not an object");
  return doc;
}

/* {title, version, base_path, operations, warnings}; throws SpecError. */
export function parseOpenAPI(text) {
  const doc = loadDocument(text);
  const swagger = String(or(doc.swagger, ""));
  const openapi = String(or(doc.openapi, ""));
  if (!swagger.startsWith("2") && !openapi.startsWith("3")) throw new SpecError("not an OpenAPI 3 or Swagger 2 document (no openapi/swagger field)");
  const paths = doc.paths;
  if (!isDict(paths) || !pyBool(paths)) throw new SpecError("the document has no paths");
  const info = isDict(doc.info) ? doc.info : {};
  let basePath;
  if (swagger) basePath = String(or(doc.basePath, ""));
  else {
    const servers = iter(or(doc.servers, []));
    const url = servers.length && isDict(servers[0]) ? String(or(servers[0].url, "")) : "";
    const m = /^(?:[a-z]+:\/\/[^/]+)?(\/[^?#]*)?/.exec(url);
    basePath = m ? (m[1] || "") : "";
  }
  basePath = basePath.replace(/\/+$/, "");
  const warnings = [], rawOps = [];
  Object.entries(paths).forEach(([path, item0]) => {
    const item = localRef(doc, item0);
    if (!isDict(item)) return;
    const shared = iter(or(item.parameters, [])).map((r) => param(doc, r)).filter(Boolean);
    Object.entries(item).forEach(([mName, op]) => {
      const method = String(mName).toUpperCase();
      if (!METHODS.includes(method)) {
        if (["HEAD", "OPTIONS", "TRACE"].includes(method)) warnings.push(`${method} ${path} skipped: SATOM calls ${METHODS.join("/")} only`);
        return;
      }
      if (!isDict(op)) return;
      const params = new Map();
      shared.forEach((p) => params.set(p.name + "\u0000" + p.in, p));
      iter(or(op.parameters, [])).forEach((r) => {
        const p = param(doc, r);
        if (p) params.set(p.name + "\u0000" + p.in, p);
      });
      const body = pyBool(op.requestBody) || Array.from(params.values()).some((p) => p.in === "body" || p.in === "formData");
      rawOps.push({
        id: or(op.operationId, ""), method, path,
        summary: or(or(op.summary, op.description), ""),
        params: Array.from(params.values()).filter((p) => PARAM_IN.includes(p.in)),
        body, tags: or(op.tags, []), deprecated: pyBool(op.deprecated),
      });
    });
  });
  const ids = new Set();
  rawOps.forEach((op) => {
    const oid = String(or(op.id, ""));
    if (oid && (!ID_RE.test(oid) || ids.has(oid))) {
      warnings.push(`operationId ${repr(oid.slice(0, 60))} replaced by ${autoId(op.method, op.path)}`);
      op.id = "";
    }
    ids.add(or(op.id, autoId(op.method, op.path)));
  });
  return {
    title: String(or(info.title, "")).slice(0, 120),
    version: String(or(info.version, "")).slice(0, 32),
    base_path: basePath.slice(0, 200),
    operations: cleanOperations(rawOps),
    warnings: warnings.slice(0, 50),
  };
}
