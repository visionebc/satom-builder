/* What the pickers offer: the objects this package creates PLUS the ones the
 * target SATOM already has. Pure.
 *
 * The target's lists come from the schema (the select params of the step
 * kinds were computed by SATOM with its own choices) and, when the snapshot
 * was fetched, from `existing` (which also carries store columns). */

function paramChoices(schema, kindKey, paramName) {
  const k = ((schema && schema.step_kinds) || []).find((x) => x.key === kindKey);
  const p = k && (k.params || []).find((x) => x.name === paramName);
  return (p && p.choices) || [];
}

function uniq(list) {
  return Array.from(new Set(list.filter((x) => x !== undefined && x !== null && x !== "")));
}

export function computeChoices(schema, app, snapshot) {
  app = app || {};
  const ex = (snapshot && snapshot.existing) || {};
  const pkgStores = (app.stores || []).map((s) => s.slug);
  const pkgHttp = (app.connections || []).filter((c) => (c.kind || "http") === "http").map((c) => c.slug);
  const pkgScripts = (app.scripts || []).map((s) => s.slug);
  const serverStores = uniq(paramChoices(schema, "store_query", "store").concat(Object.keys(ex.stores || {})));
  const serverHttp = uniq(paramChoices(schema, "api_call", "connection").concat(
    Object.entries(ex.connections || {}).filter(([, c]) => c.kind === "http").map(([k]) => k)));
  const serverPg = uniq(paramChoices(schema, "sql_query", "connection").concat(
    Object.entries(ex.connections || {}).filter(([, c]) => c.kind === "postgres").map(([k]) => k)));
  const serverScripts = uniq(paramChoices(schema, "script", "script").concat(
    Object.entries(ex.scripts || {}).filter(([, s]) => s.published).map(([k]) => k)));
  const serverApps = uniq(((schema && schema.action_kinds) || []).filter((a) => a.key === "run_app")
    .flatMap((a) => (a.params.find((p) => p.name === "app") || {}).choices || []));
  return {
    stores: uniq(pkgStores.concat(serverStores)),
    http: uniq(pkgHttp.concat(serverHttp)),
    pg: serverPg,
    scripts: uniq(pkgScripts.concat(serverScripts)),
    apps: serverApps,
    pkg: { stores: pkgStores, http: pkgHttp, scripts: pkgScripts },
    server: { stores: serverStores, http: serverHttp, scripts: serverScripts },
    columns(store) {
      const s = (app.stores || []).find((x) => x.slug === store);
      if (s) return (s.columns || []).map((c) => c.name);
      const e = (ex.stores || {})[store];
      return e ? (e.columns || []).map((c) => c.name) : null;
    },
    /* Endpoints a connection may call: the API Catalog version it is pinned
     * to, in the package or on the target, or a build of SATOM's API library
     * (contract section 19: SATOM's list, loaded by the editor). null = not
     * pinned. */
    catalog(conn) {
      const c = (app.connections || []).find((x) => x.slug === conn);
      if (c) {
        if (!c.api) return null;
        if (LIBRARY_PRODUCTS[c.api]) {
          const key = `${c.api}@${c.api_version || ""}`;
          return { api: c.api, version: c.api_version, ops: libraryOps.get(key) || [], library: true, loading: !libraryOps.has(key) };
        }
        const api = (app.apis || []).find((a) => a.slug === c.api && String(a.version) === String(c.api_version));
        if (api) return { api: api.slug, version: api.version, ops: api.operations || [] };
        const srv = serverCatalog(schema)[conn];
        return srv || { api: c.api, version: c.api_version, ops: [] };
      }
      return serverCatalog(schema)[conn] || null;
    },
  };
}

/* SATOM's API library (contract section 19): the products a connection pins
 * to a firmware build, and the endpoint lists the editor loaded for them. */
export const LIBRARY_PRODUCTS = { fortigate: "FortiGate", fortiweb: "FortiWeb", fortiadc: "FortiADC", fortiauthenticator: "FortiAuthenticator" };
const libraryOps = new Map();
export function setLibraryOps(key, ops) { libraryOps.set(key, Array.isArray(ops) ? ops : []); }
export function hasLibraryOps(key) { return libraryOps.has(key); }
/* [{product, version}] of the draft's connections pinned to the library. */
export function libraryPins(app) {
  return ((app && app.connections) || []).filter((c) => c && LIBRARY_PRODUCTS[c.api] && c.api_version)
    .map((c) => ({ product: c.api, version: String(c.api_version) }));
}

function serverCatalog(schema) {
  const k = ((schema && schema.step_kinds) || []).find((x) => x.key === "api_call");
  return (k && k.catalog) || {};
}

/* The choices a select param of a step kind or an action kind offers, the
 * package's own objects included. `owner` is the kind key. A package never
 * carries postgres connections, so sql_query keeps the target's list. */
export function selectChoices(param, choices, owner) {
  const base = param.choices || [];
  if (param.name === "store") return uniq(choices.stores.concat(base));
  if (param.name === "connection") return owner === "sql_query" ? base : uniq(choices.http.concat(base));
  if (param.name === "script") return uniq(choices.scripts.concat(base));
  return base;
}
