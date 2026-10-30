/* An app's own sign-in (contract section 20): app.json "signin",
 * {rules: [{connection, kind, require_2fa, ldap_filter?, group_dns?,
 * radius_groups?}], profile, require_approval}. SATOM's
 * app_signin_rules.clean_package decides (it runs in the rules pack); this
 * module repeats it so the editor says what is wrong while the author types.
 * Pure: no DOM. The kinds, keys and the target server's directory
 * connections and Apps only profiles come from schema.signin (a SATOM newer than 4.6.0). */

export const DEFAULTS = {
  kinds: ["ad", "ldap", "radius", "local"], max_rules: 10, max_groups: 20,
  keys: { ad: ["ldap_filter", "group_dns"], ldap: ["ldap_filter", "group_dns"], radius: ["radius_groups"], local: [] },
  kind_labels: { ad: "Active Directory", ldap: "LDAP", radius: "RADIUS / FortiAuthenticator", local: "Local SATOM accounts" },
  connection_pattern: "^[a-z][a-z0-9-]{1,38}[a-z0-9]$",
  connections: {}, profiles: [],
};

export function limits(schema) {
  const s = (schema && schema.signin) || {};
  return Object.assign({}, DEFAULTS, s);
}

/* The target's SATOM knows section 20 (else the import would drop "signin"). */
export function supported(schema) { return !!(schema && schema.signin); }

/* A new rule for *connection* (an id, or "local") of *kind*: the shape SATOM reads. */
export function blank(connection, kind, lim) {
  const L = lim || DEFAULTS;
  const k = kind || "local";
  const r = { connection: k === "local" ? "local" : String(connection || ""), kind: k, require_2fa: false };
  (L.keys[k] || []).forEach((key) => { r[key] = key === "ldap_filter" ? "" : []; });
  return r;
}

function balanced(flt) {
  let depth = 0;
  for (const ch of flt) {
    if (ch === "(") depth += 1;
    else if (ch === ")") { depth -= 1; if (depth < 0) return false; }
  }
  return depth === 0;
}

function strings(v) {
  if (v === undefined || v === null || v === "") return [];
  if (!Array.isArray(v)) return null;
  return v.map((x) => String(x).trim()).filter(Boolean);
}

/* Every problem, as {index, message}: index -1 is the signin object itself. */
export function problems(signin, lim) {
  const L = lim || DEFAULTS;
  const out = [];
  if (signin === undefined || signin === null || signin === "") return out;
  const say = (index, message) => out.push({ index, message });
  if (typeof signin !== "object" || Array.isArray(signin)) { say(-1, "signin is an object: {rules, profile, require_approval}"); return out; }
  const unknown = Object.keys(signin).filter((k) => !["rules", "profile", "require_approval"].includes(k)).sort();
  if (unknown.length) say(-1, `unknown key(s): ${unknown.join(", ")}`);
  const rules = signin.rules === undefined || signin.rules === null || signin.rules === "" ? [] : signin.rules;
  if (!Array.isArray(rules)) { say(-1, "rules is a list"); return out; }
  if (rules.length > L.max_rules) say(-1, `at most ${L.max_rules} rules`);
  const slugRe = new RegExp(L.connection_pattern);
  rules.slice(0, L.max_rules).forEach((r, i) => {
    if (!r || typeof r !== "object" || Array.isArray(r)) { say(i, "each rule is an object"); return; }
    const kind = String(r.kind || "");
    const conn = String(r.connection || "");
    if (!L.kinds.includes(kind)) { say(i, `kind ${JSON.stringify(kind)}: one of ${L.kinds.join(", ")}`); return; }
    if (kind === "local" && !["", "local"].includes(conn)) { say(i, 'a local rule names no connection (connection "local")'); return; }
    if (kind !== "local" && !slugRe.test(conn)) { say(i, `connection ${JSON.stringify(conn)}: the id of a directory connection (lowercase letters, digits and -), e.g. corp-ad`); return; }
    const allowed = ["connection", "kind", "require_2fa"].concat(L.keys[kind] || []);
    const extra = Object.keys(r).filter((k) => !allowed.includes(k)).sort();
    if (extra.length) { say(i, `${extra.join(", ")} is not part of a ${kind} rule`); return; }
    if (kind === "ad" || kind === "ldap") {
      const flt = String(r.ldap_filter || "").trim();
      if (flt && (!/^\(.*\)$/s.test(flt) || !balanced(flt) || flt.length > 1000)) say(i, "LDAP filter: one filter in parentheses, e.g. (department=NOC)");
      const dns = strings(r.group_dns);
      if (dns === null) say(i, "group_dns is a list of strings");
      else if (dns.some((d) => d.length > 500 || !d.includes("="))) say(i, "group DN: a distinguished name such as CN=noc,OU=Groups,DC=corp,DC=local");
    } else if (kind === "radius") {
      const g = strings(r.radius_groups);
      if (g === null) say(i, "radius_groups is a list of strings");
      else if (g.some((x) => x.length > 120)) say(i, "RADIUS group: at most 120 characters");
    }
  });
  if (signin.profile !== undefined && signin.profile !== null && signin.profile !== "" &&
      (typeof signin.profile !== "string" || signin.profile.length > 64)) say(-1, "profile is the name of an Apps only profile");
  return out;
}

/* What the target server will do with each rule: warnings, never refusals
 * (SATOM leaves such a rule out on import). Empty when the target lists no
 * directory connections (a SATOM before section 20 cannot say). */
export function serverNotes(signin, lim) {
  const L = lim || DEFAULTS;
  const out = [];
  const conns = L.connections;
  if (!signin || !Array.isArray(signin.rules) || !conns || typeof conns !== "object") return out;
  signin.rules.forEach((r, i) => {
    if (!r || r.kind === "local") return;
    const have = conns[r.connection];
    if (!have) out.push({ index: i, message: `directory connection ${r.connection} does not exist on the target SATOM: the import leaves this rule out` });
    else if (have.kind !== r.kind) out.push({ index: i, message: `directory connection ${r.connection} is ${have.kind} on the target SATOM, not ${r.kind}: the import leaves this rule out` });
  });
  if (signin.profile && Array.isArray(L.profiles) && !L.profiles.includes(signin.profile)) {
    out.push({ index: -1, message: `profile ${signin.profile} is not an Apps only profile on the target SATOM: an administrator chooses one there` });
  }
  return out;
}

export function lines(v) { return (Array.isArray(v) ? v : []).join("\n"); }
export function unlines(s) { return String(s || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean); }
