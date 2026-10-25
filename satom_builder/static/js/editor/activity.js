/* What an AI job is doing (satom_builder/progress.py), for Generate and the
 * assistant chat: the steps with their times and states, a live line for the
 * model call (round, model, phase, tokens, rate, time) and what the model is
 * thinking and writing, as it streams. Rendered in place on every poll: the
 * open/closed state of each part and the scroll of the live text are kept
 * per key, so a re-render every 1.5 s never closes what the author opened. */
import { h, clear } from "./ui.js";

const OPEN = new Map();          // key -> {thinking, answer, steps}
const STICK = new Map();         // key + part -> follow the end of the text

const ICON = { run: "bi-arrow-repeat ed-act-spin", ok: "bi-check-circle-fill", warn: "bi-exclamation-triangle-fill", bad: "bi-x-octagon-fill" };
const PHASE = { reading: "reading the request", thinking: "thinking", writing: "writing the answer", done: "finished" };

export function clock(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/* elapsed and wait from the server's clock while the call runs (nothing is
 * saved while the model reads the request); from the saved values after */
export function timing(lv, now) {
  const end = lv.ended || (now != null ? Math.max(now, lv.started) : null);
  if (end == null) return { elapsed: lv.elapsed, wait: lv.wait };
  return { elapsed: end - lv.started, wait: (lv.first_at || end) - lv.started };
}

export function liveLine(lvIn, running, now) {
  if (!lvIn) return "";
  const lv = Object.assign({}, lvIn, timing(lvIn, now));
  const parts = [`Round ${lv.round}/${lv.rounds}`, lv.model, PHASE[lv.phase] || lv.phase];
  if (lv.phase === "reading") parts.push(`waiting for the first token ${clock(lv.wait)}`);
  else {
    if (lv.count) parts.push(`${lv.estimated ? "≈" : ""}${Number(lv.count).toLocaleString("en")} tokens`);
    if (lv.rate) parts.push(`${lv.rate} tok/s`);
    if (lv.wait >= 2) parts.push(`first token after ${clock(lv.wait)}`);
  }
  parts.push(running || lv.phase !== "done" ? `${clock(lv.elapsed)} elapsed` : `took ${clock(lv.elapsed)}`);
  return parts.join(" · ");
}

function state(key) {
  if (!OPEN.has(key)) OPEN.set(key, { thinking: true, answer: true, steps: true });
  return OPEN.get(key);
}

function liveText(key, part, title, text, chars, st) {
  const pre = h("pre", { class: "ed-pre ed-mono ed-act-text", "aria-live": "off", text: (chars > text.length ? "…" : "") + text });
  const d = h("details", { class: "ed-act-part", "data-part": part },
    h("summary", null, title, " ", h("small", { class: "ed-muted", text: `${Number(chars).toLocaleString("en")} characters` })), pre);
  d.open = st[part] !== false;
  d.addEventListener("toggle", () => { st[part] = d.open; });
  const sk = key + ":" + part;
  pre.addEventListener("scroll", () => { STICK.set(sk, pre.scrollHeight - pre.scrollTop - pre.clientHeight < 24); });
  requestAnimationFrame(() => { if (STICK.get(sk) !== false) pre.scrollTop = pre.scrollHeight; });
  return d;
}

/* Fill *box* with the activity of *progress* (null: nothing yet).
 * opts.key: what the open state is kept under; opts.running: the job runs. */
export function renderActivity(box, progress, opts) {
  const o = opts || {};
  const key = o.key || "ai";
  clear(box);
  box.classList.add("ed-act");
  if (!progress) {
    if (o.running) box.appendChild(h("p", { class: "ed-act-wait", text: "Starting…" }));
    return box;
  }
  const st = state(key);
  const t0 = progress.started || 0;
  const lv = progress.live;
  if (lv) {
    box.appendChild(h("div", { class: "ed-act-live", role: "status", "aria-live": "polite", "data-phase": lv.phase },
      h("i", { class: "bi " + (lv.phase === "done" ? "bi-cpu" : "bi-cpu ed-act-pulse"), "aria-hidden": "true" }), " ",
      h("span", { class: "ed-act-line", text: liveLine(lv, o.running, o.running ? progress.server_now : null) })));
    if (lv.thinking) box.appendChild(liveText(key, "thinking", "What the model is thinking", lv.thinking, lv.thinking_chars, st));
    if (lv.answer) box.appendChild(liveText(key, "answer", "What it is writing", lv.answer, lv.answer_chars, st));
    if (lv.phase === "reading" && !lv.thinking && !lv.answer) {
      box.appendChild(h("p", { class: "ed-help ed-act-wait", text: "The model reads the whole request (the author guide, the target and the draft) before it writes the first token; a local model can take a minute or more here." }));
    }
  }
  const steps = progress.steps || [];
  const list = h("ol", { class: "ed-act-steps", "aria-label": "What was done" },
    steps.map((s) => h("li", { class: "ed-act-step", "data-state": s.state },
      h("i", { class: "bi " + (ICON[s.state] || ICON.ok), "aria-hidden": "true" }),
      h("span", { class: "ed-act-time", text: "+" + clock(s.at - t0) }),
      h("span", { class: "ed-act-step-text" }, s.text, s.took != null && s.took >= 1 ? h("small", { class: "ed-muted", text: ` (${clock(s.took)})` }) : null,
        s.detail ? h("pre", { class: "ed-pre ed-act-detail", text: s.detail }) : null))));
  const d = h("details", { class: "ed-act-part", "data-part": "steps" }, h("summary", { text: `Steps (${steps.length})` }), list);
  d.open = st.steps !== false;
  d.addEventListener("toggle", () => { st.steps = d.open; });
  box.appendChild(d);
  return box;
}
