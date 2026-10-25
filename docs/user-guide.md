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
5. [Generating with AI](#5-generating-with-ai)
6. [My tokens](#6-my-tokens)
7. [Publisher key](#7-publisher-key)
8. [Account](#8-account)
9. [Administration](#9-administration): targets, AI profiles, users, directory sign-in, updates, audit log
10. [Roles](#10-roles)
11. [Troubleshooting](#11-troubleshooting)

## 1. Signing in

Open the Builder's address and sign in with your user name and password.

- **Local users** sign in with the password set in the Builder.
- **Directory users** sign in with their LDAP, Active Directory or RADIUS (FortiAuthenticator, FortiToken push) password when an administrator has turned directory sign-in on. Depending on the settings, a directory user is created the first time they sign in, with the role *author*.

After five wrong passwords for one user name (or twenty from one address) within 15 minutes, sign-in is paused for that name or address; the page says how long to wait. Sessions end after 12 hours, when you sign out (user menu, top right) or when an administrator disables you or changes your role.

## 2. Projects

**Projects** (sidebar › Build) lists your projects: name, kind, slug, version, SATOM target, the result of the last check, and when it changed. Administrators can switch to *Everybody's* projects.

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

## 5. Generating with AI

The overview's **Generate with AI** card drafts or changes the project with a model an administrator has set up (section 9).

1. Pick an **AI profile**, describe what you want, and set **Max rounds** (1 to 6, default 3).
2. **What will be sent** shows the exact request: the *system* part (the rules pack's author guide, generated from SATOM's rules, followed by the output contract) and the *user* part (the target's names and shapes, the current draft, your text), with its size. Data rows, connection secrets, your tokens and the provider keys are never in it. Seed rows of data stores are left out too; the Builder keeps them.
3. **Generate** starts a job. The model answers with one JSON draft. The Builder builds the package and runs the local check. Problems go back to the model with the draft, round after round, up to *Max rounds*. An answer that is not a JSON draft counts as a round, and the error goes back to the model.
4. Each round is listed with its problems. When the job ends, the generated draft is shown. **Your project does not change until you click _Apply this draft to the project_.** If problems remain after the last round, the draft is shown with them and applying it keeps them: nothing is fixed silently.

Generation needs the target's rules pack: if it is missing or was made for another SATOM version, generation is blocked and the message says why. Provider errors (a wrong key, rate limiting, a refusal) are shown with the provider's message and the key removed. With an Anthropic profile on Anthropic's own API, a request the model declines is retried automatically on Anthropic's recommended fallback model, if the profile allows it.

## 6. My tokens

Everything the Builder does in a SATOM runs with **your own SATOM API token**, so it carries your RBAC and your ADOM and SATOM's audit log names you. Create the token in SATOM: an administrator with the *users.manage* permission creates it under **Administrator → API Tokens** and picks you as its owner (the token is shown once). Then paste it on **My tokens** next to the target.

| Scope | Allows |
|---|---|
| `read` | Reading the target (its version, choices, existing objects, schema) and downloading the rules pack. |
| `write` | Validate on SATOM; Rehearse (also needs permission to author in Plugin Studio). |
| `admin` | Import, and registering the publisher key; SATOM accepts both only from a super-admin. |

Tokens are encrypted at rest. The page only says *set* or *not set*. **Test** fetches the target and shows who you are on SATOM, your scopes and the ADOM; **Remove** deletes your token. A token that SATOM rejects is reported as "your SATOM token is invalid or lacks scope X".

## 7. Publisher key

The installation signs every package with its own Ed25519 publisher key (created by the installer). The page shows the **key_id** and the **public key** (base64 of the raw 32 bytes); the private key never leaves the server.

A SATOM that trusts this key (*Trusted publishers*) treats the Builder's packages as signed: views and widgets land in *testing* instead of *draft*. To register the key, click **Register on target**: the Builder calls SATOM with **your** token, which must have scope `admin` and belong to a SATOM super-admin. Registering again is harmless (SATOM answers with the existing key_id). A super-admin can also paste the public key into SATOM by hand.

## 8. Account

Shows your user name, role, how you sign in and your last sign-in. Local users can change their password here (at least 12 characters). Directory users change theirs in the directory.

## 9. Administration

The *Administrator* part of the sidebar is shown to administrators only.

### SATOM targets

A target is one SATOM server. Fields:

- **Name** and **Base URL** (`https://` only; the Builder calls `/api/v1/builder/*` under it).
- **CA bundle** (optional, PEM): when set, only these CAs are trusted for this target, for a SATOM with an internal CA. Empty means the system's public CAs.
- **Verify TLS**: leave it on. Off is for a lab network only, and the page warns about it.

Deleting a target removes everyone's token for it; its projects keep their drafts but lose their target. Editing a target clears the cached snapshots.

### AI profiles

Authors pick a profile for each generation. Fields:

| Field | Notes |
|---|---|
| Adapter | **Anthropic** (official SDK; default model `claude-opus-5-5`; streaming; the author guide is sent as a cached system block) or **OpenAI-compatible** (OpenAI, Azure OpenAI, Mistral, Gemini's compatible endpoint, Ollama, vLLM, LM Studio). |
| Base URL | Anthropic: empty for Anthropic's API, or your gateway. OpenAI-compatible: the URL that ends before `/chat/completions`, for example `https://api.openai.com/v1` or `http://127.0.0.1:11434/v1`. |
| Model | The provider's model name. |
| API key | Encrypted, write-only: leave empty to keep the stored key, tick *remove the key* to delete it. Local servers (Ollama, vLLM) need none. |
| Max tokens | The longest answer allowed (256 to 128000; default 64000). A draft cut off at this limit is reported as "output too long, raise max tokens". |
| Effort | Anthropic only: `low` to `max`; default `high`. |
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

## 10. Roles

| | author | admin |
|---|---|---|
| Own projects, tokens, generation, publisher registration (with a suitable SATOM token) | yes | yes |
| Everybody's projects | no | yes |
| Targets, AI profiles, users, directory sign-in, updates, audit log | no | yes |

What you may do *in SATOM* is decided by SATOM, from your token.

## 11. Troubleshooting

| Message | What to do |
|---|---|
| "You have no SATOM token for X yet" | Add your token under *My tokens*. |
| "Your SATOM token is invalid or lacks scope write/admin" | Create a token with that scope in SATOM; import and publisher registration also need a super-admin. |
| "TLS verification of … failed" | Ask an administrator to add SATOM's CA to the target. |
| "… does not offer the SATOM Builder API" | The target's SATOM release has no Builder support yet. |
| "The rules pack is for SATOM A but the target runs B" / "does not match its digest" | The pack is refused on purpose. Refresh the target (*My tokens › Test*) and retry; if it persists, SATOM is serving a pack that does not match its version. |
| "The rules pack did not answer within 30 s" | The check was stopped. Retry; if it persists, report it with the project. |
| "The output was too long: raise max tokens" | Raise *Max tokens* in the AI profile. |
| "The model declined the request (refusal category: …)" | Rephrase the request. |
| "Connection … carries a secret" | Remove the value: SATOM asks for it on import. |
