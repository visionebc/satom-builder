/* Sign-in tab: app.json "signin" (contract section 20) -- the app's own
 * sign-in page in SATOM, for people without a SATOM console account. Rules
 * name a directory connection of the target SATOM by id and kind (or "local"
 * for SATOM's own accounts) and the groups that may sign in; never a server,
 * a bind account or a secret: those stay on the SATOM server. The import
 * brings the sign-in switched off. */
import { h, clear, field, input, textarea, select, button, iconButton, badge, card, empty, checkbox, flash } from "../ui.js";
import { ensureApp, appBody } from "../model.js";
import { blank, limits, lines, problems, serverNotes, supported, unlines } from "../signin.js";

function ruleControls(r, i, pre, ctx, L) {
  const out = [];
  if (r.kind === "ad" || r.kind === "ldap") {
    const dns = textarea(lines(r.group_dns), { id: `${pre}-dns`, rows: 3, class: "form-control form-control-sm ed-mono", placeholder: "CN=noc,OU=Groups,DC=corp,DC=local" });
    dns.addEventListener("input", () => { r.group_dns = unlines(dns.value); ctx.changed(); });
    dns.addEventListener("change", () => ctx.changed({ rerender: true, focus: dns.id }));
    const flt = input(r.ldap_filter, { id: `${pre}-filter`, class: "form-control form-control-sm ed-mono", placeholder: "(department=NOC)" });
    flt.addEventListener("input", () => { r.ldap_filter = flt.value; ctx.changed(); });
    flt.addEventListener("change", () => ctx.changed({ rerender: true, focus: flt.id }));
    out.push(field("Member of any of these groups (one DN per line)", dns, { help: r.kind === "ad" ? "Nested groups count (AD matching rule in chain)." : null }));
    out.push(field("And match this LDAP filter (optional)", flt, { help: "Empty both = anyone the directory accepts." }));
  } else if (r.kind === "radius") {
    const g = textarea(lines(r.radius_groups), { id: `${pre}-groups`, rows: 2, placeholder: "satom-app-users" });
    g.addEventListener("input", () => { r.radius_groups = unlines(g.value); ctx.changed(); });
    g.addEventListener("change", () => ctx.changed({ rerender: true, focus: g.id }));
    out.push(field("The reply names any of these groups (one per line)", g, { help: "Fortinet-Group-Name in the FortiAuthenticator's Access-Accept. Empty = anyone the server accepts." }));
  } else {
    out.push(h("p", { class: "ed-intro", text: "A local SATOM account's password. It may open the app only when the app is shared with it in SATOM." }));
  }
  const two = checkbox("Require a SATOM authenticator code (set up at the first sign-in)", !!r.require_2fa, { id: `${pre}-2fa` });
  two.querySelector("input").addEventListener("change", (e) => { r.require_2fa = e.target.checked; ctx.changed({ rerender: true, focus: `${pre}-2fa` }); });
  out.push(two);
  return out;
}

export default {
  id: "signin", label: "Sign-in", icon: "bi-door-open",

  render(ctx, panel) {
    clear(panel);
    const L = limits(ctx.schema);
    const body = appBody(ctx.project);
    const si = body.signin && typeof body.signin === "object" && !Array.isArray(body.signin) ? body.signin : null;
    const rules = si && Array.isArray(si.rules) ? si.rules : [];
    const local = problems(body.signin, L);
    const notes = serverNotes(si, L);
    const conns = Object.keys(L.connections || {}).sort();
    const opts = [{ value: "local", label: L.kind_labels.local }]
      .concat(conns.map((c) => ({ value: c, label: `${c} (${L.kind_labels[L.connections[c].kind] || L.connections[c].kind})` })));
    const pick = select(opts, "local", { id: "si-pick" });
    const typed = input("", { id: "si-typed", placeholder: "or another connection id, e.g. corp-ad" });
    const typedKind = select(["ad", "ldap", "radius"], "ad", { id: "si-typed-kind" });
    const add = button("Add rule", { class: "btn btn-sm btn-fw-primary", id: "si-add", disabled: rules.length >= L.max_rules }, "bi-plus-lg");
    add.addEventListener("click", () => {
      const b = ensureApp(ctx.project);
      if (!b.signin || typeof b.signin !== "object" || Array.isArray(b.signin)) b.signin = { rules: [], profile: "", require_approval: false };
      const sig = b.signin;
      if (!Array.isArray(sig.rules)) sig.rules = [];
      const t = typed.value.trim();
      const r = t ? blank(t, typedKind.value, L)
        : pick.value === "local" ? blank("local", "local", L) : blank(pick.value, L.connections[pick.value].kind, L);
      sig.rules.push(r);
      ctx.changed({ rerender: true, focus: `si-${sig.rules.length - 1}` });
    });
    const tools = h("div", { class: "ed-inline" },
      h("label", { class: "visually-hidden", for: "si-pick", text: "Connection" }), pick,
      h("label", { class: "visually-hidden", for: "si-typed", text: "Connection id" }), typed,
      h("label", { class: "visually-hidden", for: "si-typed-kind", text: "Kind" }), typedKind, add);

    const list = h("div", { class: "ed-list" });
    if (!rules.length) list.appendChild(empty("No sign-in: the app is used by SATOM users it is shared with."));
    const top = ctx.issues().filter((x) => x.target.tab === "signin" && (x.target.id === null || x.target.id === undefined)).map((x) => x.message);
    top.concat(local.concat(notes).filter((p) => p.index < 0).map((p) => p.message))
      .forEach((m) => list.appendChild(h("div", { class: "ed-issue", role: "note", text: m })));
    rules.forEach((r, i) => {
      const pre = `si-${i}`;
      const issues = ctx.issuesFor("signin", String(i)).concat(local.filter((p) => p.index === i).map((p) => p.message));
      const warn = notes.filter((p) => p.index === i).map((p) => p.message);
      const tool = (ico, label, fn, extra) => iconButton(ico, label, { class: "btn btn-sm btn-fw-outline ed-icon-btn" + (extra || ""), on: { click: fn } });
      const move = (d) => () => { const j = i + d; if (j < 0 || j >= rules.length) return; const x = rules[i]; rules[i] = rules[j]; rules[j] = x; ctx.changed({ rerender: true }); };
      const title = r && r.kind === "local" ? L.kind_labels.local : `${(r && r.connection) || "?"} — ${L.kind_labels[r && r.kind] || (r && r.kind) || "?"}`;
      list.appendChild(h("div", { class: "ed-item" + (issues.length ? " ed-item-bad" : ""), id: pre, tabindex: "-1" },
        h("div", { class: "ed-item-head" }, badge(String(i + 1), "info"), " ", h("strong", { text: title }),
          h("div", { class: "ed-row-tools" }, tool("bi-arrow-up", `Move rule ${i + 1} up`, move(-1)), tool("bi-arrow-down", `Move rule ${i + 1} down`, move(1)),
            tool("bi-trash", `Remove rule ${i + 1}`, () => { rules.splice(i, 1); ctx.changed({ rerender: true }); }, " ed-danger"))),
        issues.length ? h("div", { class: "ed-issue", role: "note" }, issues.map((m) => h("div", { text: m }))) : null,
        warn.length ? h("div", { class: "ed-issue ed-issue-warn", role: "note" }, warn.map((m) => h("div", { text: m }))) : null,
        r && typeof r === "object" ? h("div", { class: "ed-grid ed-grid-2" }, ruleControls(r, i, pre, ctx, L)) : null));
    });

    const settings = [];
    if (si) {
      const prof = L.profiles && L.profiles.length
        ? select(L.profiles, si.profile || "", { id: "si-profile" }, "— (an administrator chooses in SATOM)")
        : input(si.profile || "", { id: "si-profile", placeholder: "App people" });
      prof.addEventListener("change", () => { si.profile = prof.value; ctx.changed({ rerender: true, focus: prof.id }); });
      const appr = checkbox("New accounts wait for an administrator (Users)", !!si.require_approval, { id: "si-approval" });
      appr.querySelector("input").addEventListener("change", (e) => { si.require_approval = e.target.checked; ctx.changed({ rerender: true, focus: "si-approval" }); });
      settings.push(h("div", { class: "ed-grid ed-grid-2" },
        field("Apps only profile of the accounts a sign-in creates", prof, { help: "A profile of the SATOM server, by name. The import leaves it empty when the server has none of that name." }), appr));
      const off = button("Remove the sign-in", { class: "btn btn-sm btn-fw-outline ed-danger", id: "si-remove" }, "bi-x-circle");
      off.addEventListener("click", () => { delete appBody(ctx.project).signin; ctx.changed({ rerender: true }); });
      settings.push(off);
    }
    const old = !supported(ctx.schema)
      ? h("div", { class: "ed-issue", role: "note", id: "si-old", text: "This target's SATOM does not know an app's sign-in yet (contract section 20): it would install the app without it. Update SATOM first." })
      : null;
    const none = supported(ctx.schema) && !conns.length
      ? h("p", { class: "ed-intro", text: "The target SATOM has no directory connection yet: an administrator adds them in Plugin Studio › Sign-in directories. You can still name one by its id; the import leaves the rule out until it exists." })
      : null;
    panel.appendChild(card("Sign-in", [
      old,
      h("p", { class: "ed-intro", text: "The app's own sign-in page in SATOM, for people who have no SATOM console account: they see this app and nothing else. "
        + "Each rule names a directory connection of the SATOM server (Active Directory, LDAP or RADIUS / FortiAuthenticator) by id, or SATOM's local accounts, and which groups may sign in. "
        + `Rules are tried in order. Never a server, an account or a secret: those stay in SATOM. The import brings the sign-in switched off. ${rules.length} / ${L.max_rules}.` }),
      none, list, ...settings,
    ], { icon: "bi-door-open", tools }));
  },

  reveal(ctx, t) {
    const el = document.getElementById(`si-${Number(t.id) >= 0 ? Number(t.id) : 0}`) || document.getElementById("si-pick");
    if (el) { flash(el); el.focus({ preventScroll: true }); }
  },
};
