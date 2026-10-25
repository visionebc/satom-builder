/* Test data tab: what SATOM's runtime reads in a test run instead of a
 * database (satom_builder/testdata.py). Datasets have SATOM's columns,
 * stores start from the package's seed rows, connections answer canned
 * responses, and the steps that would touch SATOM itself or a device answer
 * what is set here. Saved separately from the project (PUT .../testdata). */
import { h, clear, field, input, textarea, select, button, iconButton, badge, card, empty, say } from "../ui.js";
import { parseCSV } from "../csv.js";

const PAGE = 25;
export const S = { pid: null, info: null, data: null, dirty: false, loading: null, page: {}, asJson: {}, seed: 1, rows: 8 };

export async function ensureInfo(ctx, force) {
  if (S.pid !== ctx.project.id) { S.pid = ctx.project.id; S.info = null; S.data = null; S.dirty = false; }
  if (S.info && !force) return S.info;
  if (!S.loading) {
    S.loading = ctx.api.testdata(ctx.project.id).then((j) => { S.info = j; S.data = j.data; S.dirty = false; return j; })
      .finally(() => { S.loading = null; });
  }
  return S.loading;
}

function typeOf(rows, col) {
  for (const r of rows) {
    const v = r[col];
    if (v === null || v === undefined || v === "") continue;
    if (typeof v === "number") return "number";
    if (typeof v === "boolean") return "boolean";
    if (typeof v === "object") return "json";
    return "text";
  }
  return "text";
}

function parseCell(type, text) {
  const t = String(text);
  if (t === "") return type === "text" ? "" : null;
  if (type === "number") { const n = Number(t); return Number.isFinite(n) ? n : t; }
  if (type === "boolean") return /^(true|1|yes|on)$/i.test(t);
  if (type === "json") { try { return JSON.parse(t); } catch (e) { return t; } }
  return t;
}

function cellText(v) {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/* One table: columns, a page of rows with inputs, add / delete, JSON, CSV. */
function tableEditor(ctx, rerender, key, title, columns, rows, opts) {
  const o = opts || {};
  const cols = columns.length ? columns : Array.from(new Set(rows.flatMap((r) => Object.keys(r)))).filter((c) => !c.startsWith("_"));
  const types = {};
  cols.forEach((c) => { types[c] = o.types && o.types[c] ? o.types[c] : typeOf(rows, c); });
  const changed = () => { S.dirty = true; ctx.say("Test data changed: Save test data to keep it.", "info"); markDirty(); };
  const tools = [];
  const add = button("Add row", { class: "btn btn-sm btn-fw-outline", id: `td-add-${key}` }, "bi-plus-lg");
  add.addEventListener("click", () => { const r = {}; cols.forEach((c) => { r[c] = types[c] === "number" ? 0 : types[c] === "boolean" ? false : ""; }); rows.push(r); S.page[key] = Math.floor((rows.length - 1) / PAGE); changed(); rerender(); });
  tools.push(add);
  if (o.regen) {
    const g = button("Regenerate", { id: `td-regen-${key}` }, "bi-arrow-repeat");
    g.addEventListener("click", () => o.regen());
    tools.push(g);
  }
  const js = button(S.asJson[key] ? "Table" : "JSON", { id: `td-json-${key}` }, S.asJson[key] ? "bi-table" : "bi-braces");
  js.addEventListener("click", () => { S.asJson[key] = !S.asJson[key]; rerender(); });
  tools.push(js);
  const csvIn = h("input", { type: "file", accept: ".csv,text/csv", class: "visually-hidden", id: `td-csv-${key}` });
  csvIn.addEventListener("change", async () => {
    const f = csvIn.files && csvIn.files[0];
    if (!f) return;
    const grid = parseCSV(await f.text());
    if (grid.length < 2) { ctx.say("The CSV has no rows under its header line.", "bad"); return; }
    const head = grid[0].map((x) => String(x).trim());
    const fresh = grid.slice(1).filter((r) => r.some((x) => String(x).trim() !== "")).map((r) => {
      const o2 = {};
      head.forEach((c, i) => { if (c) o2[c] = parseCell(types[c] || "text", r[i] === undefined ? "" : r[i]); });
      return o2;
    });
    rows.splice(0, rows.length, ...fresh);
    S.page[key] = 0;
    changed();
    rerender();
    ctx.say(`${fresh.length} row(s) read from ${f.name}.`, "ok");
  });
  const csvBtn = h("label", { class: "btn btn-sm btn-fw-outline mb-0", for: csvIn.id }, h("i", { class: "bi bi-filetype-csv", "aria-hidden": "true" }), " CSV");
  tools.push(csvBtn, csvIn);
  const clr = iconButton("bi-trash", `Remove every row of ${title}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", id: `td-clear-${key}` });
  clr.addEventListener("click", () => { rows.splice(0, rows.length); changed(); rerender(); });
  tools.push(clr);
  let body;
  if (S.asJson[key]) {
    const ta = textarea(JSON.stringify(rows, null, 1), { id: `td-ta-${key}`, rows: 12, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
    const st = h("div", { class: "ed-status", "aria-live": "polite" });
    ta.addEventListener("change", () => {
      try {
        const v = JSON.parse(ta.value);
        if (!Array.isArray(v) || v.some((r) => !r || typeof r !== "object" || Array.isArray(r))) throw new Error("a list of row objects is expected");
        rows.splice(0, rows.length, ...v);
        changed();
        say(st, `${v.length} row(s).`, "ok");
      } catch (e) { say(st, "Not applied: " + e.message, "bad"); }
    });
    body = [ta, st];
  } else {
    const page = Math.min(S.page[key] || 0, Math.max(0, Math.ceil(rows.length / PAGE) - 1));
    const tb = h("tbody");
    rows.slice(page * PAGE, page * PAGE + PAGE).forEach((r, j) => {
      const i = page * PAGE + j;
      const tr = h("tr");
      cols.forEach((c) => {
        const el = input(cellText(r[c]), { "aria-label": `${c}, row ${i + 1}`, class: "form-control form-control-sm" + (types[c] === "number" ? " ed-mono" : "") });
        el.addEventListener("change", () => { r[c] = parseCell(types[c], el.value); changed(); });
        tr.appendChild(h("td", null, el));
      });
      const del = iconButton("bi-x-lg", `Remove row ${i + 1}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger" });
      del.addEventListener("click", () => { rows.splice(i, 1); changed(); rerender(); });
      tr.appendChild(h("td", null, del));
      tb.appendChild(tr);
    });
    const pager = [];
    const pages = Math.ceil(rows.length / PAGE);
    if (pages > 1) {
      const prev = button("Previous", { disabled: page === 0 }, "bi-chevron-left");
      prev.addEventListener("click", () => { S.page[key] = page - 1; rerender(); });
      const next = button("Next", { disabled: page >= pages - 1 }, "bi-chevron-right");
      next.addEventListener("click", () => { S.page[key] = page + 1; rerender(); });
      pager.push(h("div", { class: "ed-inline" }, prev, h("span", { class: "ed-help", text: `Page ${page + 1} of ${pages}` }), next));
    }
    body = [rows.length ? h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" },
      h("thead", null, h("tr", null, cols.map((c) => h("th", { scope: "col" }, c, h("small", { class: "text-muted", text: " " + types[c] }))), h("th", { scope: "col" }, h("span", { class: "visually-hidden", text: "Remove" })))), tb)) : empty("No rows."), pager];
  }
  return card(h("span", null, title, " ", badge(`${rows.length} row(s)`, "secondary"), o.note ? h("small", { class: "text-muted", text: " " + o.note }) : null),
    [o.help ? h("p", { class: "ed-help", text: o.help }) : null, body], { icon: o.icon || "bi-table", id: `td-card-${key}`, tools: h("span", { class: "ed-row-tools" }, tools) });
}

function markDirty() {
  const b = document.getElementById("td-save");
  if (b) b.classList.toggle("ed-attention", !!S.dirty);
  const d = document.getElementById("td-dirty");
  if (d) d.hidden = !S.dirty;
}

function jsonBox(ctx, id, value, onOk, rows) {
  const ta = textarea(value === undefined ? "" : JSON.stringify(value, null, 1), { id, rows: rows || 3, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
  ta.addEventListener("change", () => {
    if (ta.value.trim() === "") { onOk(null); S.dirty = true; markDirty(); ta.classList.remove("is-invalid"); return; }
    try { onOk(JSON.parse(ta.value)); S.dirty = true; markDirty(); ta.classList.remove("is-invalid"); } catch (e) { ta.classList.add("is-invalid"); ctx.say("Not JSON: " + e.message, "bad"); }
  });
  return ta;
}

function connectionEditor(ctx, rerender, slug, def) {
  const d = S.data.connections[slug] = S.data.connections[slug] || { responses: [], default: { status: 404, body: null }, queries: [] };
  d.responses = d.responses || []; d.queries = d.queries || []; d.default = d.default || { status: 404, body: null };
  const ch = () => { S.dirty = true; markDirty(); };
  const kids = [];
  if (def.kind === "postgres") {
    kids.push(h("p", { class: "ed-help", text: "A query answers the rows of the first entry whose text appears in its SQL (case-insensitive); none matching = no rows." }));
    d.queries.forEach((q, i) => {
      const m = input(q.match, { id: `td-q-${slug}-${i}`, class: "form-control form-control-sm ed-mono" });
      m.addEventListener("change", () => { q.match = m.value; ch(); });
      const del = iconButton("bi-trash", `Remove query ${i + 1}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger" });
      del.addEventListener("click", () => { d.queries.splice(i, 1); ch(); rerender(); });
      kids.push(h("div", { class: "ed-item" }, h("div", { class: "ed-grid ed-grid-2" }, field("SQL contains", m), h("div", { class: "ed-field ed-field-end" }, del)),
        field("Rows (JSON list)", jsonBox(ctx, `td-qr-${slug}-${i}`, q.rows || [], (v) => { q.rows = Array.isArray(v) ? v : []; }, 4))));
    });
    const add = button("Add query answer", { id: `td-qadd-${slug}` }, "bi-plus-lg");
    add.addEventListener("click", () => { d.queries.push({ match: "SELECT", rows: [] }); ch(); rerender(); });
    kids.push(add);
  } else {
    kids.push(h("p", { class: "ed-help", text: "The first response whose method (* = any) and path match answers; * is one path segment, ** anything. Nothing matching = the default." }));
    const tb = h("tbody");
    d.responses.forEach((r, i) => {
      const m = select(["*", "GET", "POST", "PUT", "PATCH", "DELETE"], r.method || "*", { "aria-label": `Method, response ${i + 1}` });
      m.addEventListener("change", () => { r.method = m.value; ch(); });
      const p = input(r.path || "*", { "aria-label": `Path, response ${i + 1}`, class: "form-control form-control-sm ed-mono" });
      p.addEventListener("change", () => { r.path = p.value; ch(); });
      const st = input(r.status || 200, { type: "number", min: 100, max: 599, "aria-label": `Status, response ${i + 1}` });
      st.addEventListener("change", () => { r.status = Number(st.value) || 200; ch(); });
      const body = jsonBox(ctx, `td-rb-${slug}-${i}`, r.body, (v) => { r.body = v; }, 2);
      body.setAttribute("aria-label", `Body, response ${i + 1}`);
      const del = iconButton("bi-x-lg", `Remove response ${i + 1}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger" });
      del.addEventListener("click", () => { d.responses.splice(i, 1); ch(); rerender(); });
      tb.appendChild(h("tr", null, h("td", null, m), h("td", null, p), h("td", null, st), h("td", { class: "ed-td-wide" }, body), h("td", null, del)));
    });
    kids.push(d.responses.length ? h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" },
      h("thead", null, h("tr", null, ["Method", "Path", "Status", "Body (JSON)", ""].map((t) => h("th", { scope: "col", text: t })))), tb)) : empty("No responses: every call gets the default."));
    const add = button("Add response", { id: `td-radd-${slug}` }, "bi-plus-lg");
    add.addEventListener("click", () => { d.responses.push({ method: "GET", path: "*", status: 200, body: {} }); ch(); rerender(); });
    const dst = input(d.default.status || 404, { type: "number", min: 100, max: 599, id: `td-ds-${slug}` });
    dst.addEventListener("change", () => { d.default.status = Number(dst.value) || 404; ch(); });
    kids.push(add, h("div", { class: "ed-grid ed-grid-2" }, field("Default status", dst), field("Default body (JSON)", jsonBox(ctx, `td-db-${slug}`, d.default.body, (v) => { d.default.body = v; }, 2))));
  }
  const regen = button("Regenerate", { id: `td-cregen-${slug}` }, "bi-arrow-repeat");
  regen.addEventListener("click", () => generate(ctx, rerender, [`connections:${slug}`]));
  return card(h("span", null, slug, " ", badge(def.kind || "http", "secondary"), " ", h("small", { class: "text-muted", text: def.from === "target" ? "on the target" : "in the package" })),
    kids, { icon: "bi-plug", id: `td-card-conn-${slug}`, tools: regen });
}

function stepsEditor(ctx, rerender) {
  const steps = (S.info && S.info.steps) || [];
  if (!steps.length) return null;
  const tb = h("tbody");
  steps.forEach((s) => {
    const t = S.data.steps[s.key] || {};
    const oc = select(["ok", "yes", "no", "error"], t.outcome || "", { "aria-label": `Answer of ${s.label}` }, "default");
    const set = () => { S.data.steps[s.key] = Object.assign(S.data.steps[s.key] || {}, { outcome: oc.value || (["http_check", "tcp_check", "console_read", "appliance_fact"].includes(s.kind) ? "yes" : "ok") }); S.dirty = true; markDirty(); };
    oc.addEventListener("change", () => { if (!oc.value) { delete S.data.steps[s.key]; S.dirty = true; markDirty(); } else set(); });
    const val = jsonBox(ctx, `td-sv-${s.key}`, t.value === null ? undefined : t.value, (v) => { set(); S.data.steps[s.key].value = v; }, 2);
    const det = input(t.detail || "", { "aria-label": `Detail of ${s.label}` });
    det.addEventListener("change", () => { set(); S.data.steps[s.key].detail = det.value; });
    tb.appendChild(h("tr", null, h("td", null, h("strong", { text: s.label }), " ", h("code", { text: s.key })), h("td", { text: s.kind }), h("td", null, oc), h("td", { class: "ed-td-wide" }, val), h("td", null, det)));
  });
  return card("SATOM and device steps", [h("p", { class: "ed-help", text: "A test run never reaches a device, the DDI, the Certificate Manager or SATOM's fleet: these steps answer what you set here (default: ok / yes, with a description of what they would do). Error takes the step's error arrow." }),
    h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" }, h("thead", null, h("tr", null, ["Step", "Kind", "Answer", "Value (JSON)", "Detail"].map((x) => h("th", { scope: "col", text: x })))), tb))], { icon: "bi-hdd-network", id: "td-card-steps" });
}

async function generate(ctx, rerender, only) {
  const st = document.getElementById("td-status");
  if (S.dirty && !only && !window.confirm("Replace the unsaved test data with generated data?")) return;
  say(st, "Generating test data…", "busy");
  try {
    const j = await ctx.api.generateTestdata(ctx.project.id, { seed: S.seed, rows: S.rows, only: only || null });
    const keep = only && S.dirty ? S.data : null;
    S.info = j;
    if (keep) { only.forEach((o) => { const [part, k] = o.split(":"); keep[part][k] = j.data[part][k]; }); S.data = keep; } else { S.data = j.data; S.dirty = false; }
    rerender();
    say(document.getElementById("td-status"), only ? "Regenerated." : "Generated and saved.", "ok");
  } catch (e) { say(st, e.message, "bad"); }
}

export default {
  id: "testdata", label: "Test data", icon: "bi-database-check",

  render(ctx, panel) {
    const rerender = () => this.render(ctx, panel);
    if (!S.info || S.pid !== ctx.project.id) {
      clear(panel).appendChild(empty("Loading the test data…"));
      ensureInfo(ctx).then(() => rerender()).catch((e) => { clear(panel).appendChild(h("div", { class: "fw-alert fw-alert-warning", text: "The test data could not be loaded: " + e.message })); });
      return;
    }
    clear(panel);
    const info = S.info, data = S.data;
    ["datasets", "stores", "connections", "steps", "sources"].forEach((k) => { data[k] = data[k] || {}; });
    if (!info.runtime.ok) panel.appendChild(h("div", { class: "fw-alert fw-alert-warning", role: "note", id: "td-no-runtime" }, h("strong", { text: "No test runtime. " }), info.runtime.message || ""));
    if (info.runtime.jail_message) panel.appendChild(h("div", { class: "fw-alert fw-alert-info", role: "note", id: "td-no-jail", text: info.runtime.jail_message }));
    const seed = input(S.seed, { type: "number", min: 1, id: "td-seed" });
    seed.addEventListener("change", () => { S.seed = Number(seed.value) || 1; });
    const rows = input(S.rows, { type: "number", min: 1, max: 200, id: "td-rows" });
    rows.addEventListener("change", () => { S.rows = Math.max(1, Math.min(200, Number(rows.value) || 8)); });
    const gen = button("Generate all", { class: "btn btn-sm btn-fw-outline", id: "td-generate", disabled: !info.runtime.ok }, "bi-magic");
    gen.addEventListener("click", () => generate(ctx, rerender, null));
    const save = button("Save test data", { class: "btn btn-sm btn-fw-primary" + (S.dirty ? " ed-attention" : ""), id: "td-save" }, "bi-save");
    save.addEventListener("click", async () => {
      const st = document.getElementById("td-status");
      say(st, "Saving…", "busy");
      try { const j = await ctx.api.saveTestdata(ctx.project.id, S.data); S.data = j.data; S.dirty = false; markDirty(); say(st, "Test data saved.", "ok"); } catch (e) { say(st, "Not saved: " + e.message, "bad"); }
    });
    panel.appendChild(card("Test data", [
      h("p", { class: "ed-help", text: "What a test run reads instead of SATOM's database. Datasets have the columns SATOM gives a view; stores start from your package's seed rows; connections answer the responses below and nothing leaves the Builder. Generated values are synthetic (lab names, documentation addresses), never a real installation's." }),
      h("div", { class: "ed-inline" }, field("Seed", seed), field("Rows per table", rows), gen, save, h("span", { class: "fw-badge fw-badge-warning", id: "td-dirty", hidden: !S.dirty, text: "Unsaved" })),
      h("div", { class: "ed-status", id: "td-status", role: "status", "aria-live": "polite" })], { icon: "bi-database-check", id: "td-card-head" }));
    const dsCols = {};
    (info.datasets || []).forEach((d) => { dsCols[d.key] = d; });
    const keys = ctx.kind === "app" ? Object.keys(data.datasets) : (info.datasets || []).map((d) => d.key);
    if (keys.length) panel.appendChild(h("h3", { class: "ed-sub", text: "Datasets" }));
    keys.forEach((k) => {
      const d = dsCols[k] || { key: k, label: k, columns: [] };
      data.datasets[k] = data.datasets[k] || { rows: [] };
      panel.appendChild(tableEditor(ctx, rerender, "ds-" + k, d.label || k, d.columns || [], data.datasets[k].rows,
        { regen: () => generate(ctx, rerender, [`datasets:${k}`]), note: k, help: d.description, icon: "bi-table" }));
    });
    const stores = info.stores || {};
    if (Object.keys(stores).length) panel.appendChild(h("h3", { class: "ed-sub", text: "Data stores" }));
    Object.keys(stores).forEach((slug) => {
      const def = stores[slug];
      data.stores[slug] = data.stores[slug] || { rows: [] };
      const types = {};
      (def.columns || []).forEach((c) => { types[c.name] = { number: "number", boolean: "boolean", json: "json" }[c.type] || "text"; });
      panel.appendChild(tableEditor(ctx, rerender, "st-" + slug, slug, (def.columns || []).map((c) => c.name), data.stores[slug].rows,
        { regen: () => generate(ctx, rerender, [`stores:${slug}`]), types, note: def.from === "target" ? "on the target (its rows never come to the Builder)" : "in the package", icon: "bi-database" }));
    });
    const conns = info.connections || {};
    if (Object.keys(conns).length) panel.appendChild(h("h3", { class: "ed-sub", text: "Connections" }));
    Object.keys(conns).forEach((slug) => panel.appendChild(connectionEditor(ctx, rerender, slug, conns[slug])));
    const steps = stepsEditor(ctx, rerender);
    if (steps) panel.appendChild(steps);
    if (ctx.kind === "app") {
      panel.appendChild(card("Lists of SATOM objects", [h("p", { class: "ed-help", text: "What a form field of type satom_select offers, per source: {\"source\": [{\"value\", \"label\"}]}." }),
        jsonBox(ctx, "td-sources", data.sources, (v) => { S.data.sources = v && typeof v === "object" && !Array.isArray(v) ? v : {}; }, 6)], { icon: "bi-list-ul", id: "td-card-sources" }));
    }
  },

  reveal() {},
};
