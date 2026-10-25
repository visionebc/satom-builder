# Vendored code in the editor

| File here | Source | SATOM commit | What was taken | What changed |
|---|---|---|---|---|
| `diagram.js` | SATOM `app/static/js/process_editor.js` | `0b7a6193` (4.0.1) | The SVG diagram drawing: node box geometry (190×58), the bottom→top / side→side anchors, the Bézier arrows, one label per step pair, the two-line label wrap, the `ask` step's own yes/no words, the branch colours calibrated against white (fw-badge palette), pan / wheel zoom / fit. | ES module class instead of an IIFE bound to fixed element ids; the graph model moved to `graph.js` (testable under node); one set of window listeners with `destroy()` (the original added two per node on every redraw); pointer events; an output port per step (drag to connect) besides the Connect mode; keyboard (Delete, arrows, Esc, +/-); check problems drawn on the step their `where` names; the inspector is built by `tabs/diagram.js` from the kind's params. |
| `openapi.js` | SATOM `app/services/studio_api_spec.py` (`clean_path`, `_auto_id`, `clean_operation`, `clean_operations`, `_local_ref`, `_param`, `parse_openapi`) | `0b7a6193` | The OpenAPI 3 / Swagger 2 → API Catalog operations conversion, line by line, including Python truthiness (`pyBool`, `or`) and `%r` quoting (`repr`). | JSON only (no YAML parser in the browser). `tests/editor/test_parity.py` runs both on the same documents and requires identical output. |
| `design.js` (`hex6`, `luminance`, `contrast`, `themeHints`, `BASE`) | SATOM `app/services/studio_design.py` | `0b7a6193` | The colour maths and the three theme pairs `warnings()` checks. | Hints only; the rules pack decides. |
| `graph.js` (`YES_NO_KINDS`) | SATOM `app/services/studio_checks.py` | `0b7a6193` | The step kinds that answer yes / no. | A schema may override it with `yes_no_kinds`. |

`tests/editor/fixtures/schema.json` is not vendored code: `tests/editor/gen_schema.py` builds it by importing SATOM's own services (with the database stubbed, like the rules pack) and `test_parity.py` fails when it goes stale.

When SATOM changes one of these sources, update the file here, bump the commit in this table, and run `python3 -m unittest discover -s tests/editor -p "test_*.py"`.
