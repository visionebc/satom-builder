/* CSV paste for data store seed rows. Pure.
 * The first line names the columns. Separator: comma, semicolon or tab,
 * whichever the header line uses most. Quotes per RFC 4180. */

export function parseCSV(text, sep) {
  text = String(text || "").replace(/^﻿/, "");
  if (!sep) {
    const first = text.split(/\r?\n/, 1)[0] || "";
    const counts = [",", ";", "\t"].map((s) => [s, first.split(s).length - 1]);
    counts.sort((a, b) => b[1] - a[1]);
    sep = counts[0][1] > 0 ? counts[0][0] : ",";
  }
  const rows = [];
  let row = [], cell = "", q = false, i = 0;
  while (i < text.length) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i += 2; continue; }
        q = false; i++; continue;
      }
      cell += c; i++; continue;
    }
    if (c === '"' && cell === "") { q = true; i++; continue; }
    if (c === sep) { row.push(cell); cell = ""; i++; continue; }
    if (c === "\r") { i++; continue; }
    if (c === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; i++; continue; }
    cell += c; i++;
  }
  if (cell !== "" || row.length) { row.push(cell); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim() !== ""));
}

export function coerce(type, raw) {
  const s = raw === undefined || raw === null ? "" : String(raw);
  const t = s.trim();
  switch (type) {
    case "number": {
      if (t === "") return null;
      const n = Number(t);
      return Number.isFinite(n) ? n : s;
    }
    case "boolean":
      if (t === "") return null;
      if (/^(1|true|yes|on|y)$/i.test(t)) return true;
      if (/^(0|false|no|off|n)$/i.test(t)) return false;
      return s;
    case "json":
      if (t === "") return null;
      try { return JSON.parse(t); } catch (e) { return s; }
    case "date":
      return t === "" ? null : t;
    default:
      return s;
  }
}

/* {rows, unknown, truncated}: rows keyed by column name, values coerced to
 * the column type; header names that are not columns are reported and
 * their values dropped from the new rows. */
export function rowsFromCSV(text, columns, maxRows) {
  const table = parseCSV(text);
  if (!table.length) return { rows: [], unknown: [], truncated: false };
  const header = table[0].map((h) => h.trim());
  const types = {};
  (columns || []).forEach((c) => { types[c.name] = c.type || "text"; });
  const unknown = header.filter((h) => h && !(h in types));
  let body = table.slice(1);
  const limit = maxRows || 500;
  const truncated = body.length > limit;
  if (truncated) body = body.slice(0, limit);
  const rows = body.map((r) => {
    const o = {};
    header.forEach((h, i) => { if (h in types) o[h] = coerce(types[h], r[i]); });
    return o;
  });
  return { rows, unknown, truncated };
}
