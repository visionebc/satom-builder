/* Where a project installs in SATOM (contract section 17). Pure.
 *
 * installAdom() is satom_builder.targets.install_adom, line for line, so the
 * editor shows the ADOM the moment a dataset is ticked instead of after the
 * next save (tests/test_adom.py runs the same cases through both). The server
 * stays the authority: every save answers its own `where`.
 *
 * A plugin installs in ONE ADOM and reads only the datasets that ADOM offers;
 * one that reads more than one product's datasets installs in Global (opened
 * from SATOM's Global console). A Global token chooses the ADOM per call; a
 * token bound to an ADOM stays in it.
 */
export const GLOBAL = "global";

export function adomLabel(a) {
  if (!a) return "the token's ADOM";
  return a === GLOBAL ? "Global" : "ADOM " + a;
}

export function reachOf(lib) {
  const r = lib && lib.reach;
  if (!r || typeof r !== "object" || Array.isArray(r)) {
    return { known: false, token_adom: String((lib && lib.product) || ""), can_choose: false, adoms: [] };
  }
  return { known: true, token_adom: String(r.token_adom || ""), can_choose: !!r.can_choose,
    adoms: (r.adoms || []).map(String) };
}

export function elsewhereOf(lib) {
  const e = lib && lib.elsewhere && lib.elsewhere.datasets;
  if (!e || typeof e !== "object" || Array.isArray(e)) return {};
  const out = {};
  Object.keys(e).forEach((k) => { out[String(k)] = String(e[k]); });
  return out;
}

const uniqSorted = (xs) => Array.from(new Set(xs)).sort();

export function installAdom(kind, datasets, override, lib) {
  const reach = reachOf(lib);
  const defs = {};
  ((lib && lib.datasets) || []).forEach((d) => { if (d && typeof d === "object" && d.key) defs[String(d.key)] = d; });
  const awayAll = elsewhereOf(lib);
  const keys = (kind === "view" || kind === "widget") ? (datasets || []).map(String) : [];
  const products = uniqSorted(keys.filter((k) => defs[k] && defs[k].product).map((k) => String(defs[k].product)));
  const away = {};
  keys.forEach((k) => { if (!defs[k] && awayAll[k]) away[k] = awayAll[k]; });
  const out = { products, away, warning: "", can_choose: reach.can_choose, adoms: reach.adoms, token_adom: reach.token_adom };
  if (!reach.known) {
    return Object.assign(out, { adom: null, shown: reach.token_adom || "the token's ADOM", mode: "legacy",
      reason: "This SATOM does not say which ADOMs the token reaches (it predates SATOM's ADOM-per-call API): the project installs in the token's ADOM." });
  }
  if (!reach.can_choose) {
    const tok = reach.token_adom;
    let warn = "";
    const ak = Object.keys(away).sort();
    if (ak.length) {
      const where = uniqSorted(Object.values(away));
      warn = `${ak.join(", ")} ${ak.length === 1 ? "lives" : "live"} in ADOM ${where.join(" and ")}, but this connection's token is bound to ADOM ${tok}: link a Global token (My tokens) to build across ADOMs.`;
    }
    return Object.assign(out, { adom: null, shown: tok, mode: "bound", warning: warn, reason: `This connection's token is bound to ADOM ${tok}.` });
  }
  const ov = String(override || "").trim().toLowerCase();
  if (ov && reach.adoms.includes(ov)) {
    let warn = "";
    const other = products.filter((p) => p !== ov);
    if (ov !== GLOBAL && other.length) {
      warn = `It reads ${other.join(", ")} datasets, but it is set to install in ADOM ${ov}, which does not offer them: SATOM will refuse it. Use Auto, or Global.`;
    }
    return Object.assign(out, { adom: ov, shown: ov, mode: "override", warning: warn, reason: `Set by hand to ${ov === GLOBAL ? "Global" : "ADOM " + ov}.` });
  }
  if (products.length === 1) {
    return Object.assign(out, { adom: products[0], shown: products[0], mode: "auto", reason: `Every product dataset it reads is ${products[0]}.` });
  }
  if (products.length > 1) {
    return Object.assign(out, { adom: GLOBAL, shown: GLOBAL, mode: "auto",
      reason: `It reads ${products.join(" and ")} datasets: a plugin that reads more than one ADOM installs in Global and is opened from SATOM's Global console.` });
  }
  return Object.assign(out, { adom: GLOBAL, shown: GLOBAL, mode: "auto",
    reason: "It reads no product dataset yet, so it installs in Global; it moves to a product's ADOM as soon as it reads only that product." });
}

/* The header badge: text, tone (a fw-badge-* suffix) and the tooltip. */
export function badgeOf(where) {
  if (!where) return null;
  const name = where.mode === "legacy" ? where.shown : adomLabel(where.shown);
  const tone = where.warning ? "warning" : (where.mode === "bound" ? "secondary" : "info");
  const suffix = where.mode === "auto" ? " (auto)" : (where.mode === "override" ? " (set)" : "");
  return { text: `Installs in: ${name}${suffix}`, tone, title: [where.reason, where.warning].filter(Boolean).join(" ") };
}

/* The datasets grouped by ADOM: the ones every ADOM offers (built-in,
 * unscoped administrator datasets: "the basics") first, then one group per
 * product in alphabetical order. Each keeps the catalogue's own order. */
export function groupByAdom(datasets) {
  const groups = {};
  (datasets || []).forEach((d) => {
    const a = (d && d.product) ? String(d.product) : "";
    (groups[a] = groups[a] || []).push(d);
  });
  return Object.keys(groups).sort((x, y) => (x === "" ? -1 : y === "" ? 1 : x.localeCompare(y)))
    .map((a) => ({ adom: a, label: a ? adomLabel(a) : "Every ADOM (the basics)", items: groups[a] }));
}
