/* SATOM Builder editor: boot, state, the toolbar actions, the Checks
 * panel, the dialogs (Rehearse, Import, AI) and the local draft.
 * Page: templates/editor/index.html. Backend: contract section 11 only. */
import { Api } from "./api.js";
import { h, clear, field, input, textarea, select, checkbox, button, badge, empty, say, keepFocus, flash } from "./ui.js";
import * as M from "./model.js";
import { clone, deepEqual } from "./util.js";
import { resolveTarget, tabsFor } from "./where.js";
import { computeChoices, libraryPins, hasLibraryOps, setLibraryOps } from "./choices.js";
import { installAdom, badgeOf, adomLabel, GLOBAL } from "./adom.js";
import { renderActivity, liveLine } from "./activity.js";
import * as F from "./fields.js";
import formTab from "./tabs/form.js";
import diagramTab from "./tabs/diagram.js";
import actionsTab from "./tabs/actions.js";
import storesTab from "./tabs/stores.js";
import connectionsTab from "./tabs/connections.js";
import apisTab from "./tabs/apis.js";
import designTab from "./tabs/design.js";
import scriptsTab from "./tabs/scripts.js";
import rawTab from "./tabs/raw.js";
import { files as filesTab, datasets as datasetsTab, params as paramsTab, preview as previewTab } from "./tabs/view.js";
import testdataTab from "./tabs/testdata.js";
import testrunTab from "./tabs/testrun.js";
import databaseTab from "./tabs/database.js";
import chatTab from "./tabs/chat.js";
import automationsTab from "./tabs/automations.js";

const TABS = { form: formTab, diagram: diagramTab, actions: actionsTab, stores: storesTab, connections: connectionsTab, apis: apisTab, design: designTab, scripts: scriptsTab, raw: rawTab, files: filesTab, datasets: datasetsTab, params: paramsTab, preview: previewTab, testdata: testdataTab, database: databaseTab, testrun: testrunTab, chat: chatTab, automations: automationsTab };
const $ = (id) => document.getElementById(id);
const DRAFT_DELAY = 400;

function boot() {
  const el = $("editor-boot");
  if (!el) return;
  const B = JSON.parse(el.textContent);
  const meta = document.querySelector('meta[name="csrf-token"]');
  const ctx = {
    api: new Api((meta && meta.getAttribute("content")) || B.csrf_token || ""),
    project: B.project,
    saved: clone(B.project),
    target: B.target || null,
    kind: B.project.kind,
    schema: null,
    snapshot: null,
    check: null,
    checkSource: "",
    active: null,
    lastRun: null,
  };
  const tabs = tabsFor(ctx.kind).filter((t) => $(`panel-${t}`));
  let draftTimer = null, saving = null;

  /* ── context the tabs use ──────────────────────────────────────────── */
  ctx.choices = () => computeChoices(ctx.schema, M.appBody(ctx.project), ctx.snapshot);
  ctx.issues = () => {
    const c = ctx.check;
    if (!c) return [];
    const one = (p, tone) => ({ where: p.where || "", message: p.message || String(p), tone, target: resolveTarget(p.where || "", p.message || "", ctx.kind) });
    return (c.problems || []).map((p) => one(p, "bad")).concat((c.warnings || []).map((p) => one(p, "warn")));
  };
  ctx.issuesFor = (tab, id) => ctx.issues().filter((x) => x.target.tab === tab && (id === null || id === undefined || x.target.id === id)).map((x) => x.message);
  ctx.say = (text, tone) => say($("ed-status"), text, tone);
  ctx.changed = (opts) => {
    const o = opts || {};
    updateDirty();
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => M.writeDraft(window.localStorage, ctx.project), DRAFT_DELAY);
    if (o.rerender) {
      keepFocus(() => renderActive());
      if (o.focus && $(o.focus)) $(o.focus).focus();
    }
  };
  ctx.ensureSaved = async () => (M.isDirty(ctx.project, ctx.saved) || rawTab.pending() ? save() : true);
  /* The connection's library (contract sections 14, 16, 17): fetched once and
     shared by the header (where the project installs) and the Datasets tab. */
  let libPromise = null;
  ctx.lib = null;
  ctx.library = (refresh) => {
    const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
    if (!tid) return Promise.resolve(null);
    if (!libPromise || refresh) {
      libPromise = ctx.api.library(tid, !!refresh).then((j) => { ctx.lib = j; renderWhere(); return j; });
      libPromise.catch(() => { libPromise = null; });
    }
    return libPromise;
  };
  /* The endpoint lists of the draft's connections pinned to SATOM's API
     library (contract section 19): loaded once each, so a Call an API step
     can pick an endpoint. A build SATOM cannot serve keeps an empty list
     (type the method and the path; the check names a wrong one). */
  const libLoading = new Set();
  ctx.loadLibraryCatalogs = () => {
    const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
    if (!tid || ctx.kind !== "app") return;
    libraryPins(M.appBody(ctx.project)).forEach(({ product, version }) => {
      const key = `${product}@${version}`;
      if (hasLibraryOps(key) || libLoading.has(key)) return;
      libLoading.add(key);
      ctx.api.libraryCatalog(tid, product, version)
        .then((j) => setLibraryOps(key, (j && j.api && j.api.operations) || []))
        .catch(() => setLibraryOps(key, []))
        .finally(() => { libLoading.delete(key); keepFocus(() => renderActive()); });
    });
  };
  /* Where the project installs: computed here from the library the moment a
     dataset is ticked; the server's own answer (every save) when there is no
     library yet. */
  ctx.where = ctx.project.where || null;
  ctx.whereNow = () => (ctx.lib ? installAdom(ctx.kind, ctx.project.datasets || [], ctx.project.adom || "", ctx.lib) : ctx.where);
  function renderWhere() {
    const w = ctx.whereNow();
    const b = $("ed-adom-badge");
    const fieldBox = $("ed-adom-field");
    if (!b) return;
    const bd = badgeOf(w);
    b.hidden = !bd;
    if (bd) { b.className = "fw-badge fw-badge-" + bd.tone; b.textContent = bd.text; b.title = bd.title; }
    if (!fieldBox) return;
    fieldBox.hidden = !w;
    if (!w) return;
    const sel = $("ed-adom");
    const opts = [["", "Auto — from the datasets it reads" + (w.mode === "auto" ? ` (now ${adomLabel(w.shown)})` : "")]]
      .concat((w.can_choose ? w.adoms : []).map((a) => [a, adomLabel(a)]));
    if (sel.options.length !== opts.length || Array.from(sel.options).some((o, i) => o.value !== opts[i][0] || o.text !== opts[i][1])) {
      clear(sel);
      opts.forEach(([v, t]) => sel.appendChild(h("option", { value: v, text: t })));
    }
    sel.value = w.can_choose && (ctx.project.adom || "") && w.adoms.includes(ctx.project.adom) ? ctx.project.adom : "";
    sel.disabled = !w.can_choose;
    const help = clear($("ed-adom-help"));
    help.appendChild(document.createTextNode(w.reason + (w.can_choose ? "" : " Nothing to choose: only a Global token chooses the ADOM.")));
    if (w.shown === GLOBAL && w.mode !== "legacy") help.appendChild(h("span", { text: " In SATOM it is opened from the Global console." }));
    if (w.warning) help.appendChild(h("div", { class: "ed-tone-bad", id: "ed-adom-warning", text: w.warning }));
  }
  const adomSel = $("ed-adom");
  if (adomSel) adomSel.addEventListener("change", () => { ctx.project.adom = adomSel.value; ctx.changed(); renderWhere(); });
  const adomBadge = $("ed-adom-badge");
  const openDetails = () => { const d = $("ed-details"); if (d) { d.open = true; const s2 = $("ed-adom"); if (s2) s2.focus(); } };
  if (adomBadge) {
    adomBadge.addEventListener("click", openDetails);
    adomBadge.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetails(); } });
  }
  ctx.testScript = (slug) => { testrunTab.pickScript(slug); activate("testrun", true); };
  ctx.activate = (id) => activate(id, true);
  /* A project saved elsewhere (a chat draft applied): it is now the saved state. */
  ctx.replaceProject = (p) => {
    ctx.project = p;
    ctx.saved = clone(p);
    M.clearDrafts(window.localStorage, p.id);
    syncDetails();
    updateDirty();
    if (p.last_check) setCheck(checkOf(p.last_check), "Check stored with the project");
  };

  /* ── header ────────────────────────────────────────────────────────── */
  /* The backend stores {source, at, result}; a fresh check() answer is the
     result itself. Read both (lab run 2026-10-25: a passed stored check
     showed as a red "0 problem(s)"). */
  function checkOf(lc) {
    if (!lc) return null;
    return (lc.result && typeof lc.result === "object") ? lc.result : lc;
  }
  function header() {
    const p = ctx.project;
    $("ed-title").textContent = p.name || "(no name)";
    $("ed-slug").textContent = p.slug || "";
    $("ed-version").textContent = p.version ? "v" + p.version : "";
    const lc = checkOf(p.last_check);
    const b = $("ed-check-badge");
    if (!lc) { b.className = "fw-badge fw-badge-secondary"; b.textContent = "Not checked"; }
    else if (lc.ok) { b.className = "fw-badge fw-badge-success"; b.textContent = "Last check passed"; }
    else { b.className = "fw-badge fw-badge-danger"; b.textContent = `Last check: ${(lc.problems || []).length} problem(s)`; }
  }
  function updateDirty() {
    const d = M.isDirty(ctx.project, ctx.saved);
    const el = $("ed-dirty");
    el.hidden = !d;
    $("btn-save").classList.toggle("ed-attention", d);
    header();
    renderWhere();
  }
  [["ed-name", "name"], ["ed-slug-input", "slug"], ["ed-version-input", "version"], ["ed-icon", "icon"], ["ed-description", "description"]].forEach(([id, key]) => {
    const el = $(id);
    if (!el) return;
    el.value = ctx.project[key] === undefined || ctx.project[key] === null ? "" : ctx.project[key];
    el.addEventListener("input", () => { ctx.project[key] = el.value; ctx.changed(); });
  });
  function syncDetails() {
    [["ed-name", "name"], ["ed-slug-input", "slug"], ["ed-version-input", "version"], ["ed-icon", "icon"], ["ed-description", "description"]].forEach(([id, key]) => {
      const el = $(id);
      if (el && document.activeElement !== el) el.value = ctx.project[key] === undefined || ctx.project[key] === null ? "" : ctx.project[key];
    });
  }

  /* ── tabs ──────────────────────────────────────────────────────────── */
  function renderActive() {
    const t = TABS[ctx.active];
    const panel = $(`panel-${ctx.active}`);
    if (!t || !panel) return;
    ctx.loadLibraryCatalogs();
    if (!ctx.schema && ctx.schemaState === "loading" && ctx.active !== "raw" && ctx.active !== "files") {
      clear(panel).appendChild(empty("Loading the target schema…"));
      return;
    }
    t.render(ctx, panel);
  }
  function activate(id, noFocus) {
    if (!tabs.includes(id)) id = tabs[0];
    if (ctx.active === "raw" && id !== "raw" && !rawTab.apply(ctx)) return false;
    ctx.active = id;
    tabs.forEach((t) => {
      const b = $(`tab-${t}`), p = $(`panel-${t}`);
      const on = t === id;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
      b.classList.toggle("is-active", on);
      p.hidden = !on;
    });
    renderActive();
    if (!noFocus) $(`tab-${id}`).focus({ preventScroll: true });
    try { history.replaceState(null, "", "#tab=" + id); } catch (e) { /* file: or sandbox */ }
    return true;
  }
  tabs.forEach((t, i) => {
    const b = $(`tab-${t}`);
    b.addEventListener("click", () => activate(t, true));
    b.addEventListener("keydown", (e) => {
      let j = null;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      if (e.key === "Home") j = 0;
      if (e.key === "End") j = tabs.length - 1;
      if (j !== null) { e.preventDefault(); activate(tabs[j]); }
    });
  });
  ctx.goto = (where, message) => {
    const t = resolveTarget(where, message, ctx.kind);
    if (t.tab === "details") {
      const d = $("ed-details");
      if (d) d.open = true;
      const map = { slug: "ed-slug-input", version: "ed-version-input", icon: "ed-icon", description: "ed-description" };
      const el = $(map[t.id] || "ed-name");
      if (el) { el.focus(); flash(el); }
      return;
    }
    if (!activate(t.tab, true)) return;
    const tab = TABS[t.tab];
    if (tab && tab.reveal) tab.reveal(ctx, Object.assign({ where }, t));
  };

  /* ── checks panel ──────────────────────────────────────────────────── */
  function setCheck(result, source) {
    ctx.check = result;
    ctx.checkSource = source;
    ctx.checkAt = new Date();
    renderChecks();
    if (ctx.active) keepFocus(() => renderActive());
  }
  function renderChecks() {
    const box = clear($("ed-checks-body"));
    const c = ctx.check;
    const count = $("ed-checks-count");
    if (!c) {
      count.textContent = "";
      box.appendChild(empty(ctx.checkSource || "Not checked yet. Save or press Check."));
      return;
    }
    const probs = c.problems || [], warns = c.warnings || [];
    count.textContent = probs.length ? String(probs.length) : "";
    box.appendChild(h("div", { class: "ed-check-src" }, ctx.checkSource, ctx.checkAt ? " · " + ctx.checkAt.toLocaleTimeString() : ""));
    box.appendChild(h("div", { class: "ed-check-verdict" }, c.ok ? badge("No problems", "success") : badge(`${probs.length} problem(s)`, "danger"), " ", warns.length ? badge(`${warns.length} warning(s)`, "warning") : null));
    const item = (p, tone) => {
      const b = h("button", { type: "button", class: "ed-check-item ed-tone-" + tone },
        p.where ? h("code", { class: "ed-where", text: p.where }) : null, h("span", { text: p.message || String(p) }));
      b.addEventListener("click", () => ctx.goto(p.where || "", p.message || ""));
      return h("li", null, b);
    };
    if (probs.length) box.appendChild(h("div", null, h("h4", { class: "ed-sub", text: "Problems" }), h("ul", { class: "ed-check-list" }, probs.map((p) => item(p, "bad")))));
    if (warns.length) box.appendChild(h("div", null, h("h4", { class: "ed-sub", text: "Warnings" }), h("ul", { class: "ed-check-list" }, warns.map((p) => item(p, "warn")))));
    const s = c.summary;
    if (s) {
      const row = (k, v) => (v === undefined || v === null || (Array.isArray(v) && !v.length) || v === "" ? null : h("div", { class: "ed-sum-row" }, h("dt", { text: k }), h("dd", { text: Array.isArray(v) ? v.join(", ") : String(v) })));
      box.appendChild(h("div", null, h("h4", { class: "ed-sub", text: "On import" }), h("dl", { class: "ed-sum" },
        row("Lands in", s.lands_in), row("Signed by", s.signed_by), row("Creates", s.creates), row("Keeps", s.keeps), row("Needs secrets", s.needs_secrets))));
    }
  }
  $("btn-checks-toggle").addEventListener("click", () => {
    const open = !document.body.classList.contains("ed-checks-open");
    document.body.classList.toggle("ed-checks-open", open);
    $("btn-checks-toggle").setAttribute("aria-expanded", String(open));
  });
  $("ed-checks-close").addEventListener("click", () => { document.body.classList.remove("ed-checks-open"); $("btn-checks-toggle").setAttribute("aria-expanded", "false"); $("btn-checks-toggle").focus(); });
  if (window.matchMedia && window.matchMedia("(min-width: 1280px)").matches) {
    document.body.classList.add("ed-checks-open");
    $("btn-checks-toggle").setAttribute("aria-expanded", "true");
  }

  /* ── save / check / validate ───────────────────────────────────────── */
  async function save() {
    if (saving) return saving;
    if (ctx.active === "raw" && !rawTab.apply(ctx)) { ctx.say("Not saved: the Raw JSON is not valid JSON.", "bad"); return false; }
    saving = (async () => {
      const sent = M.savePayload(ctx.project);
      ctx.say("Saving…", "busy");
      $("btn-save").disabled = true;
      try {
        const resp = await ctx.api.save(ctx.project.id, sent);
        const lc = resp.local_check;
        delete resp.local_check;
        if (resp.where !== undefined) ctx.where = resp.where;
        const local = M.savePayload(ctx.project);
        const editedMeanwhile = !deepEqual(local, sent);
        ctx.project = resp;
        ctx.saved = clone(resp);
        if (editedMeanwhile) M.applyPayload(ctx.project, local);
        M.clearDrafts(window.localStorage, ctx.project.id);
        if (editedMeanwhile) M.writeDraft(window.localStorage, ctx.project);
        syncDetails();
        updateDirty();
        if (lc) setCheck(lc, "Local check after save (rules pack)");
        else { if (lc === null) { ctx.check = null; ctx.checkSource = "Saved. No rules pack is loaded for the target, so nothing was checked."; renderChecks(); } keepFocus(() => renderActive()); }
        ctx.say(`Saved at ${new Date().toLocaleTimeString()}.` + (lc && !lc.ok ? ` ${(lc.problems || []).length} problem(s): see Checks.` : ""), lc && !lc.ok ? "info" : "ok");
        return true;
      } catch (e) {
        ctx.say("Not saved: " + e.message, "bad");
        return false;
      } finally {
        $("btn-save").disabled = false;
        saving = null;
      }
    })();
    return saving;
  }
  async function busy(btn, label, fn) {
    const b = $(btn);
    b.disabled = true;
    ctx.say(label, "busy");
    try { await fn(); } catch (e) { ctx.say(e.message, "bad"); } finally { b.disabled = false; }
  }
  $("btn-save").addEventListener("click", () => save());
  $("btn-check").addEventListener("click", () => busy("btn-check", "Checking with the rules pack…", async () => {
    if (!(await ctx.ensureSaved())) return;
    const r = await ctx.api.check(ctx.project.id);
    ctx.project.last_check = r;
    ctx.saved.last_check = r;
    setCheck(r, "Local check (rules pack)");
    header();
    ctx.say(r.ok ? "Check passed." : `Check: ${(r.problems || []).length} problem(s).`, r.ok ? "ok" : "info");
  }));
  $("btn-validate").addEventListener("click", () => busy("btn-validate", "Validating on the target SATOM…", async () => {
    if (!(await ctx.ensureSaved())) return;
    const r = await ctx.api.validateRemote(ctx.project.id);
    setCheck(r, "Validated on the target SATOM");
    ctx.say(r.ok ? "SATOM accepts the package." : `SATOM: ${(r.problems || []).length} problem(s).`, r.ok ? "ok" : "info");
  }));
  $("btn-download").addEventListener("click", () => busy("btn-download", "Building the package…", async () => {
    if (!(await ctx.ensureSaved())) return;
    const a = h("a", { href: ctx.api.packageUrl(ctx.project.id), download: "" });
    document.body.appendChild(a);
    a.click();
    a.remove();
    ctx.say("Download started.", "ok");
  }));

  /* ── rehearse ──────────────────────────────────────────────────────── */
  const dlgR = $("dlg-rehearse");
  const R = { values: {} };
  $("btn-rehearse").addEventListener("click", () => {
    if (ctx.kind !== "app") { activate("preview", true); previewTab.run(); return; }
    openRehearse();
  });
  function openRehearse() {
    const fields = M.list(M.appSpec(ctx.project), "fields");
    const form = clear($("rh-form"));
    if (!fields.length) form.appendChild(h("p", { class: "ed-help", text: "The app has no form." }));
    fields.forEach((f, i) => {
      const id = `rh-${i}`;
      const kindIn = F.inputKind(f);
      const cur = R.values[f.name] !== undefined ? R.values[f.name] : (f.type === "password" ? "" : f.default || "");
      let el;
      if (kindIn === "checkbox") {
        const c = checkbox(f.label || f.name, !!cur, { id });
        c.querySelector("input").addEventListener("change", (e) => { R.values[f.name] = e.target.checked; });
        form.appendChild(c);
        return;
      }
      if (kindIn === "select") el = select(F.optionList(f), cur, { id }, "—");
      else if (kindIn === "textarea") el = textarea(cur, { id, rows: 3 });
      else el = input(cur, { id, type: kindIn === "password" ? "password" : kindIn, autocomplete: kindIn === "password" ? "off" : null });
      el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => { R.values[f.name] = el.value; });
      const help = F.isListType(f.type, ctx.schema) && f.type !== "select" ? "The list is read on SATOM: type the value." : f.help;
      form.appendChild(field(f.label || f.name, el, { required: !!f.required, help }));
    });
    clear($("rh-out"));
    dlgR.showModal();
  }
  $("rh-run").addEventListener("click", async () => {
    const out = clear($("rh-out"));
    const st = h("div", { class: "ed-status", "aria-live": "polite" });
    out.appendChild(st);
    say(st, "Saving and rehearsing on the target…", "busy");
    $("rh-run").disabled = true;
    try {
      if (!(await ctx.ensureSaved())) { say(st, "Not rehearsed: the project could not be saved.", "bad"); return; }
      const fields = M.list(M.appSpec(ctx.project), "fields");
      const r = await ctx.api.rehearse(ctx.project.id, F.rehearseInputs(fields, R.values), $("rh-answers").value);
      // the password values typed for the run are not kept
      fields.forEach((f) => { if (f.type === "password") delete R.values[f.name]; });
      renderRun(out, st, r);
    } catch (e) { say(st, e.message, "bad"); } finally { $("rh-run").disabled = false; }
  });
  function renderRun(out, st, r) {
    const tone = { done: "success", failed: "danger", waiting: "warning" }[r.status] || "secondary";
    say(st, "", "info");
    out.appendChild(h("div", { class: "ed-inline" }, badge(r.status || (r.ok ? "ok" : "not run"), tone), r.error ? h("span", { class: "ed-tone-bad", text: r.error }) : null));
    if ((r.problems || []).length) out.appendChild(h("ul", { class: "ed-issue-list" }, r.problems.map((p) => h("li", { class: "ed-tone-bad", text: (p.where ? p.where + ": " : "") + (p.message || p) }))));
    if (r.waiting) out.appendChild(h("div", { class: "fw-alert fw-alert-warning" }, h("strong", { text: "Waiting for an answer: " }), String(r.waiting.prompt || r.waiting.key || JSON.stringify(r.waiting))));
    const steps = r.steps || [];
    if (steps.length) {
      const tb = h("tbody");
      steps.forEach((s) => {
        const b = h("button", { type: "button", class: "ed-link", text: s.label || s.key });
        b.addEventListener("click", () => { dlgR.close(); ctx.goto(`app.graph.nodes[${s.key}]`); });
        tb.appendChild(h("tr", null, h("td", null, b), h("td", { text: s.kind || "" }), h("td", null, badge(s.outcome || "", { ok: "success", yes: "success", error: "danger", no: "warning", waiting: "warning" }[s.outcome] || "secondary")), h("td", { class: "ed-pre", text: s.detail || "" }), h("td", { text: s.ms !== undefined ? s.ms + " ms" : "" })));
      });
      out.appendChild(h("div", { class: "ed-scroll-x" }, h("table", { class: "fw-table ed-table" }, h("thead", null, h("tr", null, ["Step", "Kind", "Outcome", "Detail", "Time"].map((t) => h("th", { scope: "col", text: t })))), tb)));
      diagramTab.overlay(steps);
      const show = button("Show on the diagram", { id: "rh-show" }, "bi-diagram-3");
      show.addEventListener("click", () => { dlgR.close(); activate("diagram"); });
      out.appendChild(show);
    }
    (r.outputs || []).forEach((o) => out.appendChild(h("div", { class: "ed-output" }, h("strong", { text: o.title || o.key || "Output" }), h("pre", { class: "ed-pre ed-mono", text: typeof o.value === "string" ? o.value : JSON.stringify(o.value, null, 2) }))));
  }
  $("rh-close").addEventListener("click", () => dlgR.close());

  /* ── import ────────────────────────────────────────────────────────── */
  const dlgI = $("dlg-import");
  $("btn-import").addEventListener("click", () => busy("btn-import", "Checking what the import needs…", async () => {
    if (!(await ctx.ensureSaved())) return;
    const r = await ctx.api.check(ctx.project.id);
    setCheck(r, "Local check before import");
    const needs = (r.summary && r.summary.needs_secrets) || [];
    const box = clear($("im-secrets"));
    if (!needs.length) box.appendChild(h("p", { class: "ed-help", text: "No connection the import creates needs a secret." }));
    needs.forEach((slug, i) => {
      const el = input("", { id: `im-secret-${i}`, type: "password", autocomplete: "new-password", "data-slug": slug, spellcheck: "false" });
      box.appendChild(field(`Secret of connection ${slug}`, el, { help: "Sent once to SATOM with this import, stored there (vault or encrypted settings), never kept by the Builder." }));
    });
    const warn = clear($("im-warn"));
    if (!r.ok) warn.appendChild(h("div", { class: "fw-alert fw-alert-warning" }, `The local check found ${(r.problems || []).length} problem(s); SATOM will very likely refuse the import. See Checks.`));
    const caller = ctx.snapshot && ctx.snapshot.caller;
    if (caller && caller.can_import === false) warn.appendChild(h("div", { class: "fw-alert fw-alert-info" }, "Your SATOM token cannot import (it needs the admin scope and a super-admin owner)."));
    if (r.summary) warn.appendChild(h("p", { class: "ed-help", text: `It lands in ${r.summary.lands_in || "draft"}; creates: ${(r.summary.creates || []).join(", ") || "nothing"}; keeps: ${(r.summary.keeps || []).join(", ") || "nothing"}.` }));
    const w = ctx.whereNow();
    const ad = clear($("im-adom"));
    if (w) {
      ad.appendChild(h("strong", { text: `Installs in ${w.mode === "legacy" ? w.shown : adomLabel(w.shown)}. ` }));
      ad.appendChild(document.createTextNode(w.reason + (w.shown === GLOBAL && w.mode !== "legacy" ? " Users who may not enter every ADOM it reads see only their part." : "")));
      if (w.warning) warn.appendChild(h("div", { class: "fw-alert fw-alert-warning", id: "im-adom-warning" }, w.warning));
    }
    clear($("im-out"));
    $("im-downgrade").checked = false;
    ctx.say("", "info");
    dlgI.showModal();
  }));
  $("im-form").addEventListener("submit", (e) => e.preventDefault());
  function clearSecrets() { dlgI.querySelectorAll('input[type="password"]').forEach((x) => { x.value = ""; }); }
  $("im-go").addEventListener("click", async () => {
    const secrets = {};
    dlgI.querySelectorAll("input[data-slug]").forEach((x) => { if (x.value) secrets[x.dataset.slug] = x.value; });
    const out = clear($("im-out"));
    const st = h("div", { class: "ed-status", "aria-live": "polite" });
    out.appendChild(st);
    say(st, "Importing into SATOM…", "busy");
    $("im-go").disabled = true;
    let r;
    try {
      r = await ctx.api.importPackage(ctx.project.id, $("im-downgrade").checked, secrets);
    } catch (e) {
      say(st, e.message, "bad");
      return;
    } finally {
      clearSecrets();
      Object.keys(secrets).forEach((k) => { secrets[k] = ""; });
      $("im-go").disabled = false;
    }
    say(st, r.ok ? `Imported: ${r.kind} ${r.slug} ${r.version}, lands in ${r.lands_in}.` : "SATOM refused the import.", r.ok ? "ok" : "bad");
    const lst = (title, items, cls) => (items && items.length ? h("div", null, h("strong", { text: title }), h("ul", { class: cls || "" }, items.map((x) => h("li", { text: typeof x === "string" ? x : (x.where ? x.where + ": " : "") + (x.message || JSON.stringify(x)) })))) : null);
    [lst("Created", r.created), lst("Kept", r.kept), lst("To do in SATOM", r.todo), lst("Problems", r.problems, "ed-tone-bad")]
      .forEach((x) => { if (x) out.appendChild(x); });
  });
  $("im-close").addEventListener("click", () => { clearSecrets(); dlgI.close(); });
  dlgI.addEventListener("close", clearSecrets);

  /* ── AI: Generate as a conversation ──────────────────────────────────
   * The job runs on the server; this window only follows it. Minimize (or
   * close / Esc while it works) folds it into a pill that stays on every tab
   * and opens it again; on load, a job that still works comes back in the
   * pill. The history is this author's conversations on this project (the
   * server never returns anyone else's); a conversation is continued from
   * the project as saved, with its earlier requests sent along. A draft that
   * arrives while the author changed the editor is never loaded over their
   * changes: it waits in the conversation ("Load this draft"). */
  const dlgA = $("dlg-ai");
  const A = { profiles: null, job: null, timer: null, thread: null, threads: [], minimized: false, done: null, pillText: "" };
  const aiRunning = () => !!A.job;
  const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "");

  async function loadProfiles() {
    if (A.profiles) return;
    const sel = $("ai-profile");
    const st = $("ai-status");
    say(st, "Loading AI profiles…", "busy");
    try {
      A.profiles = await ctx.api.aiProfiles();
      clear(sel);
      if (!A.profiles.length) sel.appendChild(h("option", { value: "", text: "No AI profile: add one under AI profiles" }));
      A.profiles.forEach((p) => sel.appendChild(h("option", { value: p.id, text: `${p.name} (${p.adapter}, ${p.model})` })));
      if (!aiRunning()) say(st, "", "info");
    } catch (e) { A.profiles = null; say(st, "AI profiles could not be loaded: " + e.message, "bad"); }
  }
  function pill() {
    const p = $("ai-pill");
    const show = A.minimized && (aiRunning() || !!A.done);
    p.hidden = !show;
    if (!show) return;
    $("ai-pill-text").textContent = A.pillText || "The AI is working…";
    p.dataset.state = aiRunning() ? "run" : A.done;
  }
  function openAI() {
    if (!dlgA.open) dlgA.showModal();
    A.minimized = false;
    if (!aiRunning()) A.done = null;
    pill();
    loadProfiles();
    loadHistory();
  }
  function minimizeAI() {
    if (dlgA.open) dlgA.close();
    A.minimized = true;
    pill();
  }
  function closeAI() {
    if (aiRunning()) { minimizeAI(); ctx.say("The AI goes on working: it is in the pill at the bottom right.", "info"); return; }
    if (dlgA.open) dlgA.close();
    A.minimized = false;
    A.done = null;
    pill();
  }
  $("btn-ai").addEventListener("click", openAI);
  $("ai-min").addEventListener("click", minimizeAI);
  $("ai-close").addEventListener("click", closeAI);
  dlgA.addEventListener("cancel", (e) => { e.preventDefault(); closeAI(); });
  $("ai-pill").addEventListener("click", openAI);

  async function loadHistory() {
    try { A.threads = (await ctx.api.aiHistory(ctx.project.id)).threads || []; } catch (e) { A.threads = A.threads || []; }
    renderThreads();
    renderThread();
    return A.threads;
  }
  function statusBadge(u) {
    if (u.status === "running") return badge("working…", "info");
    if (u.status === "failed") return badge("failed", "danger");
    if (!u.has_draft) return badge("no draft", "secondary");
    return u.ok ? badge("passes the rules", "success") : badge("problems left", "warning");
  }
  function renderThreads() {
    const box = clear($("ai-threads"));
    if (!A.threads.length) { box.appendChild(h("p", { class: "ed-help", text: "No conversation yet. What you ask is kept here, for you and this project only." })); return; }
    A.threads.forEach((t) => {
      const b = h("button", { type: "button", class: "ed-subnav-item ed-ai-thread-item" + (t.thread === A.thread ? " is-selected" : ""), "aria-current": t.thread === A.thread ? "true" : null, dataset: { thread: t.thread } },
        h("strong", { text: t.title || "(no text)" }),
        h("small", { class: "ed-muted", text: `${t.turns.length} request(s) · ${when(t.updated_at)}` }),
        t.running ? badge("working…", "info") : null);
      b.addEventListener("click", () => { A.thread = t.thread; renderThreads(); renderThread(); });
      box.appendChild(b);
    });
  }
  function renderThread() {
    const t = A.threads.find((x) => x.thread === A.thread);
    const head = clear($("ai-thread-head"));
    const box = clear($("ai-thread"));
    $("ai-generate-text").textContent = t ? "Continue" : "Generate";
    if (!t) { head.appendChild(h("p", { class: "ed-help", text: "New conversation. It stays in the list: come back to it and continue it when you want." })); return; }
    head.appendChild(h("p", { class: "ed-help" }, h("strong", { text: t.title }), " -- continuing: the AI starts from the project as saved and gets these requests and what they came to."));
    t.turns.forEach((u) => {
      box.appendChild(h("div", { class: "ed-msg ed-msg-user" }, h("div", { class: "ed-msg-meta", text: "You · " + when(u.created_at) }), h("div", { class: "ed-msg-text", text: u.prompt })));
      const current = u.job_id === A.job;
      const ai = h("div", { class: "ed-msg ed-msg-ai", dataset: { job: u.job_id } },
        h("div", { class: "ed-msg-meta" }, "AI", u.profile ? " · " + u.profile : "", " ", statusBadge(u)),
        h("div", { class: "ed-msg-text", text: current ? "Working on it: follow it below." : u.outcome }));
      if (!current && u.status !== "running") {
        const act = h("div");
        const d = h("details", { class: "ed-act-wrap" }, h("summary", { text: "What it did" }), act);
        d.addEventListener("toggle", async () => {
          if (!d.open || act.firstChild) return;
          try { const j = await ctx.api.job(u.job_id); renderActivity(act, j.progress, { key: "gen-" + u.job_id, running: false }); } catch (e) { act.appendChild(h("div", { class: "ed-tone-bad", text: e.message })); }
        });
        ai.appendChild(d);
        if (u.has_draft) {
          const b = button("Load this draft into the editor", { class: "btn btn-sm btn-fw-outline ed-ai-load", dataset: { job: u.job_id } }, "bi-box-arrow-in-down");
          b.addEventListener("click", () => loadDraft(u.job_id, false));
          ai.appendChild(h("div", { class: "ed-inline" }, b));
        }
      }
      box.appendChild(ai);
    });
    box.scrollTop = box.scrollHeight;
  }
  $("ai-new").addEventListener("click", () => {
    A.thread = null;
    renderThreads();
    renderThread();
    $("ai-prompt").focus();
  });

  /* A turn's draft into the editor as unsaved changes (the author saves it). */
  async function loadDraft(jobId, auto) {
    const st = $("ai-status");
    let j;
    try { j = await ctx.api.job(jobId); } catch (e) { say(st, e.message, "bad"); return false; }
    if (!j.project) { say(st, "That request left no draft.", "bad"); return false; }
    if (!auto && M.isDirty(ctx.project, ctx.saved) && !window.confirm("Your unsaved changes in the editor will be replaced by this draft. Go on?")) return false;
    ctx.project = j.project;
    syncDetails();
    ctx.changed();
    if (j.project.last_check) setCheck(checkOf(j.project.last_check), "Check of the generated draft");
    else keepFocus(() => renderActive());
    if (!auto) { say(st, "Draft loaded into the editor. Save to keep it.", "ok"); ctx.say("Draft loaded into the editor. Save to keep it.", "ok"); }
    return true;
  }

  $("ai-preview").addEventListener("click", async () => {
    const st = $("ai-status"), out = clear($("ai-sent"));
    if (!$("ai-profile").value) { say(st, "Pick an AI profile.", "bad"); return; }
    say(st, "Saving and assembling the request…", "busy");
    try {
      if (!(await ctx.ensureSaved())) { say(st, "Not saved: nothing assembled.", "bad"); return; }
      const r = await ctx.api.generatePreview(ctx.project.id, $("ai-profile").value, $("ai-prompt").value, A.thread);
      out.appendChild(h("div", { class: "ed-grid ed-grid-2" },
        h("div", null, h("strong", { text: "Sends" }), h("ul", null, (r.sends || []).map((x) => h("li", { text: x })))),
        h("div", null, h("strong", { text: "Never sends" }), h("ul", null, (r.never_sends || []).map((x) => h("li", { text: x }))))));
      out.appendChild(h("p", { class: "ed-help", text: `${r.bytes || 0} bytes in total.` }));
      out.appendChild(h("details", null, h("summary", { text: "System part" }), h("pre", { class: "ed-pre ed-mono ed-scroll", text: r.system || "" })));
      out.appendChild(h("details", null, h("summary", { text: "User part" }), h("pre", { class: "ed-pre ed-mono ed-scroll", text: r.user || "" })));
      say(st, "This is exactly what goes to the provider.", "ok");
    } catch (e) { say(st, e.message, "bad"); }
  });
  $("ai-generate").addEventListener("click", async () => {
    const st = $("ai-status");
    if (aiRunning()) { say(st, "The AI is still working on your last request.", "bad"); return; }
    if (!$("ai-profile").value) { say(st, "Pick an AI profile.", "bad"); return; }
    if (!$("ai-prompt").value.trim()) { say(st, "Describe what you want first.", "bad"); return; }
    try {
      if (!(await ctx.ensureSaved())) { say(st, "Not saved: generation not started.", "bad"); return; }
      const rounds = Math.max(1, Math.min(10, Number($("ai-rounds").value) || 3));
      const r = await ctx.api.generate(ctx.project.id, $("ai-profile").value, $("ai-prompt").value, rounds, A.thread);
      A.job = r.job_id;
      A.thread = r.thread || A.thread;
      A.done = null;
      A.pillText = "";
      $("ai-generate").disabled = true;
      $("ai-prompt").value = "";
      clear($("ai-sent"));
      clear($("ai-rounds-out"));
      say(st, "Started: the steps and what the model writes show below as they happen. You can minimize this window.", "busy");
      renderActivity($("ai-activity"), null, { key: "gen-" + A.job, running: true });
      await loadHistory();
      poll();
    } catch (e) { say(st, e.message, "bad"); }
  });
  async function poll() {
    clearTimeout(A.timer);
    const st = $("ai-status");
    let j;
    try { j = await ctx.api.job(A.job); } catch (e) { say(st, e.message, "bad"); A.job = null; $("ai-generate").disabled = false; pill(); return; }
    renderActivity($("ai-activity"), j.progress, { key: "gen-" + A.job, running: j.status === "running" });
    const steps = (j.progress && j.progress.steps) || [];
    if (j.status === "running" && steps.length) say(st, steps[steps.length - 1].text + "…", "busy");
    const box = clear($("ai-rounds-out"));
    (j.rounds || []).forEach((r) => box.appendChild(h("li", null, badge(`round ${r.n}`, r.ok ? "success" : "warning"), " ", r.ok ? "no problems" : `${(r.problems || []).length} problem(s)`,
      (r.problems || []).length ? h("ul", null, r.problems.map((p) => h("li", { text: (p.where ? p.where + ": " : "") + (p.message || p) }))) : null)));
    if (j.status === "running") {
      const lv = j.progress && j.progress.live;
      A.pillText = (lv ? liveLine(lv, true, j.progress.server_now) : "") || (steps.length ? steps[steps.length - 1].text : "The AI is working…");
      pill();
      A.timer = setTimeout(poll, 1500);
      return;
    }
    const finished = A.job;
    A.job = null;
    $("ai-generate").disabled = false;
    loadHistory();
    if (j.status === "failed") {
      say(st, "Generation failed: " + (j.error || "no detail"), "bad");
      ctx.say("AI generation failed.", "bad");
      A.done = "bad";
      A.pillText = "The AI request failed: open to see why";
      pill();
      return;
    }
    const rounds = j.rounds || [];
    const last = rounds[rounds.length - 1];
    let tail = "";
    if (j.project && M.isDirty(ctx.project, ctx.saved)) {
      // the author changed the editor while the AI worked: never overwrite it
      tail = " Your editor has unsaved changes, so it was not loaded: use “Load this draft into the editor” in the conversation.";
    } else if (j.project) {
      await loadDraft(finished, true);
      tail = " Save to keep it.";
    } else {
      const fresh = await ctx.api.project(ctx.project.id);
      ctx.project = fresh;
      ctx.saved = clone(fresh);
      syncDetails();
      ctx.changed();
      keepFocus(() => renderActive());
    }
    const said = j.message ? " The AI says: " + j.message : "";
    const msg = (last && last.message_only && !j.project
      ? "The AI answered without changing the draft." + said
      : `Draft generated in ${rounds.length} round(s)` + (last && !last.ok ? `, ${(last.problems || []).length} problem(s) left: they are shown in Checks, never fixed silently.` : ".") + tail + said);
    say(st, msg, last && !last.ok ? "info" : "ok");
    ctx.say(msg, last && !last.ok ? "info" : "ok");
    A.done = last && !last.ok ? "warn" : "ok";
    A.pillText = "The AI finished: " + (last && !last.ok ? "draft with problems left" : "draft ready") + " -- open";
    pill();
  }
  /* A job that still works when the editor opens (a reload, another tab,
   * the project page) comes back: in the pill, followed as before. */
  function resumeAI() {
    // read now: activating a tab rewrites the hash before the history answers
    const want = (/#ai=([0-9a-f]{32})/.exec(location.hash) || [])[1];
    loadHistory().then((threads) => {
      const t = (threads || []).find((x) => x.running);
      if (t) {
        const turn = t.turns.find((u) => u.status === "running");
        A.thread = t.thread;
        A.job = turn.job_id;
        A.minimized = !want;
        $("ai-generate").disabled = true;
        renderThreads();
        renderThread();
        poll();
      }
      if (want) {
        if (!t) A.thread = (threads || []).some((x) => x.thread === want) ? want : null;
        openAI();
      } else pill();
    });
  }

  /* ── local draft ───────────────────────────────────────────────────── */
  function offerDraft() {
    let d = null;
    try { d = M.restorable(window.localStorage, ctx.project); } catch (e) { d = null; }
    const banner = $("ed-draft-banner");
    if (!d) { banner.hidden = true; return; }
    clear(banner);
    const restore = button("Restore the local draft", { class: "btn btn-sm btn-fw-primary", id: "draft-restore" }, "bi-arrow-counterclockwise");
    const discard = button("Discard it", { id: "draft-discard" }, "bi-x-lg");
    restore.addEventListener("click", () => {
      M.applyPayload(ctx.project, d.payload);
      banner.hidden = true;
      syncDetails();
      ctx.changed({ rerender: true });
      ctx.say("Local draft restored: Save to keep it.", "info");
    });
    discard.addEventListener("click", () => { M.clearDrafts(window.localStorage, ctx.project.id); banner.hidden = true; });
    banner.appendChild(h("span", { text: `A local draft from ${new Date(d.saved_at).toLocaleString()} is newer than the saved project${d.base_updated_at !== ctx.project.updated_at ? " (it started from an older save)" : ""}. ` }));
    banner.appendChild(restore);
    banner.appendChild(discard);
    banner.hidden = false;
  }

  /* ── keyboard, leaving ─────────────────────────────────────────────── */
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) { e.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (e) => {
    if (!M.isDirty(ctx.project, ctx.saved) && !rawTab.pending()) return;
    e.preventDefault();
    e.returnValue = "";
  });

  /* ── start ─────────────────────────────────────────────────────────── */
  header();
  updateDirty();
  ctx.check = checkOf(ctx.project.last_check);
  ctx.checkSource = ctx.check ? "Last check stored with the project" : "";
  renderChecks();
  offerDraft();
  resumeAI();
  const want = (/#tab=([a-z]+)/.exec(location.hash) || [])[1];
  const tid = ctx.project.target_id || (ctx.target && ctx.target.id);
  if (tid) {
    ctx.schemaState = "loading";
    activate(want || tabs[0], true);
    ctx.library().catch(() => {});      // where it installs: the server's answer until it arrives
    Promise.all([ctx.api.schema(tid), ctx.api.snapshot(tid).catch(() => null)]).then(([schema, snap]) => {
      ctx.schema = schema;
      ctx.snapshot = snap;
      ctx.schemaState = "ok";
      keepFocus(() => renderActive());
    }).catch((e) => {
      ctx.schemaState = "failed";
      const b = $("ed-schema-banner");
      b.textContent = `The target schema could not be loaded (${e.message}). Pickers are limited and the diagram has no step kinds until it loads.`;
      b.hidden = false;
      keepFocus(() => renderActive());
    });
  } else {
    ctx.schemaState = "none";
    const b = $("ed-schema-banner");
    b.textContent = "No SATOM target is linked to this project: pickers are limited and the diagram has no step kinds. Link a target to edit with the target's vocabulary.";
    b.hidden = false;
    activate(want || tabs[0], true);
  }
  window.SatomBuilderEditor = { ctx, save, activate };
}

boot();
