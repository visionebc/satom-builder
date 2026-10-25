/* Actions tab: app.app.actions, the buttons a finished run offers
 * (studio_apps.action_schema / clean_actions). */
import { h, clear, field, input, select, checkbox, button, iconButton, badge, card, empty, flash } from "../ui.js";
import { paramForm } from "../paramform.js";
import { ensureApp, appSpec, list, ensure } from "../model.js";
import { moveItem, uniqueName, pattern } from "../util.js";

export default {
  id: "actions", label: "Actions", icon: "bi-lightning",

  render(ctx, panel) {
    clear(panel);
    const kinds = (ctx.schema && ctx.schema.action_kinds) || [];
    const byKey = Object.fromEntries(kinds.map((k) => [k.key, k]));
    const actions = list(appSpec(ctx.project), "actions");
    const max = (ctx.schema && ctx.schema.limits && ctx.schema.limits.actions) || 20;
    const kindSel = select(kinds.map((k) => ({ value: k.key, label: k.label })), kinds[0] && kinds[0].key, { id: "act-kind" });
    const add = button("Add action", { class: "btn btn-sm btn-fw-primary", id: "act-add", disabled: !kinds.length || actions.length >= max }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const kind = kindSel.value, k = byKey[kind];
      const list2 = ensure(ensureApp(ctx.project).app, "actions", () => []);
      const key = uniqueName(kind.replace(/_/g, "-") + "-" + (list2.length + 1), (x) => list2.some((a) => a.key === x));
      list2.push({ key, label: k ? k.label : kind, kind, confirm: kind === "change_request", params: {} });
      ctx.changed({ rerender: true, focus: `act-${list2.length - 1}-label` });
    });
    const tools = h("div", { class: "ed-inline" }, h("label", { class: "visually-hidden", for: "act-kind", text: "Action kind" }), kindSel, add);
    const body = h("div", { class: "ed-list" });
    if (!actions.length) body.appendChild(empty("No actions: the output is only shown."));
    const keyRe = pattern(ctx.schema, "action_key");
    actions.forEach((a, i) => {
      const k = byKey[a.kind];
      const pre = `act-${i}`;
      const issues = ctx.issuesFor("actions", a.key).slice();
      if (!keyRe.test(String(a.key || ""))) issues.push("key: lowercase letters, digits, - and _");
      if (actions.some((o, j) => j !== i && o.key === a.key)) issues.push("key is used twice");
      if (!k) issues.push(`unknown action kind ${a.kind}: kept as it is`);
      const key = input(a.key, { id: `${pre}-key`, class: "form-control form-control-sm ed-mono" });
      key.addEventListener("input", () => { a.key = key.value; ctx.changed(); });
      key.addEventListener("change", () => ctx.changed({ rerender: true }));
      const label = input(a.label, { id: `${pre}-label` });
      label.addEventListener("input", () => { a.label = label.value; ctx.changed(); });
      const conf = checkbox("Ask to confirm", !!a.confirm, { id: `${pre}-confirm` });
      conf.querySelector("input").addEventListener("change", (e) => { a.confirm = e.target.checked; ctx.changed(); });
      const values = a.params && typeof a.params === "object" ? a.params : {};
      const tools2 = h("div", { class: "ed-row-tools" },
        iconButton("bi-arrow-up", `Move ${a.key} up`, { disabled: i === 0, on: { click: () => { moveItem(actions, i, -1); ctx.changed({ rerender: true }); } } }),
        iconButton("bi-arrow-down", `Move ${a.key} down`, { disabled: i === actions.length - 1, on: { click: () => { moveItem(actions, i, 1); ctx.changed({ rerender: true }); } } }),
        iconButton("bi-trash", `Remove action ${a.key}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { actions.splice(i, 1); ctx.changed({ rerender: true }); } } }));
      body.appendChild(h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
        h("div", { class: "ed-item-head" }, badge(k ? k.label : a.kind, "info"), " ", h("strong", { text: a.label || a.key }), tools2),
        issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
        h("div", { class: "ed-grid ed-grid-3" }, field("Key", key), field("Button label", label), h("div", { class: "ed-field ed-field-end" }, conf)),
        k ? h("div", { class: "ed-grid ed-grid-2" }, paramForm(k.params, values, { owner: a.kind, choices: ctx.choices(), idPrefix: `${pre}-p`, onChange: () => { a.params = values; ctx.changed(); } }).childNodes) : null));
    });
    panel.appendChild(card("Actions on the output", [
      h("p", { class: "ed-intro", text: `Buttons a finished run offers: save to a data store, download, send to a connection, run another app, raise a change request. An action never changes a device. ${actions.length} / ${max}.` }),
      body,
    ], { icon: "bi-lightning", tools }));
  },

  reveal(ctx, t) {
    const i = list(appSpec(ctx.project), "actions").findIndex((a) => a.key === t.id);
    const el = document.getElementById(`act-${i}`);
    if (el) { flash(el); document.getElementById(`act-${i}-key`).focus({ preventScroll: true }); }
  },
};
