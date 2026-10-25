/* Test run tab: the saved project run by SATOM's own runtime on the test
 * data (Test data tab), on this Builder, with no network and nothing sent
 * to SATOM. A view renders in a sandboxed frame; an app's diagram walks
 * armed against the test data (store writes change only a copy, which you
 * may keep); a script runs in SATOM's jail. Every run is stored: the history
 * lists the last 50, and a run started here goes on if the page is closed. */
import { h, clear, field, input, textarea, select, checkbox, button, badge, card, empty, say } from "../ui.js";
import * as M from "../model.js";
import * as F from "../fields.js";
import diagramTab from "./diagram.js";
import { ensureInfo, S as TD } from "./testdata.js";

const S = { values: {}, script: "", scriptValues: {}, current: null, timer: null, runs: null };
const TONE = { done: "success", ok: "success", failed: "danger", timeout: "danger", waiting: "warning", running: "info" };

function formFields(fields, values, prefix) {
  return fields.map((f, i) => {
    const id = `${prefix}-${i}`;
    const kind = F.inputKind(f);
    const cur = values[f.name] !== undefined ? values[f.name] : (f.type === "password" ? "" : (f.default === undefined ? "" : f.default));
    if (kind === "checkbox") {
      const c = checkbox(f.label || f.name, !!cur, { id });
      c.querySelector("input").addEventListener("change", (e) => { values[f.name] = e.target.checked; });
      return c;
    }
    let el;
    if (kind === "select") el = select(F.optionList(f), cur, { id }, "—");
    else if (kind === "textarea") el = textarea(cur, { id, rows: 3 });
    else el = input(cur, { id, type: kind === "password" ? "password" : kind, autocomplete: kind === "password" ? "off" : null });
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => { values[f.name] = el.value; });
    return field(f.label || f.name, el, { required: !!f.required, help: f.type === "satom_select" || f.type === "store_select" ? "Answered from the test data." : f.help });
  });
}

function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ""; }

function showResult(ctx, box, run) {
  clear(box);
  const res = run.result || {};
  const sum = run.summary || {};
  const status = run.status === "running" ? "running" : (res.status || sum.status || run.status);
  box.appendChild(h("div", { class: "ed-inline", id: "tr-verdict" }, badge(status, TONE[status] || "secondary"),
    h("span", { class: "ed-help", text: `${run.kind}${run.subject ? " " + run.subject : ""} · ${when(run.created_at)}` }),
    (res.error || sum.error) ? h("span", { class: "ed-tone-bad", text: res.error || sum.error }) : null));
  if (run.status === "running") { box.appendChild(h("p", { class: "ed-help", text: "Running on the test data… this keeps going if you close the page; the history shows it." })); return; }
  (res.problems || []).forEach((p) => box.appendChild(h("div", { class: "ed-tone-bad", text: (p.where ? p.where + ": " : "") + (p.message || p) })));
  if (run.kind === "view") {
    const frame = h("iframe", { id: "tr-frame", class: "ed-preview", sandbox: "allow-scripts", title: "Test render (sandboxed)", referrerpolicy: "no-referrer" });
    frame.srcdoc = res.rendered || "";
    box.appendChild(h("p", { class: "ed-help", text: "Rows per dataset: " + Object.entries(res.datasets || {}).map(([k, n]) => `${k} ${n}`).join(", ") }));
    box.appendChild(frame);
    return;
  }
  if (run.kind === "script") {
    box.appendChild(h("dl", { class: "ed-sum" },
      h("div", { class: "ed-sum-row" }, h("dt", { text: "Exit code" }), h("dd", { text: String(res.exit_code === undefined || res.exit_code === null ? "—" : res.exit_code) })),
      h("div", { class: "ed-sum-row" }, h("dt", { text: "Duration" }), h("dd", { text: res.duration_ms !== undefined && res.duration_ms !== null ? res.duration_ms + " ms" : "—" }))));
    box.appendChild(h("strong", { text: "result()" }));
    box.appendChild(h("pre", { class: "ed-pre ed-mono", id: "tr-result", text: res.result === undefined || res.result === null ? "(none)" : JSON.stringify(res.result, null, 2) }));
    box.appendChild(h("strong", { text: "Log" }));
    box.appendChild(h("pre", { class: "ed-pre ed-mono ed-scroll", id: "tr-output", text: res.output || "(empty)" }));
    if ((res.connections_log || []).length) box.appendChild(h("details", null, h("summary", { text: "Connection calls" }), h("pre", { class: "ed-pre ed-mono", text: res.connections_log.join("\n") })));
    return;
  }
  if (res.waiting) box.appendChild(h("div", { class: "fw-alert fw-alert-warning" }, h("strong", { text: "Waiting for an answer: " }), String(res.waiting.prompt || res.waiting.key)));
  const steps = res.steps || [];
  if (steps.length) {
    const tb = h("tbody");
    steps.forEach((s) => {
      const b = h("button", { type: "button", class: "ed-link", text: s.label || s.key });
      b.addEventListener("click", () => ctx.goto(`app.graph.nodes[${s.key}]`));
      tb.appendChild(h("tr", null, h("td", null, b), h("td", { text: s.kind || "" }), h("td", null, badge(s.outcome || "", { ok: "success", yes: "success", error: "danger", no: "warning", waiting: "warning" }[s.outcome] || "secondary")), h("td", { class: "ed-pre", text: s.detail || "" })));
    });
    box.appendChild(h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table", id: "tr-steps" }, h("thead", null, h("tr", null, ["Step", "Kind", "Outcome", "Detail"].map((t) => h("th", { scope: "col", text: t })))), tb)));
    diagramTab.overlay(steps);
  }
  (res.outputs || []).forEach((o) => box.appendChild(h("div", { class: "ed-output" }, h("strong", { text: o.title || o.key || "Output" }), h("pre", { class: "ed-pre ed-mono", text: typeof o.value === "string" ? o.value : JSON.stringify(o.value, null, 2) }))));
  const changes = res.changes || {};
  const slugs = Object.keys(changes);
  if (slugs.length) {
    const list = h("ul", { id: "tr-changes" }, slugs.map((s) => h("li", null, h("code", { text: s }), `: ${changes[s].inserted.length} inserted, ${changes[s].updated.length} updated, ${changes[s].deleted.length} deleted`)));
    const keep = button("Keep these changes in the test data", { class: "btn btn-sm btn-fw-outline", id: "tr-keep" }, "bi-check2-all");
    const st = h("span", { class: "ed-status", "aria-live": "polite" });
    keep.addEventListener("click", async () => {
      try { const j = await ctx.api.keepRun(run.id); TD.data = j.data; TD.dirty = false; say(st, "Kept: " + j.stores.join(", "), "ok"); keep.disabled = true; } catch (e) { say(st, e.message, "bad"); }
    });
    box.appendChild(card("Data store writes (on a copy of the test data)", [list, h("div", { class: "ed-inline" }, keep, st)], { icon: "bi-database" }));
  }
  (res.scripts || []).forEach((s) => box.appendChild(h("details", { class: "ed-output" }, h("summary", null, "Script ", h("code", { text: s.slug }), " ", badge(s.status || "", TONE[s.status] || "secondary")),
    h("pre", { class: "ed-pre ed-mono", text: (s.output || "") + (s.result !== undefined && s.result !== null ? "\nresult: " + JSON.stringify(s.result, null, 2) : "") + (s.error ? "\nerror: " + s.error : "") }))));
  if ((res.log || []).length) box.appendChild(h("details", null, h("summary", { text: `What the test data answered (${res.log.length})` }), h("pre", { class: "ed-pre ed-mono", id: "tr-log", text: res.log.join("\n") })));
}

async function poll(ctx, rid) {
  clearTimeout(S.timer);
  let run;
  try { run = await ctx.api.testRunGet(rid); } catch (e) { say(document.getElementById("tr-status"), e.message, "bad"); return; }
  S.current = run;
  const box = document.getElementById("tr-out");
  if (box) showResult(ctx, box, run);
  if (run.status === "running") { S.timer = setTimeout(() => poll(ctx, rid), 1000); return; }
  say(document.getElementById("tr-status"), run.status === "done" && (run.summary || {}).ok ? "Done." : "Finished: see the result.", (run.summary || {}).ok ? "ok" : "info");
  loadHistory(ctx);
}

async function start(ctx, body) {
  const st = document.getElementById("tr-status");
  say(st, "Saving and starting the test run…", "busy");
  if (!(await ctx.ensureSaved())) { say(st, "Not run: the project could not be saved.", "bad"); return; }
  try {
    const r = await ctx.api.testRun(ctx.project.id, body);
    try { sessionStorage.setItem(`sb-run-${ctx.project.id}`, r.run_id); } catch (e) { /* private mode */ }
    say(document.getElementById("tr-status"), "Running on the test data…", "busy");
    poll(ctx, r.run_id);
  } catch (e) { say(document.getElementById("tr-status"), e.message, "bad"); }
}

async function loadHistory(ctx) {
  const box = document.getElementById("tr-history");
  if (!box) return;
  try { S.runs = (await ctx.api.testRuns(ctx.project.id)).runs; } catch (e) { clear(box).appendChild(empty("History not loaded: " + e.message)); return; }
  clear(box);
  if (!S.runs.length) { box.appendChild(empty("No test run yet.")); return; }
  const tb = h("tbody");
  S.runs.forEach((r) => {
    const b = h("button", { type: "button", class: "ed-link", text: when(r.created_at) });
    b.addEventListener("click", () => poll(ctx, r.id));
    const st = r.status === "running" ? "running" : ((r.summary || {}).status || r.status);
    tb.appendChild(h("tr", null, h("td", null, b), h("td", { text: r.kind + (r.subject ? " " + r.subject : "") }), h("td", null, badge(st, TONE[st] || "secondary")), h("td", { class: "ed-pre", text: (r.summary || {}).error || "" })));
  });
  box.appendChild(h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" }, h("thead", null, h("tr", null, ["When", "What", "Status", "Error"].map((t) => h("th", { scope: "col", text: t })))), tb)));
}

export default {
  id: "testrun", label: "Test run", icon: "bi-play-btn",

  render(ctx, panel) {
    clear(panel);
    const info = TD.pid === ctx.project.id ? TD.info : null;
    if (!info) {
      panel.appendChild(empty("Loading…"));
      ensureInfo(ctx).then(() => this.render(ctx, panel)).catch((e) => { clear(panel).appendChild(h("div", { class: "fw-alert fw-alert-warning", text: "The test runtime could not be reached: " + e.message })); });
      return;
    }
    if (!info.runtime.ok) panel.appendChild(h("div", { class: "fw-alert fw-alert-warning", role: "note", id: "tr-no-runtime" }, h("strong", { text: "No test runtime. " }), info.runtime.message || ""));
    if (info.runtime.jail_message) panel.appendChild(h("div", { class: "fw-alert fw-alert-info", role: "note", text: info.runtime.jail_message }));
    if (TD.dirty) panel.appendChild(h("div", { class: "fw-alert fw-alert-warning", role: "note", text: "The Test data tab has unsaved changes: the run uses the saved test data." }));
    const out = h("div", { id: "tr-out", "aria-live": "polite" });
    const status = h("div", { class: "ed-status", id: "tr-status", role: "status", "aria-live": "polite" });
    const help = h("p", { class: "ed-help", text: "Runs the SAVED project with SATOM's own runtime (from the target's rules pack) on the test data, here on the Builder: no network, nothing sent to SATOM. When it passes, Validate and Import send it to your test SATOM." });
    if (ctx.kind !== "app") {
      const defs = Array.isArray(ctx.project.params) ? ctx.project.params : [];
      const fields = defs.map((p) => ({ name: p.name, label: p.label || p.name, type: p.type === "select" ? "select" : p.type === "number" ? "number" : p.type === "date" ? "date" : "text", options: (p.options || []).map((o) => (typeof o === "object" ? o.value : o)), default: p.default }));
      const run = button("Render with test data", { class: "btn btn-sm btn-fw-primary", id: "tr-run", disabled: !info.runtime.ok }, "bi-play-fill");
      run.addEventListener("click", () => start(ctx, { kind: "view", inputs: Object.assign({}, S.values) }));
      panel.appendChild(card("Test run", [help, fields.length ? h("div", { class: "ed-grid ed-grid-3" }, formFields(fields, S.values, "tr-p")) : null, h("div", { class: "ed-inline" }, run, status), out], { icon: "bi-play-btn" }));
    } else {
      const fields = M.list(M.appSpec(ctx.project), "fields");
      const answers = select([{ value: "yes", label: "Yes" }, { value: "no", label: "No" }, { value: "stop", label: "Stop there (waiting)" }], "yes", { id: "tr-answers" });
      const run = button("Run the app on test data", { class: "btn btn-sm btn-fw-primary", id: "tr-run", disabled: !info.runtime.ok }, "bi-play-fill");
      run.addEventListener("click", () => start(ctx, { kind: "app", inputs: F.rehearseInputs(fields, S.values), answers: answers.value }));
      panel.appendChild(card("Run the app", [help, fields.length ? h("div", { class: "ed-grid ed-grid-2" }, formFields(fields, S.values, "tr-f")) : h("p", { class: "ed-help", text: "The app has no form." }),
        h("div", { class: "ed-inline" }, field('Answer "Ask a person" with', answers), run), h("p", { class: "ed-help", text: "The diagram walks ARMED here, because everything it touches is test data: store writes change a copy you may keep, connections answer the Test data responses, SATOM and device steps answer what the Test data tab says, scripts run in SATOM's jail." })], { icon: "bi-diagram-3" }));
      const scripts = M.list(M.appBody(ctx.project), "scripts");
      if (scripts.length) {
        if (!scripts.some((s) => s.slug === S.script)) S.script = scripts[0].slug;
        const sc = scripts.find((s) => s.slug === S.script);
        const pick = select(scripts.map((s) => ({ value: s.slug, label: `${s.name || s.slug} (${s.slug})` })), S.script, { id: "tr-script" });
        pick.addEventListener("change", () => { S.script = pick.value; S.scriptValues = {}; this.render(ctx, panel); });
        const sfields = ((sc && sc.settings) || {}).fields || [];
        const go = button("Run the script on test data", { class: "btn btn-sm btn-fw-outline", id: "tr-run-script", disabled: !info.runtime.ok || !info.runtime.jail }, "bi-terminal");
        go.addEventListener("click", () => start(ctx, { kind: "script", script: S.script, inputs: F.rehearseInputs(sfields, S.scriptValues) }));
        panel.appendChild(card("Run a script", [field("Script", pick), sfields.length ? h("div", { class: "ed-grid ed-grid-2" }, formFields(sfields, S.scriptValues, "tr-s")) : null, go,
          h("p", { class: "ed-help", text: "In SATOM's jail, with SATOM's SDK: no network, connection() answered by the test data, inputs typed like SATOM types them." })], { icon: "bi-filetype-py" }));
      }
      panel.appendChild(status);
      panel.appendChild(out);
    }
    panel.appendChild(card("History (last 50 runs)", [h("div", { id: "tr-history" }, empty("Loading…"))], { icon: "bi-clock-history" }));
    loadHistory(ctx);
    if (S.current) showResult(ctx, out, S.current);
    else {
      let last = null;
      try { last = sessionStorage.getItem(`sb-run-${ctx.project.id}`); } catch (e) { last = null; }
      if (last) poll(ctx, last);
    }
  },

  /* From the Scripts tab: pick a script and focus its run button. */
  pickScript(slug) { S.script = slug; S.scriptValues = {}; },
  reveal() {},
};
