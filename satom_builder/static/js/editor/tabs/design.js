/* Design tab: app.app.design, a block tree driven by schema.design
 * (studio_design.schema(): blocks, presets, colours, limits, required).
 * Page / Result / Theme, an inspector for props and style, colour contrast
 * hints, and a raw JSON toggle for the whole design object. */
import { h, clear, field, input, textarea, select, checkbox, button, iconButton, badge, card, empty, flash, say, icon } from "../ui.js";
import * as D from "../design.js";
import { ensureApp, appSpec, list } from "../model.js";
import { jsonProblem } from "../util.js";

const S = { surface: "page", sel: null, raw: false };

export default {
  id: "design", label: "Design", icon: "bi-palette",

  render(ctx, panel) {
    clear(panel);
    const ds = ctx.schema && ctx.schema.design;
    const design = appSpec(ctx.project).design;
    if (!design) {
      const b = button("Start a design", { class: "btn btn-sm btn-fw-primary", id: "ds-create" }, "bi-plus-lg");
      b.addEventListener("click", () => { ensureApp(ctx.project).app.design = { theme: {}, page: [], result: [] }; ctx.changed({ rerender: true }); });
      panel.appendChild(card("Design", [empty("No design: SATOM draws its default run page and result. Start one to place fields, buttons, text and outputs yourself."), b], { icon: "bi-palette" }));
      return;
    }
    if (!ds) { panel.appendChild(card("Design", [empty("The target schema is not loaded: only the raw JSON view is available.")], { icon: "bi-palette" })); S.raw = true; }
    const seg = h("div", { class: "ed-seg", role: "group", "aria-label": "Design surface" },
      ["page", "result", "theme"].map((s) => {
        const b = h("button", { type: "button", class: "btn btn-sm " + (S.surface === s && !S.raw ? "btn-fw-primary" : "btn-fw-outline"), id: `ds-surface-${s}`, "aria-pressed": String(S.surface === s && !S.raw), text: { page: "Run page", result: "Result", theme: "Theme" }[s] });
        b.addEventListener("click", () => { S.surface = s; S.raw = false; S.sel = null; this.render(ctx, panel); });
        return b;
      }),
      (() => {
        const b = h("button", { type: "button", class: "btn btn-sm " + (S.raw ? "btn-fw-primary" : "btn-fw-outline"), id: "ds-raw-toggle", "aria-pressed": String(S.raw) }, icon("bi-braces"), " Raw JSON");
        b.addEventListener("click", () => { S.raw = !S.raw; this.render(ctx, panel); });
        return b;
      })());
    const issues = ctx.issues().filter((x) => x.target.tab === "design");
    const issueBox = issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((x) => {
      const b = h("button", { type: "button", class: "ed-issue-link ed-tone-" + x.tone, text: x.message });
      b.addEventListener("click", () => { if (x.target.id) { S.sel = x.target.id; const at = D.locate(design, x.target.id); if (at) S.surface = at.surface; this.render(ctx, panel); } });
      return b;
    })) : null;
    let main;
    if (S.raw) main = rawView(ctx, design, () => this.render(ctx, panel));
    else if (S.surface === "theme") main = themeView(ctx, ds, design);
    else main = surfaceView(ctx, ds, design, () => this.render(ctx, panel));
    panel.appendChild(card("Design", [seg, issueBox, main], { icon: "bi-palette", tools: badge(`${D.countBlocks(design)} / ${(ds && ds.limits && ds.limits.blocks) || 300} blocks`, "secondary") }));
  },

  reveal(ctx, t) {
    const design = appSpec(ctx.project).design;
    if (!design) return;
    if (t.sub === "theme") S.surface = "theme";
    if (t.id) { const at = D.locate(design, t.id); if (at) { S.surface = at.surface; S.sel = t.id; } }
    else if (t.sub === "page" || t.sub === "result") S.surface = t.sub;
    S.raw = false;
    const panel = document.getElementById("panel-design");
    if (panel) { this.render(ctx, panel); flash(document.getElementById(`ds-node-${S.sel}`)); }
  },
};

function surfaceList(design, surface) {
  if (!Array.isArray(design[surface])) design[surface] = [];
  return design[surface];
}

function surfaceView(ctx, ds, design, rerender) {
  const surface = S.surface;
  const blocks = Array.isArray(design[surface]) ? design[surface] : [];
  const tree = h("div", { class: "ed-tree", role: "list", "aria-label": `${surface} blocks` });
  if (!blocks.length) tree.appendChild(empty(surface === "page" ? "Empty: SATOM draws the default run page." : "Empty: SATOM draws the default result."));
  const drawList = (lst, depth, into) => {
    lst.forEach((b, i) => {
      const spec = (ds && ds.blocks && ds.blocks[b.type]) || null;
      const sel = S.sel === b.id;
      const sum = blockSummary(b);
      const pick = h("button", { type: "button", class: "ed-tree-pick" + (sel ? " is-selected" : ""), id: `ds-node-${b.id}`, "aria-current": sel ? "true" : null },
        h("span", { class: "ed-tree-type", text: spec ? spec.label : `unknown ${b.type}` }), sum ? h("span", { class: "ed-muted", text: " " + sum }) : null);
      pick.addEventListener("click", () => { S.sel = b.id; rerender(); });
      const row = h("div", { class: "ed-tree-row", role: "listitem", style: { paddingLeft: `${(depth - 1) * 18}px` } }, pick,
        h("span", { class: "ed-row-tools" },
          iconButton("bi-arrow-up", `Move ${spec ? spec.label : b.type} up`, { disabled: i === 0, on: { click: () => { D.moveBlock(design, b.id, -1); ctx.changed(); rerender(); } } }),
          iconButton("bi-arrow-down", `Move ${spec ? spec.label : b.type} down`, { disabled: i === lst.length - 1, on: { click: () => { D.moveBlock(design, b.id, 1); ctx.changed(); rerender(); } } }),
          iconButton("bi-trash", `Remove ${spec ? spec.label : b.type}`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", on: { click: () => { D.removeBlock(design, b.id); if (S.sel === b.id) S.sel = null; ctx.changed(); rerender(); } } })));
      into.appendChild(row);
      D.childLists(b).forEach((c) => {
        into.appendChild(h("div", { class: "ed-tree-group", style: { paddingLeft: `${depth * 18}px` }, text: c.label }));
        drawList(c.items, depth + 1, into);
      });
    });
  };
  drawList(blocks, 1, tree);
  const pal = D.palette(ds, surface);
  const opts = pal.flatMap(([grp, items]) => items.map(([t, sp]) => ({ value: t, label: `${grp}: ${sp.label}` })));
  const typeSel = select(opts, opts[0] && opts[0].value, { id: "ds-add-type" });
  const add = button("Add block", { class: "btn btn-sm btn-fw-primary", id: "ds-add" }, "bi-plus-lg");
  add.addEventListener("click", () => {
    const r = D.addBlock(design, surfaceList(design, surface), typeSel.value, ds);
    if (!r.ok) { ctx.say(r.error, "bad"); return; }
    S.sel = r.block.id;
    ctx.changed();
    rerender();
  });
  const req = ((ds && ds.required && ds.required[surface]) || []).map((t) => (ds.blocks[t] || {}).label || t);
  const left = h("div", { class: "ed-design-tree" },
    h("div", { class: "ed-inline" }, h("label", { class: "visually-hidden", for: "ds-add-type", text: `Block to add to the ${surface}` }), typeSel, add),
    req.length ? h("p", { class: "ed-help", text: `Always present (added at the end when left out): ${req.join(", ")}.` }) : null,
    tree);
  const at = S.sel ? D.locate(design, S.sel) : null;
  const right = h("aside", { class: "ed-inspector", "aria-label": "Block inspector" },
    at ? blockInspector(ctx, ds, design, at, rerender) : empty("Select a block to edit it."));
  return h("div", { class: "ed-design" }, left, right);
}

function blockSummary(b) {
  const p = b.props || {};
  const s = p.title || p.text || p.label || p.name || p.key || "";
  return s ? `"${String(s).slice(0, 40)}"` : "";
}

function blockInspector(ctx, ds, design, at, rerender) {
  const b = at.block;
  const spec = (ds && ds.blocks && ds.blocks[b.type]) || null;
  const out = h("div", null, h("div", { class: "ed-insp-head" }, h("strong", { text: spec ? spec.label : `Unknown block ${b.type}` }), " ", h("code", { text: b.id || "" })));
  if (!spec) { out.appendChild(h("p", { class: "ed-help", text: "The target does not know this block type. Edit it in Raw JSON; it is kept as it is." })); return out; }
  const props = b.props && typeof b.props === "object" ? b.props : {};
  const setProp = (name, v) => { b.props = props; props[name] = v; ctx.changed(); };
  const fields = list(appSpec(ctx.project), "fields").map((f) => f.name);
  const outputs = D.outputKeys(appSpec(ctx.project).graph);
  (spec.props || []).forEach((p) => {
    const id = `ds-p-${p.name}`;
    const v = props[p.name] !== undefined ? props[p.name] : p.default;
    let ctl;
    switch (p.kind) {
      case "md": ctl = textarea(v, { id, rows: 5, maxlength: p.max || null }); ctl.addEventListener("input", () => setProp(p.name, ctl.value)); break;
      case "enum": ctl = select(p.choices.map((c) => ({ value: c, label: c === "" ? "(the step's)" : c })), v, { id }); ctl.addEventListener("change", () => setProp(p.name, ctl.value)); break;
      case "bool": {
        const c = checkbox(p.label, !!v, { id });
        c.querySelector("input").addEventListener("change", (e) => setProp(p.name, e.target.checked));
        out.appendChild(c);
        return;
      }
      case "int": ctl = input(v, { id, type: "number", min: p.lo, max: p.hi, step: 1 }); ctl.addEventListener("input", () => setProp(p.name, ctl.value === "" ? "" : Number(ctl.value))); break;
      case "field": ctl = select(fields, v, { id }, "— form field —"); ctl.addEventListener("change", () => setProp(p.name, ctl.value)); break;
      case "output": ctl = select(outputs, v, { id }, "— Output step —"); ctl.addEventListener("change", () => setProp(p.name, ctl.value)); break;
      case "asset": {
        const assets = (ctx.project.assets || []).map((a) => ({ value: a.sha256, label: a.name || a.sha256.slice(0, 12) }));
        ctl = select(assets, v, { id }, "— uploaded image —");
        ctl.addEventListener("change", () => setProp(p.name, ctl.value));
        const file = h("input", { type: "file", id: id + "-file", accept: "image/png,image/jpeg,image/gif,image/webp", class: "form-control form-control-sm" });
        const msg = h("div", { class: "ed-status", "aria-live": "polite" });
        file.addEventListener("change", async () => {
          if (!file.files[0]) return;
          say(msg, "Uploading…", "busy");
          try {
            const r = await ctx.api.uploadAsset(ctx.project.id, file.files[0]);
            ctx.project.assets = (ctx.project.assets || []).concat([{ name: r.name, sha256: r.sha256 }]);
            if (ctx.saved) ctx.saved.assets = ctx.project.assets;
            setProp(p.name, r.sha256);
            rerender();
          } catch (e) { say(msg, e.message, "bad"); }
        });
        out.appendChild(field(p.label, ctl));
        out.appendChild(field("Upload an image", file));
        out.appendChild(msg);
        return;
      }
      case "icon": ctl = input(v, { id, class: "form-control form-control-sm ed-mono", placeholder: "bi-star", pattern: "^bi-[a-z0-9-]{1,28}$" }); ctl.addEventListener("input", () => setProp(p.name, ctl.value)); break;
      default: ctl = input(v, { id, maxlength: p.max || null }); ctl.addEventListener("input", () => setProp(p.name, ctl.value));
    }
    out.appendChild(field(p.label, ctl));
  });
  // containers
  if (spec.container === "columns") {
    const cols = Array.isArray(b.columns) ? b.columns : [];
    out.appendChild(h("div", { class: "ed-label", text: "Columns (widths out of 12)" }));
    cols.forEach((c, i) => {
      const w = input(c.w, { id: `ds-col-${i}`, type: "number", min: 1, max: 12 });
      w.addEventListener("input", () => { c.w = Number(w.value) || 1; ctx.changed(); });
      out.appendChild(h("div", { class: "ed-inline" }, field(`Column ${i + 1}`, w), iconButton("bi-trash", `Remove column ${i + 1} and its blocks`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", disabled: cols.length <= 1, on: { click: () => { cols.splice(i, 1); ctx.changed(); rerender(); } } })));
    });
    const ac = button("Add column", { id: "ds-col-add" }, "bi-plus-lg");
    ac.addEventListener("click", () => { if (D.addColumn(b, ds.limits && ds.limits.columns)) { ctx.changed(); rerender(); } else ctx.say("No room for another column.", "bad"); });
    out.appendChild(ac);
  }
  if (spec.container === "tabs") {
    const tabs = Array.isArray(b.tabs) ? b.tabs : [];
    tabs.forEach((t, i) => {
      const l = input(t.label, { id: `ds-tab-${i}` });
      l.addEventListener("input", () => { t.label = l.value; ctx.changed(); });
      out.appendChild(h("div", { class: "ed-inline" }, field(`Tab ${i + 1} label`, l), iconButton("bi-trash", `Remove tab ${i + 1} and its blocks`, { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", disabled: tabs.length <= 1, on: { click: () => { tabs.splice(i, 1); ctx.changed(); rerender(); } } })));
    });
    const at2 = button("Add tab", { id: "ds-tab-add" }, "bi-plus-lg");
    at2.addEventListener("click", () => { if (D.addTab(b, ds.limits && ds.limits.tabs)) { ctx.changed(); rerender(); } else ctx.say("No room for another tab.", "bad"); });
    out.appendChild(at2);
  }
  if (spec.container) {
    const lists = D.childLists(b);
    if (spec.container === "children" && !lists.length) {
      lists.push({ label: "Inside", get list() { if (!Array.isArray(b.children)) b.children = []; return b.children; } });
    }
    if (lists.length) {
      const where = select(lists.map((c, i) => ({ value: String(i), label: c.label })), "0", { id: "ds-in-where" });
      const pal = D.palette(ds, at.surface).flatMap(([grp, items]) => items.map(([t, sp]) => ({ value: t, label: `${grp}: ${sp.label}` })));
      const t = select(pal, pal[0] && pal[0].value, { id: "ds-in-type" });
      const add = button("Add inside", { id: "ds-in-add" }, "bi-plus-lg");
      add.addEventListener("click", () => {
        const r = D.addBlock(design, lists[Number(where.value)].list, t.value, ds);
        if (!r.ok) { ctx.say(r.error, "bad"); return; }
        S.sel = r.block.id;
        ctx.changed();
        rerender();
      });
      out.appendChild(h("div", { class: "ed-grid ed-grid-2" }, field("Where", where), field("Block", t)));
      out.appendChild(add);
    }
  }
  out.appendChild(styleEditor(ctx, ds, design, b, at));
  return out;
}

function colourInput(id, label, value, onChange) {
  const txt = input(value || "", { id, class: "form-control form-control-sm ed-mono", placeholder: "#RRGGBB", maxlength: 7 });
  const pick = h("input", { type: "color", class: "form-control form-control-color ed-colour", value: D.hex6(value) || "#FFFFFF", "aria-label": `${label}: colour picker`, title: label });
  txt.addEventListener("input", () => { const h6 = D.hex6(txt.value); if (h6) pick.value = h6; onChange(txt.value.trim()); });
  pick.addEventListener("input", () => { txt.value = pick.value.toUpperCase(); onChange(txt.value); });
  // The label names the text box (the picker carries its own aria-label).
  const f = field(label, txt, { help: value && !D.hex6(value) ? "Not a hex colour: SATOM refuses it." : "" });
  const row = h("div", { class: "ed-inline ed-colour-row" });
  f.insertBefore(row, txt);
  row.appendChild(pick);
  row.appendChild(txt);
  return f;
}

function styleEditor(ctx, ds, design, b, at) {
  const box = h("details", { class: "ed-style", open: b.style && Object.keys(b.style).length > 0 }, h("summary", { text: "Style" }));
  const st = b.style && typeof b.style === "object" ? b.style : {};
  const hint = h("div", { class: "ed-contrast", "aria-live": "polite" });
  const update = () => {
    const th = design.theme || {};
    const cards = ["card", "output", "stat", "recent_runs"].includes(b.type);
    const bg = st.bg || (cards ? th.surface || D.BASE.surface : th.page_bg || D.BASE.page_bg);
    const fg = st.color || th.text || D.BASE.text;
    const c = D.contrast(fg, bg);
    const min = (ds && ds.min_contrast) || 4.5;
    hint.textContent = c === null ? "" : `Text ${D.hex6(fg)} on ${D.hex6(bg)}: contrast ${c.toFixed(2)}:1${c < min ? ` — below ${min}:1, hard to read` : ""}`;
    hint.dataset.tone = c !== null && c < min ? "bad" : "ok";
  };
  const set = (k, v) => { b.style = st; if (v === "" || v === null) delete st[k]; else st[k] = v; ctx.changed(); update(); };
  ((ds && ds.colors) || ["bg", "bg2", "color", "border_color"]).forEach((k) => {
    box.appendChild(colourInput(`ds-st-${k}`, { bg: "Background", bg2: "Gradient to", color: "Text colour", border_color: "Border colour" }[k] || k, st[k], (v) => set(k, v)));
  });
  Object.entries((ds && ds.presets) || {}).forEach(([k, choices]) => {
    const s = select(choices, st[k] || "", { id: `ds-st-${k}` }, "—");
    s.addEventListener("change", () => set(k, s.value));
    box.appendChild(field(k === "mb" ? "Space below" : k[0].toUpperCase() + k.slice(1), s));
  });
  box.appendChild(hint);
  update();
  return box;
}

function themeView(ctx, ds, design) {
  const th = design.theme && typeof design.theme === "object" ? design.theme : {};
  const hints = h("div", { class: "ed-contrast-list", "aria-live": "polite" });
  const update = () => {
    clear(hints);
    const list2 = D.themeHints(th, ds && ds.min_contrast);
    if (!list2.length) hints.appendChild(h("div", { class: "ed-contrast", "data-tone": "ok", text: "Every theme colour pair reads at the recommended contrast." }));
    list2.forEach((m) => hints.appendChild(h("div", { class: "ed-contrast", "data-tone": "bad", text: m })));
  };
  const set = (k, v) => { design.theme = th; if (v === "" || v === null) delete th[k]; else th[k] = v; ctx.changed(); update(); };
  const grid = h("div", { class: "ed-grid ed-grid-3" });
  ((ds && ds.theme_colors) || ["accent", "page_bg", "text", "surface", "border"]).forEach((k) => {
    grid.appendChild(colourInput(`ds-th-${k}`, { accent: "Accent (buttons)", page_bg: "Page background", text: "Text", surface: "Card surface", border: "Borders" }[k] || k, th[k], (v) => set(k, v)));
  });
  Object.entries((ds && ds.theme_presets) || {}).forEach(([k, choices]) => {
    const s = select(choices, th[k] || "", { id: `ds-th-${k}` }, "—");
    s.addEventListener("change", () => set(k, s.value));
    grid.appendChild(field(k[0].toUpperCase() + k.slice(1), s));
  });
  update();
  return h("div", null, h("p", { class: "ed-intro", text: `Empty colours take SATOM's defaults (page ${D.BASE.page_bg}, surface ${D.BASE.surface}, text ${D.BASE.text}, accent ${D.BASE.accent}).` }), grid, hints);
}

function rawView(ctx, design, rerender) {
  const t = textarea(JSON.stringify(design, null, 2), { id: "ds-raw", rows: 22, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
  const msg = h("div", { class: "ed-status", "aria-live": "polite" });
  const apply = button("Apply", { class: "btn btn-sm btn-fw-primary", id: "ds-raw-apply" }, "bi-check2");
  apply.addEventListener("click", () => {
    const bad = jsonProblem(t.value);
    if (bad) { say(msg, "Not valid JSON: " + bad, "bad"); return; }
    const v = JSON.parse(t.value);
    if (!v || typeof v !== "object" || Array.isArray(v)) { say(msg, "The design is a JSON object {theme, page, result}.", "bad"); return; }
    ensureApp(ctx.project).app.design = v;
    ctx.changed();
    say(msg, "Applied.", "ok");
    rerender();
  });
  const reset = button("Reset", { id: "ds-raw-reset" }, "bi-arrow-counterclockwise");
  reset.addEventListener("click", () => { t.value = JSON.stringify(appSpec(ctx.project).design, null, 2); say(msg, "", "info"); });
  return h("div", null, field("app.design (JSON)", t, { help: "Every key is kept; the rules pack checks the vocabulary on Check." }), h("div", { class: "ed-inline" }, apply, reset), msg);
}
