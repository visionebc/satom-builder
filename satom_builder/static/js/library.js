/* Library page (templates/library/index.html). Reads the author's target
 * through the Builder's own API (views/library.py): the library (contract
 * section 14), one Fortinet build's endpoints and fields on demand, and the
 * guide (step kinds, SDK, author guide) from the rules pack. Everything goes
 * in through textContent: names and descriptions come from a server. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const sel = $("lib-target");
  if (!sel) return;
  const S = { lib: null, guide: null, section: "datasets", product: "", build: "", endpoints: null, filter: "", fields: {} };

  function h(tag, attrs) {
    const el = document.createElement(tag);
    const a = attrs || {};
    Object.keys(a).forEach((k) => {
      const v = a[k];
      if (v === undefined || v === null || v === false) return;
      if (k === "text") el.textContent = v;
      else if (k === "class") el.className = v;
      else if (k === "on") Object.keys(v).forEach((e) => el.addEventListener(e, v[e]));
      else el.setAttribute(k, v === true ? "" : String(v));
    });
    for (let i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, c) {
    if (c === undefined || c === null || c === false) return;
    if (Array.isArray(c)) c.forEach((x) => add(el, x));
    else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  function say(text, tone) { const s = $("lib-status"); s.textContent = text || ""; s.dataset.tone = tone || "info"; }
  function badge(text, tone) { return h("span", { class: "fw-badge fw-badge-" + (tone || "secondary"), text }); }
  function card(title, body, id) {
    return h("section", { class: "fw-card", id },
      h("div", { class: "fw-card-header" }, h("h2", { class: "fw-card-title", text: title })),
      h("div", { class: "fw-card-body" }, body));
  }
  function table(head, rows, id) {
    return h("div", { class: "table-responsive" }, h("table", { class: "fw-table sb-lib-table", id },
      h("thead", null, h("tr", null, head.map((t) => h("th", { scope: "col", text: t })))),
      h("tbody", null, rows)));
  }
  function empty(text) { return h("p", { class: "sb-help mb-0", text }); }
  async function get(path) {
    const r = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
    let j = null;
    try { j = await r.json(); } catch (e) { j = null; }
    if (!r.ok) throw new Error((j && j.message) || `HTTP ${r.status}`);
    return j;
  }
  const base = () => `/api/targets/${encodeURIComponent(sel.value)}`;

  async function load(refresh) {
    S.lib = null; S.guide = null; S.endpoints = null; S.fields = {}; S.build = "";
    say("Reading the library from SATOM…", "busy");
    try {
      S.lib = await get(base() + "/library" + (refresh ? "?refresh=1" : ""));
      $("lib-cached").textContent = S.lib.cached_at ? "Read " + new Date(S.lib.cached_at * 1000).toLocaleString() : "";
      say("", "info");
    } catch (e) { say(e.message, "bad"); }
    render();
  }

  async function guide() {
    if (S.guide) return S.guide;
    S.guide = await get(base() + "/guide");
    return S.guide;
  }

  function render() {
    document.querySelectorAll("#lib-nav button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.section === S.section)));
    const body = clear($("lib-body"));
    const lib = S.lib;
    if (["steps", "sdk", "guide"].includes(S.section)) { renderGuide(body); return; }
    if (!lib) { body.appendChild(empty("No library loaded.")); return; }
    if (S.section === "datasets") {
      renderDatasets(body, lib);
    } else if (S.section === "stores") {
      const stores = lib.stores || {};
      const keys = Object.keys(stores);
      body.appendChild(card("Data stores on the target", keys.length ? keys.map((k) => h("div", { class: "sb-lib-item" },
        h("h3", { class: "sb-lib-h" }, h("code", { text: k }), " ", stores[k].name || ""),
        stores[k].description ? h("p", { class: "sb-help", text: stores[k].description }) : null,
        table(["Column", "Label", "Type", "Required"], (stores[k].columns || []).map((c) => h("tr", null,
          h("td", null, h("code", { text: c.name })), h("td", { text: c.label || "" }), h("td", { text: c.type || "" }),
          h("td", { text: c.required ? "yes" : "" })))))) : empty("The target has no data store."), "lib-sec-stores"));
    } else if (S.section === "apis") {
      const apis = lib.apis || [];
      body.appendChild(card("API Catalog", apis.length ? apis.map((a) => h("div", { class: "sb-lib-item" },
        h("h3", { class: "sb-lib-h" }, h("code", { text: a.slug }), " ", a.name || "", a.owner ? h("span", { class: "sb-help", text: " · " + a.owner }) : null),
        a.description ? h("p", { class: "sb-help", text: a.description }) : null,
        (a.versions || []).map((v) => h("details", { class: "sb-lib-ver" },
          h("summary", null, "Version ", h("strong", { text: v.version }), " ", badge(v.status, v.status === "published" ? "success" : v.status === "draft" ? "warning" : "secondary"),
            v.base_path ? h("code", { text: " " + v.base_path }) : null, h("span", { class: "sb-help", text: ` · ${(v.operations || []).length} endpoint(s)` })),
          v.notes ? h("p", { class: "sb-help", text: v.notes }) : null,
          table(["Id", "Method", "Path", "Summary"], (v.operations || []).map((o) => h("tr", null,
            h("td", null, h("code", { text: o.id || "" })), h("td", null, badge(o.method || "GET", "primary")), h("td", null, h("code", { text: o.path || "" })),
            h("td", { text: o.summary || "" }))))))))
        : empty("The target's API Catalog is empty."), "lib-sec-apis"));
      body.appendChild(h("p", { class: "sb-help", text: "A connection pinned to a version calls only its endpoints: connection(\"x\").op(\"getHost\", {\"id\": 7}) in a script, Call an API with the endpoint id in a diagram." }));
    } else if (S.section === "fortinet") {
      renderFortinet(body);
    }
  }

  const SOURCE = { builtin: ["built-in", "secondary"], admin: ["administrator", "info"], objects: ["device objects", "secondary"] };

  /* Hundreds of datasets (built-in, administrator, one per cached device-object
   * type): searchable, grouped, one open at a time with its columns, its
   * relations and how SATOM builds it. */
  function renderDatasets(body, lib) {
    const all = lib.datasets || [];
    const f = (S.dsFilter || "").toLowerCase();
    const hit = (d) => !f || (d.key + " " + (d.label || "") + " " + (d.columns || []).join(" ")).toLowerCase().includes(f);
    const groups = {};
    all.forEach((d) => { const g = d.group || "Datasets"; (groups[g] = groups[g] || []).push(d); });
    const counts = {};
    all.forEach((d) => { counts[d.source || "builtin"] = (counts[d.source || "builtin"] || 0) + 1; });
    const filt = h("input", { type: "search", class: "form-control form-control-sm", id: "lib-ds-filter", placeholder: "Search: key, name or column", value: S.dsFilter || "", "aria-label": "Search datasets" });
    const list = h("div", { id: "lib-datasets" });
    const draw = () => {
      clear(list);
      const ff = (S.dsFilter || "").toLowerCase();
      let shown = 0;
      Object.keys(groups).forEach((g) => {
        const items = groups[g].filter((d) => !ff || (d.key + " " + (d.label || "") + " " + (d.columns || []).join(" ")).toLowerCase().includes(ff));
        if (!items.length) return;
        shown += items.length;
        const det = h("details", { class: "sb-lib-ver", open: g === "Built-in" || g === "Administrator datasets" || !!ff || null },
          h("summary", null, h("strong", { text: g }), h("span", { class: "sb-help", text: ` · ${items.length}` })));
        items.forEach((d) => {
          const src = SOURCE[d.source] || [d.source || "", "secondary"];
          const item = h("details", { class: "sb-lib-item", id: "lib-ds-" + d.key },
            h("summary", null, h("code", { text: d.key }), " ", d.label || "", " ", src[0] ? badge(src[0], src[1]) : null,
              h("span", { class: "sb-help", text: ` · ${(d.columns || []).length} columns` })));
          item.addEventListener("toggle", () => {
            if (!item.open || item.dataset.filled) return;
            item.dataset.filled = "1";
            item.appendChild(h("div", { class: "sb-lib-ds" },
              d.description ? h("p", { class: "sb-help", text: d.description }) : null,
              h("p", { class: "sb-help" }, "In a view: ", h("code", { text: `{% for r in ${d.key}.rows %}…{% endfor %}` })),
              h("div", null, (d.columns || []).map((c) => [h("code", { class: "sb-chip" + ((d.masked || []).includes(c) ? " sb-chip-masked" : ""), text: c, title: (d.masked || []).includes(c) ? "masked: reads ********" : null }), " "])),
              (d.relations || []).length ? h("ul", { class: "sb-help mb-1" }, d.relations.map((r) => h("li", null, h("code", { text: r.column }), " → ", h("code", { text: `${r.to}.${r.to_column}` }), r.inferred ? " (inferred from the name)" : ""))) : null,
              d.sql ? h("pre", { class: "sb-lib-sql", text: d.sql }) : null,
              d.spec ? h("pre", { class: "sb-lib-sql", text: "Report Builder query (built by an administrator):\n" + JSON.stringify(d.spec, null, 1) }) : null,
              d.object ? h("p", { class: "sb-help", text: `The cached ${d.object.product} objects of type ${d.object.logical_name} (${d.object.layer} layer).` }) : null));
          });
          det.appendChild(item);
        });
        list.appendChild(det);
      });
      if (!shown) list.appendChild(empty("No dataset matches."));
      $("lib-ds-count").textContent = `${all.length} datasets: ${counts.builtin || 0} built-in, ${counts.admin || 0} from administrators, ${counts.objects || 0} device-object types` + (ff ? ` · ${shown} match` : "");
    };
    filt.addEventListener("input", () => { S.dsFilter = filt.value; draw(); });
    body.appendChild(card("Datasets a view reads", [
      h("p", { class: "sb-help", text: "A view declares the datasets it reads; each arrives as {columns, rows}, cut to the viewer's ADOM and devices. Three sources: SATOM's built-in queries, the ones an administrator published in Administrator › Database › Plugin datasets (admin_<name>), and one per object type the device cache holds (<product>_<type>). Open one for its columns (masked ones read ********), its relations and how SATOM builds it." }),
      h("div", { class: "d-flex gap-2 align-items-center flex-wrap mb-2" }, filt, h("span", { class: "sb-help", id: "lib-ds-count", "aria-live": "polite" })),
      list], "lib-sec-datasets"));
    draw();
  }

  function renderFortinet(body) {
    const prods = (S.lib.fortinet || {}).products || {};
    const names = Object.keys(prods);
    if (!names.length) { body.appendChild(card("Fortinet API library", empty("Your token's ADOM covers no product of the API library."), "lib-sec-fortinet")); return; }
    if (!S.product || !prods[S.product]) S.product = names[0];
    const ps = h("select", { class: "form-select form-select-sm", id: "lib-product", "aria-label": "Product" });
    names.forEach((n) => { const o = h("option", { value: n, text: n }); if (n === S.product) o.selected = true; ps.appendChild(o); });
    ps.addEventListener("change", () => { S.product = ps.value; S.build = ""; S.endpoints = null; render(); });
    const builds = prods[S.product] || [];
    const rows = builds.map((b) => {
      const btn = h("button", { type: "button", class: "btn btn-link btn-sm p-0", text: b.version, on: { click: () => openBuild(b.version) } });
      return h("tr", { class: b.version === S.build ? "table-active" : "" }, h("td", null, btn), h("td", { text: b.build || "" }),
        h("td", null, b.measured ? badge(b.vendor_only ? "vendor" : "measured", b.vendor_only ? "secondary" : "success") : badge("unmeasured", "warning")),
        h("td", { text: b.in_fleet ? "in the fleet" : "" }));
    });
    body.appendChild(card("Fortinet API library", [
      h("p", { class: "sb-help", text: "What each firmware build serves, as SATOM measured it (or the vendor documents it). Unmeasured means unknown, never compatible." }),
      h("div", { class: "mb-2 sb-lib-product" }, ps),
      builds.length ? table(["Version", "Build", "Evidence", ""], rows, "lib-builds") : empty("No build known for " + S.product + ".")], "lib-sec-fortinet"));
    if (S.build) {
      const eps = S.endpoints;
      const filt = h("input", { class: "form-control form-control-sm", id: "lib-filter", placeholder: "Filter endpoints", value: S.filter, "aria-label": "Filter endpoints" });
      const list = h("div", { id: "lib-endpoints" });
      const draw = () => {
        clear(list);
        if (!eps) { list.appendChild(empty("Loading…")); return; }
        const f = S.filter.toLowerCase();
        const shown = (eps.endpoints || []).filter((e) => !f || e.endpoint.toLowerCase().includes(f) || (e.section || "").toLowerCase().includes(f));
        list.appendChild(h("p", { class: "sb-help", text: `${shown.length} of ${(eps.endpoints || []).length} endpoint(s)` }));
        shown.slice(0, 300).forEach((e) => {
          const d = h("details", { class: "sb-lib-ep" }, h("summary", null, h("code", { text: e.endpoint }), " ", badge(e.verdict || "?", e.verdict === "ok" ? "success" : "secondary"), e.section ? h("span", { class: "sb-help", text: " · " + e.section }) : null));
          d.addEventListener("toggle", () => { if (d.open) openFields(d, e.endpoint); });
          list.appendChild(d);
        });
        if (shown.length > 300) list.appendChild(empty("Showing the first 300: filter to narrow."));
      };
      filt.addEventListener("input", () => { S.filter = filt.value; draw(); });
      body.appendChild(card(`${S.product} ${S.build}: endpoints`, [filt, list], "lib-sec-build"));
      draw();
    }
  }

  async function openBuild(version) {
    S.build = version; S.endpoints = null; S.filter = "";
    render();
    try {
      S.endpoints = await get(`${base()}/library/fortinet/${encodeURIComponent(S.product)}/${encodeURIComponent(version)}`);
    } catch (e) { say(e.message, "bad"); S.endpoints = { endpoints: [] }; }
    render();
  }

  async function openFields(box, endpoint) {
    if (box.querySelector(".sb-lib-fields")) return;
    const slot = h("div", { class: "sb-lib-fields" }, empty("Loading fields…"));
    box.appendChild(slot);
    try {
      const j = await get(`${base()}/library/fortinet/${encodeURIComponent(S.product)}/${encodeURIComponent(S.build)}?endpoint=${encodeURIComponent(endpoint)}`);
      clear(slot);
      const names = Object.keys(j.fields || {});
      slot.appendChild(h("p", { class: "sb-help", text: `Status: ${j.status}${j.status === "measured" ? "" : " (an empty list is unknown, not none)"}` }));
      if (names.length) slot.appendChild(table(["Field", "Type", "Detail"], names.map((n) => {
        const f = j.fields[n] || {};
        const extra = Object.keys(f).filter((k) => !["type", "sources"].includes(k)).map((k) => `${k}: ${typeof f[k] === "object" ? JSON.stringify(f[k]) : f[k]}`).join(" · ");
        return h("tr", null, h("td", null, h("code", { text: n })), h("td", { text: f.type || "" }), h("td", { class: "sb-help", text: extra.slice(0, 400) }));
      })));
    } catch (e) { clear(slot).appendChild(empty(e.message)); }
  }

  async function renderGuide(body) {
    body.appendChild(empty("Loading from the rules pack…"));
    let g;
    try { g = await guide(); } catch (e) { clear(body).appendChild(empty(e.message)); return; }
    clear(body);
    if (S.section === "steps") {
      const kinds = (g.schema && g.schema.step_kinds) || [];
      const groups = {};
      kinds.forEach((k) => { (groups[k.group || "other"] = groups[k.group || "other"] || []).push(k); });
      body.appendChild(card("Step kinds of a diagram", Object.keys(groups).length ? Object.keys(groups).map((gname) => h("div", { class: "sb-lib-item" },
        h("h3", { class: "sb-lib-h", text: gname }),
        groups[gname].map((k) => h("details", { class: "sb-lib-ep" },
          h("summary", null, h("code", { text: k.key }), " ", k.label || "", k.writes ? [" ", badge("writes", "warning")] : null, h("span", { class: "sb-help", text: " · " + (k.summary || "") })),
          table(["Param", "Kind", "Required", "Help"], (k.params || []).map((p) => h("tr", null, h("td", null, h("code", { text: p.name })),
            h("td", { text: p.kind || "text" }), h("td", { text: p.required ? "yes" : "" }), h("td", { class: "sb-help", text: p.help || "" })))))))) : empty("The schema lists no step kind."), "lib-sec-steps"));
    } else if (S.section === "sdk") {
      const sc = (g.schema && g.schema.script) || {};
      body.appendChild(card("Script SDK (satom_script)", [
        h("p", null, "Names a packaged script may use: ", (sc.sdk || []).map((n) => [h("code", { class: "sb-chip", text: n }), " "])),
        h("p", { class: "sb-help", text: `Field types: ${(sc.field_types || []).join(", ") || "—"} · timeout at most ${sc.max_timeout || "?"} s · no network: connection("slug") reaches a declared connection.` }),
        g.sdk_doc ? h("pre", { class: "sb-lib-pre", id: "lib-sdk", text: g.sdk_doc.trim() }) : empty("This target's rules pack carries no SDK text (SATOM 4.2.0 and later do).")], "lib-sec-sdk"));
    } else {
      body.appendChild(card(`Author guide (SATOM ${g.satom_version || ""})`, [
        h("p", { class: "sb-help", text: "Generated by SATOM from its own rules and limits: the same text the AI gets." }),
        h("pre", { class: "sb-lib-pre", id: "lib-guide", text: g.guide || "" })], "lib-sec-guide"));
    }
  }

  document.querySelectorAll("#lib-nav button").forEach((b) => b.addEventListener("click", () => { S.section = b.dataset.section; render(); }));
  sel.addEventListener("change", () => load(false));
  $("lib-refresh").addEventListener("click", () => load(true));
  load(false);
})();
