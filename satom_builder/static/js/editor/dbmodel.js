/* The test database's schema (GET .../testdata/schema) turned into what the
 * Database tab draws: the order of the tables, which column references which,
 * and example queries. Pure: no DOM, tested with node --test. */

export function ident(name) {
  const s = String(name);
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(s) ? s : '"' + s.replace(/"/g, '""') + '"';
}

/* The fleet first (everything points at it), then tables that reference or
 * are referenced, then the rest; datasets before stores. */
export function orderTables(schema) {
  const tables = (schema && schema.tables) || [];
  const rels = (schema && schema.relations) || [];
  const linked = new Set(rels.flatMap((r) => [r.from, r.to]));
  const rank = (t) => (t.name === "fleet_appliances" ? 0 : linked.has(t.name) ? 1 : 2) + (t.kind === "store" ? 3 : 0);
  return tables.map((t, i) => ({ t, i })).sort((a, b) => rank(a.t) - rank(b.t) || a.i - b.i).map((x) => x.t);
}

/* {"table.column": {to, to_column}} for the columns that reference another table. */
export function refsByColumn(schema) {
  const out = {};
  ((schema && schema.relations) || []).forEach((r) => { out[`${r.from}.${r.column}`] = { to: r.to, to_column: r.to_column }; });
  return out;
}

/* [{label, sql}]: one per table, one per relation (a join), and a count by
 * kind when the fleet has one. */
export function examples(schema) {
  const out = [];
  const tables = orderTables(schema);
  tables.forEach((t) => out.push({ label: `Rows of ${t.name}`, sql: `SELECT * FROM ${ident(t.name)} LIMIT 50` }));
  ((schema && schema.relations) || []).forEach((r) => {
    const to = tables.find((t) => t.name === r.to);
    const show = to && to.columns.some((c) => c.name === "name") && r.to_column !== "name" ? ", b.name AS " + ident(r.to + "_name") : "";
    out.push({ label: `${r.from} with its ${r.to}`,
      sql: `SELECT a.*${show}\nFROM ${ident(r.from)} a\nLEFT JOIN ${ident(r.to)} b ON b.${ident(r.to_column)} = a.${ident(r.column)}\nLIMIT 50` });
  });
  const fleet = tables.find((t) => t.name === "fleet_appliances");
  if (fleet && fleet.columns.some((c) => c.name === "kind")) {
    out.push({ label: "Devices per kind", sql: "SELECT kind, COUNT(*) AS devices\nFROM fleet_appliances\nGROUP BY kind\nORDER BY devices DESC" });
  }
  return out;
}
