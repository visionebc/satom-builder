/* The app form: app.json `app.fields`, a list of
 * {name, label, type, required, help, default, ...type keys}
 * (SATOM services/studio_apps.clean_fields). Pure: no DOM.
 *
 * Like the graph model, every edit mutates a field in place; switching the
 * type keeps the keys of the old type (the rules pack drops what it does not
 * want, the editor never does). */
import { pattern, uniqueName } from "./util.js";

export const DEFAULT_FIELD_TYPES = ["text", "textarea", "number", "select", "checkbox", "password", "date",
  "store_select", "api_select", "satom_select"];
export const DEFAULT_LIST_TYPES = ["select", "store_select", "api_select", "satom_select"];

export function fieldTypes(schema) {
  return (schema && schema.field_types) || DEFAULT_FIELD_TYPES;
}

export function isListType(type, schema) {
  return ((schema && schema.list_types) || DEFAULT_LIST_TYPES).includes(type);
}

export function maxFields(schema) {
  return (schema && schema.limits && schema.limits.fields) || 40;
}

export function newField(fields, type) {
  const name = uniqueName("field_" + (fields.length + 1), (n) => fields.some((f) => f.name === n), "_");
  return { name, label: "Field " + (fields.length + 1), type: type || "text", required: false };
}

export function addField(fields, schema, type) {
  if (fields.length >= maxFields(schema)) return { ok: false, error: `at most ${maxFields(schema)} fields` };
  const f = newField(fields, type);
  fields.push(f);
  return { ok: true, field: f };
}

export function setType(field, type) {
  field.type = type;
  if (type === "select" && typeof field.options !== "string") {
    field.options = Array.isArray(field.options) ? field.options.join("\n") : "";
  }
  if (type === "password") field.default = "";
  return field;
}

/* Options of a `select` field: SATOM keeps them as ONE string, one per line. */
export function optionList(field) {
  const o = field.options;
  if (Array.isArray(o)) return o.map(String);
  return String(o || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
}

/* Inline hints, {where, message}, `where` = app.fields[<name>]. */
export function hints(fields, schema, choices) {
  const out = [];
  const re = pattern(schema, "field");
  const seen = new Set();
  const types = fieldTypes(schema);
  if (fields.length > maxFields(schema)) out.push({ where: "app.fields", message: `at most ${maxFields(schema)} fields` });
  fields.forEach((f) => {
    const name = String(f.name || "");
    const where = `app.fields[${name}]`;
    const add = (m) => out.push({ where, message: `field ${name}: ${m}` });
    if (!re.test(name)) add("lowercase letters, digits and _");
    if (seen.has(name)) add("is listed twice");
    seen.add(name);
    if (!types.includes(f.type || "text")) add(`unknown type ${JSON.stringify(f.type)}`);
    if (f.type === "select" && !optionList(f).length) add("a list needs options, one per line");
    if (f.type === "store_select" && (!String(f.store || "").trim() || !String(f.value_col || "").trim())) add("pick the data store and the value column");
    if (f.type === "store_select" && f.store && choices && choices.stores && !choices.stores.includes(f.store)) add(`data store ${f.store} is neither in the package nor on the target`);
    if (f.type === "api_select" && !String(f.connection || "").trim()) add("pick the HTTP connection");
    if (f.type === "satom_select") {
      const keys = ((schema && schema.satom_sources) || []).map((s) => s.key);
      if (!f.source || (keys.length && !keys.includes(f.source))) add("pick what the SATOM list shows");
    }
  });
  return out;
}

/* How the rehearse form asks for a field's value. */
export function inputKind(field) {
  switch (field.type) {
    case "textarea": return "textarea";
    case "number": return "number";
    case "checkbox": return "checkbox";
    case "password": return "password";
    case "date": return "date";
    case "select": return "select";
    default: return "text"; // store/api/satom lists resolve on SATOM: typed as text here
  }
}

/* The inputs a rehearse request sends, from the values the form holds. */
export function rehearseInputs(fields, values) {
  const out = {};
  fields.forEach((f) => {
    const v = values[f.name];
    if (f.type === "checkbox") out[f.name] = !!v;
    else if (v !== undefined && v !== null && v !== "") out[f.name] = v;
    else out[f.name] = "";
  });
  return out;
}
