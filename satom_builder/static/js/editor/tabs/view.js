/* View / widget tabs: Files (body.jinja, style.css, script.js), Datasets,
 * Params and Preview. A view or widget package carries exactly these three
 * files (plugin_package.CONTENT_FILES) plus datasets and params in its
 * manifest; params follow plugin_sandbox.clean_param_defs. */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash, say } from "../ui.js";
import { CodeEditor } from "../codeedit.js";
import { ensure } from "../model.js";
import { moveItem, uniqueName } from "../util.js";

const FILES = [["body.jinja", "Body (Jinja / HTML)"], ["style.css", "CSS"], ["script.js", "JavaScript"]];
const S = { file: "body.jinja", editor: null, values: {} };

export const files = {
  id: "files", label: "Files", icon: "bi-file-earmark-code",
  render(ctx, panel) {
    clear(panel);
    const f = ctx.project.files && typeof ctx.project.files === "object" ? ctx.project.files : {};
    const tabs = h("div", { class: "ed-filetabs", role: "tablist", "aria-label": "Plugin files" });
    FILES.forEach(([name, label]) => {
      const n = ctx.issues().filter((x) => x.target.tab === "files" && x.target.file === name).length;
      const b = h("button", { type: "button", role: "tab", class: "ed-filetab" + (S.file === name ? " is-selected" : ""), "aria-selected": String(S.file === name), id: `fl-tab-${name.replace(".", "-")}` }, name, n ? badge(String(n), "danger") : null);
      b.title = label;
      b.addEventListener("click", () => { S.file = name; this.render(ctx, panel); });
      tabs.appendChild(b);
    });
    const label = FILES.find((x) => x[0] === S.file)[1];
    S.editor = new CodeEditor({ id: "fl-code", label: `${S.file}: ${label}`, value: f[S.file] || "", rows: 24,
      onChange: (v) => { ensure(ctx.project, "files", () => ({}))[S.file] = v; ctx.changed(); } });
    const issues = ctx.issues().filter((x) => x.target.tab === "files" && (x.target.file === S.file || (!x.target.file && S.file === "body.jinja")));
    S.editor.setMarkers(issues.filter((x) => x.target.line).map((x) => ({ line: x.target.line, message: x.message, tone: x.tone })));
    const lint = h("ul", { class: "ed-issue-list", id: "fl-lint" }, issues.map((x) => {
      const b = h("button", { type: "button", class: "ed-issue-link ed-tone-" + x.tone, text: (x.target.line ? `line ${x.target.line}: ` : "") + x.message });
      b.addEventListener("click", () => { if (x.target.line) S.editor.gotoLine(x.target.line); });
      return h("li", null, b);
    }));
    const notes = {
      "body.jinja": "Rendered in SATOM with the datasets as {{ data.<key>.rows }} and inputs as {{ params.<name> }}.",
      "style.css": "Use the --fw-* tokens and the .fw-badge-* status colours. No @import, no external URLs, no position:fixed overlays; data: only for images and fonts.",
      "script.js": "Runs inside the sandboxed plugin frame.",
    };
    panel.appendChild(card("Files", [tabs, h("p", { class: "ed-help", text: notes[S.file] }), S.editor.el,
      issues.length ? h("div", null, h("strong", { text: "Findings of the last check" }), lint) : null], { icon: "bi-file-earmark-code" }));
  },
  reveal(ctx, t) {
    if (t.file) S.file = t.file;
    this.render(ctx, document.getElementById("panel-files"));
    if (t.line) S.editor.gotoLine(t.line);
  },
};

function datasetList(schema) {
  return ((schema && schema.datasets) || []).map((d) => (typeof d === "string" ? { key: d, label: d, description: "", group: "Datasets" } : d));
}

/* The catalogue comes from the target's library (contract section 16):
 * built-in, administrator and device-object datasets, with labels, groups and
 * columns. An older SATOM has no library: the schema's keys are listed. */
const DS = { tid: null, lib: null, loading: null, error: "", q: "" };
const SOURCE_LABEL = { builtin: "built-in", admin: "administrator", objects: "device objects" };

function loadLibrary(ctx) {
  const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
  if (!tid) return Promise.resolve(null);
  if (DS.tid === tid && (DS.lib || DS.error)) return Promise.resolve(DS.lib);
  if (!DS.loading) {
    DS.tid = tid;
    DS.loading = ctx.api.library(tid).then((j) => { DS.lib = j; DS.error = ""; return j; })
      .catch((e) => { DS.lib = null; DS.error = e.message; return null; })
      .finally(() => { DS.loading = null; });
  }
  return DS.loading;
}

function catalogue(ctx) {
  if (DS.lib && Array.isArray(DS.lib.datasets) && DS.lib.datasets.length) return DS.lib.datasets;
  return datasetList(ctx.schema);
}

export const datasets = {
  id: "datasets", label: "Datasets", icon: "bi-table",
  render(ctx, panel) {
    const rerender = () => this.render(ctx, panel);
    const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
    if (tid && DS.tid !== tid && !DS.loading) { clear(panel).appendChild(empty("Loading the target's datasets…")); loadLibrary(ctx).then(rerender); return; }
    clear(panel);
    const all = catalogue(ctx);
    const byKey = {};
    all.forEach((d) => { byKey[d.key] = d; });
    const chosen = Array.isArray(ctx.project.datasets) ? ctx.project.datasets : [];
    const toggle = (key, on) => {
      const l = ensure(ctx.project, "datasets", () => []);
      const i = l.indexOf(key);
      if (on && i < 0) l.push(key);
      if (!on && i >= 0) l.splice(i, 1);
      ctx.changed();
      rerender();
    };
    // what the plugin reads now
    const picked = h("div", { class: "ed-ds-picked", id: "dsx-picked" }, chosen.length ? chosen.map((k) => {
      const d = byKey[k];
      const rm = iconButton("bi-x-lg", `Stop reading ${k}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn" });
      rm.addEventListener("click", () => toggle(k, false));
      return h("span", { class: "ed-ds-chip" + (d ? "" : " ed-ds-missing") }, h("code", { text: k }), d ? null : h("small", { text: " not offered by the target" }), rm);
    }) : empty("The plugin reads no dataset yet."));
    // search + groups
    const q = input(DS.q, { id: "dsx-search", type: "search", placeholder: "Search: name, key or column", "aria-label": "Search the target's datasets" });
    const t = DS.q.trim().toLowerCase();
    const hit = (d) => !t || (d.key + " " + (d.label || "") + " " + (d.columns || []).join(" ")).toLowerCase().includes(t);
    const groups = {};
    all.forEach((d) => { const g = d.group || SOURCE_LABEL[d.source] || "Datasets"; (groups[g] = groups[g] || []).push(d); });
    const count = h("span", { class: "ed-help", id: "dsx-count", "aria-live": "polite" });
    const list = h("div", { class: "ed-ds-groups", id: "dsx-groups" });
    let shown = 0, idx = 0;
    Object.keys(groups).forEach((g) => {
      const items = groups[g].filter(hit);
      if (!items.length) return;
      shown += items.length;
      const open = g === "Built-in" || g === "Administrator datasets" || !!t || items.some((d) => chosen.includes(d.key));
      const det = h("details", { class: "ed-ds-group", open: open || null }, h("summary", null, g, " ", h("small", { class: "text-muted", text: `(${items.length})` })));
      items.forEach((d) => {
        idx += 1;
        const c = checkbox(`${d.label || d.key}`, chosen.includes(d.key), { id: `dsx-${idx}` });
        c.querySelector("input").dataset.key = d.key;
        c.querySelector("input").addEventListener("change", (e) => toggle(d.key, e.target.checked));
        det.appendChild(h("div", { class: "ed-ds" }, c, h("div", { class: "ed-help" },
          h("code", { text: d.key }), d.source ? [" ", badge(SOURCE_LABEL[d.source] || d.source, d.source === "admin" ? "info" : "secondary")] : null,
          d.columns ? ` · ${d.columns.length} columns` : "", d.description ? " · " + d.description : "")));
      });
      list.appendChild(det);
    });
    count.textContent = `${chosen.length} chosen · ${all.length} offered` + (t ? ` · ${shown} match` : "");
    q.addEventListener("input", () => { DS.q = q.value; const pos = q.selectionStart; rerender(); const n = document.getElementById("dsx-search"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } });
    const refresh = button("Refresh from SATOM", { id: "dsx-refresh" }, "bi-arrow-repeat");
    refresh.addEventListener("click", () => { DS.lib = null; DS.error = ""; DS.loading = ctx.api.library(tid, true).then((j) => { DS.lib = j; }).catch((e) => { DS.error = e.message; }).finally(() => { DS.loading = null; rerender(); }); });
    const issues = ctx.issuesFor("datasets", null);
    panel.appendChild(card("Datasets", [issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
      h("p", { class: "ed-help", text: "In the body: {{ <key>.rows }} (or {{ data.<key>.rows }}). A plugin only reads the datasets it declares. The target offers SATOM's built-in datasets, the ones its administrators published (admin_<name>) and one per object type its device cache holds (<product>_<type>)." }),
      DS.error ? h("div", { class: "fw-alert fw-alert-info", role: "note", text: "Only the dataset keys are listed: " + DS.error }) : null,
      h("h4", { class: "ed-sub", text: "Read by this plugin" }), picked,
      h("div", { class: "ed-inline" }, field("Find a dataset", q), refresh, count),
      list.childNodes.length ? list : empty(t ? "No dataset matches." : "The target lists no dataset.")], { icon: "bi-table", id: "dsx-card" }));
  },
  reveal() {},
};

export const params = {
  id: "params", label: "Params", icon: "bi-sliders",
  render(ctx, panel) {
    clear(panel);
    const types = (ctx.schema && ctx.schema.param_types) || ["device", "select", "text", "number", "date"];
    const defs = Array.isArray(ctx.project.params) ? ctx.project.params : [];
    const add = button("Add input", { class: "btn btn-sm btn-fw-primary", id: "pm-add" }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const l = ensure(ctx.project, "params", () => []);
      l.push({ name: uniqueName("input_" + (l.length + 1), (x) => l.some((p) => p.name === x), "_"), label: "", type: "text", options: [], default: "", required: false });
      ctx.changed({ rerender: true, focus: `pm-${l.length - 1}-name` });
    });
    const body = h("div", { class: "ed-list" });
    if (!defs.length) body.appendChild(empty("No inputs: the viewer cannot filter the view."));
    defs.forEach((p, i) => {
      const pre = `pm-${i}`;
      const issues = ctx.issuesFor("params", p.name).slice();
      if (!/^[a-z0-9_]{1,40}$/.test(String(p.name || ""))) issues.push("name: lowercase letters, digits and _ (up to 40); SATOM rewrites anything else");
      if (defs.some((o, j) => j !== i && o.name === p.name)) issues.push("name is used twice: SATOM keeps the first");
      const name = input(p.name, { id: `${pre}-name`, class: "form-control form-control-sm ed-mono" });
      name.addEventListener("input", () => { p.name = name.value; ctx.changed(); });
      const label = input(p.label, { id: `${pre}-label` });
      label.addEventListener("input", () => { p.label = label.value; ctx.changed(); });
      const type = select(types, p.type || "text", { id: `${pre}-type` });
      type.addEventListener("change", () => { p.type = type.value; ctx.changed({ rerender: true }); });
      const def = input(p.default, { id: `${pre}-default` });
      def.addEventListener("input", () => { p.default = def.value; ctx.changed(); });
      const req = checkbox("Required", !!p.required, { id: `${pre}-req` });
      req.querySelector("input").addEventListener("change", (e) => { p.required = e.target.checked; ctx.changed(); });
      const extra = [];
      if (p.type === "select") {
        const txt = (p.options || []).map((o) => (typeof o === "object" ? (o.label && o.label !== o.value ? `${o.value}|${o.label}` : o.value) : o)).join("\n");
        const o = textarea(txt, { id: `${pre}-options`, rows: 3 });
        o.addEventListener("input", () => {
          p.options = o.value.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => { const [v, ...rest] = l.split("|"); return { value: v.trim(), label: (rest.join("|").trim() || v.trim()) }; });
          ctx.changed();
        });
        extra.push(field("Options (one per line, value|Label)", o));
      }
      body.appendChild(h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre },
        h("div", { class: "ed-item-head" }, h("strong", { text: p.label || p.name }), " ", h("code", { text: `params.${p.name}` }),
          h("span", { class: "ed-row-tools" },
            iconButton("bi-arrow-up", `Move ${p.name} up`, { disabled: i === 0, on: { click: () => { moveItem(defs, i, -1); ctx.changed({ rerender: true }); } } }),
            iconButton("bi-arrow-down", `Move ${p.name} down`, { disabled: i === defs.length - 1, on: { click: () => { moveItem(defs, i, 1); ctx.changed({ rerender: true }); } } }),
            iconButton("bi-trash", `Remove ${p.name}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { defs.splice(i, 1); ctx.changed({ rerender: true }); } } }))),
        issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
        h("div", { class: "ed-grid ed-grid-4" }, field("Name", name), field("Label", label), field("Type", type), field("Default", def)),
        h("div", { class: "ed-grid ed-grid-2" }, extra, h("div", { class: "ed-field ed-field-end" }, req))));
    });
    panel.appendChild(card("Inputs (params)", [h("p", { class: "ed-help", text: "Selectors the viewer fills; read as {{ params.<name> }} in the body. They only filter loaded data, never widen it." }), body], { icon: "bi-sliders", tools: add }));
  },
  reveal(ctx, t) {
    const i = (ctx.project.params || []).findIndex((p) => p.name === t.id);
    flash(document.getElementById(`pm-${i}`));
  },
};

export const preview = {
  id: "preview", label: "Preview", icon: "bi-eye",
  render(ctx, panel) {
    clear(panel);
    const defs = Array.isArray(ctx.project.params) ? ctx.project.params : [];
    const bar = h("div", { class: "ed-grid ed-grid-4" });
    defs.forEach((p, i) => {
      const id = `pv-${i}`;
      const v = S.values[p.name] !== undefined ? S.values[p.name] : p.default || "";
      let el;
      if (p.type === "select") el = select((p.options || []).map((o) => (typeof o === "object" ? o : { value: o, label: o })), v, { id }, "—");
      else el = input(v, { id, type: p.type === "number" ? "number" : p.type === "date" ? "date" : "text" });
      el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => { S.values[p.name] = el.value; });
      bar.appendChild(field(p.label || p.name, el, { help: p.type === "device" ? "A device id on the target" : "" }));
    });
    const frame = h("iframe", { id: "pv-frame", class: "ed-preview", sandbox: "allow-scripts", title: "Plugin preview (sandboxed)", referrerpolicy: "no-referrer" });
    const msg = h("div", { class: "ed-status", "aria-live": "polite", id: "pv-status" });
    const probs = h("ul", { class: "ed-issue-list", id: "pv-problems" });
    const run = button("Render on SATOM", { class: "btn btn-sm btn-fw-primary", id: "pv-run" }, "bi-play-fill");
    run.addEventListener("click", async () => {
      clear(probs);
      say(msg, "Saving and rendering on the target…", "busy");
      // A save re-renders this tab: look the elements up again afterwards.
      const saved = await ctx.ensureSaved();
      const m = document.getElementById("pv-status") || msg;
      const fr = document.getElementById("pv-frame") || frame;
      const pr = document.getElementById("pv-problems") || probs;
      if (!saved) { say(m, "Not rendered: the project could not be saved.", "bad"); return; }
      say(m, "Rendering on the target…", "busy");
      try {
        const r = await ctx.api.rehearse(ctx.project.id, Object.assign({}, S.values), "yes");
        (r.problems || []).forEach((p) => pr.appendChild(h("li", { class: "ed-tone-bad", text: (p.where ? p.where + ": " : "") + (p.message || p) })));
        if (r.ok && typeof r.rendered === "string") {
          fr.srcdoc = r.rendered;
          say(m, "Rendered with the target's datasets, cut to your token's access.", "ok");
        } else say(m, r.error || "The target did not render the plugin.", "bad");
      } catch (e) { say(m, e.message, "bad"); }
    });
    const local = button("Render with test data", { class: "btn btn-sm btn-fw-outline", id: "pv-local" }, "bi-database-check");
    local.addEventListener("click", () => ctx.activate("testrun"));
    panel.appendChild(card("Preview", [
      h("p", { class: "ed-help", text: "Saves, then asks the target SATOM to render the plugin (Rehearse) with the target's datasets and shows the HTML in a sandboxed frame (scripts only: no same-origin access, no forms, no popups). To render with your own test data, here on the Builder, use Test run." }),
      defs.length ? bar : null, h("div", { class: "ed-inline" }, run, local, msg), probs, frame], { icon: "bi-eye" }));
  },
  reveal() {},
  run() { const b = document.getElementById("pv-run"); if (b) b.click(); },
};
