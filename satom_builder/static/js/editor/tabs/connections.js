/* Connections tab: app.json `connections`, HTTP connections the package
 * creates [{slug, name, description, kind, base_url, auth, username,
 * header_name, token_url, scope, verify_tls, timeout, api, api_version}]
 * (studio_connections.save, the form rules only). NO SECRET is typed
 * here, ever: SATOM asks for it at import. */
import { h, clear, field, input, select, checkbox, button, badge, card, empty, flash } from "../ui.js";
import { ensure, appBody, list } from "../model.js";
import { uniqueName, pattern } from "../util.js";

const SECRET_KEYS = ["password", "secret", "token", "api_key", "client_secret"];
const AUTH_LABELS = { none: "None", basic: "Basic (user + password)", bearer: "Bearer token", api_key: "API key in a header", oauth2: "OAuth2 client credentials", session: "Session login (user + password)" };

export default {
  id: "connections", label: "Connections", icon: "bi-plug",

  render(ctx, panel) {
    clear(panel);
    const conns = list(appBody(ctx.project), "connections");
    const add = button("Add HTTP connection", { class: "btn btn-sm btn-fw-primary", id: "cn-add" }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const l = ensure(ctx.project.app = ctx.project.app || {}, "connections", () => []);
      const slug = uniqueName("conn-" + (l.length + 1), (x) => l.some((c) => c.slug === x));
      l.push({ slug, name: "New connection", description: "", kind: "http", base_url: "https://", auth: "none", username: "", header_name: "", token_url: "", scope: "", verify_tls: true, timeout: 20 });
      ctx.changed({ rerender: true, focus: `cn-${l.length - 1}-slug` });
    });
    const body = h("div", { class: "ed-list" });
    if (!conns.length) body.appendChild(empty("This package creates no connection."));
    conns.forEach((c, i) => body.appendChild(connCard(ctx, c, i, conns)));
    panel.appendChild(card("HTTP connections", [
      h("div", { class: "fw-alert fw-alert-info ed-secret-note", role: "note" },
        h("strong", { text: "No secrets here. " }),
        "The secret is typed in SATOM at import (the import dialog or SATOM's own wizard). The package and the Builder never hold it. An existing connection on the target is kept as it is."),
      body,
    ], { icon: "bi-plug", tools: add }));
  },

  reveal(ctx, t) {
    const i = list(appBody(ctx.project), "connections").findIndex((c) => c.slug === t.id);
    const el = document.getElementById(`cn-${i}`);
    if (el) { flash(el); document.getElementById(`cn-${i}-slug`).focus({ preventScroll: true }); }
  },
};

function connCard(ctx, c, i, conns) {
  const schema = ctx.schema;
  const pre = `cn-${i}`;
  const auths = (schema && schema.connection && schema.connection.auth) || Object.keys(AUTH_LABELS);
  const kinds = (schema && schema.connection && schema.connection.kinds) || ["http"];
  const kind = c.kind || "http";
  const issues = ctx.issuesFor("connections", c.slug).slice();
  if (!kinds.includes(kind)) issues.push(`a ${kind} connection cannot travel in an app package: remove it (the target's own ${kind} connections stay usable)`);
  const secrets = SECRET_KEYS.filter((k) => c[k] !== undefined && c[k] !== null && c[k] !== "");
  if (secrets.length) issues.push(`carries ${secrets.join(", ")}: SATOM refuses a package with a secret in it`);
  if (!pattern(schema, "connection_slug").test(String(c.slug || ""))) issues.push("slug: 2-48 lowercase letters, digits, - or _");
  if (conns.some((o, j) => j !== i && o.slug === c.slug)) issues.push("slug is used twice");
  const set = (k) => (v) => { c[k] = v; ctx.changed(); };
  const txt = (k, label, o) => {
    const el = input(c[k], Object.assign({ id: `${pre}-${k}` }, (o && o.attrs) || {}));
    el.addEventListener("input", () => set(k)(el.value));
    if (o && o.rerender) el.addEventListener("change", () => ctx.changed({ rerender: true }));
    return field(label, el, o);
  };
  const auth = c.auth || "none";
  const authSel = select(auths.map((a) => ({ value: a, label: AUTH_LABELS[a] || a })), auth, { id: `${pre}-auth` });
  authSel.addEventListener("change", () => { c.auth = authSel.value; ctx.changed({ rerender: true }); });
  const tls = checkbox("Verify the TLS certificate", c.verify_tls !== false, { id: `${pre}-tls` });
  tls.querySelector("input").addEventListener("change", (e) => set("verify_tls")(e.target.checked));
  const maxT = (schema && schema.limits && schema.limits.connection_timeout) || 120;
  const to = input(c.timeout === undefined ? 20 : c.timeout, { id: `${pre}-timeout`, type: "number", min: 1, max: maxT });
  to.addEventListener("input", () => set("timeout")(to.value === "" ? "" : Number(to.value)));

  // API Catalog binding: the package's own API versions plus the target's.
  const apiOpts = [];
  list(appBody(ctx.project), "apis").forEach((a) => apiOpts.push({ value: `${a.slug}@${a.version}`, label: `${a.slug} ${a.version} (this package)` }));
  ((ctx.snapshot && ctx.snapshot.existing && ctx.snapshot.existing.api_versions) || []).forEach(([s, v, st]) => {
    if (!apiOpts.some((o) => o.value === `${s}@${v}`)) apiOpts.push({ value: `${s}@${v}`, label: `${s} ${v} (target, ${st})` });
  });
  const cur = c.api ? `${c.api}@${c.api_version || ""}` : "";
  const apiSel = select(apiOpts, cur, { id: `${pre}-api` }, "— not pinned —");
  apiSel.addEventListener("change", () => {
    if (!apiSel.value) { delete c.api; delete c.api_version; } else { const [a, v] = apiSel.value.split("@"); c.api = a; c.api_version = v; }
    ctx.changed();
  });

  const authFields = [];
  if (["basic", "session", "oauth2"].includes(auth)) authFields.push(txt("username", auth === "oauth2" ? "Client id" : "User", { required: true }));
  if (auth === "api_key") authFields.push(txt("header_name", "Header name", { required: true, help: "e.g. X-API-Key", attrs: { class: "form-control form-control-sm ed-mono" } }));
  if (auth === "oauth2") {
    authFields.push(txt("token_url", "Token URL", { required: true, help: "https://host/path: the client secret travels in this request." }));
    authFields.push(txt("scope", "Scope"));
  }
  if (auth === "session") authFields.push(txt("token_url", "Login path", { help: "A path under the base URL, e.g. /api/auth/login", attrs: { placeholder: "/api/auth/login" } }));
  const rm = button("Remove connection", { class: "btn btn-sm btn-fw-outline ed-danger", id: `${pre}-remove` }, "bi-trash");
  rm.addEventListener("click", () => { conns.splice(i, 1); ctx.changed({ rerender: true }); });
  const secretRm = secrets.length ? button("Remove the secret values", { class: "btn btn-sm btn-fw-danger", id: `${pre}-strip` }, "bi-shield-x") : null;
  if (secretRm) secretRm.addEventListener("click", () => { secrets.forEach((k) => delete c[k]); ctx.changed({ rerender: true }); });
  return h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
    h("div", { class: "ed-item-head" }, h("strong", { text: c.name || c.slug }), " ", h("code", { text: c.slug }), " ", badge(kind, kinds.includes(kind) ? "info" : "danger"), " ", badge("secret: typed in SATOM", "secondary")),
    issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m })), secretRm) : null,
    h("div", { class: "ed-grid ed-grid-3" }, txt("slug", "Slug", { required: true, rerender: true, attrs: { class: "form-control form-control-sm ed-mono" } }), txt("name", "Name"), txt("description", "Description")),
    h("div", { class: "ed-grid ed-grid-3" }, txt("base_url", "Base URL", { required: true, cls: "ed-span-2", help: "https://host/prefix: steps add paths under it." }), field("Timeout (s)", to, { help: `1 to ${maxT}` })),
    h("div", { class: "ed-grid ed-grid-3" }, field("Authentication", authSel), authFields, h("div", { class: "ed-field ed-field-end" }, tls)),
    h("div", { class: "ed-grid ed-grid-2" }, field("API Catalog version", apiSel, { help: "A pinned connection calls only the endpoints of that version (Call an API picks them by id)." })),
    h("div", { class: "ed-item-foot" }, rm));
}
