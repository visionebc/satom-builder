# Contract: SATOM ⇄ SATOM Builder (v1)

This contract is binding for both repos (SATOM and SATOM Builder). A change to it changes both sides in the same round. Everything here is in English, like everything the product publishes.

Design background: `docs/2026-10-25-satom-builder-design.md`.

## 0. Principles

- **SATOM is authoritative.** The Builder validates locally with SATOM's own rule code (the rules pack). SATOM validates again on every validate, rehearse and import call. A Builder bug can never let a bad package into SATOM.
- **One rule implementation.** The rules pack is SATOM's `package_check` module and its dependencies, copied verbatim. Nothing is re-implemented on the Builder side.
- **No secrets in the Builder or in packages.** Connection secrets are typed into SATOM, either in its import wizard or in the API import call's `secrets` field, which SATOM stores and the Builder never keeps.
- **The author's own token.** Every call carries `Authorization: Bearer <SATOM API token>` for the author who is logged in to the Builder. The token is bound to one ADOM and can never exceed its owner's RBAC.

## 1. Endpoints (prefix `/api/v1/builder`, JSON unless noted)

All errors use SATOM's API error shape: `{"error": "<code>", "message": "<English sentence>"}` with HTTP 400, 401, 403, 404, 409, 413 or 422.

| Method + path | Scope | Extra requirement | Purpose |
|---|---|---|---|
| `GET /target` | read | — | Target snapshot (section 2) |
| `GET /rules-pack` | read | — | Rules pack zip (section 3). Header `X-Satom-Rules-Sha256` |
| `POST /validate` | write | — | Dry run, nothing is written (section 4) |
| `POST /rehearse` | write | the owner may author in Plugin Studio (`studio.script_library`) | Unarmed run of the package's app against this server's existing objects, installs nothing (section 5) |
| `POST /import` | admin | owner is super-admin | Same path as the UI import (section 6) |
| `GET /publishers` | read | — | `[{key_id, label, enabled, created_at}]` |
| `GET /data` | read | the owner may author in Plugin Studio (`studio.script_library`) | This server's rows for the Builder's test data (section 15) |
| `POST /publishers` | admin | owner is super-admin | Register a publisher key: body `{"label": str<=80, "public_key": base64 of the raw 32-byte Ed25519 key}` → `201 {"key_id"}`. The call is idempotent: the same key returns `200` with the existing `key_id`. |

Package bodies (`validate`, `rehearse`, `import`) are `multipart/form-data`:
- `package` (file, `.satomplugin`, at most 10 MB);
- optional `options`, a JSON string:
  - `rehearse`: `{"inputs": {...}, "answers": "yes"|"no"|"stop"}`
  - `import`: `{"allow_downgrade": bool, "secrets": {"<connection slug>": "<secret>"}}`

Every call is written to SATOM's audit log with the token and the owner.

## 2. Target snapshot: `GET /target`

```json
{
  "format": "satom.builder-target/1",
  "satom_version": "4.0.1",
  "rules_sha256": "<hex>",
  "product": "<adom key the token is bound to>",
  "caller": {"username": "...", "is_super_admin": false, "scopes": ["read","write"],
             "can_author": true, "can_import": false},
  "choices": { ... exactly services.studio_apps.choices() ... ,
               "datasets": ["fleet_appliances", ...] },
  "templates": {"apps": [...], "stores": [...], "connections": [...], "scripts": [...]},
  "existing": {
    "apps":        {"<slug>": {"kind": "flow|script", "pkg_version": "1.0.0", "status": "draft|published", "template": false}},
    "stores":      {"<slug>": {"columns": [{"name": "...", "type": "..."}]}},
    "connections": {"<slug>": {"kind": "http|postgres", "base_url": "...", "auth": "...", "has_secret": true}},
    "api_versions": [["<api slug>", "<version>", "draft|published|deprecated"]],
    "scripts":     {"<slug>": {"published": true}}
  },
  "publishers": [{"key_id": "...", "label": "..."}],
  "schema": { ... section 3.2, computed with this server's choices ... }
}
```

The `target` argument of `check()` (section 3) is this exact object. The Builder passes it unchanged.

## 3. Rules pack

### 3.1 File

`GET /rules-pack` returns `application/zip`, named `satom-rules-<satom_version>-<sha12>.zip`:

```
rules.json                  {"format": "satom.rules-pack/1", "satom_version": "...", "commit": "<git sha or ''>",
                             "files": {"<path>": "<sha256>"}, "entry": "satom_rules"}
satom_rules/__init__.py     from .app.services.package_check import check, schema, author_guide, POLICIES, CHECK_VERSION
satom_rules/app/__init__.py           (empty)
satom_rules/app/extensions.py         STUB: `db` raises RuntimeError("no database in the rules pack") on any attribute
satom_rules/app/models_studio.py      STUB: module __getattr__ returns placeholder classes
satom_rules/app/services/*.py         VERBATIM copies of SATOM's modules (package_check, plugin_package,
                                      plugin_css, studio_flow, studio_apps, studio_store, studio_connections,
                                      studio_api_spec, studio_design, studio_checks, studio_satom,
                                      script_library, ... the import closure, computed by the builder)
```

The pack needs Python ≥3.10 with `jinja2`, `markupsafe` and `cryptography` installed. Nothing else.

**Pinning (Builder side):**
- The Builder stores the zip under `data/rules/<sha256>.zip`.
- Before every load it checks the zip's sha256 against `X-Satom-Rules-Sha256` and every member's hash against `rules.json`.
- It refuses a pack whose `satom_version` differs from the target's.
- It runs the pack **in a subprocess**: `python -I -c ...`, with `sys.path` holding only the unpacked pack and the venv's site-packages, an environment without the Builder's secrets, and a 30 s timeout. The subprocess speaks JSON over stdin and stdout.

### 3.2 API of the pack

```python
check(data: bytes, target: dict, policy: str = "builder") -> dict
# -> {"ok": bool,
#     "problems": [{"where": "app.graph.nodes[ddi]" | "manifest" | "stores[rules]" | ..., "message": str}],
#     "warnings": [{"where": ..., "message": ...}],
#     "summary":  {"kind", "slug", "version", "signer", "signed_by": "vision-ebc|publisher|none",
#                  "creates": [str], "keeps": [str], "needs_secrets": [connection slugs], "lands_in": "draft|testing"}}
# POLICIES = ("builder", "import"). The Builder always uses "builder".
# Problems never raise: a malformed package is a problem, not an exception.

schema(target: dict) -> dict
# -> {"field_types", "list_types", "satom_sources": [{key,label}],
#     "step_kinds": studio_flow.kinds_json(choices),      # [{key,label,group,summary,writes,colour,params:[{name,label,kind,required,choices,default,help}], ...}]
#     "branches": [...], "action_kinds": studio_apps.action_schema(choices),
#     "column_types": [...], "connection": {"kinds": ["http"], "auth": [...]},
#     "design": studio_design.schema(), "datasets": [...], "param_types": [...],
#     "script": {"field_types": [...], "sdk": ["inputs","result","log","connection"], "max_timeout": 900},
#     "limits": {...every numeric limit, by name...}}

author_guide(target: dict) -> str
# Markdown generated FROM the rules and limits above (never hand-written). It is the
# fixed part of every AI prompt.

CHECK_VERSION = 1   # bumped when check()'s output shape changes
```

## 4. Validate

`POST /validate` runs `check(data, target_of_this_server, policy="builder")` server-side and returns its dict, HTTP 200 even when `ok` is false.

## 5. Rehearse

For `kind: app` only. A view or widget gets `{"ok": true, "rendered": "<html>"}`, rendered with this server's datasets cut to the caller.

The package is checked first and its problems are returned.

The rehearsal walks the package's diagram **unarmed**: write steps are recorded, not executed. It uses the server's existing stores, connections and secrets.

An object the package would create and that does not exist yet becomes a problem: `"connection ddi is not on this server yet: import the package first"`.

`ask` steps are answered with `options.answers`: `"yes"`, `"no"`, or `"stop"`, which returns `status: "waiting"` with the prompt.

Response:

```json
{"ok": true, "status": "done|failed|waiting", "steps": [...], "outputs": [...],
 "waiting": {...} | null, "error": "", "problems": []}
```

Nothing is persisted, apart from one audit line.

## 6. Import

The same code path as Plugin Studio's import page: `views.plugins` and `studio_app_package.install`, preceded by `check(policy=...)`.

**Policy**
- `import` for packages signed by a key in `TRUSTED_KEYS` (Vision EBC).
- `builder` for everything else: packages signed by a registered publisher, or unsigned packages that arrive through this API.
- An unsigned upload through the UI keeps `import` policy, except for the rules that apply to all policies (below).

**Rules for ALL policies (new):**
- an app package can never replace a template (`ex-*`);
- an app package can never replace a script app;
- a connection carrying a non-empty `password`, `secret`, `token`, `api_key` or `client_secret` is refused;
- `kind: postgres` connections are refused in app packages;
- an API version without endpoints is refused;
- every packaged script must pass the script rules (section 7);
- `min_satom` above this server's version is refused;
- `assets/*` must match their names (already enforced).

**Additional rules for `builder` policy:**
- `kind: code` is refused, whoever signed it;
- a diagram, form, action or design problem refuses the import;
- the CSS lint (`plugin_css`) errors refuse the import.

**Secrets:** `options.secrets` holds values for the connections the import CREATES. They are stored through `studio_connections.set_secret` (vault or Fernet). Values for connections that already exist are ignored and reported in `kept`.

**Response:**

```json
{"ok": true, "kind": "app", "slug": "...", "version": "...", "lands_in": "draft|testing",
 "created": [...], "kept": [...], "todo": [...], "problems": [...]}
```

## 7. Package format additions (`satom.plugin/1`, still the same format string)

`app.json` gains an optional `"scripts"` list, one entry per Script Library script the diagram's `script` steps run:

```json
{"slug": "parse-report", "name": "Parse report", "description": "",
 "files": {"main.py": "<utf-8 source>", "lib/x.py": "..."},
 "settings": {"fields": [...script_library field defs...], "timeout": 60, "audience": "admins",
              "connections": ["crm"], "entry": "main.py"}}
```

Script rules (`script_library` limits plus these):
- `network` is absent or `"none"`;
- `credentials` is absent or `[]`;
- no source file may use `secret` or `tcp` from `satom_script` / `satom_sdk` (checked on the AST: imports, attribute access and bare names);
- no call may pass `write=True`;
- `connections` lists only `http` connections that exist on the server or are created by the same package.

On install, a missing script is created and published as version 1, with audience `admins`. An existing script is kept, and a warning is given if its published files differ.

## 8. Signature (Builder publisher key)

`plugin.sig` uses the existing format:

```json
{"alg": "ed25519", "key_id": "<sha256 hex of the raw public key>", "sig": "<base64 Ed25519 over plugin.json bytes>"}
```

SATOM's `read_package` trusts `TRUSTED_KEYS` plus the **enabled** registered publishers.

A view or widget signed by a publisher lands in `testing`. Unsigned ones land in `draft`. `code` signed by a publisher is refused.

## 9. Builder update queue (internal to the Builder; shared by its web app and its runner)

**Request:** `data/update-requests/<id>.json`:

```json
{"id": "<uuid hex>", "kind": "package|rollback|service", "path": "data/update-uploads/<file>",
 "requested_by": "<user>", "at": "<iso>", "allow_downgrade": false}
```

**Status:** `data/update-status/<id>.json`:

```json
{"id", "status": "queued|running|done|failed|rolled-back", "steps": [{"name", "ok", "detail", "at"}],
 "from_version", "to_version"}
```

The web app writes requests and reads statuses only. `satom-builder-updater.path` (DirectoryNotEmpty) runs the root runner.

## 10. Builder app interface used by the installer

- **Package:** `satom_builder/`, Flask factory `satom_builder:create_app()`. WSGI is `satom_builder.wsgi:app`, served by gunicorn on `127.0.0.1:8100`.
- **Management CLI:** `python -m satom_builder.manage <cmd>`. Every command is idempotent.
  - `migrate`: SQLite schema, `schema_version` table.
  - `create-admin --username U --password-file F`.
  - `keygen-publisher`: creates `/etc/satom-builder/publisher/publisher_ed25519.key` if missing, raw PKCS#8 PEM, root:satom-builder 0640; prints the key_id.
  - `check`: config sanity, exit 0/1.
  - `version`.
- **Health:** `GET /healthz` returns `200 {"status": "ok", "version": "<VERSION>"}`.
- **`.env`** at `/opt/satom-builder/.env`, root:satom-builder 0640:
  - `SECRET_KEY`, `FERNET_KEY`;
  - `SATOM_BUILDER_DATA=/opt/satom-builder/data`;
  - `SATOM_BUILDER_PUBLISHER_KEY=/etc/satom-builder/publisher/publisher_ed25519.key`;
  - `SATOM_BUILDER_BIND=127.0.0.1:8100`.
- **Version:** `VERSION` at the repo root, X.Y.Z, read by the app.

## 11. Builder internal JSON API (web UI ⇄ backend, same repo)

The editor front-end (templates and JS) talks to the backend only through these routes:
- They use the session cookie and require the header `X-CSRF-Token`, whose value comes from `<meta name="csrf-token">`.
- They return JSON.
- Errors come back as `{"error": "<code>", "message": "..."}`.

| Route | Body → Answer |
|---|---|
| `GET /api/projects/<id>` | → `{"id","name","kind":"view|widget|app","slug","version","icon","description","target_id","files":{"body.jinja","style.css","script.js"},"datasets":[],"params":[],"app":{...app.json body: app, stores, connections, apis, scripts...},"assets":[{"name","sha256","size"}],"updated_at","last_check":{...}|null}` |
| `PUT /api/projects/<id>` | The same shape; every key is optional and replaces that key. → the saved project plus `"local_check"`, the `check()` result, or `null` when no rules pack is available |
| `POST /api/projects/<id>/assets` | Multipart `file` (an image). → `{"name","sha256"}` |
| `GET /api/targets/<id>/schema` | → `schema` (contract §3.2) from the cached target snapshot |
| `GET /api/targets/<id>/snapshot` | → the target snapshot (§2), cached; `?refresh=1` refetches it |
| `POST /api/projects/<id>/check` | → local `check()` result, from the rules pack in a subprocess |
| `POST /api/projects/<id>/validate-remote` | → SATOM `/validate` answer |
| `POST /api/projects/<id>/rehearse` | `{"inputs":{},"answers":"yes|no|stop"}` → SATOM `/rehearse` answer |
| `POST /api/projects/<id>/import` | `{"allow_downgrade":false,"secrets":{}}` → SATOM `/import` answer. The secrets are relayed and never stored. |
| `GET /api/projects/<id>/package` | → the signed `.satomplugin` file (attachment) |
| `POST /api/projects/<id>/generate/preview` | `{"profile_id","prompt"}` → `{"system": str, "user": str, "bytes": int, "sends": ["author guide", "schema", "draft", "your text"], "never_sends": ["data rows", "secrets"]}` |
| `POST /api/projects/<id>/generate` | `{"profile_id","prompt","max_rounds":3}` → `{"job_id"}` |
| `GET /api/jobs/<job_id>` | → `{"status":"running|done|failed","rounds":[{"n","ok","problems":[...],"at"}],"error":"","project":{...}|null}` |

The base layout is `satom_builder/templates/base.html`, owned by the core, with the blocks `title`, `head`, `content` and `scripts`. Editor pages extend it.

## 12. Amendments (2026-10-25, as built and verified against SATOM 4.0.3 + stage 0)

These amend the sections above. Where they conflict, this section wins.

**§1 Errors and publishers**
- Error codes:

  | Code | HTTP | Meaning |
  |---|---|---|
  | `bad_request` | 400 | |
  | `invalid_key` | 400 | the publisher key is invalid |
  | `insufficient_scope` / `owner_forbidden` / `wrong_product` | 403 | |
  | `busy` | 409 | too many rehearsals at once |
  | `too_large` | 413 | |
  | `package_refused` | 422 | comes with `problems` |

- `POST /publishers` answers `{"key_id", "created": bool}`.
- `GET /publishers` answers a bare array.
- "Super-admin" means the owner holds `studio.plugin_studio`.

**§2 Target snapshot**
- Each entry in `publishers` is `{"key_id", "label", "public_key": <base64 raw 32 bytes>}`. Only the enabled publishers that apply to the token's ADOM are listed.

**§3.1 Rules pack**
- `rules.json` also carries:
  - `"stubs": [modules]`: SATOM modules that need Flask, the database or a library the pack doesn't carry. They are packed as STUBs, never dropped.
  - `"requires": ["jinja2", "markupsafe", "cryptography"]`.
- `satom_rules/app/__init__.py` holds only a docstring.
- The pack is pinned by the zip's sha256 (the `X-Satom-Rules-Sha256` header) and by the hash of every member. There is no separate signature: SATOM re-checks every validate, rehearse and import call, so it stays authoritative.
- **The Builder child process** runs with `sys.path` = the pack directory + the interpreter's own stdlib path (`lib-dynload` included) + site-packages. Nothing else: no cwd, no user site, no Builder source.

**§3.2 `check()` and `schema()`**
- Every item in `problems` and `warnings` also carries `"rule": "<id>"`, using the ids from `package_check.RULES`.
- `schema()` also carries:
  - `methods` and `param_in` (API Catalog);
  - `reserved_vars`;
  - `patterns`: the regexes of the modules that enforce them, for `slug`, `version`, `icon`, `field`, `action_key`, `app_slug`, `store_slug`, `column`, `connection_slug`, `step_key`, `variable` and `operation_id`.
- `datasets` is a list of strings.
- `script.field_types` excludes the database field types.
- The `where` values that `check()` uses, and the editor understands:
  - `manifest[.key]`
  - `files[<file>][:<line>]` and `style.css:<line>`
  - `datasets`, `params[<name>]`
  - `app.fields[<name>]`, `app.graph`, `app.graph.nodes[<key>]`, `app.graph.edges[<i>]`, `app.actions[<key>]`, `app.design[.page|.result|.theme][<id>]`
  - `stores[<slug>][.columns[<name>]|.rows[<i>]]`
  - `connections[<slug>]`
  - `apis[<slug>@<version>][.operations[<id>]]`
  - `scripts[<slug>][.files[<file>]][:<line>]`

**§5 Rehearse**
- Write steps **and script steps** are recorded, not executed.
- When an object is missing, or the package is a `code` package, the answer is `ok: false, status: "failed", steps: []` and nothing is walked.

**§6 Policy rules**
- **For all policies:**
  - the template rule covers the ids of apps, stores, connections and scripts;
  - code must be signed by Vision EBC;
  - every structural failure that install would hit is refused before any write: app, fields, actions, design and graph shape; store slug, columns and rows; connection definition; API definition; image type and size;
  - a connection pinned to an API version that is a draft on the server is refused.
- **Builder policy only** (under `import` they are warnings):
  - a `body.jinja` that does not parse;
  - more than 500 seed rows.
- **Response:** the import answer also carries `warnings`, `signed_by`, `signer` and `policy`.

**§7 Scripts in packages**
- Database field types (`lookup`, `lookup_multi`, `dataset`) are refused.
- The entry is `main.py`.
- `write=` with any value other than the literal `False` is refused.
- `getattr(..., "secret"|"tcp")` and `from satom_script|satom_sdk import *` are refused.
- Export carries `settings.network` and `settings.credentials` (names only) when they are set, so the import refuses them by name.
- An audience other than `admins` is a warning; install forces `admins`.

**§8 Publishers**
- A publisher may be limited to some ADOMs (`adom_keys`). Outside those ADOMs its signature is not trusted.

**§11 Internal API, additions**
- `GET /api/ai/profiles` → `[{"id", "name", "adapter": "anthropic"|"openai_compatible", "model"}]`. Only enabled profiles are listed, and never a key.
- `GET /api/jobs/<id>` also returns `"id"` and `"ok"`.
- `PUT /api/projects/<id>` adds `"local_check_note"` when `local_check` is null.
- `POST …/generate` answers `202`.
- `GET` and `DELETE /api/projects/<id>/assets/<sha>`.
- A draft connection that carries a secret value is refused with `400 secret_in_draft`.
- `last_check` is stored and returned as `{"source": "local"|"remote", "at": <epoch>, "result": <check() result>}`, or `null`. A fresh `check()` answer is the bare result.

## 13. The test runtime in the rules pack (2026-10-25, SATOM 4.2.0, Builder 0.2.0)

The rules pack also carries the code the Builder TESTS a package with, on its own test data, before the package reaches any server. Nothing of it is re-implemented in the Builder.

- **SATOM side** (`app/services/rules_pack.py`): `RUNTIME_MODULES` join `package_check` as roots of the closure, packed exactly like the rules (verbatim, or a stub when a module needs the server). They are `plugin_frame`, `plugin_sandbox`, `studio_flow`, `studio_apps`, `studio_store`, `studio_connections`, `studio_apis`, `studio_api_spec`, `studio_checks`, `studio_satom`, `script_library`, `sandbox`, `sandbox_boot`, `script_sdk`, `integration_sdk`, `studio_broker`, `studio_http` and `script_runner`.
- `rules.json` gains `"runtime": {"format": "satom.runtime/1", "modules": {"app.services.<m>": "packed"|"stub"}, "kit_css": "satom_rules/runtime/plugin-kit.css", "tokens_css": "satom_rules/runtime/tokens.css"}`. Both data files are hashed in `files` like every member. `plugin-kit.css` is `static/css/plugin-kit.css` byte for byte; `tokens.css` is `plugin_frame.default_tokens_css()`.
- New pure functions the runtime uses: `plugin_frame.frame_document(plugin, body_html, data, params, tokens=, kit=)` (the console page, the editor preview and the rehearsal use it too), `plugin_frame.kit_css_from(text)`, `plugin_sandbox.dataset_columns(key)` (the columns a dataset's rows carry, read from its fixed SQL) and `plugin_sandbox.param_args(inputs)` (params by name → the `p_<name>` arguments `resolve_params` reads).
- **Builder side** (`satom_builder/runtime.py`, `runtime_child.py`): a pack is usable for test runs only when `runtime.format` is `satom.runtime/1` and every module above is `packed`; otherwise the Builder says the target needs SATOM 4.2.0. The child runs like the rules (`python -I -B`, `sys.path` = the pack + stdlib + site-packages, no Builder secret in its environment) and, before anything else, makes every AF_INET/AF_INET6 connect and name lookup fail. It replaces ONLY the data layer: `studio_store` get/query/insert/update/delete_row (rows in memory; SATOM's `clean_row`, `_match`, `upsert` and `delete_where` run on top), `studio_http.http_request`/`pg_request` (canned answers), `studio_connections.get`/`secret_state`/`definition`/`for_runner`, `studio_apis.catalog_of`, `studio_checks.run` and `studio_satom.run` for the steps that would touch SATOM or a device (except `ask`, `activity` and `render_names`, which run verbatim), `studio_satom.source_options`, and `studio_apps.choices`/`script_disabled`. Scripts are laid out as a Script Library under a temporary `FM_APP_DIR` and run by SATOM's `script_runner` in SATOM's jail (`sandbox.execute`, bubblewrap), with SATOM's broker answering `connection()`.
- A diagram walks with `Ctx(armed=True, scripts=True)`: every effect lands on test data.

## 14. The library (2026-10-25, SATOM 4.2.0, Builder 0.2.0)

`GET /api/v1/builder/library` (scope `read`, the owner's ADOM like every call): structure only, never a row or a secret.

```json
{"format": "satom.builder-library/1", "product": "<adom>",
 "datasets": [{"key", "label", "description", "columns": [...]}],
 "stores": {"<slug>": {"name", "description", "columns": [{"name", "label", "type", "required", ...}]}},
 "apis": [{"slug", "name", "owner", "docs_url", "description",
           "versions": [{"version", "status", "base_path", "notes", "operations": [...]}]}],
 "fortinet": {"products": {"<product>": [{"version", "build", "line", "measured", "vendor_only", "cli_measured", "in_fleet"}]}}}
```

`fortinet` covers the token's ADOM product, or every product of the API library for `global`.

`GET /api/v1/builder/library/fortinet/<product>/<version>` → `{"product", "version", "endpoints": [{"endpoint", "urn", "section", "verdict", "fields_known", "sources", "vendor_only"}]}`; with `?endpoint=<name>` → `{"product", "version", "endpoint", "status": "measured"|"blind"|"absent"|"unmeasured", "fields": {...}}`. A product outside the token's ADOM is `403 wrong_product`; a malformed version or endpoint `400 bad_request`. A SATOM without the route answers 404, which the Builder reports as "needs SATOM 4.2.0".

**§5 Rehearse, amendment:** the `inputs` of a view rehearsal are its params by name; SATOM maps them with `plugin_sandbox.param_args` (before 4.2.0 they were ignored and the view rendered with its defaults).

**§11 Internal API, additions (Builder 0.2.0)**

| Route | Body → Answer |
|---|---|
| `GET /api/projects/<id>/testdata` | → `{"data", "updated_at", "datasets": [{key, label, description, columns}], "stores": {slug: {columns, from}}, "connections": {slug: {kind, from, base_url}}, "steps": [{key, kind, label}], "sources", "runtime": {"ok", "message", "satom_version", "jail", "jail_message"}}` |
| `PUT /api/projects/<id>/testdata` | `{"data"}` → `{"data", "updated_at"}`; `400 bad_testdata` names the place |
| `POST /api/projects/<id>/testdata/generate` | `{"seed", "rows", "only": ["datasets:x", "stores:y", "connections:z"]}` → the GET shape, saved |
| `POST /api/projects/<id>/test-runs` | `{"kind": "view"|"app"|"script", "script", "inputs", "answers"}` → `202 {"run_id"}`; `409 no_runtime` / `409 no_jail` |
| `GET /api/projects/<id>/test-runs` | → `{"runs": [...]}` (last 50, no answers) |
| `GET /api/test-runs/<run_id>` | → `{id, kind, subject, status, summary, inputs, created_at, finished_at, result}` |
| `POST /api/test-runs/<run_id>/keep` | → the run's store writes become the test data |
| `GET /api/targets/<id>/library` | → §14 (cached per author; `?refresh=1`) |
| `GET /api/targets/<id>/library/fortinet/<product>/<version>` | → §14 (`?endpoint=`) |
| `GET /api/targets/<id>/guide` | → `{"guide", "sdk_doc", "schema", "satom_version"}` |
| `GET/POST /api/projects/<id>/chats`, `GET/PUT/DELETE /api/chats/<cid>` | conversations |
| `POST /api/chats/<cid>/files`, `GET /api/chats/<cid>/files/<fid>` | files (images by their bytes, UTF-8 text files) |
| `POST /api/chats/<cid>/messages` | `{"text", "files", "profile_id", "max_rounds"}` → `202 {"user", "reply"}` |
| `GET /api/chats/<cid>/messages/<mid>[/draft]`, `POST …/apply` | a reply, its draft, apply it to the project |

The AI answer of a conversation may carry `"message"` (a note to the author) and may be `{"message": ...}` alone (no change).

## 15. Test data from the target's SATOM, and the test database (2026-10-25, SATOM 4.3.0, Builder 0.3.0)

Requested: "add the data we have in SATOM to the Builder so something already exists, with the database diagram and SQL so it can be queried".

`GET /api/v1/builder/data?datasets=<a,b>&stores=<x,y>&limit=<n>` (scope `read`; the owner must be a Plugin Studio author, else `403 owner_forbidden`; the owner's ADOM like every call). `datasets` / `stores` are comma lists, absent = all, empty = none; `limit` is rows per table, 1 to 2000, default 200.

```json
{"format": "satom.builder-data/1", "satom_version", "product", "taken_at", "limit",
 "datasets": {"<key>": {"columns": [...], "rows": [{...}], "total": n, "truncated": bool, "error"?: str}},
 "stores": {"<slug>": {"columns": [{"name", "label", "type", "required"}], "rows": [{..., "_id"}], "total": n, "truncated": bool}},
 "sources": {"<satom_select source>": [{"value", "label"}]},
 "unknown": {"datasets": [...], "stores": [...]}}
```

- **Dataset rows** are what a view rendered on that SATOM shows the token's owner: `plugin_sandbox.load_datasets` (the fixed query through `dbintrospect.run_query`: read-only, sensitive columns masked, values as text), cut to the owner's visible appliances and ADOM, `audit_recent` only with `audit.view`.
- **Store rows** carry the declared columns plus `_id`; never `_by`, `_created_at`, `_updated_at`.
- **sources** are `studio_satom.source_options` for every source the server can build. Since 4.3.0 the device lists (`appliances`, `fortiweb`) follow the viewer's ADOM and maintenance permission in a request (before, a form listed the whole fleet in every ADOM).
- The answer stays under 3 MB: a table that does not fit is cut and marked `truncated`. One audit line `builder.data` with the row counts per table.
- A SATOM without the route answers 404 (`{"error": "Not found"}` on 4.2.0); the Builder reports "needs SATOM 4.3.0" (`409 no_data_api`) and generates test data instead.

**Builder side.** The rows are the test data of ONE project on the Builder that asked: never written to a package, a repository or a prompt. `testdata.from_satom` replaces the datasets the project's test data holds (a view: every dataset, the declared ones first; an app: `fleet_appliances` only), the stores the project uses that exist on the target (a package-only store keeps its seed rows) and the `sources`; connections and step answers stay. The test data document gains `origin`: `{"datasets:<k>" | "stores:<slug>" | "sources": {"source": "satom"|"generated"|"package", "at", "target", "total", "truncated", "edited"}}`. A document over 4 MB loses rows from its largest table first, marked `truncated`. A project with no test data yet is filled by the editor the first time it opens Test data, Database or Test run (from SATOM, else generated).

**The test database.** `testdata.sql_tables` lays the saved test data out as tables: a dataset = its key, a store = `store_<slug>` (`_id` first, then its declared columns). `testdata.schema` adds the relations a diagram draws, from the known references `appliance_id` → `fleet_appliances.id`, `device` → `fleet_appliances.name`, `server_pool` → `server_pools.name`, `wpp` → `web_protection_profiles.name` (only between tables that are there). `testdata.run_sql` runs one statement in a private in-memory SQLite: an authorizer allows SELECT, READ, FUNCTION and RECURSIVE only (no write, no ATTACH, no PRAGMA; `zeroblob`, `randomblob`, `load_extension`, `readfile`, `writefile`, `edit`, `fts3_tokenizer` refused), a progress handler stops it after 3 s, a value is at most 10 MB, at most 500 rows come back. The runtime's `info` op now also returns each dataset's `sql` (the fixed query from `plugin_sandbox.DATASETS`, shown, never run by the Builder).

The assistant's prompt gains "The project's test database": `testdata.schema_text`, i.e. table names, kinds, column names and types, row counts, origins and relations; never a row.

**§11 Internal API, additions (Builder 0.3.0)**

| Route | Body → Answer |
|---|---|
| `GET /api/projects/<id>/testdata` | as in 0.2.0; `datasets[]` gains `sql` and `used` (the view reads it; always true for an app) |
| `POST /api/projects/<id>/testdata/import` | `{"limit"}` → the GET shape, saved, with the target SATOM's rows; `409 no_data_api` (SATOM older than 4.3.0), `403 satom_forbidden` (owner not a Plugin Studio author) |
| `GET /api/projects/<id>/testdata/schema` | → `{"tables": [{name, kind, key, label, sql, columns: [{name, type}], rows: <count>, origin}], "relations": [{from, column, to, to_column}]}`; no row |
| `POST /api/projects/<id>/testdata/sql` | `{"sql"}` → `{"columns", "rows": [[...]], "truncated", "ms"}`; `400 bad_sql` with SQLite's message |

## 16. Every dataset of the target (2026-10-25, SATOM 4.3.0, Builder 0.3.0)

Requested: "create the ~600 [device-object datasets] plus what the administrator creates; the Builder must receive the datasets created in SATOM, so both are in line".

**SATOM side.** `services.plugin_datasets` is the one catalogue of what a plugin can read, from three sources:

| Source | Key | Built from |
|---|---|---|
| `builtin` | the nine keys of `plugin_sandbox.DATASETS` | their fixed SQL |
| `objects` | `<product>_<logical>` (config layer), `<product>_deep_<logical>` (deep layer); `/` in a logical name → `__`; a key over 100 characters is cut and suffixed with 8 hex of its sha256 | one per (product, layer, logical name) the device cache (`device_objects`) holds; columns `id`, `appliance_id`, `device`, `mkey`, `parent_id` + the object's own scalar fields (`-` → `_`, at most 120: `PRIORITY_FIELDS` first, then the ones holding a value, then the most common; FortiOS `q_*` and `_*` bookkeeping left out, a field named like a fixed column gets `f_`); fields whose name says secret are `********` |
| `admin` | `admin_<slug>` | a `PluginDataset` row (Administrator › Database › Plugin datasets): a Report Builder spec run through `db_lookup` (read-only console role, the ADOM scope added by the server); offered only when `published` |

Every key is a Jinja identifier: a view reads `{{ <key>.rows }}`. Rows are cut to the viewer (visible appliances, ADOM). A concrete ADOM lists only its product's object datasets and the admin datasets of that ADOM or of every ADOM. The catalogue of object types is cached in-process for 300 s.

- `GET /builder/library` → `datasets[]` is that catalogue: `{key, label, description, columns, source, group, product, relations: [{column, to, to_column, inferred}], masked}`, plus `sql` (built-in), `spec` (admin) or `object: {product, layer, logical_name}` (objects). Relations: `appliance_id` → `fleet_appliances.id`; a sub-object's `parent_id` → its parent type's `id`; a field named like another object type of the same product and layer → that type's `mkey` (`inferred: true`); an admin column `appliance_id`/`*_appliance_id` → the fleet (inferred).
- `GET /builder/target` → `choices.datasets` lists every key. The author guide (`package_check.author_guide`) names the built-in and admin ones and counts the object ones per product.
- `GET /builder/data` → any key; `datasets` absent = the built-in and admin ones (an object dataset only when named).
- **Packages** (`satom.plugin/1`, same format string): a view or widget manifest may carry `dataset_defs` (at most 20): `[{key: admin_<slug> (one of its datasets), label, description, product, spec: {base, ...}}]`. SATOM's export writes them; an import into a server without that dataset creates it `pending` (`source` = "<slug> <version>"), keeps the key in the plugin and adds a to-do; the view reads no rows from it until an administrator publishes it. A definition the destination refuses (its query does not compile there) is dropped with a warning. `package_check` warns "comes with the package: it lands pending" (`view.datasets`, `summary.pending_datasets`).

**Builder side.**
- `targets.dataset_defs()` reads the library (cached per author like the snapshot) → `{key: entry}`; the built-in entries are merged with the rules pack runtime's `info` (their `sql`). A target without the library (SATOM < 4.3.0) offers the built-in datasets only.
- **Test data** of a view or widget holds the datasets it reads, then the nine built-in ones, then any the author added: `POST /api/projects/<id>/testdata/import` `{"add": [keys], "limit"}` reads only those keys from SATOM and keeps everything else (`400` for an unknown key or for an app). Removing an added one is a `PUT` of the test data without it. An app's test data keeps only `fleet_appliances`.
- **Test runs**: the view render request carries `dataset_columns` `{key: [columns]}` for the datasets the rules pack's `plugin_sandbox.DATASETS` does not know.
- **Database**: `schema.relations` = the relations SATOM gave (first) plus the built-in references, each `{from, column, to, to_column, inferred}`; tables carry `source`, `spec`, `masked`.
- **Packages**: `package.build_from_project(..., dataset_defs=)` writes the definitions of the admin datasets a view reads, from the library (`package.dataset_defs_from`); `validate_manifest` applies SATOM's rules to `dataset_defs`.
- **Prompts**: `ai_loop.dataset_summary` — named keys (built-in, admin) in full, object keys counted per product with 8 examples; the `datasets` key list leaves the prompt's schema.
- **Editor**: the Datasets tab and the Library page list every dataset grouped and searchable; the Database tab adds any dataset of the target to the test data and removes it.

## 17. The ADOM of a call, what a token reaches, and datasets of other ADOMs (2026-10-25, SATOM 4.3.x stage 1; Builder side = stage 2)

Requested: "nowhere in the Builder does it say I have to choose the ADOM; and what if I want something from two ADOMs, like every firewall rule that holds the IPs of the FortiWeb interfaces? Make it see every dataset SATOM has, with the basics by default, so the user can see and pick them." Cause found: the lab token was bound to ADOM `fortiweb`, so `fortigate_*` never reached the Builder and the AI said the dataset "does not exist"; and `package_check` only WARNED for a dataset the ADOM does not offer, so the import would have installed the view in the wrong ADOM with the dataset silently dropped.

**The ADOM is part of the plugin, not a detail of the token.** `import` installs in the call's ADOM (`plugin_import.import_package(product=...)`, `product_scope.stamp`); a view reads only the datasets its ADOM offers. A view that reads more than one ADOM installs in **Global** and is opened from the Global console (rows still cut to the viewer's ADOMs). A Global record is also visible in the FortiWeb ADOM (unscoped = FortiWeb, `product_scope`).

**SATOM side (built, stage 1).**
- `?adom=<key>` on every `/api/v1/builder/*` call except `rules-pack` and `publishers`. With a **Global token** (`api_tokens.product` = `global`) it is the ADOM the whole call runs in (`g.product`): target, library, data, validate, rehearse, import. Allowed: `global` and every ADOM the token's owner may enter (`User.allowed_adoms`; none named = all). With a token **bound to an ADOM**, only that ADOM may be named: anything else is `403 wrong_product` ("only a Global token chooses the ADOM of a call") -- a bound token never widens. Unknown key: `400 bad_request`. Absent: the token's ADOM, as before.
- In Global the datasets offered are those of the ADOMs the owner may enter (built-in and unscoped admin datasets always) -- `builder_target.offered(owner, product)`, used by `choices.datasets` and the library alike; the library's `fortinet` builds follow the same rule (`builder_library.products_for(adom, owner)`).
- `reach` (library and target; target only when called with a token): `{"token_adom": "global"|<key>, "adom": <the call's ADOM or "global">, "can_choose": bool, "adoms": ["global", <owner's ADOMs>...] | [<bound key>]}`.
- `elsewhere` (library and target): `{"datasets": {key: adom}}` -- the datasets that exist in the owner's OTHER ADOMs and not in the call's ADOM. Names only: no columns, no rows. Empty in Global (which already offers them). The console's import wizard gets it too (its snapshot is the same function).
- `package_check` rule **`view.datasets_adom`** (scope `builder`: refuses a Builder package, warns on a console import): a dataset the view reads that is in `elsewhere` is reported as "dataset X lives in ADOM Y, not in ADOM Z where this package would install: install it in ADOM Y, or in Global when it reads more than one ADOM"; `summary.other_adoms` = `{key: adom}`. Without `elsewhere` (an older target) the old `view.datasets` "not offered … the import drops it" warning stands.
- The author guide (the fixed part of every AI prompt) says the ADOM ("Datasets this package can read in ADOM X") and, when there are any, counts the datasets of other ADOMs per ADOM with examples and tells the model never to say they do not exist but which ADOM has them.

**Builder side (stage 2, not built yet).** One SATOM connection per author, recommended Global, whose page says what it reaches (`reach`); the project's ADOM is computed from the datasets it reads (one product → that ADOM; several → Global; built-in/unscoped only → the connection's default) and shown, never asked; every call sends `?adom=`; the dataset browser lists every reachable dataset grouped by ADOM with the basics preselected; a dataset in `elsewhere` produces "it lives in ADOM X" with a one-click switch; `targets.set_token` also drops the link's `library_cache` (today it keeps the old library after a token change).
