/* Diagram tab: app.app.graph. Palette of the target's step kinds grouped
 * by `group` with their `colour`, the SVG canvas (diagram.js) and an
 * inspector whose params form is generated from the kind's params. */
import { h, clear, field, input, textarea, select, button, iconButton, badge, empty, say } from "../ui.js";
import * as G from "../graph.js";
import { DiagramCanvas } from "../diagram.js";
import { paramForm } from "../paramform.js";
import { ensureApp, appSpec } from "../model.js";
import { jsonProblem } from "../util.js";

const GROUP_LABELS = { flow: "Flow", data: "Data", act: "Actions", check: "Checks", satom: "SATOM" };
const S = { canvas: null, sel: null, view: null, overlay: null };

function graphOf(ctx) { return appSpec(ctx.project).graph || null; }

export default {
  id: "diagram", label: "Diagram", icon: "bi-diagram-3",

  render(ctx, panel) {
    if (S.canvas) { S.view = Object.assign({}, S.canvas.view); S.canvas.destroy(); S.canvas = null; }
    clear(panel);
    const kinds = (ctx.schema && ctx.schema.step_kinds) || [];
    const g = graphOf(ctx);
    if (!g) {
      const start = button("Start a diagram", { class: "btn btn-sm btn-fw-primary", id: "dg-create" }, "bi-plus-lg");
      start.addEventListener("click", () => {
        const spec = ensureApp(ctx.project).app;
        spec.graph = { nodes: [], edges: [] };
        const km = G.kindMap(kinds);
        G.addNode(spec.graph, "start", km.start || { label: "Start" }, { x: 80, y: 40 });
        G.addNode(spec.graph, "end", km.end || { label: "End" }, { x: 80, y: 160 });
        G.connect(spec.graph, "start", "end", "always", ctx.schema);
        ctx.changed({ rerender: true });
      });
      panel.appendChild(h("div", { class: "fw-card ed-card" }, h("div", { class: "fw-card-body" },
        empty("This app has no diagram yet."), start)));
      return;
    }
    G.ensureGraph(g);
    if (G.needsLayout(g)) G.autoLayout(g);
    const km = G.kindMap(kinds);

    const hintEl = h("div", { class: "ed-hint", "aria-live": "polite", id: "dg-hint" });
    const host = h("div", { class: "ed-canvas", id: "dg-canvas" });
    const inspector = h("aside", { class: "ed-inspector", id: "dg-inspector", "aria-label": "Inspector" });
    const hints = h("div", { class: "ed-diagram-hints", id: "dg-hints" });

    const tb = (label, ico, fn, id) => { const b = button(label, { id }, ico); b.addEventListener("click", fn); return b; };
    const toolbar = h("div", { class: "ed-toolbar", role: "toolbar", "aria-label": "Diagram tools" },
      tb("Connect", "bi-arrow-down-right", () => S.canvas.startConnect(), "dg-connect"),
      tb("Delete", "bi-trash", () => del(S.sel), "dg-delete"),
      tb("Auto-layout", "bi-grid-3x3-gap", () => { G.autoLayout(g); S.canvas.fit(); ctx.changed(); }, "dg-layout"),
      tb("", "bi-zoom-out", () => S.canvas.zoomAt(0.9), "dg-zoom-out"),
      tb("", "bi-zoom-in", () => S.canvas.zoomAt(1.1), "dg-zoom-in"),
      tb("Fit", "bi-arrows-fullscreen", () => S.canvas.fit(), "dg-fit"),
      S.overlay ? tb("Clear rehearsal", "bi-eraser", () => { S.overlay = null; S.canvas.setOverlay([]); }, "dg-clear-run") : null);
    toolbar.querySelector("#dg-zoom-out").setAttribute("aria-label", "Zoom out");
    toolbar.querySelector("#dg-zoom-in").setAttribute("aria-label", "Zoom in");

    const pal = h("nav", { class: "ed-palette", "aria-label": "Step kinds" });
    const groups = new Map();
    kinds.forEach((k) => { if (!groups.has(k.group)) groups.set(k.group, []); groups.get(k.group).push(k); });
    if (!kinds.length) pal.appendChild(empty("No step kinds: the target schema is not loaded."));
    groups.forEach((list, grp) => {
      pal.appendChild(h("div", { class: "ed-palette-group", text: GROUP_LABELS[grp] || grp }));
      list.forEach((k) => {
        const hasStart = k.key === "start" && g.nodes.some((n) => n.kind === "start");
        const b = h("button", {
          type: "button", class: "ed-palette-item", id: `dg-add-${k.key}`, disabled: hasStart,
          title: hasStart ? "A diagram has exactly one Start step" : (k.summary || ""), style: { borderLeftColor: k.colour || "#3D4550" },
        }, k.label, k.writes ? h("span", { class: "ed-writes", text: "writes" }) : null);
        b.addEventListener("click", () => {
          const v = S.canvas.view;
          const n = G.addNode(g, k.key, k, { x: (60 - v.x) / v.k + (g.nodes.length % 3) * 30, y: (60 - v.y) / v.k + (g.nodes.length % 6) * 26 });
          S.sel = { type: "node", key: n.key };
          ctx.changed();
          refreshAll();
          S.canvas.select(S.sel, true);
        });
        pal.appendChild(b);
      });
    });

    panel.appendChild(h("div", { class: "ed-diagram" }, pal,
      h("div", { class: "ed-diagram-main" }, toolbar, hintEl, host, hints), inspector));

    S.canvas = new DiagramCanvas(host, {
      kinds: km, branches: (ctx.schema && ctx.schema.branches) || undefined,
      onSelect: (s) => { S.sel = s; renderInspector(); },
      onChange: () => ctx.changed(),
      onHint: (t) => say(hintEl, t, "info"),
      onConnect: (src, dst) => {
        const r = G.connect(g, src, dst, null, ctx.schema);
        if (!r.ok) { say(hintEl, r.error, "bad"); return; }
        S.sel = { type: "edge", index: g.edges.length - 1 };
        say(hintEl, `Arrow ${src} → ${dst} (${r.edge.branch}). Pick its outcome in the inspector.`, "ok");
        ctx.changed();
        refreshAll();
        S.canvas.select(S.sel, true);
      },
      onDelete: (s) => del(s),
    });
    S.canvas.setGraph(g);
    if (S.view) { S.canvas.view = S.view; S.canvas.draw(); } else S.canvas.fit();
    if (S.overlay) S.canvas.setOverlay(S.overlay);

    function del(s) {
      if (!s) { say(hintEl, "Select a step or an arrow first.", "info"); return; }
      if (s.type === "edge") G.deleteEdge(g, s.index);
      else G.deleteNode(g, s.key);
      S.sel = null;
      ctx.changed();
      refreshAll();
      S.canvas.select(null, true);
    }

    function problemsMap() {
      const m = {};
      ctx.issues().forEach((x) => {
        if (x.target.tab !== "diagram") return;
        let key = x.target.id;
        if (key === undefined || key === null) {
          if (x.target.edge !== undefined && x.target.edge !== null && g.edges[x.target.edge]) key = g.edges[x.target.edge].src;
        }
        if (key) (m[key] = m[key] || []).push(x.message);
      });
      return m;
    }

    function refreshAll() {
      S.canvas.setProblems(problemsMap());
      renderInspector();
      renderHints();
      pal.querySelectorAll(".ed-palette-item").forEach((b) => {
        if (b.id === "dg-add-start") b.disabled = g.nodes.some((n) => n.kind === "start");
      });
    }

    function renderHints() {
      clear(hints);
      const local = G.validate(g, kinds, ctx.schema);
      const checks = ctx.issues().filter((x) => x.target.tab === "diagram");
      if (!local.length && !checks.length) return;
      const item = (where, msg, tone) => {
        const t = G.nodeByKey(g, (/nodes\[([^\]]*)\]/.exec(where) || [])[1]);
        const b = h("button", { type: "button", class: "ed-issue-link ed-tone-" + tone, text: msg });
        b.addEventListener("click", () => { if (t) { S.sel = { type: "node", key: t.key }; S.canvas.select(S.sel); S.canvas.reveal(t.key); } });
        return h("li", null, b);
      };
      if (checks.length) hints.appendChild(h("div", null, h("strong", { text: "From the last check" }), h("ul", { class: "ed-issue-list" }, checks.map((x) => item(x.where, x.message, x.tone)))));
      if (local.length) hints.appendChild(h("div", null, h("strong", { text: "Editor hints (the check decides)" }), h("ul", { class: "ed-issue-list" }, local.map((x) => item(x.where, x.message, "hint")))));
    }

    function renderInspector() {
      clear(inspector);
      const s = S.sel;
      const pick = select(g.nodes.map((n) => ({ value: n.key, label: `${n.label || n.key} (${n.key})` })), s && s.type === "node" ? s.key : "", { id: "dg-pick" }, "— select a step —");
      pick.addEventListener("change", () => {
        if (!pick.value) return;
        S.sel = { type: "node", key: pick.value };
        S.canvas.select(S.sel);
        S.canvas.reveal(pick.value);
      });
      inspector.appendChild(field("Step", pick));
      if (!s) { inspector.appendChild(empty("Click a step or an arrow to edit it. Drag from the round port under a step to another step to add an arrow.")); return; }
      if (s.type === "edge") { edgeInspector(s.index); return; }
      const n = G.nodeByKey(g, s.key);
      if (!n) { S.sel = null; renderInspector(); return; }
      const kind = km[n.kind];
      inspector.appendChild(h("div", { class: "ed-insp-head" }, h("strong", { text: kind ? kind.label : "Unknown kind " + n.kind }), " ", kind && kind.writes ? badge("writes", "danger") : null));
      if (kind && kind.summary) inspector.appendChild(h("p", { class: "ed-help", text: kind.summary }));
      const probs = problemsMap()[n.key] || [];
      if (probs.length) inspector.appendChild(h("div", { class: "ed-issue", role: "note" }, probs.map((m) => h("div", { text: m }))));
      const run = S.overlay && S.overlay.find((x) => x.key === n.key);
      if (run) inspector.appendChild(h("div", { class: "ed-run-box" }, h("strong", { text: "Last rehearsal: " }), run.outcome, run.detail ? h("div", { class: "ed-pre", text: run.detail }) : null));

      const key = input(n.key, { id: "dg-key", class: "form-control form-control-sm ed-mono", spellcheck: "false" });
      const keyMsg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
      key.addEventListener("change", () => {
        const r = G.renameNode(g, n.key, key.value, ctx.schema);
        if (!r.ok) { keyMsg.textContent = r.error; key.value = n.key; return; }
        S.sel = { type: "node", key: n.key };
        ctx.changed();
        refreshAll();
        S.canvas.select(S.sel, true);
      });
      const kf = field("Step key", key, { help: "Arrows follow a rename." });
      kf.appendChild(keyMsg);
      inspector.appendChild(kf);
      const label = input(n.label || "", { id: "dg-label" });
      label.addEventListener("input", () => { n.label = label.value; S.canvas.draw(); ctx.changed(); });
      inspector.appendChild(field("Label", label));
      if (kind) {
        const values = n.params && typeof n.params === "object" ? n.params : {};
        inspector.appendChild(paramForm(kind.params, values, {
          owner: n.kind, choices: ctx.choices(), idPrefix: "dg-p",
          onChange: () => { n.params = values; ctx.changed(); S.canvas.draw(); },
          rerender: () => renderInspector(),
          issuesFor: () => [],
        }));
      } else {
        const raw = textarea(JSON.stringify(n.params || {}, null, 2), { id: "dg-raw-params", rows: 8, class: "form-control form-control-sm ed-mono" });
        const msg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
        raw.addEventListener("input", () => {
          const bad = jsonProblem(raw.value);
          msg.textContent = bad ? "Not valid JSON: " + bad : "";
          if (!bad) { n.params = JSON.parse(raw.value); ctx.changed(); }
        });
        const f = field("Parameters (JSON)", raw, { help: "The target does not know this step kind; its parameters are kept as they are." });
        f.appendChild(msg);
        inspector.appendChild(f);
      }
      arrowsOut(n);
      const rm = button("Delete step", { class: "btn btn-sm btn-fw-outline ed-danger", id: "dg-del-node" }, "bi-trash");
      rm.addEventListener("click", () => del({ type: "node", key: n.key }));
      inspector.appendChild(rm);
    }

    function arrowsOut(n) {
      const allowed = G.allowedBranches(n.kind, ctx.schema);
      const box = h("div", { class: "ed-arrows" }, h("div", { class: "ed-label", text: "Arrows from this step" }));
      const outs = g.edges.map((e, i) => [e, i]).filter(([e]) => e.src === n.key);
      if (!outs.length) box.appendChild(h("div", { class: "ed-help", text: allowed.length ? "None yet." : "This step ends the run." }));
      outs.forEach(([e, i]) => {
        const b = h("button", { type: "button", class: "ed-arrow-link" }, badge(e.branch || "always", branchTone(e.branch)), " → ", e.dst);
        b.addEventListener("click", () => { S.sel = { type: "edge", index: i }; S.canvas.select(S.sel); });
        box.appendChild(b);
      });
      if (allowed.length) {
        const used = G.branchesUsed(g, n.key);
        const free = allowed.filter((b) => !used.includes(b));
        if (free.length) {
          const to = select(g.nodes.filter((x) => x.key !== n.key).map((x) => ({ value: x.key, label: x.label || x.key })), "", { id: "dg-arrow-to" }, "— to step —");
          const br = select(free, free[0], { id: "dg-arrow-branch" });
          const add = button("Add arrow", { id: "dg-arrow-add" }, "bi-plus-lg");
          add.addEventListener("click", () => {
            if (!to.value) return;
            const r = G.connect(g, n.key, to.value, br.value, ctx.schema);
            if (!r.ok) { say(hintEl, r.error, "bad"); return; }
            ctx.changed();
            refreshAll();
          });
          box.appendChild(h("div", { class: "ed-grid ed-grid-2" }, field("To", to), field("When", br)));
          box.appendChild(add);
        }
      }
      inspector.appendChild(box);
    }

    function edgeInspector(i) {
      const e = g.edges[i];
      if (!e) { S.sel = null; renderInspector(); return; }
      const src = G.nodeByKey(g, e.src);
      inspector.appendChild(h("div", { class: "ed-insp-head" }, h("strong", { text: "Arrow" }), " ", h("code", { text: e.src }), " → ", h("code", { text: e.dst })));
      const allowed = src ? G.allowedBranches(src.kind, ctx.schema) : [];
      const opts = Array.from(new Set(allowed.concat([e.branch || "always"])));
      const br = select(opts, e.branch || "always", { id: "dg-edge-branch" });
      const msg = h("div", { class: "ed-json-msg", "aria-live": "polite" });
      br.addEventListener("change", () => {
        const r = G.setBranch(g, i, br.value);
        if (!r.ok) { msg.textContent = r.error; br.value = e.branch || "always"; return; }
        msg.textContent = "";
        ctx.changed();
        S.canvas.draw();
        renderHints();
      });
      const f = field("Follow when the step ends", br, { help: "An explicit outcome wins over always; after an error only an error arrow is followed." });
      f.appendChild(msg);
      inspector.appendChild(f);
      const rm = button("Delete arrow", { class: "btn btn-sm btn-fw-outline ed-danger", id: "dg-del-edge" }, "bi-trash");
      rm.addEventListener("click", () => del({ type: "edge", index: i }));
      inspector.appendChild(rm);
    }

    S.refresh = refreshAll;
    refreshAll();
    if (S.sel) S.canvas.select(S.sel, true);
  },

  reveal(ctx, t) {
    const g = graphOf(ctx);
    if (!g || !S.canvas) return;
    if (t.id && G.nodeByKey(g, t.id)) { S.sel = { type: "node", key: t.id }; S.canvas.select(S.sel); S.canvas.reveal(t.id); }
    else if (t.edge !== undefined && t.edge !== null && g.edges[t.edge]) { S.sel = { type: "edge", index: t.edge }; S.canvas.select(S.sel); }
  },

  /* The steps of a rehearsal laid over the diagram. */
  overlay(steps) {
    S.overlay = steps && steps.length ? steps : null;
    if (S.canvas) { S.canvas.setOverlay(steps || []); if (S.refresh) S.refresh(); }
  },
};

function branchTone(b) {
  return { ok: "success", yes: "success", error: "danger", no: "warning" }[b] || "secondary";
}
