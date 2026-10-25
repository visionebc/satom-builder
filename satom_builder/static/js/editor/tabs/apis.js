/* API Catalog tab: app.json `apis`, API versions the package creates
 * [{slug, name, owner, docs_url, description, version, base_path, operations}]
 * with operations in the shape of studio_api_spec.clean_operations. An
 * OpenAPI 3 / Swagger 2 JSON document can be imported (openapi.js). */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash, say } from "../ui.js";
import { ensure, appBody, list } from "../model.js";
import { uniqueName, jsonProblem } from "../util.js";
import { parseOpenAPI, METHODS, autoId } from "../openapi.js";

export default {
  id: "apis", label: "API Catalog", icon: "bi-journal-code",

  render(ctx, panel) {
    clear(panel);
    const apis = list(appBody(ctx.project), "apis");
    const add = button("Add API version", { class: "btn btn-sm btn-fw-primary", id: "api-add" }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const l = ensure(ctx.project.app = ctx.project.app || {}, "apis", () => []);
      const slug = uniqueName("api-" + (l.length + 1), (x) => l.some((a) => a.slug === x));
      l.push({ slug, name: "New API", owner: "", docs_url: "", description: "", version: "1.0", base_path: "", operations: [] });
      ctx.changed({ rerender: true, focus: `api-${l.length - 1}-slug` });
    });
    const body = h("div", { class: "ed-list" });
    if (!apis.length) body.appendChild(empty("This package creates no API Catalog version."));
    apis.forEach((a, i) => body.appendChild(apiCard(ctx, a, i, apis)));
    panel.appendChild(card("API Catalog versions", [
      h("p", { class: "ed-intro", text: "A version is created and published on import when the target lacks it; a version that exists is frozen and kept. A version needs at least one endpoint." }),
      body,
    ], { icon: "bi-journal-code", tools: add }));
  },

  reveal(ctx, t) {
    const apis = list(appBody(ctx.project), "apis");
    const i = apis.findIndex((a) => a.slug === t.id && (!t.version || String(a.version) === String(t.version)));
    const el = document.getElementById(`api-${i}`);
    if (!el) return;
    flash(el);
    if (t.operation) {
      const j = (apis[i].operations || []).findIndex((o) => o.id === t.operation);
      const op = document.getElementById(`api-${i}-op-${j}-path`);
      if (op) op.focus();
    }
  },
};

function apiCard(ctx, a, i, apis) {
  const pre = `api-${i}`;
  const ops = Array.isArray(a.operations) ? a.operations : [];
  const issues = ctx.issuesFor("apis", a.slug).slice();
  if (!ops.length) issues.push("a version without endpoints is refused on import");
  if (apis.some((o, j) => j !== i && o.slug === a.slug && String(o.version) === String(a.version))) issues.push("this slug and version appear twice");
  const txt = (k, label, o) => {
    const el = input(a[k], Object.assign({ id: `${pre}-${k}` }, (o && o.attrs) || {}));
    el.addEventListener("input", () => { a[k] = el.value; ctx.changed(); });
    if (o && o.rerender) el.addEventListener("change", () => ctx.changed({ rerender: true }));
    return field(label, el, o);
  };

  const table = h("table", { class: "fw-table ed-table" },
    h("thead", null, h("tr", null, ["Method", "Path", "Id", "Summary", "Params", "Body", "Deprecated", ""].map((t) => h("th", { scope: "col", text: t })))));
  const tb = h("tbody");
  ops.forEach((op, j) => {
    const oid = `${pre}-op-${j}`;
    const m = select(METHODS, op.method || "GET", { id: `${oid}-method`, "aria-label": `Endpoint ${j + 1} method` });
    m.addEventListener("change", () => { op.method = m.value; ctx.changed(); });
    const p = input(op.path, { id: `${oid}-path`, class: "form-control form-control-sm ed-mono", "aria-label": `Endpoint ${j + 1} path` });
    p.addEventListener("input", () => { op.path = p.value; ctx.changed(); });
    const id = input(op.id, { id: `${oid}-id`, class: "form-control form-control-sm ed-mono", "aria-label": `Endpoint ${j + 1} id`, placeholder: autoId(op.method || "GET", op.path || "/") });
    id.addEventListener("input", () => { op.id = id.value; ctx.changed(); });
    const s = input(op.summary, { id: `${oid}-summary`, "aria-label": `Endpoint ${j + 1} summary` });
    s.addEventListener("input", () => { op.summary = s.value; ctx.changed(); });
    const params = paramsEditor(ctx, op, `${oid}-params`, j);
    const body = h("input", { type: "checkbox", class: "form-check-input", id: `${oid}-body`, checked: !!op.body, "aria-label": `Endpoint ${j + 1} takes a body` });
    body.addEventListener("change", () => { op.body = body.checked; ctx.changed(); });
    const dep = h("input", { type: "checkbox", class: "form-check-input", id: `${oid}-dep`, checked: !!op.deprecated, "aria-label": `Endpoint ${j + 1} deprecated` });
    dep.addEventListener("change", () => { op.deprecated = dep.checked; ctx.changed(); });
    tb.appendChild(h("tr", { id: oid }, h("td", null, m), h("td", null, p), h("td", null, id), h("td", null, s), h("td", null, params), h("td", null, body), h("td", null, dep),
      h("td", null, iconButton("bi-trash", `Remove endpoint ${op.method} ${op.path}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { ops.splice(j, 1); ctx.changed({ rerender: true }); } } }))));
  });
  table.appendChild(tb);
  const addOp = button("Add endpoint", { id: `${pre}-op-add` }, "bi-plus-lg");
  addOp.addEventListener("click", () => {
    const l = ensure(a, "operations", () => []);
    l.push({ id: "", method: "GET", path: "/", summary: "", params: [], body: false, tags: [], deprecated: false });
    ctx.changed({ rerender: true, focus: `${pre}-op-${l.length - 1}-path` });
  });

  // OpenAPI import
  const doc = textarea("", { id: `${pre}-openapi`, rows: 6, class: "form-control form-control-sm ed-mono", spellcheck: "false", placeholder: '{"openapi": "3.0.3", "paths": {...}}' });
  const mode = select([{ value: "replace", label: "Replace the endpoints" }, { value: "merge", label: "Add new endpoints, keep the others" }], "replace", { id: `${pre}-openapi-mode` });
  const msg = h("div", { class: "ed-status", "aria-live": "polite", id: `${pre}-openapi-msg` });
  const imp = button("Import", { class: "btn btn-sm btn-fw-primary", id: `${pre}-openapi-go` }, "bi-box-arrow-in-down");
  imp.addEventListener("click", () => {
    let r;
    try { r = parseOpenAPI(doc.value); } catch (e) { say(msg, e.message, "bad"); return; }
    const l = ensure(a, "operations", () => []);
    let added = 0;
    if (mode.value === "replace") { l.splice(0, l.length); r.operations.forEach((o) => l.push(o)); added = r.operations.length; }
    else {
      r.operations.forEach((o) => {
        if (!l.some((x) => (x.method === o.method && x.path === o.path) || (x.id && x.id === o.id))) { l.push(o); added++; }
      });
    }
    if (!a.name || a.name === "New API") a.name = r.title || a.name;
    if (!a.base_path && r.base_path) a.base_path = r.base_path;
    if ((!a.version || a.version === "1.0") && r.version) a.version = r.version;
    doc.value = "";
    ctx.changed({ rerender: true });
    ctx.say(`${added} endpoint(s) imported${r.warnings.length ? "; " + r.warnings.length + " warning(s): " + r.warnings.join(" · ") : ""}. The check validates them.`, r.warnings.length ? "info" : "ok");
  });
  const rm = button("Remove API version", { class: "btn btn-sm btn-fw-outline ed-danger", id: `${pre}-remove` }, "bi-trash");
  rm.addEventListener("click", () => { apis.splice(i, 1); ctx.changed({ rerender: true }); });

  return h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
    h("div", { class: "ed-item-head" }, h("strong", { text: a.name || a.slug }), " ", h("code", { text: `${a.slug} ${a.version}` }), " ", badge(`${ops.length} endpoints`, ops.length ? "secondary" : "danger")),
    issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
    h("div", { class: "ed-grid ed-grid-4" }, txt("slug", "API slug", { required: true, rerender: true, attrs: { class: "form-control form-control-sm ed-mono" } }), txt("version", "Version", { required: true, rerender: true }), txt("name", "Name"), txt("owner", "Owner")),
    h("div", { class: "ed-grid ed-grid-3" }, txt("base_path", "Base path", { attrs: { class: "form-control form-control-sm ed-mono", placeholder: "/api" } }), txt("docs_url", "Documentation URL"), txt("description", "Description")),
    h("h4", { class: "ed-sub", text: "Endpoints" }),
    ops.length ? h("div", { class: "ed-scroll-x" }, table) : empty("No endpoints yet."),
    addOp,
    h("details", { class: "ed-csv" }, h("summary", { text: "Import OpenAPI (JSON)" }),
      field("OpenAPI 3 or Swagger 2 document, JSON form", doc, { help: "Parsed in the browser exactly like SATOM's importer; YAML: convert it to JSON first." }),
      h("div", { class: "ed-inline" }, h("label", { class: "visually-hidden", for: `${pre}-openapi-mode`, text: "Import mode" }), mode, imp), msg),
    h("div", { class: "ed-item-foot" }, rm));
}

/* Params of an endpoint: a compact summary that opens a JSON editor. */
function paramsEditor(ctx, op, id, j) {
  const ps = Array.isArray(op.params) ? op.params : [];
  const t = textarea(JSON.stringify(ps, null, 1), { id, rows: 4, class: "form-control form-control-sm ed-mono", "aria-label": `Endpoint ${j + 1} parameters (JSON)` });
  const msg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
  t.addEventListener("input", () => {
    const bad = jsonProblem(t.value);
    msg.textContent = bad ? "Not valid JSON: " + bad : "";
    if (!bad) { const v = JSON.parse(t.value || "[]"); if (Array.isArray(v)) { op.params = v; ctx.changed(); } else msg.textContent = "A list of {name, in, required, type}."; }
  });
  return h("details", { class: "ed-params-cell" }, h("summary", { text: ps.length ? ps.map((p) => `${p.name} (${p.in})`).join(", ") : "none" }), t, msg);
}
