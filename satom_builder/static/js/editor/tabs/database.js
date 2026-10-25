/* Database tab: the project's test data as a database. A diagram of its
 * tables (a dataset = its key, a data store = store_<slug>) with their
 * columns and the relations between them, what each table is (SATOM's own
 * query for a dataset, where its rows came from), and a console that runs
 * one read-only SQLite SELECT over the SAVED test data
 * (POST .../testdata/sql). Rows never go to the AI: the Assistant tab gets
 * this schema (names and counts) and answers with queries to run here. */
import { h, clear, field, textarea, select, button, badge, card, empty, say } from "../ui.js";
import * as D from "../dbmodel.js";
import { ensureInfo, originBadge, S as TD } from "./testdata.js";

const S = { pid: null, schema: null, version: -1, selected: null, sql: "", result: null, error: "", observer: null };
const SVG = "http://www.w3.org/2000/svg";

async function load(ctx, force) {
  await ensureInfo(ctx);
  if (S.pid !== ctx.project.id) { S.pid = ctx.project.id; S.schema = null; S.selected = null; S.sql = ""; S.result = null; S.error = ""; }
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
  if (!svg) return;
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const box = wrap.getBoundingClientRect();
  svg.setAttribute("width", String(wrap.scrollWidth));
  svg.setAttribute("height", String(wrap.scrollHeight));
  (S.schema.relations || []).forEach((r) => {
    const a = wrap.querySelector(`[data-col="${CSS.escape(r.from + "." + r.column)}"]`);
    const b = wrap.querySelector(`[data-col="${CSS.escape(r.to + "." + r.to_column)}"]`);
    if (!a || !b) return;
    const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
    const right = ra.left < rb.left;
    const x1 = (right ? ra.right : ra.left) - box.left, y1 = ra.top + ra.height / 2 - box.top;
    const x2 = (right ? rb.left : rb.right) - box.left, y2 = rb.top + rb.height / 2 - box.top;
    const bend = Math.max(30, Math.abs(x2 - x1) / 2) * (right ? 1 : -1);
    const p = document.createElementNS(SVG, "path");
    p.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
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
