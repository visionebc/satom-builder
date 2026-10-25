/* A plain code editor: a monospace textarea with a line-number gutter, Tab
 * indents, and lint markers per line. No dependency, no highlighting.
 *
 * Keyboard: Tab / Shift+Tab indent and outdent (two spaces). To move the
 * focus out with the keyboard press Esc, then Tab (WCAG 2.1.2: no trap). */
import { h, uid } from "./ui.js";

const INDENT = "  ";

export class CodeEditor {
  constructor(opts) {
    const o = opts || {};
    this.onChange = o.onChange || (() => {});
    this.markers = {};
    this.escaped = false;
    const id = o.id || uid("code");
    this.gutter = h("div", { class: "ed-code-gutter", "aria-hidden": "true" });
    this.ta = h("textarea", {
      id, class: "ed-code-text", spellcheck: "false", autocomplete: "off", autocapitalize: "off",
      wrap: "off", rows: o.rows || 18, "aria-describedby": id + "-kbd", "data-lang": o.lang || "",
    });
    this.ta.value = o.value || "";
    this.el = h("div", { class: "ed-code" },
      h("label", { class: "form-label ed-label", for: id, text: o.label || "Code" }),
      h("div", { class: "ed-code-box" }, this.gutter, this.ta),
      h("div", { class: "form-text ed-help", id: id + "-kbd", text: "Tab indents. Press Esc, then Tab, to leave the editor." }));
    this.ta.addEventListener("input", () => { this.renderGutter(); this.onChange(this.ta.value); });
    this.ta.addEventListener("scroll", () => { this.gutter.scrollTop = this.ta.scrollTop; });
    this.ta.addEventListener("keydown", (e) => this.key(e));
    this.renderGutter();
  }

  get value() { return this.ta.value; }
  set value(v) { this.ta.value = v || ""; this.renderGutter(); }

  key(e) {
    if (e.key === "Escape") { this.escaped = true; return; }
    if (e.key !== "Tab" || e.ctrlKey || e.metaKey || e.altKey) { this.escaped = false; return; }
    if (this.escaped) { this.escaped = false; return; } // let the focus move on
    e.preventDefault();
    const ta = this.ta, v = ta.value;
    const s = ta.selectionStart, en = ta.selectionEnd;
    const lineStart = v.lastIndexOf("\n", s - 1) + 1;
    if (s === en && !e.shiftKey) {
      ta.setRangeText(INDENT, s, en, "end");
    } else {
      const block = v.slice(lineStart, en);
      const lines = block.split("\n");
      const out = e.shiftKey
        ? lines.map((l) => (l.startsWith(INDENT) ? l.slice(INDENT.length) : l.replace(/^ /, "")))
        : lines.map((l) => INDENT + l);
      const text = out.join("\n");
      ta.setRangeText(text, lineStart, en, "preserve");
      ta.selectionStart = lineStart;
      ta.selectionEnd = lineStart + text.length;
    }
    this.renderGutter();
    this.onChange(ta.value);
  }

  renderGutter() {
    const n = Math.max(1, this.ta.value.split("\n").length);
    const g = this.gutter;
    while (g.childNodes.length > n) g.removeChild(g.lastChild);
    for (let i = g.childNodes.length + 1; i <= n; i++) g.appendChild(h("div", { class: "ed-code-ln", text: String(i) }));
    Array.from(g.childNodes).forEach((d, i) => {
      const m = this.markers[i + 1];
      d.classList.toggle("ed-code-ln-bad", !!(m && m.tone === "bad"));
      d.classList.toggle("ed-code-ln-warn", !!(m && m.tone === "warn"));
      d.title = m ? m.messages.join("\n") : "";
    });
    g.scrollTop = this.ta.scrollTop;
  }

  /* markers: [{line, message, tone: "bad"|"warn"}] */
  setMarkers(list) {
    this.markers = {};
    (list || []).forEach((m) => {
      if (!m.line) return;
      const cur = this.markers[m.line] || { tone: "warn", messages: [] };
      if (m.tone === "bad") cur.tone = "bad";
      cur.messages.push(m.message);
      this.markers[m.line] = cur;
    });
    this.renderGutter();
  }

  gotoLine(line) {
    const lines = this.ta.value.split("\n");
    const n = Math.min(Math.max(1, line || 1), lines.length);
    let off = 0;
    for (let i = 0; i < n - 1; i++) off += lines[i].length + 1;
    this.ta.focus();
    this.ta.setSelectionRange(off, off + lines[n - 1].length);
    const lh = parseFloat(getComputedStyle(this.ta).lineHeight) || 18;
    this.ta.scrollTop = Math.max(0, (n - 4) * lh);
    this.gutter.scrollTop = this.ta.scrollTop;
  }
}
