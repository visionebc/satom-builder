# SATOM Builder: user guide

SATOM Builder is where you build SATOM plugins: **views** (a page), **widgets** (a dashboard tile) and Plugin Studio **apps** (a form, a diagram, data stores, connections, API Catalog versions, a design and sandboxed scripts). The Builder checks every package with the target SATOM's own rules, can rehearse and import it through SATOM's API with your own token, signs it with this installation's publisher key and, if you want, drafts it with an AI model.

Two promises hold everywhere:

- **SATOM decides.** Validation runs SATOM's own rule code (the *rules pack*), and SATOM checks everything again when you validate, rehearse or import there. The Builder never "fixes" a draft behind your back.
- **No secrets and no data rows leave SATOM.** Connection passwords and tokens are typed into SATOM when you import, relayed once and never stored by the Builder. The AI model never receives data rows or secrets.

## Contents

1. [Signing in](#1-signing-in)
2. [Projects](#2-projects)
3. [A project's overview](#3-a-projects-overview)
4. [The editor](#4-the-editor)
5. [Generating with AI](#5-generating-with-ai) and the Assistant
6. [Library](#6-library)
7. [My tokens](#7-my-tokens)
8. [Publisher key](#8-publisher-key)
9. [Account](#9-account)
10. [Administration](#10-administration): targets, AI profiles, users, directory sign-in, updates, audit log
11. [Roles](#11-roles)
12. [Troubleshooting](#12-troubleshooting)

## 1. Signing in

Open the Builder's address and sign in with your user name and password.

- **Local users** sign in with the password set in the Builder.
- **Directory users** sign in with their LDAP, Active Directory or RADIUS (FortiAuthenticator, FortiToken push) password when an administrator has turned directory sign-in on. Depending on the settings, a directory user is created the first time they sign in, with the role *author*.

After five wrong passwords for one user name (or twenty from one address) within 15 minutes, sign-in is paused for that name or address; the page says how long to wait. Sessions end after 12 hours, when you sign out (user menu, top right) or when an administrator disables you or changes your role.

## 2. Projects

**Projects** (sidebar › Build) lists your projects: name, the result of the last check, kind, version, SATOM target, slug, and when it changed. Administrators can switch to *Everybody's* projects.

### New project

*New project* asks for:

| Field | Meaning |
|---|---|
| Name | What SATOM shows (up to 200 characters). |
| Kind | *App*, *View* or *Widget*. (SATOM's `code` kind cannot be built here.) |
| Slug | The plugin's identity in SATOM: lowercase letters, digits and dashes. Left empty, it is made from the name. |
| Version | `X.Y.Z`. |
| Icon | A Bootstrap icon class, for example `bi-puzzle`. |
| SATOM target | The SATOM this plugin is meant for. Its rules pack is the one that validates the project. You can set it later. |
| Description | Up to 500 characters. |

### Adopt a package

*Adopt a package* turns an existing `.satomplugin` file (a SATOM export, or a package built elsewhere) into a project. The file's format and member hashes are checked. A signature in it is dropped: when the Builder builds the package again, it signs it with this installation's key. Packages whose connections carry a secret are refused (see the promises above).

## 3. A project's overview

Click a project to open its overview.

**Header buttons**

- **Open editor**: the structured editor (section 4).
- **Download package**: the `.satomplugin` file, built from the current draft and signed with the publisher key when the installation has one. Its `min_satom` is the target's SATOM version when you have a token for the target.
- **Bump** (*patch*, *minor* or *major*): raises the version, for example 1.4.2 › 1.4.3, 1.5.0 or 2.0.0. SATOM refuses to import a lower version over a higher one unless you allow a downgrade.

**SATOM card**

The card says what is missing, if anything: a target for the project, or your token for that target.

- **Validate locally** runs the target's rules pack on this server. The first time, the Builder downloads the pack from SATOM, checks its digest and the digest of every file in it, and refuses it if it was made for another SATOM version. The result lists *problems* (each with where it is, for example `app.graph.nodes[ddi]`, and what is wrong), *warnings*, and a summary: who signed the package, whether it lands in *draft* or *testing*, which objects it creates or keeps in SATOM, and which connections need a secret.
- **Validate on SATOM** sends the package to SATOM, which runs the same check there and installs nothing. Needs a token with scope `write`.
- **Rehearse on SATOM** (apps only) walks the app's diagram *unarmed* on SATOM: write steps are recorded, not executed, and SATOM's existing stores, connections and secrets are used. Give the form inputs as a JSON object and choose how `ask` steps are answered: *yes*, *no*, or *stop* (the rehearsal returns the question). An object that the package would create but that does not exist on SATOM yet is reported as a problem: import first. Needs scope `write` and permission to author in Plugin Studio.
- **Import into SATOM** uses the same path as SATOM's own import page. It needs a token with scope `admin` whose owner is a SATOM super-admin. After a validation, the card lists the connections that need a secret; type them there. They are relayed to SATOM with this one request and the Builder keeps no copy. Tick *Allow a downgrade* to replace a newer version.

The last check (local or on SATOM) is remembered and shown on the overview and in the project list.

**Project settings** changes the name, slug, version, icon, target and description. **Images** lists the images of an app's design (the editor uploads them). **Delete** removes the project from the Builder only (type the slug to confirm); it never touches SATOM.

## 4. The editor

*Open editor* shows the structured editor for the project's kind: the form fields, the diagram, data stores (columns and seed rows), connections (URL and auth type, never a secret), API Catalog versions, the design, scripts, and for views and widgets `body.jinja`, `style.css` and `script.js`. Every save runs the local check, so problems appear as you work. Images (PNG, JPEG, GIF or WebP, recognised from their bytes, at most 512 KiB each and 12 per project) are uploaded from the design tab.

A connection with a non-empty `password`, `secret`, `token`, `api_key` or `client_secret` cannot be saved: SATOM asks for secrets when the package is imported.

### 4.0 Datasets: what a view reads

The **Datasets** tab lists every dataset the target SATOM offers (SATOM 4.3.0 and later), grouped and searchable; tick the ones the view reads and read them in the body as `{{ <key>.rows }}`. Three kinds:

- **Built-in** (`fleet_appliances`, `server_policies`, `server_policies_full`, `server_pools`, `web_protection_profiles`, `certificates`, `audit_recent`, `fleet_counts`, `scheduled_actions`): SATOM's curated queries.
- **Device objects** (`<product>_<object type>`, e.g. `fortiweb_vip`, `fortiweb_server_policy`, `fortigate_firewall_policy`, `fortiadc_load_balance_pool__pool_member`; deep layer `fortiweb_deep_<type>`): one per object type the target's device cache holds, hundreds of them. Columns `id`, `appliance_id`, `device`, `mkey` (the object's name on the device), `parent_id`, then the object's own fields (`deployment-mode` reads `deployment_mode`); secret-looking fields read `********`.
- **Administrator** (`admin_<name>`): a query an administrator of the target built and published in SATOM (Administrator › Database › Plugin datasets). When a plugin needs something no dataset gives, ask them for one: it appears here at the next **Refresh from SATOM**. Your package carries its definition, so a SATOM that does not have it yet creates it pending its administrator's approval.

Your token decides what you see. A **Global** token sees the datasets of every ADOM you may enter; the tab groups them by ADOM: first **Every ADOM (the basics)** — the built-in datasets and the administrator datasets of every ADOM — then one group per product. A token bound to one ADOM sees only that ADOM's datasets; the others SATOM has are listed apart, under *In ADOMs this connection does not reach*, with the ADOM that holds each one. The **Library** page lists the same catalogue with each dataset's columns, relations and how SATOM builds it.

**Where the plugin installs (the ADOM).** You never pick an ADOM to start: a plugin installs in the ADOM of the datasets it reads. Only FortiGate datasets → the FortiGate ADOM; FortiGate **and** FortiWeb datasets (every firewall rule next to the FortiWeb interface IPs, say) → **Global**, where SATOM's Global console opens it; only basics → Global. The editor's header says **Installs in: …** and changes as you tick datasets; click it, or open *Project details*, to read why and, with a Global token, to fix the ADOM by hand (**Installs in (SATOM ADOM)**: Auto, Global or an ADOM). A fixed ADOM that does not offer what the plugin reads is flagged: SATOM would refuse it. Check, Validate, Rehearse, Import and *Load from SATOM* all run in that ADOM; **Generate** and the **Assistant** look in Global, so the AI may pick datasets of any product, and the ADOM then follows what it picked. In SATOM, a Global plugin shows each user only the rows of the ADOMs they may enter.

### 4.1 Test data

The **Test data** tab holds what a test run reads instead of SATOM's database, for this project only. A project with no test data yet **starts from the data of its SATOM target** the first time you open the tab (or Database, or Test run): the tab says so. When the target cannot send it (a SATOM older than 4.3.0, no token, unreachable), the project starts from generated rows instead, and the tab says why.

- **Datasets**: a view or widget gets the datasets it reads (*Datasets this view reads*), SATOM's nine built-in ones and any you added in the Database tab (*Other datasets*). An app gets the fleet only (it answers *Load an appliance* and the lists of SATOM objects). The columns are exactly those SATOM gives a view.
- **Data stores**: the stores this project uses; from the target's rows, or from the package's seed rows for a store that exists only in the package.
- **Connections**: what each connection answers. HTTP: a list of responses, each with a method (`*` = any), a path (`*` = one path segment, `**` = anything), a status and a JSON body; the first match answers, otherwise the default. PostgreSQL: rows per text found in the SQL.
- **SATOM and device steps**: what a check, a DDI, certificate or FortiWeb step answers in a test run (ok, yes, no or error, a value, a detail). A test run never reaches a device or SATOM's fleet.

**Load from SATOM** reads the rows again, at most *SATOM rows per table* (default 200, up to 2,000). They are the rows the target SATOM shows **you**: your token's ADOM, no maintenance device unless you may see them, the audit log only with the audit permission, sensitive columns masked, values as text (as a view sees them). Connections and step answers are not SATOM's data and stay as they are. Those rows stay on the Builder: they never travel in a package and are never sent to the AI. SATOM writes an audit line for each read.

**Generate all** fills everything with synthetic values instead (lab names such as `fwb-lab-01`, documentation addresses such as 192.0.2.10, `example.com` hosts), the same for the same **Seed**. **Regenerate** on a table redoes only that table. Every table shows where its rows came from: *from SATOM* (target, time, and *first rows of N* when SATOM had more), *generated (seed N)*, *package seed rows*, and *edited* once you change it. Edit cells in the table, switch to **JSON**, or load a **CSV** file (its header names the columns). **Save test data** keeps your changes; test data is separate from the project and never travels in the package.

### 4.2 Database

The **Database** tab shows the saved test data as a database:

- **Diagram**: one box per table, with its columns and their types. A dataset is a table named by its key (`fleet_appliances`, `fortiweb_vip`), a data store a table `store_<slug>`. Lines join a column to the table it refers to, as SATOM describes it: `appliance_id` to `fleet_appliances`, a sub-object's `parent_id` to its parent, a field named like another object type (a policy's `vserver`) to that type's `mkey` (inferred from the name), and among the built-in datasets `device`, `server_pool` and `wpp`; the relations are also listed as text.
- **More of SATOM's data**: search the target's datasets and **Add** one to the test data (SATOM's rows for you, like Load from SATOM) to look at it, join it and query it; **Remove** takes it out again. The datasets the view reads and the built-in ones are always there. Click a table to highlight its lines and see its columns, where its rows came from and, for a dataset, **the query SATOM fills it with** (PostgreSQL, run on SATOM).
- **SQL**: one read-only SQLite `SELECT` (or `WITH`) over the saved test data, at most 500 rows back, stopped after 3 seconds. **Example queries** offers one per table, a join per relation and devices per kind. Ctrl+Enter runs the query. A write, `ATTACH`, `PRAGMA` or a file function is refused: change data in the Test data tab.
- The **Assistant** knows these tables (names, types, row counts, relations; never a row) and answers questions about the data with a query you can run here.

### 4.3 Test runs

The **Test run** tab runs the **saved** project with SATOM's own runtime, taken verbatim from the target's rules pack, on the test data, here on the Builder. Nothing is sent to SATOM and nothing leaves the Builder: the run has no network.

- **A view or a widget** renders in SATOM's frame with the test data and the params you choose.
- **An app** walks its diagram for real against the test data: store writes change a copy of the test data, which you can keep with **Keep these changes in the test data**; API calls get the test responses; *Ask a person* is answered with the answer you pick. The steps, outputs, store writes and what the test data answered are shown, and the steps are marked on the diagram.
- **A script** (also from the Scripts tab: **Run on test data**) runs in SATOM's jail with SATOM's SDK: `inputs`, `result`, `log` and `connection()`, answered by the test data. The result, the log, the exit code and the connection calls are shown.

Every run is kept: the history lists the last 50 runs of the project, with their inputs (passwords masked), and a run goes on if you close the page. Test runs need SATOM 4.2.0 or later on the target; script runs need `bubblewrap` on the Builder server (the installer installs it; the tab says so when it is missing).

### 4.4 From the Builder to production

1. Build and test here: **Check** (the rules pack), then **Test run** on your test data until it does what you want.
2. Send it to your **test SATOM**: link the project to that target, **Validate on SATOM**, **Rehearse** (unarmed, against that server's own objects), then **Import** (it lands in draft or testing there).
3. For **production**, download the signed package (**Download package**) and import it in the production SATOM, or import it through a production target with a token that may import. The production SATOM checks it again with its own rules.

## 5. Generating with AI

The overview's **Generate with AI** card drafts or changes the project with a model an administrator has set up (section 10).

1. Pick an **AI profile**, describe what you want, and set **Max rounds** (1 to 6, default 3).
2. **What will be sent** shows the exact request: the *system* part (the rules pack's author guide, generated from SATOM's rules, followed by the output contract) and the *user* part (the target's names and shapes, the current draft, your text), with its size. Data rows, connection secrets, your tokens and the provider keys are never in it. Seed rows of data stores are left out too; the Builder keeps them.
3. **Generate** starts a job. The model answers with one JSON draft. The Builder builds the package and runs the local check; a view or widget that passes it is also **rendered** with SATOM's own runtime over generated rows that have exactly each dataset's columns (with no input and with sample inputs), because SATOM's rules check that a template parses, not that it runs. A render failure — a column that does not exist, say — is a problem like any other, sent back with the columns of the datasets the view reads. Problems go back to the model with the draft, round after round, up to *Max rounds*. An answer that is not a JSON draft counts as a round, and the error goes back to the model.
4. Each round is listed with its problems. When the job ends, the generated draft is shown. **Your project does not change until you click _Apply this draft to the project_.** If problems remain after the last round, the draft is shown with them and applying it keeps them: nothing is fixed silently.

**Your conversations.** Every Generate is a turn of a conversation, kept for you and this project only. The card lists your conversations (title, requests, date, *working* while one runs) — the same ones the editor's **AI** window shows. Open one to read each request and what the AI answered, with its status (*passes the rules*, *problems left*, *no draft*, *failed*), **What it did** (every step) and **Show this result (and apply it)**; the button then reads **Continue**, and the AI starts from the project as saved plus that conversation. **New conversation** starts over.

**View or app?** SATOM's data — FortiGate rules, FortiWeb objects, every dataset — is read by **views and widgets**. An **app** (a form and a diagram) has no step that reads a dataset, so a request such as "look an IP up in the FortiGate rules" is a view with an input parameter (the IP): create a view project for it. Asked in an app, the AI says so instead of building something else.

Generation needs the target's rules pack: if it is missing or was made for another SATOM version, generation is blocked and the message says why. Provider errors (a wrong key, rate limiting, a refusal) are shown with the provider's message and the key removed. With an Anthropic profile on Anthropic's own API, a request the model declines is retried automatically on Anthropic's recommended fallback model, if the profile allows it.

### 5.1 The Assistant (conversations)

The editor's **Assistant** tab keeps conversations with an AI profile about the project. Each conversation is stored on the Builder with its files: close the page or open the project tomorrow and it is there, with the status of every reply.

- **Attach** images (PNG, JPEG, GIF, WebP, up to 5 MB) and text files (code, JSON, CSV, YAML, logs, up to 512 KB). Images reach the model as images: pick a profile whose model reads images. Text files go into the request.
- **Send** (or Ctrl+Enter) saves the project and starts the reply on the server. The model gets the author guide, the target's names and shapes, the saved draft, the conversation so far, your files and the tables of the test database (names, types, row counts, relations); never data rows or secrets.
- A reply may answer a question without changing anything, or propose a draft. A draft is checked by the rules pack and repaired like Generate; problems left are shown under the reply.
- **Apply to the project** makes the draft the project (a normal save: the previous version is kept as a revision). Until then nothing changes. **Show the draft** shows it first.
- A reply cut by a restart of the Builder is marked as such: send the message again.

## 6. Library

**Library** (in the sidebar) shows, for a target you have a token for, what a plugin builds on: the **datasets** a view reads (built-in, administrator and one per device-object type: search them, open one for its columns, masked ones marked, its relations and its SQL or query), the **data stores** and their columns, the **API Catalog** with every version's endpoints, the **Fortinet API library** (pick a build to list its endpoints, open one for its fields), the **step kinds** of a diagram, the **script SDK** and the **author guide**. It is read with your token and shows structure only: no row of the target's data and no secret. **Refresh from SATOM** reads it again. It needs SATOM 4.2.0 or later on the target.

## 7. My tokens

Everything the Builder does in a SATOM runs with **your own SATOM API token**, so it carries your RBAC and your ADOMs and SATOM's audit log names you. Create the token in SATOM: an administrator with the *users.manage* permission creates it under **Administrator → API Tokens**, picks you as its owner and, recommended, the **Global** ADOM (the token is shown once). Then paste it on **My tokens** next to the target. With a Global token the Builder sees the datasets of every ADOM you may enter and each project installs in the ADOM of what it reads (section 4.0); a token bound to one ADOM keeps every project in that ADOM. The page says which one each link is: *Global token* with the datasets per ADOM, or *Bound to ADOM X* with how many datasets live in ADOMs it cannot reach. Replacing or removing a token drops what the Builder had cached through it.

| Scope | Allows |
|---|---|
| `read` | Reading the target (its version, choices, existing objects, schema) and downloading the rules pack. |
| `write` | Validate on SATOM; Rehearse (also needs permission to author in Plugin Studio). |
| `admin` | Import, and registering the publisher key; SATOM accepts both only from a super-admin. |

Tokens are encrypted at rest. The page only says *set* or *not set*. **Test** fetches the target and the library again and shows who you are on SATOM, your scopes, the ADOM and what the token reaches; **Remove** deletes your token. A token that SATOM rejects is reported as "your SATOM token is invalid or lacks scope X".

## 8. Publisher key

The installation signs every package with its own Ed25519 publisher key (created by the installer). The page shows the **key_id** and the **public key** (base64 of the raw 32 bytes); the private key never leaves the server.

A SATOM that trusts this key (*Trusted publishers*) treats the Builder's packages as signed: views and widgets land in *testing* instead of *draft*. To register the key, click **Register on target**: the Builder calls SATOM with **your** token, which must have scope `admin` and belong to a SATOM super-admin. Registering again is harmless (SATOM answers with the existing key_id). A super-admin can also paste the public key into SATOM by hand.

## 9. Account

Shows your user name, role, how you sign in and your last sign-in. Local users can change their password here (at least 12 characters). Directory users change theirs in the directory.

## 10. Administration

The *Administrator* part of the sidebar is shown to administrators only. On a phone (768 px or narrower) the sidebar is hidden: the menu button at the top left opens it over the page, and a tap outside or Escape closes it.

### SATOM targets

A target is one SATOM server. Fields:

- **Name** and **Base URL** (`https://` only; the Builder calls `/api/v1/builder/*` under it).
- **CA bundle** (optional, PEM): when set, only these CAs are trusted for this target, for a SATOM with an internal CA. Empty means the system's public CAs.
- **Verify TLS**: leave it on. Off is for a lab network only, and the page warns about it.

Deleting a target removes everyone's token for it; its projects keep their drafts but lose their target. Editing a target clears the cached snapshots.

### AI profiles

Keep as many profiles as you need, enabled at the same time (for example Claude and a private Qwen
or DeepSeek): every enabled profile is offered to authors, who pick one for each generation.

**Start from** (new profiles) fills the form for Anthropic, a private server (Ollama, vLLM or LM
Studio) or another OpenAI-compatible service. A private server's URL comes with `your-ai-server` in
it: replace it with your server's name or address (a profile that still names `your-ai-server` is
refused), save, then press **Test**. The *Model* field suggests common models (`qwen3:32b`,
`deepseek-r1:32b`, `Qwen/Qwen3-32B`, …); any name your server knows is accepted.

**Private models.** A model your company runs itself (Qwen, DeepSeek, Llama, Mistral…) is an
*OpenAI-compatible* profile pointing at your own server, so prompts and answers never leave your
network. Reasoning models (DeepSeek-R1, Qwen3, QwQ) think before they answer. Builder drops a
`<think>…</think>` block when the server leaves it in the answer, and reads the answer alone when the
server sends the thinking in its own field (Ollama does). The thinking still counts against
*Max tokens*: give these models 32,768 or more.

Fields:

| Field | Notes |
|---|---|
| Adapter | **Anthropic** (official SDK; default model `claude-opus-5-5`; streaming; the author guide is sent as a cached system block) or **OpenAI-compatible** (OpenAI, Azure OpenAI, Mistral, Gemini's compatible endpoint, Ollama, vLLM, LM Studio). |
| Base URL | Anthropic: empty for Anthropic's API, or your gateway. OpenAI-compatible: the URL that ends before `/chat/completions`, for example `https://api.openai.com/v1` or `http://127.0.0.1:11434/v1`. |
| Model | The provider's model name. |
| API key | Encrypted, write-only: leave empty to keep the stored key, tick *remove the key* to delete it. Local servers (Ollama, vLLM) need none. |
| Max tokens | The longest answer allowed (256 to 128000; default 64000). A draft cut off at this limit is reported as "output too long, raise max tokens". |
| Effort | Anthropic only (hidden for other adapters): `low` to `max`; default `high`. |
| Refusal fallbacks | Anthropic only, and only on Anthropic's own API: a declined request is retried on Anthropic's recommended fallback model. On by default; the list shows *n/a here* for other base URLs. |
| Enabled | Disabled profiles are hidden from authors. |

**Context window.** A request carries SATOM's author guide (about 5,000 tokens) plus the target's schema:
about 7,000 tokens for a view and up to 30,000 for an app. Hosted models have room. A local server
truncates silently at its own default (Ollama: 2,048–4,096 tokens), and the model then never sees the
output contract. Start Ollama with `OLLAMA_CONTEXT_LENGTH=32768` or more, or the equivalent setting
of your server.

**Test** sends a tiny prompt and shows the answer or the provider's error.

### Users

Create local users (role *author* or *admin*, password of at least 12 characters) or directory users (they sign in with their directory password). For each user you can change the role, disable or enable, reset a local password, or delete the user together with their projects and tokens. You cannot disable or delete yourself, and the last enabled administrator keeps its role.

### Directory sign-in

The same engines as SATOM:

- **LDAP**: a service account (bind DN and password) finds the user's DN under the base DN with the user attribute or filter (`{username}` is replaced, escaped), then the Builder binds as that DN with the password typed at sign-in.
- **Active Directory**: binds directly as `user@domain` (the *AD domain* field) and, with a base DN, confirms the account exists there.
- **RADIUS**: an Access-Request with the shared secret and the NAS-Identifier; *Access-Accept* signs the user in. This is the path for FortiAuthenticator and FortiToken push.

TLS: LDAPS or StartTLS, certificate verification, and an optional CA bundle for the directory. The bind password and the RADIUS secret are encrypted and write-only. *Create users on their first directory sign-in* gives new directory users the role *author*.

**Test connection** checks the saved settings: without credentials it checks the server and the service bind (RADIUS: that the host resolves); with a test user and password it runs a full sign-in.

### Updates

Upload a signed Builder update package (`satom-builder-update-<version>`, as .tar, .tar.gz or .zip) and it is queued. The web application only queues requests; the updater runs as root, checks the package's signature against the Builder's release keys, backs up the database, migrates, and rolls back if a step fails. **Roll back to the previous version** and **Restart the service** queue those actions. *Waiting for the updater* lists queued requests; **Runs** lists each run's status (*queued*, *running*, *done*, *failed*, *rolled-back*), the versions, and every step with its log (click a run to expand it).

### Audit log

Every security-relevant action is recorded with its time, user, object, details and address: sign-ins (successful, failed and blocked), sign-outs, password changes, user, target, AI profile and directory changes, token changes and tests, publisher registrations, project creation, adoption, changes and deletion, validations, rehearsals and imports on SATOM, package downloads, generation jobs and update requests. Secrets never appear in it. Filter by user, action or object.

## 11. Roles

| | author | admin |
|---|---|---|
| Own projects, tokens, generation, publisher registration (with a suitable SATOM token) | yes | yes |
| Everybody's projects | no | yes |
| Targets, AI profiles, users, directory sign-in, updates, audit log | no | yes |

What you may do *in SATOM* is decided by SATOM, from your token.

## 12. Troubleshooting

| Message | What to do |
|---|---|
| "You have no SATOM token for X yet" | Add your token under *My tokens*. |
| "Your SATOM token is invalid or lacks scope write/admin" | Create a token with that scope in SATOM; import and publisher registration also need a super-admin. |
| "TLS verification of … failed" | Ask an administrator to add SATOM's CA to the target. |
| "… does not offer the SATOM Builder API" | The target's SATOM release has no Builder support yet. |
| "The rules pack is for SATOM A but the target runs B" / "does not match its digest" | The pack is refused on purpose. Refresh the target (*My tokens › Test*) and retry; if it persists, SATOM is serving a pack that does not match its version. |
| "The rules pack did not answer within 30 s" | The check was stopped. Retry; if it persists, report it with the project. |
| "The output was too long: raise max tokens" | Raise *Max tokens* in the AI profile. |
| "The model spent all of max tokens reasoning" / "returned only its reasoning" | A reasoning model (DeepSeek-R1, Qwen3) thought until the limit: raise *Max tokens* in the AI profile. |
| "Replace your-ai-server in the base URL" | The profile came from *Start from*: put your AI server's name or address in the base URL. |
| "The model declined the request (refusal category: …)" | Rephrase the request. |
| "Connection … carries a secret" | Remove the value: SATOM asks for it on import. |
