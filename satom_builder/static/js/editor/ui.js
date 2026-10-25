/* DOM helpers. Text always goes in through textContent / attributes, never
 * through innerHTML: everything the editor shows (names, messages, AI
 * output) is data. */

let SEQ = 0;
export function uid(prefix) { SEQ += 1; return (prefix || "ed") + "-" + SEQ; }

/* h("div", {class: "x", on: {click: fn}, dataset: {k: v}}, child, "text", [more]) */
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  const a = attrs || {};
  Object.keys(a).forEach((k) => {
    const v = a[k];
    if (v === undefined || v === null || v === false) return;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "on") Object.keys(v).forEach((ev) => el.addEventListener(ev, v[ev]));
    else if (k === "dataset") Object.keys(v).forEach((d) => { el.dataset[d] = v[d]; });
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else if (k === "selected") el.selected = !!v;
    else if (k === "disabled") el.disabled = !!v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  });
  append(el, kids);
  return el;
}

function append(el, kids) {
  kids.forEach((c) => {
    if (c === undefined || c === null || c === false) return;
    if (Array.isArray(c)) append(el, c);
    else if (c instanceof Node) el.appendChild(c);
    else el.appendChild(document.createTextNode(String(c)));
  });
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function icon(name) { return h("i", { class: "bi " + name, "aria-hidden": "true" }); }

/* A labelled control: <div><label for=id>..</label>control<div help/></div> */
export function field(label, control, opts) {
  const o = opts || {};
  if (!control.id) control.id = uid("f");
  const kids = [h("label", { class: "form-label ed-label", for: control.id }, label, o.required ? h("span", { class: "ed-req", "aria-hidden": "true", text: " *" }) : null)];
  if (o.required) control.setAttribute("aria-required", "true");
  kids.push(control);
  if (o.help) {
    const hid = control.id + "-help";
    kids.push(h("div", { class: "form-text ed-help", id: hid, text: o.help }));
    control.setAttribute("aria-describedby", hid);
  }
  if (o.issues && o.issues.length) {
    control.classList.add("is-invalid");
    control.setAttribute("aria-invalid", "true");
    kids.push(h("div", { class: "ed-issue", role: "note" }, o.issues.map((m) => h("div", { text: m }))));
  }
  return h("div", { class: "ed-field " + (o.cls || "") }, kids);
}

export function input(value, attrs) {
  return h("input", Object.assign({ class: "form-control form-control-sm", type: "text", value: value === undefined || value === null ? "" : String(value) }, attrs || {}));
}

export function textarea(value, attrs) {
  const t = h("textarea", Object.assign({ class: "form-control form-control-sm", rows: 3 }, attrs || {}));
  t.value = value === undefined || value === null ? "" : String(value);
  return t;
}

/* options: ["a", ...] or [{value, label}] ; blank: label of an empty first option */
export function select(options, value, attrs, blank) {
  const s = h("select", Object.assign({ class: "form-select form-select-sm" }, attrs || {}));
  const seen = new Set();
  if (blank !== undefined && blank !== null) s.appendChild(h("option", { value: "", text: blank }));
  (options || []).forEach((o) => {
    const v = typeof o === "object" ? o.value : o;
    const l = typeof o === "object" ? (o.label || o.value) : o;
    seen.add(String(v));
    s.appendChild(h("option", { value: v, text: l, selected: String(v) === String(value === undefined || value === null ? "" : value) }));
  });
  // A value that is not offered (an object the target lacks) stays visible and selected.
  if (value !== undefined && value !== null && value !== "" && !seen.has(String(value))) {
    s.appendChild(h("option", { value, text: value + " (not offered)", selected: true }));
  }
  return s;
}

export function checkbox(label, checked, attrs) {
  const c = h("input", Object.assign({ class: "form-check-input", type: "checkbox", checked }, attrs || {}));
  if (!c.id) c.id = uid("c");
  return h("div", { class: "form-check ed-check" }, c, h("label", { class: "form-check-label", for: c.id, text: label }));
}

export function button(label, attrs, ico) {
  const a = Object.assign({ type: "button", class: "btn btn-sm btn-fw-outline" }, attrs || {});
  return h("button", a, ico ? icon(ico) : null, ico && label ? " " : null, label);
}

export function iconButton(ico, label, attrs) {
  return h("button", Object.assign({ type: "button", class: "btn btn-sm btn-fw-outline ed-icon-btn", "aria-label": label, title: label }, attrs || {}), icon(ico));
}

export function badge(text, tone) {
  return h("span", { class: "fw-badge fw-badge-" + (tone || "secondary"), text });
}

export function card(title, body, opts) {
  const o = opts || {};
  return h("section", { class: "fw-card ed-card " + (o.cls || ""), id: o.id, "aria-label": typeof title === "string" ? title : null },
    h("div", { class: "fw-card-header" }, h("h3", { class: "fw-card-title ed-card-title" }, o.icon ? icon(o.icon) : null, o.icon ? " " : null, title), o.tools || null),
    h("div", { class: "fw-card-body" }, body));
}

export function empty(text) { return h("div", { class: "ed-empty text-muted", text }); }

/* Status line for an action: tone = ok | bad | busy | info */
export function say(el, text, tone) {
  if (!el) return;
  el.textContent = text || "";
  el.dataset.tone = tone || "info";
}

/* After a re-render, put the focus back on the control that had it. */
export function keepFocus(fn) {
  const a = document.activeElement;
  const id = a && a.id;
  const pos = a && typeof a.selectionStart === "number" ? [a.selectionStart, a.selectionEnd] : null;
  fn();
  if (!id) return;
  const b = document.getElementById(id);
  if (b && b !== document.activeElement) {
    b.focus({ preventScroll: true });
    if (pos && typeof b.setSelectionRange === "function") { try { b.setSelectionRange(pos[0], pos[1]); } catch (e) { /* not a text box */ } }
  }
}

/* Mutate model on input; call `structural` (a re-render) on change when asked. */
export function bindValue(el, get, set, opts) {
  const o = opts || {};
  const ev = el.type === "checkbox" || el.tagName === "SELECT" ? "change" : "input";
  el.addEventListener(ev, () => {
    const v = el.type === "checkbox" ? el.checked : (o.number ? (el.value === "" ? "" : Number(el.value)) : el.value);
    set(v);
    if (o.after) o.after(v);
  });
  return el;
}

export function flash(el) {
  if (!el) return;
  el.classList.remove("ed-flash");
  void el.offsetWidth; // restart the animation
  el.classList.add("ed-flash");
  el.scrollIntoView({ block: "center", behavior: "smooth" });
}
