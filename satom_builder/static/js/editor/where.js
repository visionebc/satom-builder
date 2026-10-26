/* A check() problem names WHERE it is (contract section 3.2): `manifest`,
 * `app.graph.nodes[ddi]`, `stores[rules]`, ... This module turns that string
 * into the editor place to jump to: {tab, id?, sub?, line?, file?}. Pure.
 *
 * Grammar understood (anything else lands on the Raw JSON tab, never lost):
 *   manifest | manifest.<key>                       -> details (header)
 *   files[<file>][:<line>] | <file>[:<line>]        -> files tab (view/widget)
 *   datasets[...] | manifest.datasets               -> datasets tab
 *   params[<name>] | manifest.params                -> params tab
 *   app.fields[<name>]                              -> form tab
 *   app.graph | app.graph.nodes[<key>] | app.graph.edges[<i>] -> diagram tab
 *   app.actions[<key>]                              -> actions tab
 *   app.design[.page|.result][...]                  -> design tab
 *   stores[<slug>][.columns[<name>]|.rows[<i>]]     -> stores tab
 *   connections[<slug>]                             -> connections tab
 *   apis[<slug>][@<version>] | apis[<slug>].operations[<id>] -> apis tab
 *   scripts[<slug>][.files[<file>]][:<line>]        -> scripts tab
 *   automations[<name>] | automations              -> automations tab
 * A line may also be written " line N" / "(line N)" at the end, or carried
 * by the message ("... line 12 ..." for a file target). */

const CONTENT_FILES = ["body.jinja", "style.css", "script.js"];

function brackets(s) {
  // "a[b].c[d]" -> [["a","b"],["c","d"]]
  const out = [];
  const re = /\.?([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\]]*)\])?/g;
  let m;
  while ((m = re.exec(s)) !== null) {
    if (m[0] === "") { re.lastIndex++; continue; }
    out.push([m[1], m[2] === undefined ? null : m[2]]);
  }
  return out;
}

function splitLine(where, message) {
  let line = null;
  let w = String(where || "").trim();
  let m = /(?::|\s+line\s+|\s*\(line\s+)(\d+)\)?$/i.exec(w);
  if (m) { line = Number(m[1]); w = w.slice(0, m.index); }
  if (line === null && message) {
    const mm = /\bline\s+(\d+)\b/i.exec(String(message));
    if (mm) line = Number(mm[1]);
  }
  return { w, line };
}

export function parseWhere(where, message, kind) {
  const { w, line } = splitLine(where, message);
  const isApp = kind === "app";
  if (!w) return { tab: isApp ? "raw" : "files" };
  if (CONTENT_FILES.includes(w)) return { tab: "files", file: w, line };
  const parts = brackets(w);
  if (!parts.length) return { tab: "raw" };
  const [head, arg] = parts[0];
  const second = parts[1] || [null, null];
  switch (head) {
    case "manifest":
      if (second[0] === "datasets") return { tab: "datasets" };
      if (second[0] === "params") return { tab: "params", id: second[1] };
      return { tab: "details", id: second[0] };
    case "files":
      return { tab: isApp ? "raw" : "files", file: arg, line };
    case "datasets":
      return { tab: "datasets", id: arg };
    case "params":
      return { tab: "params", id: arg };
    case "app": {
      const [sub, subArg] = second;
      if (sub === "fields") return { tab: "form", id: subArg };
      if (sub === "graph") {
        const [n, nArg] = parts[2] || [null, null];
        if (n === "nodes") return { tab: "diagram", id: nArg };
        if (n === "edges") return { tab: "diagram", edge: nArg === null ? null : Number(nArg) };
        return { tab: "diagram" };
      }
      if (sub === "actions") return { tab: "actions", id: subArg };
      if (sub === "design") {
        const [surf, sArg] = parts[2] || [null, null];
        return { tab: "design", sub: surf === "page" || surf === "result" || surf === "theme" ? surf : null, id: sArg };
      }
      return { tab: "raw" };
    }
    case "stores": {
      const r = { tab: "stores", id: arg };
      if (second[0] === "columns") r.column = second[1];
      if (second[0] === "rows") r.row = second[1] === null ? null : Number(second[1]);
      return r;
    }
    case "connections":
      return { tab: "connections", id: arg };
    case "automations":
      return { tab: "automations", id: arg };
    case "apis": {
      let id = arg, version = null;
      if (id && id.includes("@")) [id, version] = id.split("@");
      const r = { tab: "apis", id, version };
      if (second[0] === "operations") r.operation = second[1];
      return r;
    }
    case "scripts": {
      const r = { tab: "scripts", id: arg, line };
      if (second[0] === "files") r.file = second[1];
      if (second[0] === "settings") r.sub = "settings";
      return r;
    }
    default:
      return { tab: "raw" };
  }
}

/* The tabs a project kind shows; a target on a tab the kind lacks goes to Raw. */
export function tabsFor(kind) {
  if (kind === "app") return ["form", "diagram", "actions", "stores", "connections", "apis", "design", "scripts", "automations", "testdata", "database", "testrun", "chat", "raw"];
  return ["files", "datasets", "params", "preview", "testdata", "database", "testrun", "chat", "raw"];
}

export function resolveTarget(where, message, kind) {
  const t = parseWhere(where, message, kind);
  if (t.tab !== "details" && !tabsFor(kind).includes(t.tab)) return { tab: "raw", where };
  return t;
}
