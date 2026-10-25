/* Form tab: app.app.fields (studio_apps.clean_fields). */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash } from "../ui.js";
import * as F from "../fields.js";
import { ensureApp, appSpec, list, ensure } from "../model.js";
import { moveItem } from "../util.js";

export default {
  id: "form", label: "Form", icon: "bi-ui-checks",

  render(ctx, panel) {
    clear(panel);
    const schema = ctx.schema;
    const fields = list(appSpec(ctx.project), "fields");
    const choices = ctx.choices();
    const hints = F.hints(fields, schema, choices);
    const max = F.maxFields(schema);
    const add = button("Add field", { class: "btn btn-sm btn-fw-primary", id: "form-add", disabled: fields.length >= max }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const r = F.addField(ensure(ensureApp(ctx.project).app, "fields", () => []), schema);
      if (!r.ok) { ctx.say(r.error, "bad"); return; }
      ctx.changed({ rerender: true, focus: `fld-${fields.length}-name` });
    });
    const body = h("div", { class: "ed-list" });
    if (!fields.length) body.appendChild(empty("No fields: the app runs without a form."));
    fields.forEach((f, i) => body.appendChild(fieldCard(ctx, f, i, fields, choices, hints)));
    panel.appendChild(card("Form fields", [
      h("p", { class: "ed-intro", text: `The form an operator fills before a run. Steps read a value as {{ form.<name> }}. ${fields.length} / ${max} fields.` }),
      body,
    ], { icon: "bi-ui-checks", tools: add }));
  },

  reveal(ctx, t) {
    if (!t.id) return;
    const i = list(appSpec(ctx.project), "fields").findIndex((f) => f.name === t.id);
    const el = document.getElementById(`fld-${i}`);
    if (el) { flash(el); const n = document.getElementById(`fld-${i}-name`); if (n) n.focus({ preventScroll: true }); }
  },
};

function fieldCard(ctx, f, i, fields, choices, hints) {
  const schema = ctx.schema;
  const pre = `fld-${i}`;
  const ch = () => ctx.changed();
  const re = () => ctx.changed({ rerender: true });
  const issues = ctx.issuesFor("form", f.name).concat(hints.filter((x) => x.where === `app.fields[${f.name}]`).map((x) => x.message));

  const name = input(f.name, { id: `${pre}-name`, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
  name.addEventListener("input", () => { f.name = name.value; ch(); });
  name.addEventListener("change", re);
  const label = input(f.label, { id: `${pre}-label` });
  label.addEventListener("input", () => { f.label = label.value; ch(); });
  const type = select(F.fieldTypes(schema), f.type || "text", { id: `${pre}-type` });
  type.addEventListener("change", () => { F.setType(f, type.value); re(); });
  const req = checkbox("Required", !!f.required, { id: `${pre}-req` });
  req.querySelector("input").addEventListener("change", (e) => { f.required = e.target.checked; ch(); });

  const row1 = h("div", { class: "ed-grid ed-grid-4" },
    field("Name", name, { help: "lowercase, digits and _" }), field("Label", label), field("Type", type), h("div", { class: "ed-field ed-field-end" }, req));
  const extra = h("div", { class: "ed-grid ed-grid-3" });
  const t = f.type || "text";
  if (t === "select") {
    const o = textarea(typeof f.options === "string" ? f.options : F.optionList(f).join("\n"), { id: `${pre}-options`, rows: 3 });
    o.addEventListener("input", () => { f.options = o.value; ch(); });
    extra.appendChild(field("Options (one per line)", o, { cls: "ed-span-2" }));
  } else if (t === "store_select") {
    const st = select(choices.stores, f.store || "", { id: `${pre}-store` }, "— data store —");
    st.addEventListener("change", () => { f.store = st.value; re(); });
    extra.appendChild(field("Data store", st, { help: storeHelp(choices, f.store) }));
    const cols = choices.columns(f.store);
    ["value_col", "label_col"].forEach((k) => {
      const lab = k === "value_col" ? "Value column" : "Label column (optional)";
      let c;
      if (cols) { c = select(cols, f[k] || "", { id: `${pre}-${k}` }, "—"); c.addEventListener("change", () => { f[k] = c.value; ch(); }); }
      else { c = input(f[k], { id: `${pre}-${k}`, class: "form-control form-control-sm ed-mono" }); c.addEventListener("input", () => { f[k] = c.value; ch(); }); }
      extra.appendChild(field(lab, c, { required: k === "value_col" }));
    });
  } else if (t === "api_select") {
    const c = select(choices.http, f.connection || "", { id: `${pre}-connection` }, "— HTTP connection —");
    c.addEventListener("change", () => { f.connection = c.value; ch(); });
    extra.appendChild(field("Connection", c, { required: true }));
    [["path", "Path", "/items"], ["list_path", "List JSON path", "items"], ["value_key", "Value key", "id"], ["label_key", "Label key", "name"]].forEach(([k, lab, ph]) => {
      const x = input(f[k], { id: `${pre}-${k}`, placeholder: ph, class: "form-control form-control-sm ed-mono" });
      x.addEventListener("input", () => { f[k] = x.value; ch(); });
      extra.appendChild(field(lab, x));
    });
  } else if (t === "satom_select") {
    const src = select((schema && schema.satom_sources) || [], f.source || "", { id: `${pre}-source` }, "— what the list shows —");
    src.addEventListener("change", () => { f.source = src.value; ch(); });
    extra.appendChild(field("SATOM list", src, { required: true, cls: "ed-span-2" }));
  }
  if (F.isListType(t, schema)) {
    const s = checkbox("Searchable (a search box over the choices)", !!f.search, { id: `${pre}-search` });
    s.querySelector("input").addEventListener("change", (e) => { if (e.target.checked) f.search = true; else delete f.search; ch(); });
    extra.appendChild(h("div", { class: "ed-field ed-field-end" }, s));
  }
  const row3 = h("div", { class: "ed-grid ed-grid-2" });
  if (t !== "password") {
    const d = input(f.default, { id: `${pre}-default` });
    d.addEventListener("input", () => { f.default = d.value; ch(); });
    row3.appendChild(field("Default", d));
  } else {
    row3.appendChild(h("div", { class: "ed-note", text: "A password field never has a default." }));
  }
  const help = input(f.help, { id: `${pre}-help` });
  help.addEventListener("input", () => { f.help = help.value; ch(); });
  row3.appendChild(field("Help text", help));

  const tools = h("div", { class: "ed-row-tools" },
    iconButton("bi-arrow-up", `Move ${f.name} up`, { disabled: i === 0, on: { click: () => { moveItem(fields, i, -1); ctx.changed({ rerender: true, focus: `fld-${i - 1}-name` }); } } }),
    iconButton("bi-arrow-down", `Move ${f.name} down`, { disabled: i === fields.length - 1, on: { click: () => { moveItem(fields, i, 1); ctx.changed({ rerender: true, focus: `fld-${i + 1}-name` }); } } }),
    iconButton("bi-trash", `Remove field ${f.name}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { fields.splice(i, 1); ctx.changed({ rerender: true }); } } }));
  return h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
    h("div", { class: "ed-item-head" }, h("strong", { text: f.label || f.name || "(no name)" }), " ", badge(t, "info"), f.required ? badge("required", "secondary") : null, tools),
    issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
    row1, extra.childNodes.length ? extra : null, row3);
}

function storeHelp(choices, store) {
  if (!store) return "";
  if (choices.pkg.stores.includes(store)) return "Created by this package.";
  if (choices.server.stores.includes(store)) return "Exists on the target.";
  return "Neither in the package nor on the target.";
}
