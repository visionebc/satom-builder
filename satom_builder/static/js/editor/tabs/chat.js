/* Assistant tab: conversations with an AI profile about this project
 * (satom_builder/chat.py). Everything is kept on the Builder: the
 * conversations, their files, each reply's status and the draft it proposed.
 * A reply goes on if the page is closed; coming back shows it. A draft
 * reaches the project only with "Apply to the project" (a normal save: the
 * previous draft stays as a revision). */
import { h, clear, field, textarea, select, button, iconButton, badge, card, empty, say } from "../ui.js";
import { renderActivity, clock } from "../activity.js";

const S = { pid: null, chats: null, chat: null, current: null, profiles: null, pending: [], timer: null, rounds: 3, profile: "" };
const KEY = (pid) => `sb-chat-${pid}`;
const OPEN_ACT = new Set();      // finished replies whose activity the author opened
const ACCEPT = "image/png,image/jpeg,image/gif,image/webp,.txt,.md,.json,.csv,.tsv,.yaml,.yml,.xml,.log,.py,.js,.css,.html,.jinja,.j2,.sql,.ini,.conf,.cfg,.toml,.sh";

function when(ts) { return ts ? new Date(ts * 1000).toLocaleString() : ""; }
function size(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }

async function loadList(ctx) {
  S.chats = (await ctx.api.req("GET", `/api/projects/${ctx.project.id}/chats`)).chats;
}

async function open(ctx, cid) {
  S.current = cid;
  try { localStorage.setItem(KEY(ctx.project.id), String(cid)); } catch (e) { /* private mode */ }
  S.chat = await ctx.api.req("GET", `/api/chats/${cid}`);
  if (S.chat.profile_id) S.profile = String(S.chat.profile_id);
}

function schedule(ctx, rerender) {
  clearTimeout(S.timer);
  if (!S.chat || !S.chat.running) return;
  S.timer = setTimeout(async () => {
    if (document.getElementById("chat-thread") === null) return;     // tab left
    try { await open(ctx, S.current); await loadList(ctx); } catch (e) { return; }
    rerender();
  }, 1500);
}

function fileChip(ctx, f) {
  const a = h("a", { href: `/api/chats/${S.current}/files/${f.id}`, target: "_blank", rel: "noopener", class: "ed-chip" },
    h("i", { class: "bi " + (f.kind === "image" ? "bi-image" : "bi-file-earmark-text"), "aria-hidden": "true" }), " ", f.name, " ", h("small", { class: "text-muted", text: size(f.size) }));
  return a;
}

function messageView(ctx, m, rerender) {
  if (m.role === "user") {
    return h("div", { class: "ed-msg ed-msg-user", id: `chat-m-${m.id}` },
      h("div", { class: "ed-msg-meta", text: "You · " + when(m.created_at) }),
      h("div", { class: "ed-msg-text", text: m.content }),
      (m.files || []).length ? h("div", { class: "ed-msg-files" }, m.files.map((f) => fileChip(ctx, f))) : null);
  }
  const kids = [h("div", { class: "ed-msg-meta" }, "Assistant", m.profile ? " · " + m.profile : "", " · ", when(m.updated_at), " ",
    m.status === "running" ? badge("writing…", "info") : m.status === "failed" ? badge("failed", "danger") : null,
    m.applied_at ? badge("applied", "success") : null)];
  if (m.status === "running") {
    kids.push(h("div", { class: "ed-msg-text text-muted", text: "Working on the reply. You can leave this page: it goes on, and it is here when you come back." }));
    kids.push(renderActivity(h("div", { id: `chat-act-${m.id}` }), m.progress, { key: `chat-${m.id}`, running: true }));
  } else if (m.progress && (m.progress.steps || []).length) {
    const steps = m.progress.steps;
    const took = steps[steps.length - 1].at - m.progress.started;
    const d = h("details", { class: "ed-act-wrap", id: `chat-act-${m.id}` },
      h("summary", { text: `What it did (${steps.length} steps, ${clock(took)})` }));
    d.open = OPEN_ACT.has(m.id);
    d.addEventListener("toggle", () => { if (d.open) OPEN_ACT.add(m.id); else OPEN_ACT.delete(m.id); });
    d.appendChild(renderActivity(h("div"), m.progress, { key: `chat-${m.id}`, running: false }));
    kids.push(d);
  }
  if (m.status === "failed") kids.push(h("div", { class: "ed-msg-text ed-tone-bad", text: m.error || "The reply failed." }));
  if (m.status === "done") kids.push(h("div", { class: "ed-msg-text", text: m.content }));
  const rounds = (m.rounds || []).filter((r) => !r.message_only);
  if (rounds.length) {
    kids.push(h("div", { class: "ed-msg-rounds" }, rounds.map((r) => [badge(`round ${r.n}: ${r.ok ? "no problems" : (r.problems || []).length + " problem(s)"}`, r.ok ? "success" : "warning"), " "])));
    const last = rounds[rounds.length - 1];
    if (m.status === "done" && last && !last.ok && (last.problems || []).length) {
      kids.push(h("details", null, h("summary", { text: "Problems left" }), h("ul", { class: "ed-issue-list" }, last.problems.map((p) => h("li", { class: "ed-tone-bad", text: (p.where ? p.where + ": " : "") + (p.message || p) })))));
    }
  }
  if (m.status === "done" && m.has_draft) {
    const show = button("Show the draft", { id: `chat-draft-${m.id}` }, "bi-braces");
    const box = h("div");
    show.addEventListener("click", async () => {
      if (box.firstChild) { clear(box); return; }
      try { const d = await ctx.api.req("GET", `/api/chats/${S.current}/messages/${m.id}/draft`); box.appendChild(h("pre", { class: "ed-pre ed-mono ed-scroll", text: JSON.stringify(d.draft, null, 2) })); } catch (e) { box.appendChild(h("div", { class: "ed-tone-bad", text: e.message })); }
    });
    const apply = button(m.applied_at ? "Apply again" : "Apply to the project", { class: "btn btn-sm " + (m.applied_at ? "btn-fw-outline" : "btn-fw-primary"), id: `chat-apply-${m.id}` }, "bi-check2-circle");
    const st = h("span", { class: "ed-status", "aria-live": "polite" });
    apply.addEventListener("click", async () => {
      if ((window.SatomBuilderEditor && window.SatomBuilderEditor.ctx && ctx.ensureSaved) && !(await ctx.ensureSaved())) { say(st, "Save or discard your own changes first.", "bad"); return; }
      say(st, "Applying…", "busy");
      try {
        const r = await ctx.api.req("POST", `/api/chats/${S.current}/messages/${m.id}/apply`, {});
        ctx.replaceProject(r.project);
        await open(ctx, S.current);
        rerender();
        ctx.say("The assistant's draft is now the project (the previous one is kept as a revision). Check and test it.", "ok");
      } catch (e) { say(st, e.message, "bad"); }
    });
    kids.push(h("div", { class: "ed-inline" }, apply, show, st), box);
  }
  return h("div", { class: "ed-msg ed-msg-ai", id: `chat-m-${m.id}` }, kids);
}

export default {
  id: "chat", label: "Assistant", icon: "bi-chat-dots",

  render(ctx, panel) {
    const rerender = () => this.render(ctx, panel);
    if (S.pid !== ctx.project.id) { S.pid = ctx.project.id; S.chats = null; S.chat = null; S.current = null; S.pending = []; }
    if (!S.chats || !S.profiles) {
      clear(panel).appendChild(empty("Loading the conversations…"));
      Promise.all([loadList(ctx), ctx.api.aiProfiles().then((p) => { S.profiles = p; })]).then(async () => {
        let want = null;
        try { want = Number(localStorage.getItem(KEY(ctx.project.id))) || null; } catch (e) { want = null; }
        const pick = S.chats.find((c) => c.id === want) || S.chats[0];
        if (pick) await open(ctx, pick.id);
        rerender();
      }).catch((e) => { clear(panel).appendChild(h("div", { class: "fw-alert fw-alert-warning", text: "The assistant could not be loaded: " + e.message })); });
      return;
    }
    clear(panel);
    const newBtn = button("New conversation", { class: "btn btn-sm btn-fw-primary", id: "chat-new" }, "bi-plus-lg");
    newBtn.addEventListener("click", async () => {
      try {
        const c = await ctx.api.req("POST", `/api/projects/${ctx.project.id}/chats`, S.profile ? { profile_id: Number(S.profile) } : {});
        await loadList(ctx);
        await open(ctx, c.id);
        rerender();
        const ta = document.getElementById("chat-text");
        if (ta) ta.focus();
      } catch (e) { ctx.say(e.message, "bad"); }
    });
    const list = h("nav", { class: "ed-subnav ed-chat-list", "aria-label": "Conversations", id: "chat-list" });
    S.chats.forEach((c) => {
      const b = h("button", { type: "button", class: "ed-subnav-item" + (c.id === S.current ? " is-selected" : ""), "aria-current": c.id === S.current ? "true" : null, id: `chat-pick-${c.id}` },
        h("strong", { text: c.title || "New conversation" }), h("small", { class: "text-muted", text: `${c.messages} message(s) · ${when(c.updated_at)}` }), c.running ? badge("writing…", "info") : null);
      b.addEventListener("click", async () => { await open(ctx, c.id); rerender(); });
      list.appendChild(b);
    });
    if (!S.chats.length) list.appendChild(empty("No conversation yet."));
    const side = h("div", { class: "ed-chat-side" }, newBtn, list);
    let main;
    if (!S.chat) {
      main = h("div", { class: "ed-chat-main" }, empty("Start a conversation: describe what to build or change, attach a mockup or a sample file, ask how something works."));
    } else {
      const thread = h("div", { class: "ed-chat-thread", id: "chat-thread", "aria-live": "polite" });
      (S.chat.messages || []).forEach((m) => thread.appendChild(messageView(ctx, m, rerender)));
      if (!(S.chat.messages || []).length) thread.appendChild(empty("No message yet."));
      const prof = select((S.profiles || []).map((p) => ({ value: String(p.id), label: `${p.name} (${p.model})` })), S.profile || (S.profiles[0] ? String(S.profiles[0].id) : ""), { id: "chat-profile" }, S.profiles.length ? null : "No AI profile: an administrator adds one");
      prof.addEventListener("change", () => { S.profile = prof.value; });
      if (!S.profile && S.profiles[0]) S.profile = String(S.profiles[0].id);
      const ta = textarea("", { id: "chat-text", rows: 3, placeholder: "Write to the assistant… (Ctrl+Enter sends)" });
      const fileIn = h("input", { type: "file", multiple: true, accept: ACCEPT, class: "visually-hidden", id: "chat-file" });
      const attach = h("label", { class: "btn btn-sm btn-fw-outline mb-0", for: "chat-file" }, h("i", { class: "bi bi-paperclip", "aria-hidden": "true" }), " Attach");
      const chips = h("div", { class: "ed-msg-files", id: "chat-pending" });
      const st = h("span", { class: "ed-status", id: "chat-status", "aria-live": "polite" });
      const drawPending = () => {
        clear(chips);
        S.pending.forEach((f, i) => {
          const x = iconButton("bi-x", `Remove ${f.name}`, { class: "btn btn-sm btn-link p-0" });
          x.addEventListener("click", () => { S.pending.splice(i, 1); drawPending(); });
          chips.appendChild(h("span", { class: "ed-chip" }, h("i", { class: "bi " + (f.kind === "image" ? "bi-image" : "bi-file-earmark-text"), "aria-hidden": "true" }), " ", f.name, " ", x));
        });
      };
      drawPending();
      fileIn.addEventListener("change", async () => {
        for (const f of Array.from(fileIn.files || [])) {
          const fd = new FormData();
          fd.append("file", f);
          say(st, `Uploading ${f.name}…`, "busy");
          try { S.pending.push(await ctx.api.req("POST", `/api/chats/${S.current}/files`, fd)); say(st, "", "info"); } catch (e) { say(st, `${f.name}: ${e.message}`, "bad"); }
        }
        fileIn.value = "";
        drawPending();
      });
      const rounds = h("input", { class: "form-control form-control-sm", type: "number", min: 1, max: 6, value: S.rounds, id: "chat-rounds" });
      rounds.addEventListener("change", () => { S.rounds = Math.max(1, Math.min(6, Number(rounds.value) || 3)); });
      const send = button("Send", { class: "btn btn-sm btn-fw-primary", id: "chat-send", disabled: !!S.chat.running }, "bi-send");
      const go = async () => {
        if (!ta.value.trim()) { say(st, "Write a message first.", "bad"); return; }
        if (!S.profile) { say(st, "Pick an AI profile.", "bad"); return; }
        say(st, "Saving the project and sending…", "busy");
        if (!(await ctx.ensureSaved())) { say(st, "Not sent: the project could not be saved.", "bad"); return; }
        try {
          await ctx.api.req("POST", `/api/chats/${S.current}/messages`, { text: ta.value, files: S.pending.map((f) => f.id), profile_id: Number(S.profile), max_rounds: S.rounds });
          S.pending = [];
          await open(ctx, S.current);
          await loadList(ctx);
          rerender();
        } catch (e) { say(st, e.message, "bad"); }
      };
      send.addEventListener("click", go);
      ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); go(); } });
      const del = iconButton("bi-trash", "Delete this conversation", { class: "btn btn-sm btn-fw-outline ed-icon-btn ed-danger", id: "chat-delete" });
      del.addEventListener("click", async () => {
        if (!window.confirm("Delete this conversation and its files?")) return;
        try { await ctx.api.req("DELETE", `/api/chats/${S.current}`); S.chat = null; S.current = null; await loadList(ctx); rerender(); } catch (e) { say(st, e.message, "bad"); }
      });
      main = h("div", { class: "ed-chat-main" },
        h("div", { class: "ed-inline ed-chat-head" }, h("strong", { text: S.chat.title || "New conversation" }), del),
        thread,
        h("div", { class: "ed-chat-compose" }, ta, chips,
          h("div", { class: "ed-inline" }, attach, fileIn, field("AI profile", prof, { cls: "ed-chat-prof" }), field("Repair rounds", rounds, { cls: "ed-chat-rounds" }), send, st)),
        h("p", { class: "ed-help", text: "Sent to the AI provider: the author guide, the target's names and shapes, the saved draft, this conversation and the files you attach. Never data rows or secrets. Images reach only a model that reads images." }));
    }
    panel.appendChild(card("Assistant", h("div", { class: "ed-chat" }, side, main), { icon: "bi-chat-dots", id: "chat-card" }));
    const th = document.getElementById("chat-thread");
    if (th) th.scrollTop = th.scrollHeight;
    schedule(ctx, rerender);
  },

  reveal() {},
};
