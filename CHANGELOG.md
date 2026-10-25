# Changelog

All notable changes to SATOM Builder are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/).

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
