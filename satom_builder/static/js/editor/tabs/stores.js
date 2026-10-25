/* Stores tab: app.json `stores`, data stores the package creates
 * [{slug, name, description, columns:[{name,label,type,required}], rows:[{...}]}]
 * (studio_store.clean_columns; at most schema.limits.seed_rows seed rows). */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash, say } from "../ui.js";
import { ensure, appBody, list } from "../model.js";
import { moveItem, uniqueName, pattern } from "../util.js";
import { rowsFromCSV, coerce } from "../csv.js";

const S = { open: null };

export default {
  id: "stores", label: "Stores", icon: "bi-database",

  render(ctx, panel) {
    clear(panel);
    const stores = list(appBody(ctx.project), "stores");
    const add = button("Add data store", { class: "btn btn-sm btn-fw-primary", id: "st-add" }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const l = ensure(ctx.project.app = ctx.project.app || {}, "stores", () => []);
      const slug = uniqueName("store-" + (l.length + 1), (x) => l.some((s) => s.slug === x));
      l.push({ slug, name: "New data store", description: "", columns: [{ name: "name", label: "Name", type: "text", required: false }], rows: [] });
      S.open = slug;
      ctx.changed({ rerender: true, focus: `st-${l.length - 1}-slug` });
    });
    const body = h("div", { class: "ed-list" });
    if (!stores.length) body.appendChild(empty("This package creates no data store. Steps may still use the target's stores."));
    stores.forEach((st, i) => body.appendChild(storeCard(ctx, st, i, stores)));
    panel.appendChild(card("Data stores", [
      h("p", { class: "ed-intro", text: "Stores are created on import with their columns and seed rows. A store that already exists on the target keeps its rows; new columns are appended." }),
      body,
    ], { icon: "bi-database", tools: add }));
  },

  reveal(ctx, t) {
    const i = list(appBody(ctx.project), "stores").findIndex((s) => s.slug === t.id);
    if (i < 0) return;
    const d = document.getElementById(`st-${i}`);
    if (d) { d.open = true; flash(d); }
    if (t.column) {
      const st = ctx.project.app.stores[i];
      const c = (st.columns || []).findIndex((x) => x.name === t.column);
      const el = document.getElementById(`st-${i}-col-${c}-name`);
      if (el) el.focus();
    } else if (t.row !== undefined && t.row !== null) {
      const el = document.getElementById(`st-${i}-r${t.row}-0`);
      if (el) { el.focus(); flash(el.closest("tr")); }
    }
  },
};

function storeCard(ctx, st, i, stores) {
  const schema = ctx.schema;
  const pre = `st-${i}`;
  const types = (schema && schema.column_types) || ["text", "number", "boolean", "date", "json"];
  const maxRows = (schema && schema.limits && schema.limits.seed_rows) || 500;
  const maxCols = (schema && schema.limits && schema.limits.store_columns) || 40;
  const issues = ctx.issuesFor("stores", st.slug).slice();
  if (!pattern(schema, "store_slug").test(String(st.slug || ""))) issues.push("slug: 2-48 lowercase letters, digits, - or _");
  if (stores.some((o, j) => j !== i && o.slug === st.slug)) issues.push("slug is used twice");
  const cols = Array.isArray(st.columns) ? st.columns : [];
  const rows = Array.isArray(st.rows) ? st.rows : [];
  const colRe = pattern(schema, "column");

  const slug = input(st.slug, { id: `${pre}-slug`, class: "form-control form-control-sm ed-mono" });
  slug.addEventListener("input", () => { st.slug = slug.value; S.open = st.slug; ctx.changed(); });
  slug.addEventListener("change", () => ctx.changed({ rerender: true }));
  const name = input(st.name, { id: `${pre}-name` });
  name.addEventListener("input", () => { st.name = name.value; ctx.changed(); });
  const desc = input(st.description, { id: `${pre}-desc` });
  desc.addEventListener("input", () => { st.description = desc.value; ctx.changed(); });

  // columns
  const colTable = h("table", { class: "fw-table ed-table" },
    h("thead", null, h("tr", null, ["Column", "Label", "Type", "Required", ""].map((t) => h("th", { scope: "col", text: t })))));
  const tb = h("tbody");
  cols.forEach((c, j) => {
    const cid = `${pre}-col-${j}`;
    const bad = !colRe.test(String(c.name || "")) || String(c.name || "").startsWith("_");
    const cn = input(c.name, { id: `${cid}-name`, class: "form-control form-control-sm ed-mono" + (bad ? " is-invalid" : ""), "aria-label": `Column ${j + 1} name` });
    cn.addEventListener("input", () => { c.name = cn.value; ctx.changed(); });
    cn.addEventListener("change", () => ctx.changed({ rerender: true }));
    const cl = input(c.label, { id: `${cid}-label`, "aria-label": `Column ${j + 1} label` });
    cl.addEventListener("input", () => { c.label = cl.value; ctx.changed(); });
    const ct = select(types, c.type || "text", { id: `${cid}-type`, "aria-label": `Column ${j + 1} type` });
    ct.addEventListener("change", () => { c.type = ct.value; ctx.changed({ rerender: true }); });
    const cr = h("input", { type: "checkbox", class: "form-check-input", id: `${cid}-req`, checked: !!c.required, "aria-label": `Column ${j + 1} required` });
    cr.addEventListener("change", () => { c.required = cr.checked; ctx.changed(); });
    tb.appendChild(h("tr", null, h("td", null, cn), h("td", null, cl), h("td", null, ct), h("td", null, cr),
      h("td", { class: "ed-row-tools" },
        iconButton("bi-arrow-up", `Move column ${c.name} up`, { disabled: j === 0, on: { click: () => { moveItem(cols, j, -1); ctx.changed({ rerender: true }); } } }),
        iconButton("bi-arrow-down", `Move column ${c.name} down`, { disabled: j === cols.length - 1, on: { click: () => { moveItem(cols, j, 1); ctx.changed({ rerender: true }); } } }),
        iconButton("bi-trash", `Remove column ${c.name}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { cols.splice(j, 1); ctx.changed({ rerender: true }); } } }))));
  });
  colTable.appendChild(tb);
  const addCol = button("Add column", { id: `${pre}-col-add`, disabled: cols.length >= maxCols }, "bi-plus-lg");
  addCol.addEventListener("click", () => {
    const l = ensure(st, "columns", () => []);
    l.push({ name: uniqueName("col_" + (l.length + 1), (x) => l.some((c) => c.name === x), "_"), label: "", type: "text", required: false });
    ctx.changed({ rerender: true, focus: `${pre}-col-${l.length - 1}-name` });
  });

  // rows grid
  const grid = h("table", { class: "fw-table ed-table ed-grid-table" },
    h("thead", null, h("tr", null, h("th", { scope: "col", text: "#" }), cols.map((c) => h("th", { scope: "col" }, c.name, " ", h("span", { class: "ed-muted", text: c.type || "text" }))), h("th", { scope: "col", text: "" }))));
  const gb = h("tbody");
  rows.forEach((r, ri) => {
    const tr = h("tr", null, h("td", { class: "ed-muted", text: String(ri + 1) }));
    cols.forEach((c, ci) => tr.appendChild(h("td", null, cellInput(ctx, r, c, `${pre}-r${ri}-${ci}`, `Row ${ri + 1} ${c.name}`))));
    tr.appendChild(h("td", null, iconButton("bi-x-lg", `Remove row ${ri + 1}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { rows.splice(ri, 1); ctx.changed({ rerender: true }); } } })));
    gb.appendChild(tr);
  });
  grid.appendChild(gb);
  const addRow = button("Add row", { id: `${pre}-row-add`, disabled: rows.length >= maxRows || !cols.length }, "bi-plus-lg");
  addRow.addEventListener("click", () => { ensure(st, "rows", () => []).push({}); ctx.changed({ rerender: true, focus: `${pre}-r${st.rows.length - 1}-0` }); });

  const csv = textarea("", { id: `${pre}-csv`, rows: 4, class: "form-control form-control-sm ed-mono", placeholder: cols.map((c) => c.name).join(",") + "\n..." });
  const csvMsg = h("div", { class: "ed-status", "aria-live": "polite" });
  const doCsv = (replace) => {
    const r = rowsFromCSV(csv.value, cols, maxRows);
    if (!r.rows.length) { say(csvMsg, "Nothing to add: the first line names the columns, the next lines are rows.", "bad"); return; }
    const l = ensure(st, "rows", () => []);
    if (replace) l.splice(0, l.length);
    const room = maxRows - l.length;
    const take = r.rows.slice(0, Math.max(0, room));
    take.forEach((x) => l.push(x));
    const notes = [`${take.length} row(s) ${replace ? "loaded" : "added"}.`];
    if (r.unknown.length) notes.push(`Ignored columns that are not in the store: ${r.unknown.join(", ")}.`);
    if (r.truncated || take.length < r.rows.length) notes.push(`Seed rows stop at ${maxRows}.`);
    csv.value = "";
    ctx.changed({ rerender: true });
    ctx.say(notes.join(" "), "ok");
  };
  const csvAppend = button("Append rows", { id: `${pre}-csv-append` }, "bi-plus-lg");
  csvAppend.addEventListener("click", () => doCsv(false));
  const csvReplace = button("Replace all rows", { id: `${pre}-csv-replace` }, "bi-arrow-repeat");
  csvReplace.addEventListener("click", () => doCsv(true));

  const rm = button("Remove store", { class: "btn btn-sm btn-fw-outline ed-danger", id: `${pre}-remove` }, "bi-trash");
  rm.addEventListener("click", () => { stores.splice(i, 1); ctx.changed({ rerender: true }); });

  const det = h("details", { class: "ed-item ed-collapsible" + (issues.length ? " ed-item-bad" : ""), id: pre, open: S.open === st.slug || issues.length > 0 || stores.length === 1 },
    h("summary", null, h("strong", { text: st.name || st.slug }), " ", h("code", { text: st.slug }), " ", badge(`${cols.length} columns`, "secondary"), " ", badge(`${rows.length} / ${maxRows} rows`, rows.length > maxRows ? "danger" : "secondary")),
    issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
    h("div", { class: "ed-grid ed-grid-3" }, field("Slug", slug, { required: true }), field("Name", name), field("Description", desc)),
    h("h4", { class: "ed-sub", text: "Columns" }), colTable, addCol,
    h("h4", { class: "ed-sub", text: "Seed rows" }),
    cols.length ? h("div", { class: "ed-scroll-x" }, grid) : empty("Add a column first."),
    addRow,
    h("details", { class: "ed-csv" }, h("summary", { text: "Paste CSV" }),
      field("CSV (first line: column names; comma, semicolon or tab)", csv), h("div", { class: "ed-inline" }, csvAppend, csvReplace), csvMsg),
    h("div", { class: "ed-item-foot" }, rm));
  det.addEventListener("toggle", () => { if (det.open) S.open = st.slug; });
  return det;
}

function cellInput(ctx, row, col, id, label) {
  const name = col.name;
  const v = row[name];
  const t = col.type || "text";
  let el;
  if (t === "boolean") {
    el = select([{ value: "true", label: "true" }, { value: "false", label: "false" }], v === true ? "true" : v === false ? "false" : "", { id, "aria-label": label }, "—");
    el.addEventListener("change", () => { if (el.value === "") delete row[name]; else row[name] = el.value === "true"; ctx.changed(); });
    return el;
  }
  if (t === "json") {
    el = input(v === undefined || v === null ? "" : JSON.stringify(v), { id, "aria-label": label, class: "form-control form-control-sm ed-mono" });
    el.addEventListener("input", () => { if (el.value.trim() === "") delete row[name]; else row[name] = coerce("json", el.value); ctx.changed(); });
    return el;
  }
  el = input(v === undefined || v === null ? "" : String(v), { id, "aria-label": label, type: t === "number" ? "number" : t === "date" ? "date" : "text", step: t === "number" ? "any" : null });
  el.addEventListener("input", () => {
    if (el.value === "" && t !== "text") delete row[name];
    else row[name] = t === "number" ? coerce("number", el.value) : el.value;
    ctx.changed();
  });
  return el;
}
