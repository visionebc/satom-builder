/* Scripts tab: app.json `scripts` (contract section 7)
 * [{slug, name, description, files: {"main.py": src, ...},
 *   settings: {fields, timeout, audience, connections, entry}}].
 * A script runs in SATOM's jail with network "none" and no credentials:
 * this tab offers neither. The SDK card lists the only names a script may
 * use. The source checks here are hints; the rules pack checks the AST. */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash, say } from "../ui.js";
import { ensure, appBody, list } from "../model.js";
import { moveItem, uniqueName, pattern } from "../util.js";
import { CodeEditor } from "../codeedit.js";
import { computeChoices } from "../choices.js";

const S = { sel: 0, file: "main.py", editor: null };
const STARTER = 'from satom_script import inputs, result, log\n\nname = inputs.get("name") or "world"\nlog("hello, %s" % name)\nresult({"greeting": "hello, %s" % name})\n';

/* Source hints mirroring contract section 7 (the AST check is the rules pack's). */
export function sourceHints(path, src) {
  const out = [];
  const add = (i, m) => out.push({ line: i + 1, message: `${path}:${i + 1}: ${m}` });
  String(src || "").split("\n").forEach((line, i) => {
    const code = line.replace(/#.*$/, "");
    const imp = /^\s*from\s+satom_(?:script|sdk)\s+import\s+(.+)$/.exec(code);
    const names = imp ? imp[1].replace(/[()]/g, "").split(",").map((x) => x.trim().split(/\s+as\s+/)[0]) : [];
    if (names.includes("secret") || /\bsecret\s*\(/.test(code)) add(i, "secret() is not available to packaged scripts");
    if (names.includes("tcp") || /\btcp\s*\(/.test(code)) add(i, "tcp() is not available to packaged scripts");
    if (/\bwrite\s*=\s*True\b/.test(code)) add(i, "write=True is refused (external databases are read-only)");
    if (/^\s*(?:import|from)\s+(?:socket|requests|urllib|http\.client|subprocess)\b/.test(code)) add(i, 'the script has no network: use connection("slug")');
  });
  return out;
}

export default {
  id: "scripts", label: "Scripts", icon: "bi-filetype-py",

  render(ctx, panel) {
    if (S.editor) S.editor = null;
    clear(panel);
    const scripts = list(appBody(ctx.project), "scripts");
    if (S.sel >= scripts.length) S.sel = Math.max(0, scripts.length - 1);
    const add = button("Add script", { class: "btn btn-sm btn-fw-primary", id: "sc-add" }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const l = ensure(ctx.project.app = ctx.project.app || {}, "scripts", () => []);
      const slug = uniqueName("script-" + (l.length + 1), (x) => l.some((s) => s.slug === x));
      l.push({ slug, name: "New script", description: "", files: { "main.py": (ctx.schema && ctx.schema.script && ctx.schema.script.starter) || STARTER },
        settings: { fields: [], timeout: (ctx.schema && ctx.schema.script && ctx.schema.script.default_timeout) || 60, audience: "admins", connections: [], entry: "main.py" } });
      S.sel = l.length - 1;
      S.file = "main.py";
      ctx.changed({ rerender: true, focus: "sc-slug" });
    });
    const nav = h("nav", { class: "ed-subnav", "aria-label": "Scripts" });
    scripts.forEach((s, i) => {
      const b = h("button", { type: "button", class: "ed-subnav-item" + (i === S.sel ? " is-selected" : ""), id: `sc-pick-${i}`, "aria-current": i === S.sel ? "true" : null },
        h("strong", { text: s.name || s.slug }), h("code", { text: s.slug }), ctx.issuesFor("scripts", s.slug).length ? badge("problems", "danger") : null);
      b.addEventListener("click", () => { S.sel = i; S.file = "main.py"; this.render(ctx, panel); });
      nav.appendChild(b);
    });
    if (!scripts.length) nav.appendChild(empty("No script. A diagram's Run a script step can also run a script the target already has."));
    const main = scripts.length ? scriptEditor(ctx, scripts[S.sel], S.sel, scripts, () => this.render(ctx, panel)) : null;
    panel.appendChild(h("div", { class: "ed-scripts" },
      h("div", { class: "ed-scripts-main" }, card("Scripts", [h("div", { class: "ed-scripts-cols" }, nav, main || h("div"))], { icon: "bi-filetype-py", tools: add })),
      sdkCard()));
  },

  reveal(ctx, t) {
    const scripts = list(appBody(ctx.project), "scripts");
    const i = scripts.findIndex((s) => s.slug === t.id);
    if (i < 0) return;
    S.sel = i;
    if (t.file) S.file = t.file;
    const panel = document.getElementById("panel-scripts");
    this.render(ctx, panel);
    if (t.line && S.editor) S.editor.gotoLine(t.line);
    else if (t.sub === "settings") flash(document.getElementById("sc-settings"));
  },
};

function scriptEditor(ctx, sc, i, scripts, rerender) {
  const schema = ctx.schema;
  const sch = (schema && schema.script) || {};
  const issues = ctx.issuesFor("scripts", sc.slug).slice();
  if (!pattern(schema, "connection_slug").test(String(sc.slug || ""))) issues.push("slug: 2-48 lowercase letters, digits, - or _");
  if (scripts.some((o, j) => j !== i && o.slug === sc.slug)) issues.push("slug is used twice");
  const settings = sc.settings && typeof sc.settings === "object" ? sc.settings : {};
  const setS = (k, v) => { sc.settings = settings; settings[k] = v; ctx.changed(); };
  if (settings.network !== undefined && settings.network !== "none") issues.push(`network ${JSON.stringify(settings.network)}: a packaged script has no network (always none)`);
  if (Array.isArray(settings.credentials) && settings.credentials.length) issues.push("credentials: a packaged script declares none (secrets live in SATOM)");
  const files = sc.files && typeof sc.files === "object" ? sc.files : {};
  const names = Object.keys(files);
  if (!names.includes("main.py")) issues.push("main.py is required: it is what runs");
  if (!names.includes(S.file)) S.file = names.includes("main.py") ? "main.py" : names[0];

  const txt = (k, label, o) => {
    const el = input(sc[k], Object.assign({ id: `sc-${k}` }, (o && o.attrs) || {}));
    el.addEventListener("input", () => { sc[k] = el.value; ctx.changed(); });
    if (o && o.rerender) el.addEventListener("change", rerender);
    return field(label, el, o);
  };
  const strip = (issues.some((m) => /^network|^credentials/.test(m))) ? button("Remove network / credentials", { class: "btn btn-sm btn-fw-danger", id: "sc-strip" }, "bi-shield-x") : null;
  if (strip) strip.addEventListener("click", () => { delete settings.network; delete settings.credentials; ctx.changed(); rerender(); });

  // files
  const fileTabs = h("div", { class: "ed-filetabs", role: "tablist", "aria-label": "Script files" });
  names.forEach((n) => {
    const b = h("button", { type: "button", role: "tab", class: "ed-filetab" + (n === S.file ? " is-selected" : ""), "aria-selected": String(n === S.file), id: `sc-file-${n.replace(/[^a-z0-9]/gi, "_")}`, text: n });
    b.addEventListener("click", () => { S.file = n; rerender(); });
    fileTabs.appendChild(b);
  });
  const newName = input("", { id: "sc-newfile", class: "form-control form-control-sm ed-mono", placeholder: "lib/helpers.py" });
  const fileMsg = h("div", { class: "ed-status", "aria-live": "polite" });
  const addFile = button("Add file", { id: "sc-addfile" }, "bi-file-earmark-plus");
  addFile.addEventListener("click", () => {
    const n = newName.value.trim();
    const bad = badPath(n, sch, names);
    if (bad) { say(fileMsg, bad, "bad"); return; }
    sc.files = files;
    files[n] = "";
    S.file = n;
    ctx.changed();
    rerender();
  });
  const rmFile = S.file && S.file !== "main.py" ? button(`Remove ${S.file}`, { class: "btn btn-sm btn-fw-outline ed-danger", id: "sc-rmfile" }, "bi-trash") : null;
  if (rmFile) rmFile.addEventListener("click", () => { delete files[S.file]; S.file = "main.py"; ctx.changed(); rerender(); });
  let editor = null;
  const lintBox = h("ul", { class: "ed-issue-list", id: "sc-lint" });
  if (S.file !== undefined) {
    editor = new CodeEditor({ id: "sc-code", label: S.file, value: files[S.file], lang: S.file.endsWith(".py") ? "python" : "", rows: 20,
      onChange: (v) => { sc.files = files; files[S.file] = v; ctx.changed(); lint(); } });
    S.editor = editor;
  }
  const lint = () => {
    if (!editor) return;
    const local = S.file.endsWith(".py") ? sourceHints(S.file, editor.value) : [];
    const fromCheck = ctx.issues().filter((x) => x.target.tab === "scripts" && x.target.id === sc.slug && (x.target.file || "main.py") === S.file && x.target.line);
    editor.setMarkers(local.map((x) => ({ line: x.line, message: x.message, tone: "warn" })).concat(fromCheck.map((x) => ({ line: x.target.line, message: x.message, tone: x.tone === "bad" ? "bad" : "warn" }))));
    clear(lintBox);
    fromCheck.concat(local.map((x) => ({ message: x.message, target: { line: x.line }, tone: "hint" }))).forEach((x) => {
      const b = h("button", { type: "button", class: "ed-issue-link ed-tone-" + x.tone, text: x.message });
      b.addEventListener("click", () => editor.gotoLine(x.target.line));
      lintBox.appendChild(h("li", null, b));
    });
  };
  lint();

  // settings: fields, timeout, audience, connections
  const fieldsBox = scriptFields(ctx, sc, settings, setS, sch, rerender);
  const maxT = sch.max_timeout || 900, minT = sch.min_timeout || 5;
  const to = input(settings.timeout === undefined ? (sch.default_timeout || 60) : settings.timeout, { id: "sc-timeout", type: "number", min: minT, max: maxT });
  const toMsg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
  to.addEventListener("input", () => {
    const v = Number(to.value);
    toMsg.textContent = to.value !== "" && (v < minT || v > maxT) ? `Between ${minT} and ${maxT} seconds.` : "";
    setS("timeout", to.value === "" ? "" : v);
  });
  const aud = select(sch.audiences || ["admins", "operators"], settings.audience || "admins", { id: "sc-audience" });
  aud.addEventListener("change", () => setS("audience", aud.value));
  const ch = computeChoices(schema, appBody(ctx.project), ctx.snapshot);
  const declared = Array.isArray(settings.connections) ? settings.connections : [];
  const connBox = h("fieldset", { class: "ed-fieldset" }, h("legend", { class: "ed-label", text: "Connections the script may call" }));
  const allConns = Array.from(new Set(ch.http.concat(declared)));
  if (!allConns.length) connBox.appendChild(h("div", { class: "ed-help", text: "No HTTP connection in the package or on the target." }));
  allConns.forEach((c, j) => {
    const where = ch.pkg.http.includes(c) ? "this package" : ch.server.http.includes(c) ? "target" : "unknown: not in the package nor on the target";
    const cb = checkbox(`${c} (${where})`, declared.includes(c), { id: `sc-conn-${j}` });
    cb.querySelector("input").addEventListener("change", (e) => {
      const l = declared.slice();
      if (e.target.checked && !l.includes(c)) l.push(c);
      if (!e.target.checked) l.splice(l.indexOf(c), 1);
      setS("connections", l);
      rerender();
    });
    connBox.appendChild(cb);
  });
  const settingsCard = h("section", { class: "ed-settings", id: "sc-settings", "aria-label": "Script settings" },
    h("h4", { class: "ed-sub", text: "Settings" }),
    h("div", { class: "ed-grid ed-grid-3" }, field("Timeout (s)", to, { help: `${minT} to ${maxT}` }), field("Audience", aud), field("Entry", input(settings.entry || "main.py", { id: "sc-entry", disabled: true, class: "form-control form-control-sm ed-mono" }), { help: "Always main.py." })),
    toMsg,
    h("div", { class: "ed-grid ed-grid-2" }, connBox,
      h("div", { class: "fw-alert fw-alert-info", role: "note" }, h("strong", { text: "Network: none. Credentials: none. " }), "A packaged script reaches the outside only through the connections it declares, with the secrets SATOM keeps.")),
    h("h4", { class: "ed-sub", text: "Form fields (inputs)" }), fieldsBox);

  const rm = button("Remove script", { class: "btn btn-sm btn-fw-outline ed-danger", id: "sc-remove" }, "bi-trash");
  rm.addEventListener("click", () => { scripts.splice(i, 1); S.sel = 0; ctx.changed(); rerender(); });

  return h("div", { class: "ed-script" + (issues.length ? " ed-item-bad" : "") },
    issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m })), strip) : null,
    h("div", { class: "ed-grid ed-grid-3" }, txt("slug", "Slug", { required: true, rerender: true, attrs: { class: "form-control form-control-sm ed-mono" } }), txt("name", "Name", { required: true }), txt("description", "Description")),
    h("h4", { class: "ed-sub", text: "Files" }), fileTabs,
    editor ? editor.el : empty("No file."),
    lintBox,
    h("div", { class: "ed-inline" }, h("label", { class: "visually-hidden", for: "sc-newfile", text: "New file name" }), newName, addFile, rmFile), fileMsg,
    settingsCard,
    h("div", { class: "ed-item-foot" }, rm));
}

function badPath(n, sch, names) {
  if (!n) return "Type a file name, e.g. lib/helpers.py.";
  const suffixes = sch.text_suffixes || [".py", ".txt", ".json", ".csv", ".yaml", ".yml", ".md", ".cfg", ".ini", ".j2", ".tmpl", ".xml", ".sql"];
  const reserved = sch.reserved_files || ["satom_script.py", "satom_sdk.py", "_satom_boot.py"];
  if (names.includes(n)) return `${n} exists.`;
  if (n.startsWith("/") || n.includes("//") || n.includes("\\")) return "A relative path inside the script, e.g. lib/x.py.";
  const segs = n.split("/");
  if (segs.length > 4) return "At most 4 levels deep.";
  if (segs.some((s) => s === "." || s === ".." || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$/.test(s))) return "Letters, digits, _ . - in each part.";
  if (!suffixes.some((x) => n.toLowerCase().endsWith(x))) return `Only text files are kept (${suffixes.join(", ")}).`;
  if (reserved.includes(n) || n.startsWith(".")) return `${n} is reserved by SATOM.`;
  return "";
}

function scriptFields(ctx, sc, settings, setS, sch, rerender) {
  const types = sch.field_types || ["text", "textarea", "number", "select", "checkbox", "password"];
  const fields = Array.isArray(settings.fields) ? settings.fields : [];
  const box = h("div", { class: "ed-list" });
  if (!fields.length) box.appendChild(h("div", { class: "ed-help", text: "No inputs. The script reads them as inputs[\"name\"]." }));
  fields.forEach((f, j) => {
    const pre = `sc-fld-${j}`;
    const name = input(f.name, { id: `${pre}-name`, class: "form-control form-control-sm ed-mono" });
    name.addEventListener("input", () => { f.name = name.value; ctx.changed(); });
    const label = input(f.label, { id: `${pre}-label` });
    label.addEventListener("input", () => { f.label = label.value; ctx.changed(); });
    const type = select(types, f.type || "text", { id: `${pre}-type` });
    type.addEventListener("change", () => { f.type = type.value; if (f.type === "select" && !Array.isArray(f.options)) f.options = []; ctx.changed(); rerender(); });
    const req = checkbox("Required", !!f.required, { id: `${pre}-req` });
    req.querySelector("input").addEventListener("change", (e) => { f.required = e.target.checked; ctx.changed(); });
    const extra = [];
    if (f.type === "select") {
      const o = textarea((Array.isArray(f.options) ? f.options : String(f.options || "").split("\n")).join("\n"), { id: `${pre}-options`, rows: 3 });
      o.addEventListener("input", () => { f.options = o.value.split("\n").map((x) => x.trim()).filter(Boolean); ctx.changed(); });
      extra.push(field("Options (one per line)", o));
    }
    if (f.type !== "password") {
      const d = input(f.default === null || f.default === undefined ? "" : f.default, { id: `${pre}-default` });
      d.addEventListener("input", () => { f.default = d.value === "" ? null : d.value; ctx.changed(); });
      extra.push(field("Default", d));
    }
    box.appendChild(h("div", { class: "ed-item" },
      h("div", { class: "ed-grid ed-grid-4" }, field("Name", name), field("Label", label), field("Type", type), h("div", { class: "ed-field ed-field-end" }, req,
        h("span", { class: "ed-row-tools" },
          iconButton("bi-arrow-up", `Move input ${f.name} up`, { disabled: j === 0, on: { click: () => { moveItem(fields, j, -1); ctx.changed(); rerender(); } } }),
          iconButton("bi-arrow-down", `Move input ${f.name} down`, { disabled: j === fields.length - 1, on: { click: () => { moveItem(fields, j, 1); ctx.changed(); rerender(); } } }),
          iconButton("bi-trash", `Remove input ${f.name}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { fields.splice(j, 1); ctx.changed(); rerender(); } } })))),
      extra.length ? h("div", { class: "ed-grid ed-grid-2" }, extra) : null));
  });
  const max = (ctx.schema && ctx.schema.limits && ctx.schema.limits.script_fields) || 40;
  const add = button("Add input", { id: "sc-fld-add", disabled: fields.length >= max }, "bi-plus-lg");
  add.addEventListener("click", () => {
    const l = fields.slice ? fields : [];
    settings.fields = l;
    sc.settings = settings;
    l.push({ name: uniqueName("input_" + (l.length + 1), (x) => l.some((f) => f.name === x), "_"), type: "text", label: "", required: false, help: "", default: null });
    ctx.changed();
    rerender();
  });
  box.appendChild(add);
  return box;
}

function sdkCard() {
  const ex = (title, code) => h("div", { class: "ed-sdk-ex" }, h("div", { class: "ed-label", text: title }), h("pre", { class: "ed-pre ed-mono", text: code }));
  return h("aside", { class: "fw-card ed-card ed-sdk", "aria-label": "Script SDK", id: "sc-sdk" },
    h("div", { class: "fw-card-header" }, h("h3", { class: "fw-card-title ed-card-title", text: "Script SDK" })),
    h("div", { class: "fw-card-body" },
      h("p", { class: "ed-help", text: "A packaged script may use these four names from satom_script, and nothing else: no network, no secret(), no tcp(), no writes to external databases." }),
      h("dl", { class: "ed-sdk-list" },
        h("dt", null, h("code", { text: "inputs" })), h("dd", { text: "The form values, by field name (a dict)." }),
        h("dt", null, h("code", { text: "result(value)" })), h("dd", { text: "What the step returns to the diagram (JSON-able)." }),
        h("dt", null, h("code", { text: "log(text)" })), h("dd", { text: "A line in the run log." }),
        h("dt", null, h("code", { text: 'connection("slug")' })), h("dd", { text: "A declared HTTP connection; SATOM adds the secret." })),
      ex("Read inputs, return a result", 'from satom_script import inputs, result, log\n\nrows = inputs.get("rows") or []\nlog("%d rows" % len(rows))\nresult({"count": len(rows)})'),
      ex("Call a declared connection", 'from satom_script import connection, result\n\ncrm = connection("crm")      # listed under Connections\nr = crm.get("/tickets", params={"open": 1})\nresult(r.json())'),
      ex("Call an API Catalog endpoint by id", 'from satom_script import connection, result\n\ninv = connection("inv")      # pinned to an API version\nresult(inv.op("getHost", {"id": 7}).json())'),
      h("p", { class: "ed-help", text: "Source: SATOM app/services/script_sdk.py (satom_script)." })));
}
