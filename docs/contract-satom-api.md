# Contract: SATOM ⇄ SATOM Builder (v1)

This contract is binding for both repos (`visionebc/SATOM` and `visionebc/satom-builder`). A change to it changes both sides in the same round. Everything here is in English, like everything the product publishes.

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
