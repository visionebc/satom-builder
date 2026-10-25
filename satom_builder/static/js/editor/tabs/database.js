/* Database tab: the project's test data as a database. A diagram of its
 * tables (a dataset = its key, a data store = store_<slug>) with their
 * columns and the relations between them, what each table is (SATOM's own
 * query for a dataset, where its rows came from), and a console that runs
 * one read-only SQLite SELECT over the SAVED test data
 * (POST .../testdata/sql). Rows never go to the AI: the Assistant tab gets
 * this schema (names and counts) and answers with queries to run here. */
import { h, clear, field, input, textarea, select, button, badge, card, empty, say } from "../ui.js";
import * as D from "../dbmodel.js";
import { ensureInfo, originBadge, S as TD } from "./testdata.js";

const S = { pid: null, schema: null, version: -1, selected: null, sql: "", result: null, error: "", observer: null,
  lib: null, libTid: null, libError: "", find: "", busy: "" };
const SVG = "http://www.w3.org/2000/svg";

async function load(ctx, force) {
  await ensureInfo(ctx);
  if (S.pid !== ctx.project.id) { S.pid = ctx.project.id; S.schema = null; S.selected = null; S.sql = ""; S.result = null; S.error = ""; S.lib = null; S.libTid = null; S.find = ""; }
  if (!S.schema || force || S.version !== TD.version) {
    S.schema = await ctx.api.testdataSchema(ctx.project.id);
    S.version = TD.version;
  }
  return S.schema;
}

/* The lines between a referencing column and the key it points at, drawn
 * over the grid from the boxes' positions (redrawn when the layout moves). */
function drawLines(wrap) {
  const svg = wrap.querySelector("svg.db-lines");
  if (!svg || !S.schema || !wrap.isConnected) return;   // the schema is reloading, or the panel moved on
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const box = wrap.getBoundingClientRect();
  svg.setAttribute("width", String(wrap.scrollWidth));
  svg.setAttribute("height", String(wrap.scrollHeight));
  (S.schema.relations || []).forEach((r) => {
    const a = wrap.querySelector(`[data-col="${CSS.escape(r.from + "." + r.column)}"]`);
    const b = wrap.querySelector(`[data-col="${CSS.escape(r.to + "." + r.to_column)}"]`);
    if (!a || !b) return;
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const y1 = ra.top + ra.height / 2 - box.top, y2 = rb.top + rb.height / 2 - box.top;
    let x1, x2, d;
    if (ra.left < rb.right && rb.left < ra.right) {
      // same column: out of the left edge and back, never across another box
      x1 = ra.left - box.left; x2 = rb.left - box.left;
      const out = Math.min(x1, x2) - 14 - Math.min(10, Math.abs(y2 - y1) / 40);
      d = `M ${x1} ${y1} C ${out} ${y1}, ${out} ${y2}, ${x2} ${y2}`;
    } else {
      const right = ra.left < rb.left;
      x1 = (right ? ra.right : ra.left) - box.left; x2 = (right ? rb.left : rb.right) - box.left;
      const bend = Math.max(30, Math.abs(x2 - x1) / 2) * (right ? 1 : -1);
      d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
    }
    const p = document.createElementNS(SVG, "path");
    p.setAttribute("d", d);
    const on = S.selected && (S.selected === r.from || S.selected === r.to);
    p.setAttribute("class", "db-line" + (on ? " is-on" : ""));
    svg.appendChild(p);
    const dot = document.createElementNS(SVG, "circle");
    dot.setAttribute("cx", String(x2)); dot.setAttribute("cy", String(y2)); dot.setAttribute("r", "3");
    dot.setAttribute("class", "db-dot" + (on ? " is-on" : ""));
    svg.appendChild(dot);
  });
}

function tableBox(t, refs, pick) {
  const cols = t.columns.map((c) => {
    const ref = refs[`${t.name}.${c.name}`];
    return h("li", { class: "db-col" + (ref ? " is-ref" : ""), "data-col": `${t.name}.${c.name}` },
      h("span", { class: "db-col-name ed-mono", text: c.name }),
      h("span", { class: "db-col-type", text: c.type }),
      ref ? h("span", { class: "db-col-ref", title: `references ${ref.to}.${ref.to_column}`, text: "→ " + ref.to }) : null);
  });
  const btn = h("button", { type: "button", class: "db-table" + (S.selected === t.name ? " is-selected" : ""), id: `db-t-${t.name}`, "aria-pressed": String(S.selected === t.name), "aria-label": `${t.name}, ${t.kind}, ${t.rows} rows` },
    h("span", { class: "db-table-head" },
      h("span", { class: "db-table-name ed-mono", text: t.name }),
      h("span", { class: "db-table-meta" }, badge(t.kind, t.kind === "store" ? "secondary" : "primary"), " ", h("span", { text: `${t.rows} rows` }))),
    h("ul", { class: "db-cols" }, cols));
  btn.addEventListener("click", () => pick(t.name));
  return btn;
}

function details(ctx, t, rerender) {
  if (!t) return null;
  const q = button("Query this table", { class: "btn btn-sm btn-fw-outline", id: "db-query-table" }, "bi-terminal");
  q.addEventListener("click", () => { S.sql = `SELECT * FROM ${D.ident(t.name)} LIMIT 50`; rerender(); run(ctx, rerender); });
  const what = t.kind === "dataset"
    ? [h("p", { class: "ed-help", text: "A SATOM dataset: a view that declares it reads these rows. SATOM fills it with this query on its own database (PostgreSQL, run there, read-only and masked), then keeps the rows the viewer may see:" }),
      h("pre", { class: "db-sql ed-mono", id: "db-satom-sql", text: t.sql || "(not in this SATOM's runtime)" })]
    : [h("p", { class: "ed-help", text: `The data store ${t.key}: an app's store steps read and write these rows. _id is the row id a store step sees.` })];
  return card(h("span", null, t.name, " ", originBadge(t.origin)), [
    what,
    h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" },
      h("thead", null, h("tr", null, ["Column", "Type", "References"].map((x) => h("th", { scope: "col", text: x })))),
      h("tbody", null, t.columns.map((c) => {
        const ref = D.refsByColumn(S.schema)[`${t.name}.${c.name}`];
        return h("tr", null, h("td", { class: "ed-mono", text: c.name }), h("td", { text: c.type }), h("td", { class: "ed-mono", text: ref ? `${ref.to}.${ref.to_column}` : "" }));
      })))),
    q], { icon: t.kind === "store" ? "bi-database" : "bi-table", id: "db-details" });
}

/* More of SATOM's data: any dataset of the target (built-in, administrator,
 * device objects) added to the test data to explore it here; a view's own
 * datasets and the built-in ones are always there. */
async function loadLib(ctx) {
  const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
  if (!tid || S.libTid === tid) return;
  S.libTid = tid;
  try { S.lib = await ctx.api.library(tid); S.libError = ""; } catch (e) { S.lib = null; S.libError = e.message; }
}

function addCard(ctx, rerender) {
  if (ctx.kind === "app") return null;
  const info = TD.info || {};
  const have = new Set(((S.schema && S.schema.tables) || []).filter((t) => t.kind === "dataset").map((t) => t.key));
  const fixed = new Set((info.datasets || []).filter((d) => d.used || d.builtin).map((d) => d.key));
  const kids = [h("p", { class: "ed-help", text: "Add any dataset of the target to the test data (its rows come from SATOM, as it shows them to you) to look at it, join it and query it here. The datasets the view reads and SATOM's built-in ones are always in the test data." })];
  if (!S.lib) {
    const b = button("Show the target's datasets", { id: "db-lib-load" }, "bi-collection");
    b.addEventListener("click", async () => { S.libTid = null; await loadLib(ctx); rerender(); });
    kids.push(S.libError ? h("div", { class: "fw-alert fw-alert-info", role: "note", text: "The target's library could not be read: " + S.libError }) : null, b);
  } else {
    const q = input(S.find, { id: "db-find", type: "search", placeholder: "Search: key, name or column (e.g. vip, firewall_policy)", "aria-label": "Search the target's datasets" });
    const t = S.find.trim().toLowerCase();
    const all = (S.lib.datasets || []).filter((d) => !have.has(d.key));
    const hits = all.filter((d) => !t || (d.key + " " + (d.label || "") + " " + (d.columns || []).join(" ")).toLowerCase().includes(t)).slice(0, 40);
    const list = h("ul", { class: "db-add-list", id: "db-add-list" }, hits.map((d) => {
      const add = button("Add", { class: "btn btn-sm btn-fw-outline", "aria-label": `Add ${d.key} to the test data`, disabled: !!S.busy }, "bi-plus-lg");
      add.addEventListener("click", async () => {
        S.busy = d.key; rerender();
        try {
          const j = await ctx.api.importTestdata(ctx.project.id, { add: [d.key], limit: TD.limit || 200 });
          TD.info = j; TD.data = j.data; TD.dirty = false; TD.version += 1;
          S.schema = null; S.selected = d.key; ctx.say(`${d.key} added to the test data.`, "ok");
        } catch (e) { ctx.say(e.message, "bad"); }
        S.busy = ""; rerender();
      });
      return h("li", null, add, " ", h("code", { text: d.key }), " ", d.label || "", h("small", { class: "text-muted", text: ` · ${(d.columns || []).length} columns · ${d.group || ""}` }));
    }));
    q.addEventListener("input", () => { S.find = q.value; const pos = q.selectionStart; rerender(); const n = document.getElementById("db-find"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } });
    kids.push(field("Find a dataset", q), S.busy ? h("div", { class: "ed-status", role: "status", text: `Reading ${S.busy} from SATOM…` }) : null,
      hits.length ? list : empty(t ? "No dataset matches." : "Every dataset of the target is already here."),
      all.length > hits.length ? h("p", { class: "ed-help", text: `${all.length - hits.length} more: narrow the search.` }) : null);
  }
  const extra = ((S.schema && S.schema.tables) || []).filter((tb) => tb.kind === "dataset" && !fixed.has(tb.key));
  if (extra.length) {
    kids.push(h("h4", { class: "ed-sub", text: "Added to explore" }), h("ul", { class: "db-add-list", id: "db-extra-list" }, extra.map((tb) => {
      const rm = button("Remove", { class: "btn btn-sm btn-fw-outline", "aria-label": `Remove ${tb.key} from the test data` }, "bi-x-lg");
      rm.addEventListener("click", async () => {
        const data = TD.data || {};
        delete (data.datasets || {})[tb.key];
        delete (data.origin || {})["datasets:" + tb.key];
        try { const j = await ctx.api.saveTestdata(ctx.project.id, data); TD.data = j.data; TD.info = null; TD.version += 1; S.schema = null; if (S.selected === tb.key) S.selected = null; } catch (e) { ctx.say(e.message, "bad"); }
        rerender();
      });
      return h("li", null, rm, " ", h("code", { text: tb.key }), h("small", { class: "text-muted", text: ` · ${tb.rows} rows` }));
    })));
  }
  return card("More of SATOM's data", kids, { icon: "bi-cloud-plus", id: "db-card-add" });
}

async function run(ctx, rerender) {
  const st = document.getElementById("db-status");
  say(st, "Running…", "busy");
  try {
    S.result = await ctx.api.testdataSql(ctx.project.id, S.sql);
    S.error = "";
  } catch (e) { S.result = null; S.error = e.message; }
  rerender();
}

function resultView() {
  if (S.error) return h("div", { class: "fw-alert fw-alert-danger", role: "alert", id: "db-error", text: S.error });
  const r = S.result;
  if (!r) return empty("Run a SELECT to see its rows.");
  const cell = (v) => (v === null || v === undefined ? h("span", { class: "text-muted", text: "NULL" }) : String(v));
  return [h("p", { class: "ed-help", id: "db-count", text: `${r.rows.length} row(s)${r.truncated ? " (the first ones only)" : ""} in ${r.ms} ms.` }),
    r.rows.length ? h("div", { class: "ed-scroll-x db-result" }, h("table", { class: "fw-table ed-table", id: "db-result" },
      h("thead", null, h("tr", null, r.columns.map((c) => h("th", { scope: "col", text: c })))),
      h("tbody", null, r.rows.map((row) => h("tr", null, row.map((v) => h("td", null, cell(v)))))))) : empty("No rows.")];
}

export default {
  id: "database", label: "Database", icon: "bi-diagram-2",

  render(ctx, panel) {
    const rerender = () => this.render(ctx, panel);
    if (!S.schema || S.pid !== ctx.project.id || S.version !== TD.version) {
      if (S.observer) { S.observer.disconnect(); S.observer = null; }
      clear(panel).appendChild(empty("Loading the test database…"));
      load(ctx).then(() => rerender()).catch((e) => { clear(panel).appendChild(h("div", { class: "fw-alert fw-alert-warning", id: "db-unavailable", text: "The test database could not be read: " + e.message })); });
      return;
    }
    if (S.observer) { S.observer.disconnect(); S.observer = null; }
    clear(panel);
    const sch = S.schema;
    const tables = D.orderTables(sch);
    const refs = D.refsByColumn(sch);
    const pick = (name) => { S.selected = S.selected === name ? null : name; rerender(); };
    const refresh = button("Refresh", { id: "db-refresh" }, "bi-arrow-repeat");
    refresh.addEventListener("click", () => { S.schema = null; TD.info = null; rerender(); });
    panel.appendChild(card("Test database", [
      h("p", { class: "ed-help", text: "Your test data as a database: each dataset is a table named by its key, each data store a table store_<slug>. Click a table for its columns, its relations and, for a dataset, the query SATOM fills it with. The console runs one read-only SQLite SELECT over the saved test data (Test data tab: Load from SATOM or Generate). Ask the Assistant about the data: it knows these tables and answers with a query to run here." }),
      TD.dirty ? h("div", { class: "fw-alert fw-alert-warning", role: "note", id: "db-unsaved", text: "The Test data tab has unsaved changes: the diagram and the console read the saved test data." }) : null,
      refresh], { icon: "bi-diagram-2", id: "db-card-head" }));
    if (!tables.length) {
      panel.appendChild(empty("No tables yet: load or generate test data in the Test data tab."));
    } else {
      const wrap = h("div", { class: "db-diagram", id: "db-diagram" },
        document.createElementNS(SVG, "svg"),
        h("div", { class: "db-grid" }, tables.map((t) => tableBox(t, refs, pick))));
      wrap.firstChild.setAttribute("class", "db-lines");
      wrap.firstChild.setAttribute("aria-hidden", "true");
      const relList = (sch.relations || []).length
        ? h("ul", { class: "db-rel-list", id: "db-relations", "aria-label": "Relations" }, sch.relations.map((r) => h("li", { class: "ed-mono", text: `${r.from}.${r.column} → ${r.to}.${r.to_column}` })))
        : h("p", { class: "ed-help", text: "No relations between these tables." });
      panel.appendChild(card("Diagram", [h("div", { class: "ed-scroll-x" }, wrap), h("h4", { class: "ed-sub", text: "Relations" }), relList], { icon: "bi-diagram-3", id: "db-card-diagram" }));
      requestAnimationFrame(() => drawLines(wrap));
      if (typeof ResizeObserver !== "undefined") {
        S.observer = new ResizeObserver(() => drawLines(wrap));
        S.observer.observe(wrap);
      }
      const det = details(ctx, tables.find((t) => t.name === S.selected), rerender);
      if (det) panel.appendChild(det);
    }
    if (ctx.kind !== "app" && S.libTid === null) loadLib(ctx).then(rerender);
    const more = addCard(ctx, rerender);
    if (more) panel.appendChild(more);
    const ex = D.examples(sch);
    const exSel = select(ex.map((e, i) => ({ value: String(i), label: e.label })), "", { id: "db-examples", "aria-label": "Example queries" }, "Example queries…");
    exSel.addEventListener("change", () => { const e = ex[Number(exSel.value)]; if (e) { S.sql = e.sql; rerender(); } });
    const ta = textarea(S.sql, { id: "db-sql", rows: 6, class: "form-control form-control-sm ed-mono", spellcheck: "false", placeholder: "SELECT * FROM fleet_appliances LIMIT 50" });
    ta.addEventListener("input", () => { S.sql = ta.value; });
    ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); run(ctx, rerender); } });
    const go = button("Run", { class: "btn btn-sm btn-fw-primary", id: "db-run" }, "bi-play-fill");
    go.addEventListener("click", () => run(ctx, rerender));
    panel.appendChild(card("SQL", [
      h("p", { class: "ed-help", text: "SQLite, read-only: SELECT and WITH only, at most 500 rows back, stopped after 3 s. Changes go through the Test data tab." }),
      field("Query", ta), h("div", { class: "ed-inline" }, go, exSel, h("small", { class: "text-muted", text: "Ctrl+Enter runs it" })),
      h("div", { class: "ed-status", id: "db-status", role: "status", "aria-live": "polite" }), resultView()], { icon: "bi-terminal", id: "db-card-sql" }));
  },

  reveal() {},
};
