/* Automations tab: app.json "automations" (contract section 18) -- the app on
 * a schedule. In SATOM each one is an automation of Automations ("Run a
 * Plugin Studio app") that runs the PUBLISHED app as the importing user,
 * with these form values; the import creates them switched off. */
import { h, clear, field, input, textarea, select, button, iconButton, badge, card, empty, flash } from "../ui.js";
import { ensureApp, appSpec, appBody, list, ensure } from "../model.js";
import { uniqueName } from "../util.js";
import { blank, describe, limits, problems, WEEKDAYS } from "../automation.js";

function scheduleControls(a, pre, ctx, L) {
  const s = a.schedule && typeof a.schedule === "object" ? a.schedule : (a.schedule = {});
  const num = (key, label, attrs) => {
    const el = input(s[key], Object.assign({ id: `${pre}-${key}`, inputmode: "numeric" }, attrs || {}));
    el.addEventListener("input", () => { s[key] = /^\d+$/.test(el.value.trim()) ? Number(el.value.trim()) : el.value; ctx.changed(); });
    el.addEventListener("change", () => ctx.changed({ rerender: true, focus: el.id }));
    return field(label, el);
  };
  const time = () => {
    const el = input(s.time, { id: `${pre}-time`, type: "time" });
    el.addEventListener("change", () => { s.time = el.value; ctx.changed({ rerender: true, focus: el.id }); });
    return field("Time (server clock)", el);
  };
  if (a.schedule_kind === "interval") {
    const unit = select(L.units, s.unit, { id: `${pre}-unit` });
    unit.addEventListener("change", () => { s.unit = unit.value; ctx.changed({ rerender: true, focus: unit.id }); });
    return [num("every", "Every"), field("Unit", unit)];
  }
  if (a.schedule_kind === "weekly") {
    const wd = select(WEEKDAYS.map((d, i) => ({ value: String(i), label: d })), String(s.weekday), { id: `${pre}-weekday` });
    wd.addEventListener("change", () => { s.weekday = Number(wd.value); ctx.changed({ rerender: true, focus: wd.id }); });
    return [field("Day", wd), time()];
  }
  if (a.schedule_kind === "monthly") return [num("day", "Day of the month"), time()];
  if (a.schedule_kind === "cron") {
    const el = input(s.expr, { id: `${pre}-expr`, class: "form-control form-control-sm ed-mono", placeholder: "0 2 * * *" });
    el.addEventListener("input", () => { s.expr = el.value; ctx.changed(); });
    el.addEventListener("change", () => ctx.changed({ rerender: true, focus: el.id }));
    return [field("Cron (minute hour day month weekday)", el)];
  }
  return [time()];
}

function inputControls(a, pre, ctx, fields) {
  if (!fields.length) return [empty("The form has no fields: nothing to fill in.")];
  const values = a.inputs && typeof a.inputs === "object" && !Array.isArray(a.inputs) ? a.inputs : (a.inputs = {});
  return fields.map((f, j) => {
    const has = Object.prototype.hasOwnProperty.call(values, f.name);
    const v = has ? values[f.name] : "";
    const el = input(typeof v === "object" ? JSON.stringify(v) : v,
      { id: `${pre}-in-${j}`, placeholder: f.default ? `default: ${f.default}` : (f.required ? "required" : "empty = not given") });
    el.addEventListener("input", () => {
      if (el.value === "") delete values[f.name]; else values[f.name] = el.value;
      ctx.changed();
    });
    el.addEventListener("change", () => ctx.changed({ rerender: true, focus: el.id }));
    return field(`${f.label || f.name} (${f.name})`, el, { required: !!f.required && !f.default, help: f.help || null });
  });
}

export default {
  id: "automations", label: "Automations", icon: "bi-calendar2-check",

  render(ctx, panel) {
    clear(panel);
    const L = limits(ctx.schema);
    const autos = list(appBody(ctx.project), "automations");
    const fields = list(appSpec(ctx.project), "fields").filter((f) => f && f.name);
    const local = problems(autos, fields, L);
    const kindSel = select(L.schedule_kinds.map((k) => ({ value: k, label: k })), "daily", { id: "au-kind" });
    const add = button("Add automation", { class: "btn btn-sm btn-fw-primary", id: "au-add", disabled: autos.length >= L.max }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const lst = ensure(ensureApp(ctx.project), "automations", () => []);
      const kind = kindSel.value;
      const name = uniqueName(kind === "daily" ? "Daily run" : `Run ${kind}`, (x) => lst.some((o) => String(o.name || "").toLowerCase() === x.toLowerCase()));
      lst.push(blank(kind, name));
      ctx.changed({ rerender: true, focus: `au-${lst.length - 1}-name` });
    });
    const tools = h("div", { class: "ed-inline" }, h("label", { class: "visually-hidden", for: "au-kind", text: "When" }), kindSel, add);
    const body = h("div", { class: "ed-list" });
    if (!autos.length) body.appendChild(empty("No automation: the app runs only when somebody opens it and fills in the form."));
    local.filter((p) => p.index < 0).forEach((p) => body.appendChild(h("div", { class: "ed-issue", role: "note", text: p.message })));
    autos.forEach((a, i) => {
      const pre = `au-${i}`;
      const issues = ctx.issuesFor("automations", a.name).concat(local.filter((p) => p.index === i).map((p) => p.message));
      const name = input(a.name, { id: `${pre}-name` });
      name.addEventListener("input", () => { a.name = name.value; ctx.changed(); });
      name.addEventListener("change", () => ctx.changed({ rerender: true, focus: name.id }));
      const kind = select(L.schedule_kinds.map((k) => ({ value: k, label: k })), a.schedule_kind, { id: `${pre}-kind` });
      kind.addEventListener("change", () => {
        const fresh = blank(kind.value, a.name);
        a.schedule_kind = fresh.schedule_kind; a.schedule = fresh.schedule;
        ctx.changed({ rerender: true, focus: kind.id });
      });
      const desc = textarea(a.description, { id: `${pre}-desc`, rows: 2, maxlength: 500 });
      desc.addEventListener("input", () => { a.description = desc.value; ctx.changed(); });
      const remove = iconButton("bi-trash", `Remove automation ${a.name}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { autos.splice(i, 1); ctx.changed({ rerender: true }); } } });
      body.appendChild(h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
        h("div", { class: "ed-item-head" }, badge(describe(a.schedule_kind, a.schedule), "info"), " ", h("strong", { text: a.name || "(no name)" }), h("div", { class: "ed-row-tools" }, remove)),
        issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
        h("div", { class: "ed-grid ed-grid-3" }, field("Name", name, { required: true }), field("When", kind), ...scheduleControls(a, pre, ctx, L)),
        h("h4", { class: "ed-subtitle", text: "Form values" }),
        h("p", { class: "ed-intro", text: "Nobody fills in the form when it runs on its own: every required field is given here." }),
        h("div", { class: "ed-grid ed-grid-2" }, inputControls(a, pre, ctx, fields)),
        field("Description", desc)));
    });
    const old = ctx.schema && !ctx.schema.automations
      ? h("div", { class: "ed-issue", role: "note", id: "au-old", text: "This target's SATOM does not know automations yet (contract section 18): it would install the app and leave them out. Update SATOM first." })
      : null;
    panel.appendChild(card("Automations", [
      old,
      h("p", { class: "ed-intro", text: "The app on a schedule. In SATOM each one becomes an automation (Automations › \"Run a Plugin Studio app\") that runs the PUBLISHED app as the user who imports it, with the form values below. The import creates them switched off: turn them on in SATOM once the app is published. "
        + `An interval is at least ${L.min_interval_minutes} minutes; times are the SATOM server's clock. ${autos.length} / ${L.max}.` }),
      body,
    ], { icon: "bi-calendar2-check", tools }));
  },

  reveal(ctx, t) {
    const i = list(appBody(ctx.project), "automations").findIndex((a) => a.name === t.id);
    const el = document.getElementById(`au-${Math.max(i, 0)}`);
    if (el) { flash(el); const n = document.getElementById(`au-${Math.max(i, 0)}-name`); if (n) n.focus({ preventScroll: true }); }
  },
};
