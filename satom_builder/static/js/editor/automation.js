/* An app's automations (contract section 18): app.json "automations", each
 * {name, schedule_kind, schedule, inputs, description}. SATOM's
 * plugin_automations.clean_entries / input_problems decide; this module
 * repeats them so the editor says what is wrong while the author types.
 * Pure: no DOM. The limits come from the target's schema.automations
 * (SATOM >= 4.5.0), with SATOM's values as the fallback. */

export const DEFAULTS = {
  schedule_kinds: ["interval", "daily", "weekly", "monthly", "cron"],
  max: 10, min_interval_minutes: 5, units: ["minutes", "hours", "days"],
};
export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const UNIT_MINUTES = { minutes: 1, hours: 60, days: 1440 };
const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

export function limits(schema) {
  const a = (schema && schema.automations) || {};
  return Object.assign({}, DEFAULTS, a);
}

/* A new automation of the kind: the shape SATOM reads. */
export function blank(kind, name) {
  const shapes = { interval: { every: 1, unit: "hours" }, daily: { time: "02:00" },
    weekly: { weekday: 0, time: "02:00" }, monthly: { day: 1, time: "02:00" },
    cron: { expr: "0 2 * * *" } };
  return { name: name || "Daily run", schedule_kind: kind || "daily",
    schedule: Object.assign({}, shapes[kind || "daily"] || shapes.daily), inputs: {}, description: "" };
}

export function describe(kind, spec) {
  const s = spec || {};
  if (kind === "interval") return `every ${s.every} ${s.unit}`;
  if (kind === "daily") return `daily at ${s.time}`;
  if (kind === "weekly") return `weekly on ${WEEKDAYS[Number(s.weekday)] || s.weekday} at ${s.time}`;
  if (kind === "monthly") return `monthly on day ${s.day} at ${s.time}`;
  if (kind === "cron") return `cron ${s.expr}`;
  return String(kind || "?");
}

function whole(v, lo, hi) {
  const t = String(v === undefined || v === null ? "" : v).trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= lo && n <= hi ? n : null;
}

/* What is wrong with ONE automation's schedule ("" = nothing). Cron is only
 * checked for five fields here: SATOM's rules check the expression itself. */
export function scheduleProblem(kind, spec, lim) {
  const L = lim || DEFAULTS;
  const s = spec && typeof spec === "object" ? spec : null;
  if (!L.schedule_kinds.includes(kind)) return `schedule: one of ${L.schedule_kinds.join(", ")}`;
  if (!s) return "schedule is an object";
  const time = () => (HHMM.test(String(s.time || "").trim()) ? "" : `time ${JSON.stringify(s.time)}: HH:MM, 00:00 to 23:59`);
  if (kind === "interval") {
    if (!UNIT_MINUTES[s.unit]) return "unit: minutes, hours or days";
    const n = whole(s.every, 1, 10000);
    if (n === null) return "every: a whole number from 1 to 10000";
    if (n * UNIT_MINUTES[s.unit] < L.min_interval_minutes) return `an app runs at most every ${L.min_interval_minutes} minutes`;
    return "";
  }
  if (kind === "daily") return time();
  if (kind === "weekly") return whole(s.weekday, 0, 6) === null ? "weekday: 0 (Monday) to 6 (Sunday)" : time();
  if (kind === "monthly") return whole(s.day, 1, 31) === null ? "day: 1 to 31" : time();
  const parts = String(s.expr || "").trim().split(/\s+/).filter(Boolean);
  return parts.length === 5 ? "" : "cron: five fields (minute hour day month weekday)";
}

/* Every problem, as {index, name, message}: names, schedules, inputs. */
export function problems(entries, fields, lim) {
  const L = lim || DEFAULTS;
  const out = [];
  const list = Array.isArray(entries) ? entries : [];
  if (list.length > L.max) out.push({ index: -1, name: "", message: `an app carries at most ${L.max} automations` });
  const names = new Set((fields || []).filter((f) => f && f.name).map((f) => f.name));
  const seen = new Set();
  list.forEach((e, i) => {
    const say = (message) => out.push({ index: i, name: (e && e.name) || "", message });
    if (!e || typeof e !== "object") { say("each automation is an object"); return; }
    const name = String(e.name || "").trim().replace(/\s+/g, " ");
    if (!name || name.length > 128) say("a name of 1 to 128 characters is required");
    else if (seen.has(name.toLowerCase())) say(`automation ${JSON.stringify(name)} is listed twice`);
    seen.add(name.toLowerCase());
    const sp = scheduleProblem(e.schedule_kind, e.schedule, L);
    if (sp) say(sp);
    const inputs = e.inputs === undefined || e.inputs === null || e.inputs === "" ? {} : e.inputs;
    if (typeof inputs !== "object" || Array.isArray(inputs)) { say("inputs is an object of form field: value"); return; }
    Object.keys(inputs).forEach((k) => { if (!names.has(k)) say(`input ${JSON.stringify(k)} is not a field of the app's form`); });
    (fields || []).forEach((f) => {
      if (f && f.required && !(f.name in inputs) && (f.default === undefined || f.default === null || f.default === "")) {
        say(`form field ${JSON.stringify(f.name)} is required and has no default: give it here`);
      }
    });
  });
  return out;
}
