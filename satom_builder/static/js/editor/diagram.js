/* Diagram canvas: plain SVG, no dependencies.
 *
 * VENDORED AND ADAPTED from SATOM app/static/js/process_editor.js at commit
 * 0b7a6193 (see VENDORED.md): node box geometry, the bottom->top /
 * side->side anchors, the Bezier arrows, one label per step pair, the
 * two-line label wrap, the branch colours of the fw-badge palette and the
 * pan / zoom / fit maths are SATOM's. What changed:
 *   - an ES module class instead of an IIFE bound to fixed element ids;
 *   - the graph model lives in graph.js (add / connect / delete / validate /
 *     layout, testable under node); this file only draws and forwards input;
 *   - one set of window listeners (the original added two per node per draw);
 *   - pointer events, an output PORT per step (drag from it to a step to
 *     connect) besides the Connect mode, keyboard (Delete, arrows, Esc, +/-);
 *   - check problems drawn on the step `where` names (red frame + count);
 *   - the inspector is built by the host (tabs/diagram.js) from the kind params.
 * Like the original, it knows no rule: kinds and branches come from the
 * target schema and the rules pack re-checks every save. */
import { NODE_W as W, NODE_H as H, nodeByKey } from "./graph.js";

const NS = "http://www.w3.org/2000/svg";
const MARKS = { "#5E6C84": "pa0", "#15692A": "pa1", "#8B1C2A": "pa2", "#7A5700": "pa3" };
const OUTCOME_COLOUR = { ok: "#15692A", yes: "#15692A", no: "#7A5700", error: "#8B1C2A", waiting: "#7A5700", rehearsal: "#0A5F6C" };

function el(name, attrs) {
  const n = document.createElementNS(NS, name);
  Object.keys(attrs || {}).forEach((a) => { if (attrs[a] !== null && attrs[a] !== undefined) n.setAttribute(a, attrs[a]); });
  return n;
}

function wrapLabel(text, max) {
  text = String(text);
  if (text.length <= max) return [text];
  let cut = text.lastIndexOf(" ", max);
  if (cut < 8) cut = max;
  const rest = text.slice(cut).trim();
  return [text.slice(0, cut).trim(), rest.length > max ? rest.slice(0, max - 1) + "…" : rest];
}

function anchors(a, b) {
  const ax = a.x + W / 2, ay = a.y + H / 2, bx = b.x + W / 2, by = b.y + H / 2;
  const dx = bx - ax, dy = by - ay;
  if (Math.abs(dx) > W && Math.abs(dx) > Math.abs(dy)) {
    return dx > 0 ? { side: true, x1: a.x + W, y1: ay, x2: b.x, y2: by } : { side: true, x1: a.x, y1: ay, x2: b.x + W, y2: by };
  }
  if (dy < 0) return { side: false, x1: ax, y1: a.y, x2: bx, y2: b.y + H };
  return { side: false, x1: ax, y1: a.y + H, x2: bx, y2: b.y };
}

export class DiagramCanvas {
  constructor(host, opts) {
    this.host = host;
    this.o = opts || {};
    this.kinds = this.o.kinds || {};
    this.graph = { nodes: [], edges: [] };
    this.sel = null;
    this.view = { x: 0, y: 0, k: 1 };
    this.problems = {};
    this.overlay = {};
    this.connectFrom = null;
    this.drag = null;
    this.branch = {};
    (this.o.branches || [{ key: "always", color: "#5E6C84" }]).forEach((b) => {
      this.branch[b.key] = { c: b.color, m: MARKS[b.color] || "pa0", t: b.key === "always" ? "" : (b.label || b.key) };
    });

    this.svg = el("svg", { width: "100%", height: "100%", tabindex: "0", role: "img", class: "ed-diagram-svg", focusable: "true" });
    const defs = el("defs");
    Object.keys(MARKS).forEach((c) => {
      const m = el("marker", { id: "sbm-" + MARKS[c], viewBox: "0 0 10 10", refX: "9", refY: "5", markerWidth: "6", markerHeight: "6", orient: "auto-start-reverse" });
      m.appendChild(el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: c }));
      defs.appendChild(m);
    });
    this.svg.appendChild(defs);
    this.root = el("g");
    this.svg.appendChild(this.root);
    this.rubber = el("path", { fill: "none", stroke: "#0A3F9F", "stroke-width": 1.6, "stroke-dasharray": "4 3", visibility: "hidden" });
    host.appendChild(this.svg);

    this.svg.addEventListener("pointerdown", (ev) => {
      if (ev.target !== this.svg && ev.target.closest && ev.target.closest(".ed-node, .ed-edge-hit")) return;
      this.drag = { type: "pan", x: ev.clientX, y: ev.clientY, vx: this.view.x, vy: this.view.y };
      if (this.connectFrom !== null) { this.connectFrom = null; this.hint(""); }
      this.select(null);
    });
    this.onMove = (ev) => this.move(ev);
    this.onUp = (ev) => this.up(ev);
    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
    this.svg.addEventListener("wheel", (ev) => {
      ev.preventDefault();
      this.zoomAt(ev.deltaY < 0 ? 1.1 : 0.9, ev.clientX, ev.clientY);
    }, { passive: false });
    this.svg.addEventListener("keydown", (ev) => this.key(ev));
  }

  destroy() {
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    if (this.svg.parentNode) this.svg.parentNode.removeChild(this.svg);
  }

  /* ── state from the host ─────────────────────────────────────────── */
  setGraph(g) { this.graph = g; this.draw(); }
  setProblems(map) { this.problems = map || {}; this.draw(); }
  setOverlay(steps) {
    this.overlay = {};
    (steps || []).forEach((s) => { if (s && s.key) this.overlay[s.key] = s; });
    this.draw();
  }
  hint(t) { if (this.o.onHint) this.o.onHint(t || ""); }
  changed() { if (this.o.onChange) this.o.onChange(); }

  select(s, quiet) {
    this.sel = s;
    this.draw();
    if (!quiet && this.o.onSelect) this.o.onSelect(s);
  }

  startConnect() {
    if (!this.sel || this.sel.type !== "node") { this.hint("Select a step first, then press Connect and click the step the arrow points to."); return; }
    this.connectFrom = this.sel.key;
    this.hint(`Click the step the arrow from ${this.sel.key} points to (Esc cancels).`);
  }

  /* ── geometry ─────────────────────────────────────────────────────── */
  toGraph(cx, cy) {
    const r = this.svg.getBoundingClientRect();
    return { x: (cx - r.left - this.view.x) / this.view.k, y: (cy - r.top - this.view.y) / this.view.k };
  }

  zoomAt(f, cx, cy) {
    const k = Math.max(0.35, Math.min(2.5, this.view.k * f));
    const r = this.svg.getBoundingClientRect();
    const px = cx === undefined ? r.width / 2 : cx - r.left, py = cy === undefined ? r.height / 2 : cy - r.top;
    this.view.x = px - (px - this.view.x) * (k / this.view.k);
    this.view.y = py - (py - this.view.y) * (k / this.view.k);
    this.view.k = k;
    this.draw();
  }

  fit() {
    const ns = this.graph.nodes || [];
    if (!ns.length) { this.view = { x: 0, y: 0, k: 1 }; this.draw(); return; }
    const xs = ns.map((n) => n.x || 0), ys = ns.map((n) => n.y || 0);
    const minx = Math.min(...xs), miny = Math.min(...ys), maxx = Math.max(...xs) + W, maxy = Math.max(...ys) + H;
    const bw = this.host.clientWidth || 800, bh = this.host.clientHeight || 470;
    this.view.k = Math.max(0.35, Math.min(1.4, Math.min((bw - 40) / (maxx - minx || 1), (bh - 40) / (maxy - miny || 1))));
    this.view.x = 20 - minx * this.view.k;
    this.view.y = 20 - miny * this.view.k;
    this.draw();
  }

  /* Scroll a step into view without changing the zoom. */
  reveal(key) {
    const n = nodeByKey(this.graph, key);
    if (!n) return;
    const bw = this.host.clientWidth || 800, bh = this.host.clientHeight || 470;
    this.view.x = bw / 2 - (n.x + W / 2) * this.view.k;
    this.view.y = bh / 2 - (n.y + H / 2) * this.view.k;
    this.draw();
  }

  /* ── input ────────────────────────────────────────────────────────── */
  nodeDown(ev, n) {
    ev.stopPropagation();
    this.svg.focus({ preventScroll: true });
    if (this.connectFrom !== null) {
      const from = this.connectFrom;
      this.connectFrom = null;
      this.hint("");
      if (from !== n.key && this.o.onConnect) this.o.onConnect(from, n.key);
      return;
    }
    this.select({ type: "node", key: n.key });
    this.drag = { type: "node", node: n, x: ev.clientX, y: ev.clientY, nx: n.x || 0, ny: n.y || 0, moved: false };
    ev.preventDefault();
  }

  portDown(ev, n) {
    ev.stopPropagation();
    ev.preventDefault();
    this.drag = { type: "port", node: n };
    this.root.appendChild(this.rubber);
    this.rubber.setAttribute("visibility", "visible");
  }

  move(ev) {
    const d = this.drag;
    if (!d) return;
    if (d.type === "pan") {
      this.view.x = d.vx + (ev.clientX - d.x);
      this.view.y = d.vy + (ev.clientY - d.y);
      this.draw();
    } else if (d.type === "node") {
      const dx = (ev.clientX - d.x) / this.view.k, dy = (ev.clientY - d.y) / this.view.k;
      if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true;
      if (!d.moved) return;
      d.node.x = Math.round(d.nx + dx);
      d.node.y = Math.round(d.ny + dy);
      this.draw();
    } else if (d.type === "port") {
      const p = this.toGraph(ev.clientX, ev.clientY);
      const x1 = d.node.x + W / 2, y1 = d.node.y + H;
      this.rubber.setAttribute("d", `M ${x1} ${y1} L ${p.x} ${p.y}`);
    }
  }

  up(ev) {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.type === "node" && d.moved) this.changed();
    if (d.type === "port") {
      this.rubber.setAttribute("visibility", "hidden");
      const p = this.toGraph(ev.clientX, ev.clientY);
      const hit = (this.graph.nodes || []).find((n) => p.x >= n.x && p.x <= n.x + W && p.y >= n.y && p.y <= n.y + H);
      if (hit && hit.key !== d.node.key && this.o.onConnect) this.o.onConnect(d.node.key, hit.key);
    }
  }

  key(ev) {
    const s = this.sel;
    if (ev.key === "Escape") { this.connectFrom = null; this.hint(""); return; }
    if (ev.key === "+" || ev.key === "=") { this.zoomAt(1.1); ev.preventDefault(); return; }
    if (ev.key === "-") { this.zoomAt(0.9); ev.preventDefault(); return; }
    if (!s) return;
    if ((ev.key === "Delete" || ev.key === "Backspace") && this.o.onDelete) { ev.preventDefault(); this.o.onDelete(s); return; }
    if (s.type === "node" && ev.key.startsWith("Arrow")) {
      const n = nodeByKey(this.graph, s.key);
      if (!n) return;
      const step = ev.shiftKey ? 50 : 10;
      if (ev.key === "ArrowLeft") n.x -= step;
      if (ev.key === "ArrowRight") n.x += step;
      if (ev.key === "ArrowUp") n.y -= step;
      if (ev.key === "ArrowDown") n.y += step;
      ev.preventDefault();
      this.draw();
      this.changed();
    }
  }

  /* ── drawing ──────────────────────────────────────────────────────── */
  edgeText(e) {
    const br = this.branch[e.branch] || this.branch.always || { t: e.branch };
    const a = nodeByKey(this.graph, e.src);
    const p = (a && a.kind === "ask" && a.params) || {};
    if (e.branch === "yes" && p.pass_label) return p.pass_label;
    if (e.branch === "no" && p.fail_label) return p.fail_label;
    return br.t;
  }

  draw() {
    const root = this.root;
    while (root.firstChild) root.removeChild(root.firstChild);
    root.setAttribute("transform", `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`);
    const nodes = this.graph.nodes || [], edges = this.graph.edges || [];
    this.svg.setAttribute("aria-label", `Diagram with ${nodes.length} steps and ${edges.length} arrows. Use the inspector to edit; with the diagram focused, arrow keys move the selected step and Delete removes it.`);
    const pairLabel = {}, pairFirst = {};
    edges.forEach((e, i) => {
      const t = this.edgeText(e), pk = e.src + "\u0000" + e.dst;
      if (!(pk in pairFirst)) { pairFirst[pk] = i; pairLabel[pk] = []; }
      if (t) pairLabel[pk].push(t);
    });
    edges.forEach((e, i) => {
      const a = nodeByKey(this.graph, e.src), b = nodeByKey(this.graph, e.dst);
      if (!a || !b) return;
      const br = this.branch[e.branch || "always"] || { c: "#3D4550", m: "pa0", t: e.branch };
      const g = anchors(a, b);
      let d, lx, ly;
      if (g.side) {
        const mx = (g.x1 + g.x2) / 2;
        d = `M ${g.x1} ${g.y1} C ${mx} ${g.y1}, ${mx} ${g.y2}, ${g.x2} ${g.y2}`;
        lx = mx; ly = (g.y1 + g.y2) / 2;
      } else {
        const my = (g.y1 + g.y2) / 2;
        d = `M ${g.x1} ${g.y1} C ${g.x1} ${my}, ${g.x2} ${my}, ${g.x2} ${g.y2}`;
        lx = (g.x1 + g.x2) / 2; ly = my;
      }
      const hit = el("path", { d, fill: "none", stroke: "transparent", "stroke-width": 12, class: "ed-edge-hit", style: "cursor:pointer" });
      hit.addEventListener("pointerdown", (ev) => { ev.stopPropagation(); this.select({ type: "edge", index: i }); });
      root.appendChild(hit);
      const selected = this.sel && this.sel.type === "edge" && this.sel.index === i;
      root.appendChild(el("path", { d, fill: "none", stroke: br.c, "stroke-width": selected ? 3 : 1.6, "marker-end": `url(#sbm-${br.m})`, "pointer-events": "none" }));
      const pk = e.src + "\u0000" + e.dst;
      if (pairFirst[pk] === i && pairLabel[pk].length) {
        const t = el("text", { x: lx, y: ly - 3, "text-anchor": "middle", fill: br.c, "font-size": "10", "font-family": "Inter, sans-serif", "pointer-events": "none", "paint-order": "stroke", stroke: "#FFFFFF", "stroke-width": "3" });
        t.textContent = pairLabel[pk].join(" / ");
        root.appendChild(t);
      }
    });
    nodes.forEach((n) => this.drawNode(n));
  }

  drawNode(n) {
    const kind = this.kinds[n.kind];
    const known = !!kind;
    const k = kind || { label: n.kind || "?", colour: "#3D4550", writes: false };
    const g = el("g", { transform: `translate(${n.x || 0},${n.y || 0})`, class: "ed-node", "data-key": n.key });
    g.style.cursor = "move";
    const selected = this.sel && this.sel.type === "node" && this.sel.key === n.key;
    const probs = this.problems[n.key] || [];
    const ran = this.overlay[n.key];
    const oc = ran ? (OUTCOME_COLOUR[ran.outcome] || "#3D4550") : null;
    let stroke = "#C1CBDD", sw = 1;
    if (oc) { stroke = oc; sw = 2; }
    if (probs.length) { stroke = "#8B1C2A"; sw = 2; }
    if (selected) { stroke = "#0A3F9F"; sw = 2.5; }
    g.appendChild(el("rect", { width: W, height: H, rx: 6, fill: known ? "#FFFFFF" : "#F4F5F7", stroke, "stroke-width": sw, "stroke-dasharray": known ? null : "5 3" }));
    g.appendChild(el("rect", { width: 4, height: H, rx: 2, fill: k.colour || "#3D4550" }));
    wrapLabel(n.label || n.key, 26).forEach((ln, i) => {
      const t = el("text", { x: 12, y: 17 + i * 14, "font-size": "12", "font-weight": "600", fill: "#172B4D", "font-family": "Inter, sans-serif" });
      t.textContent = ln;
      g.appendChild(t);
    });
    const tip = el("title");
    tip.textContent = (n.label || n.key) + (probs.length ? "\n" + probs.join("\n") : "") + (ran ? `\nlast rehearsal: ${ran.outcome}${ran.detail ? " — " + ran.detail : ""}` : "");
    g.appendChild(tip);
    const t2 = el("text", { x: 12, y: 49, "font-size": "10", fill: "#51607F", "font-family": "Inter, sans-serif" });
    t2.textContent = (known ? k.label : "unknown kind " + (n.kind || "?")) + " · " + n.key;
    g.appendChild(t2);
    if (probs.length) {
      g.appendChild(el("circle", { cx: W - 2, cy: 2, r: 9, fill: "#8B1C2A" }));
      const c = el("text", { x: W - 2, y: 6, "text-anchor": "middle", "font-size": "10", "font-weight": "700", fill: "#FFFFFF", "font-family": "Inter, sans-serif" });
      c.textContent = String(probs.length);
      g.appendChild(c);
    } else if (k.writes) {
      const w = el("text", { x: W - 10, y: 19, "font-size": "10", "text-anchor": "end", fill: "#8B1C2A", "font-family": "Inter, sans-serif" });
      w.textContent = "writes";
      g.appendChild(w);
    }
    if (ran) {
      const b = el("text", { x: W - 10, y: 49, "font-size": "10", "text-anchor": "end", "font-weight": "600", fill: oc, "font-family": "Inter, sans-serif" });
      b.textContent = ran.outcome;
      g.appendChild(b);
    }
    g.addEventListener("pointerdown", (ev) => this.nodeDown(ev, n));
    if (n.kind !== "end" && n.kind !== "fail") {
      const port = el("circle", { cx: W / 2, cy: H, r: 5.5, fill: "#FFFFFF", stroke: "#0A3F9F", "stroke-width": 1.5, class: "ed-port" });
      port.style.cursor = "crosshair";
      const pt = el("title");
      pt.textContent = "Drag to a step to add an arrow";
      port.appendChild(pt);
      port.addEventListener("pointerdown", (ev) => this.portDown(ev, n));
      g.appendChild(port);
    }
    this.root.appendChild(g);
  }
}
