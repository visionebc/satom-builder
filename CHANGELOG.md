# Changelog

All notable changes to SATOM Builder are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

## [0.2.0] - 2026-10-25

Everything a plugin author needs to build AND test, with SATOM's own code, on
test data, before anything reaches a SATOM: test data, test runs, the
library of what plugins build on, and an assistant conversation that keeps
its history and files. **Needs SATOM 4.2.0 or later on the target** for test
runs and the Library (an older SATOM keeps working for everything else and
the pages say what they need).

Requested: "the chat must take files and images, and keep its history and
state without being lost when closed; a database with mock data so the user
can test; the API in its structure and format so the user sees the library;
scripts run in the sandbox with SATOM's rules; scripts test-run with the mock
data; then send it to the test SATOM, and users bring it into production."

### Added

- **Test data** (editor tab): what SATOM's runtime reads instead of a
  database. Datasets carry exactly the columns SATOM gives a view (read from
  SATOM's own dataset queries, through the rules pack); data stores start
  from the package's seed rows; connections answer canned responses
  (method + path patterns, status, JSON body; SQL answers for PostgreSQL);
  the steps that would touch a device, the DDI, the Certificate Manager or
  SATOM's fleet answer what the author sets (ok / yes / no / error, a value,
  a detail). Generated values are synthetic and deterministic per seed (lab
  names, documentation addresses 192.0.2.0/24, 198.51.100.0/24,
  203.0.113.0/24, example.com): never a value of a real installation. Edit in
  a table, as JSON, or from a CSV file.
- **Test run** (editor tab): the saved project run by SATOM's own runtime,
  copied verbatim from the target's rules pack (contract section 13), here on
  the Builder, with no network and nothing sent to SATOM:
  - a view or widget renders in SATOM's frame (tokens, class kit) with the
    test data and the chosen params;
  - an app's diagram walks ARMED, because everything it touches is test data:
    store writes change a copy you may keep ("Keep these changes"), an API
    call gets the canned response, SATOM and device steps answer the Test
    data, "Ask a person" is answered yes / no / stop;
  - a script runs in SATOM's jail (bubblewrap: no network, no Builder file)
    with SATOM's SDK, `connection()` answered by the test data through SATOM's
    own connection broker, inputs typed the way SATOM types them.
  Every run is kept (the last 50 per project, with the inputs, passwords
  masked, and the full answer) and goes on if the page is closed.
- **Library** (sidebar): what a plugin builds on, read from the target with
  your token: the datasets and their columns, the data stores and their
  columns, the API Catalog with every version's endpoints, the Fortinet API
  library (builds, then a build's endpoints, then an endpoint's fields), the
  step kinds of a diagram, the script SDK and the author guide. Structure
  only: no row, no secret. Cached per author like the target snapshot.
- **Assistant** (editor tab): conversations about the project with an AI
  profile, kept on the Builder with their files. Attach images (PNG, JPEG,
  GIF, WebP: they reach the model as images, so use a model that reads them)
  and text files (code, JSON, CSV, YAML, logs: they go into the prompt). A
  reply is written on the server: close the page and it is there when you
  come back; a reply cut by a restart says so. The model may answer a
  question without changing anything, or propose a draft, which is checked by
  the rules pack and repaired like Generate; the draft reaches the project
  only with "Apply to the project" (a normal save: the previous draft stays as
  a revision).
- **Brand:** SATOM's emblem with the Builder's badge, in the top bar, on the
  sign-in page and as the favicon.
- The installer installs `bubblewrap` (all three families, also in the
  offline bundles) and checks that the jail works as the service account.

### Fixed

- **A draft made with AI (Generate) was lost on the next reload.** The editor
  marked it as saved although the job never saves (the author applies it). It
  is now an unsaved change, kept in the local draft until Save.

### Upgrading

- Script test runs need `bubblewrap` on the Builder server. A new install or
  a re-run of the installer installs it; after an update package, install the
  `bubblewrap` package with the system's package manager. Until then the Test
  run tab says so and runs views and diagrams without script steps.

## [0.1.6] - 2026-10-25

Private AI models, and an installer RE-RUN that no longer trusts what the
service account wrote. **Upgrade every node, and re-run the installer only
with 0.1.6 or later.**

### Security

- **Re-running the installer could give the service account root.** On a node
  that is already installed, the installer ran the venv's `python`/`pip` as
  root (installing dependencies, generating `FERNET_KEY` and the publisher
  key), sourced `.env` as root shell code, and installed the units, the
  operator CLI, the update runner, the release keys and the TLS helper from
  `/opt/satom-builder/deploy` -- all of which the service account owns and can
  rewrite. Now:
  - root-side files come from a root-only copy of the installer's OWN release
    (the bundle, the tarball, the checkout or the clone it installs from),
    never from the installed tree;
  - the venv, `pip` and `manage` run as the service account, which also reads
    `.env` itself; the publisher key is generated by the account and copied in
    through a descriptor that does not follow a link;
  - `.env` is created, appended and sealed through descriptors, and a `.env`
    that is not root's stops the install (inspect it, restore it as
    `root:<account> 0640`, re-run);
  - the installer refuses to run from inside the installation directory.
  Verified on a real node with the attacks planted: a replaced venv `python`
  ran only as the service account, a modified `deploy/install-cli.sh` never
  ran, the installed runner unit is the release's byte for byte, and a
  `.env` holding shell code was refused without being executed.
- **An update no longer turns a `.env` the service account wrote into
  root's.** The runner re-sealed whatever `.env` it found as
  `root:<account> 0640`; it now re-seals only the root-owned one it opened
  before handing the tree back.

### Added

- **Private AI models, several at once.** *AI profiles* has a **Start from**
  list: Anthropic, a private server (Ollama, vLLM, LM Studio: Qwen, DeepSeek,
  Llama, Mistral…) or another OpenAI-compatible service. It fills the adapter,
  base URL, model and max tokens; the model field suggests common models. A
  profile still naming the placeholder `your-ai-server` is refused. The page
  says that every enabled profile is offered to authors at the same time, and
  hides the Anthropic-only fields (effort, refusal fallbacks) for other
  adapters.

### Fixed

- **A reasoning model's thinking no longer breaks a generation.** When a server
  leaves DeepSeek-R1's or Qwen3's `<think>…</think>` block in the answer (vLLM
  without a reasoning parser, llama.cpp), the JSON was read from the first `{`
  of the reasoning and failed as "not valid JSON". The block is dropped first,
  also in DeepSeek-R1's form that carries only the closing tag.
- **"Spent all of max tokens reasoning"** replaces "the output was too long"
  when the answer is empty and the thinking came in its own field (Ollama),
  and an answer that is only reasoning says to raise max tokens.

## [0.1.5] - 2026-10-25

The console on a phone (measured at 360, 390 and 430 px in a real browser),
and root no longer touches the service account's files by path.

### Security

Found by the release-trust review of `deploy/`. The application tree and
`data/` belong to the service account, which can turn any entry into a symbolic
link at any moment, including between a check and the write that follows it.
Root processes wrote, `chmod`-ed and `chown`-ed there BY PATH. **Upgrade every
node.**

- **`satom-builder update apply` could overwrite and give away any file on the
  system.** It copied the package through a predictable
  `data/update-uploads/.<name>.part` and changed its mode and owner by path: a
  link planted there by the service account made root write the package over
  the file it pointed at (the runner's own code, for one) and hand it to that
  account. `update fetch` and every queued request had the same flaw. They now
  write through descriptors that never follow a link (`O_NOFOLLOW`, a random
  `O_EXCL` temp name, mode and owner set on the open file, atomic rename); a
  package given by a path inside the application tree, other than
  `data/update-uploads/<name>`, is refused.
- **The update runner writes the application tree as the service account.**
  The verified `app.tar.gz` is still validated by root (no links, devices,
  absolute or `..` paths, size ceiling); the account extracts it, removes the
  files the new revision dropped, and restores the venv and, on
  `rollback --restore-db`, the databases. The package is copied to root-only
  staging through a descriptor before it is verified, and the database backups
  are read the same way.
- **`VERSION` and the status files are read without following a link.** The
  runner repeated `VERSION` in the status file the service account reads, so a
  link there disclosed any root-readable file; only a plain version is kept.
- **`.env` is read only when it is a regular file owned by root.** A link put
  in its place made the runner read any `KEY=value` file root can read (another
  product's secrets) and pass it to a process running as the service account.
- **`install-cli.sh` and `install-runner.sh` refuse a source that is a link**
  (`cp -a` copied it as a link, and the root-owned copy executed whatever it
  pointed at). `chown -R` of the tree no longer follows links (`-h`).
- **Re-running the installer refuses links** at `.env`, `data/`, its queue
  directories, `state/`, `data/.installed` and the log directory instead of
  writing, `chmod`-ing and `chown`-ing what they point at.

### Fixed

- **The menu button opens the sidebar on a phone.** It only toggled the
  desktop collapse, so below 768 px the sidebar stayed hidden and every page
  outside the current one (Administrator, AI profiles, tokens) was out of
  reach. It is now an overlay: a tap outside or Escape closes it.
- **The footer no longer keeps the sidebar's margin on a phone** (it wrapped
  in a narrow strip on the right).
- **No field below 16 px on a phone**, so iOS Safari does not zoom into the
  page when a field is focused. The script editor's line numbers follow.
- **The editor is one column on a phone**: a three-column field row cut the
  data store's name and broke "Searchable" into one word per line.
- **Users:** the role of each user is visible again (its select had shrunk to
  the arrow).
- Small buttons grow to a tappable size on a phone.

### Changed

- **Projects:** "Last check" is the second column, so the check result is on
  the first screen of a phone; the slug moved after the target.

## [0.1.4] - 2026-10-25

First public release: the repository and its releases are published at
https://github.com/visionebc/satom-builder. No change to the application.

### Changed

- **Built-in upgrade feed.** `satom-builder update fetch` reads the
  `satom-builder-update-latest.json` asset of the latest GitHub release when
  no `--feed` is given. `SATOM_BUILDER_UPGRADE_FEED` (or `--feed`) still
  replaces it with an internal mirror.
- **README** with what Builder guarantees, how to install from a release
  bundle and how to update; the documentation no longer names internal
  development repositories.

## [0.1.3] - 2026-10-25

Fixes found by the first end-to-end runs against a live SATOM, a local model
and a real browser. (0.1.1 and 0.1.2 were lab builds, never published.)

### Fixed

- **The editor reads a stored check from its result.** A project whose last
  check had passed showed a red "Last check: 0 problem(s)" badge.
- **A view's or widget's AI request carries only what a view can use**
  (datasets, parameter types, name patterns, limits), as compact JSON. With
  the whole app schema a view's request was about 27,000 tokens, past a
  local model's default context, and the model never saw the output
  contract. The context window a local server needs is documented on the AI
  profiles page and in the user guide.
- **The local check runs SATOM's real rules pack.** The pack ran in a child
  interpreter whose import path came from `sysconfig`. Inside a virtual
  environment that path has no `lib-dynload`, so SATOM's own pack stopped at
  `_contextvars`. The child now gets the interpreter's real standard library
  path, and still nothing else: no working directory, no user site, no
  Builder source.
- **Registering the publisher key refreshes what the Builder knows about the
  target.** Cached target snapshots taken before the registration made the
  local check report "a key this server does not trust" while SATOM's own
  validation accepted the package.
- **An untrusted key that is this installation's own publisher key gets a
  hint:** register it on the target from the Publisher page.

## [0.1.0] - 2026-10-25

First version. A customer-installed tool to author SATOM plugins and Plugin
Studio apps, check them with the target SATOM's own rules, rehearse and import
them through SATOM's API with each author's own token, and optionally generate
them with an AI model the user chooses.

### Added

- **Editors** for views, widgets and complete Plugin Studio apps:
  - form fields;
  - the diagram of steps;
  - output actions;
  - data stores with columns and seed rows;
  - HTTP connections without secrets;
  - API Catalog versions, including OpenAPI import;
  - design blocks;
  - sandboxed scripts that may use only `inputs`, `result`, `log` and their declared connections.
- **Checks with SATOM's own rules.** The target's rules pack is downloaded, pinned by its sha256 and run in a bare subprocess. SATOM checks again on validate, rehearse and import.
- **Per-author SATOM tokens.** Everything runs with that person's rights and ADOM, and SATOM's audit log shows who acted.
- **A publisher key per installation**, which a SATOM super-admin registers as a Trusted publisher.
- **AI profiles:**
  - Anthropic, through the official SDK;
  - any OpenAI-compatible endpoint, including local models.

  A repair loop sends the rules' problems back to the model. Before anything is sent, the author sees exactly what goes out: never data rows, never secrets.
- **Installer, offline bundles and signed update packages** that follow SATOM's installer and update rules:
  - Debian 12, RHEL 9 and SUSE 15, online and offline;
  - updates through a privileged runner with rollback.
