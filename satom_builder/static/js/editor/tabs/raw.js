/* Raw JSON tab: the whole project, editable. Apply (or Save) parses it as
 * JSON first; only the editable keys are taken (id, kind, assets,
 * updated_at, last_check stay the server's). */
import { h, clear, field, textarea, button, card, say } from "../ui.js";
import { parseRaw, applyPayload, READ_ONLY } from "../model.js";

const S = { el: null, base: "" };

function text(ctx) { return JSON.stringify(ctx.project, null, 2); }

export default {
  id: "raw", label: "Raw JSON", icon: "bi-braces",

  render(ctx, panel) {
    clear(panel);
    S.base = text(ctx);
    S.el = textarea(S.base, { id: "raw-json", rows: 30, class: "form-control form-control-sm ed-mono", spellcheck: "false" });
    const msg = h("div", { class: "ed-status", "aria-live": "polite", id: "raw-status" });
    S.msg = msg;
    const apply = button("Apply", { class: "btn btn-sm btn-fw-primary", id: "raw-apply" }, "bi-check2");
    apply.addEventListener("click", () => this.apply(ctx));
    const reset = button("Reset", { id: "raw-reset" }, "bi-arrow-counterclockwise");
    reset.addEventListener("click", () => { S.el.value = text(ctx); S.base = S.el.value; say(msg, "", "info"); });
    panel.appendChild(card("Raw JSON", [
      h("p", { class: "ed-help", text: `Everything the editor holds, including keys no tab shows. ${READ_ONLY.join(", ")} belong to the server and are not changed from here.` }),
      field("Project (JSON)", S.el), h("div", { class: "ed-inline" }, apply, reset), msg], { icon: "bi-braces" }));
  },

  /* true when there is nothing pending or it applied; false on bad JSON. */
  apply(ctx) {
    if (!S.el || !document.body.contains(S.el) || S.el.value === S.base) return true;
    const r = parseRaw(S.el.value);
    if (!r.ok) { say(S.msg, r.error, "bad"); S.el.focus(); return false; }
    applyPayload(ctx.project, r.value);
    S.base = S.el.value;
    ctx.changed();
    say(S.msg, "Applied.", "ok");
    return true;
  },

  pending() { return !!(S.el && document.body.contains(S.el) && S.el.value !== S.base); },

  reveal(ctx, t) {
    if (!t.where || !S.el) return;
    const key = String(t.where).split(/[.[\]]/).filter(Boolean).pop();
    const i = key ? S.el.value.indexOf(`"${key}"`) : -1;
    if (i >= 0) { S.el.focus(); S.el.setSelectionRange(i, i + key.length + 2); }
  },
};
