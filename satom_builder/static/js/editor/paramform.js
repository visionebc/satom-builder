/* A form GENERATED from a kind's params (step kinds: studio_flow.Param,
 * action kinds: studio_apps.action_schema): text | textarea | select | json,
 * with choices, required marks and help. The editor knows no kind by name,
 * with one exception kept from SATOM's process editor: the api_call
 * "operation" param is an endpoint picker over the connection's API Catalog
 * version. Values are written only when the author edits them. */
import { h, field, input, textarea, select, uid } from "./ui.js";
import { selectChoices } from "./choices.js";
import { jsonText, jsonProblem } from "./util.js";

export function paramForm(params, values, o) {
  const opts = o || {};
  const prefix = opts.idPrefix || uid("p");
  const wrap = h("div", { class: "ed-params" });
  const set = (name, v) => { values[name] = v; if (opts.onChange) opts.onChange(name, v); };
  (params || []).forEach((p) => {
    const id = `${prefix}-${p.name}`;
    const cur = values[p.name] !== undefined ? values[p.name] : p.default;
    const issues = opts.issuesFor ? opts.issuesFor(p) : [];
    const label = p.label || p.name;
    let ctl;
    if (opts.owner === "api_call" && p.name === "operation" && opts.choices) {
      wrap.appendChild(operationPicker(p, values, opts, id, set, issues));
      return;
    }
    if (p.kind === "select") {
      const choices = opts.choices ? selectChoices(p, opts.choices, opts.owner) : (p.choices || []);
      ctl = select(choices, cur, { id }, p.required && cur ? null : "—");
      ctl.addEventListener("change", () => {
        set(p.name, ctl.value);
        if (opts.owner === "api_call" && p.name === "connection" && opts.rerender) opts.rerender();
      });
    } else if (p.kind === "textarea") {
      ctl = textarea(cur, { id, rows: 4 });
      ctl.addEventListener("input", () => set(p.name, ctl.value));
    } else if (p.kind === "json") {
      ctl = textarea(jsonText(cur), { id, rows: 3, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
      const msg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
      const check = () => {
        const bad = jsonProblem(ctl.value);
        msg.textContent = bad ? "Not valid JSON yet: " + bad : "";
        ctl.classList.toggle("ed-json-bad", !!bad);
      };
      ctl.addEventListener("input", () => { set(p.name, ctl.value); check(); });
      const f = field(label, ctl, { required: p.required, help: p.help, issues });
      f.appendChild(msg);
      check();
      wrap.appendChild(f);
      return;
    } else {
      ctl = input(cur, { id });
      ctl.addEventListener("input", () => set(p.name, ctl.value));
    }
    wrap.appendChild(field(label, ctl, { required: p.required, help: p.help, issues }));
  });
  return wrap;
}

function operationPicker(p, values, opts, id, set, issues) {
  const cat = opts.choices.catalog(values.connection);
  if (cat && cat.library) return libraryPicker(p, values, opts, id, set, issues, cat);
  const ops = (cat && cat.ops) || [];
  const cur = values.operation || "";
  const list = ops.map((op) => ({ value: op.id, label: `${op.method} ${op.path} — ${op.id}${op.summary ? " (" + op.summary + ")" : ""}` }));
  const s = select(list, cur, { id, disabled: !cat && !cur }, "— type method and path —");
  s.addEventListener("change", () => {
    set("operation", s.value);
    const op = ops.find((x) => x.id === s.value);
    if (op) { values.method = op.method; values.path = op.path; }
    if (opts.rerender) opts.rerender();
  });
  const help = cat
    ? `API ${cat.api} ${cat.version}: the method and the path come from the endpoint.`
    : "The connection is not pinned to an API Catalog version: type the method and the path.";
  return field(p.label || p.name, s, { help, issues });
}

/* A connection pinned to a build of SATOM's API library (contract section
 * 19): thousands of endpoints, so a search box over "METHOD path" instead of
 * a select. Picking one sets the endpoint, the method and the path. */
function libraryPicker(p, values, opts, id, set, issues, cat) {
  const ops = cat.ops || [];
  const label = (op) => `${op.method} ${op.path}`;
  const cur = ops.find((x) => x.id === values.operation);
  const box = h("input", { id, type: "search", class: "form-control form-control-sm ed-mono", list: `${id}-list`, value: cur ? label(cur) : "", placeholder: cat.loading ? "Loading SATOM's endpoint list…" : "Type to search, e.g. addrgrp member", disabled: !!cat.loading, autocomplete: "off" });
  const dl = h("datalist", { id: `${id}-list` });
  ops.forEach((op) => dl.appendChild(h("option", { value: label(op) }, op.summary || "")));
  box.addEventListener("change", () => {
    const op = ops.find((x) => label(x) === box.value.trim());
    if (!op) { if (!box.value.trim()) { set("operation", ""); if (opts.rerender) opts.rerender(); } return; }
    set("operation", op.id);
    values.method = op.method; values.path = op.path;
    if (opts.rerender) opts.rerender();
  });
  const n = ops.length;
  const help = cat.loading
    ? `SATOM's API library, ${cat.api} ${cat.version}: loading its endpoints.`
    : n ? `SATOM's API library, ${cat.api} ${cat.version}: ${n} endpoints. Pick one, or type the method and the path below — the check refuses a path that is not one of them.`
      : `SATOM did not serve the endpoints of ${cat.api} ${cat.version}: type the method and the path; the check names a wrong one.`;
  return field(p.label || p.name, h("div", {}, box, dl), { help, issues });
}
