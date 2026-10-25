# Code vendored from SATOM

SATOM Builder's installer and update system copy SATOM's patterns. Generic
pieces are **vendored** (copied, renamed, attributed here) -- never imported
from SATOM at runtime, and no path, account, unit, key, schema or trust store is
shared with SATOM.

- Source repository: SATOM (same licensor, Elastic License 2.0).
- Source revision: **`0b7a6193`** ("chore(release): system-upgrade feed for 4.0.1").
- Paths below are relative to the SATOM repository root.

## Rename map (applies to every entry)

| SATOM | SATOM Builder |
|---|---|
| account `satom` | `satom-builder` |
| `/opt/satom` | `/opt/satom-builder` |
| `/etc/satom/update-keys` | `/etc/satom-builder/update-keys` |
| `/etc/satom/release-trust.allowed_signers` (`.pub`) | `/etc/satom-builder/release-trust.allowed_signers` (`.pub`) |
| `/usr/local/lib/satom-runner`, `/usr/local/lib/satom-cli` | `/usr/local/lib/satom-builder-runner`, `/usr/local/lib/satom-builder-cli` |
| `/usr/local/sbin/satom`, `/usr/bin/satom` | `/usr/local/sbin/satom-builder`, `/usr/bin/satom-builder` |
| `/var/lib/satom-release/mirror.git` | `/var/lib/satom-builder-release/mirror.git` |
| `/var/lib/satom-runner`, `/var/lib/satom-rollback` | `/var/lib/satom-builder-runner`, `/var/lib/satom-builder-rollback` |
| `/root/.satom-release/release_ed25519`, principal `satom-release` | `/root/.satom-builder-release/release_ed25519`, principal `satom-builder-release` |
| `satom.service`, `satom-updater.{path,service}` | `satom-builder.service`, `satom-builder-updater.{path,service}` |
| `/etc/sudoers.d/satom`, `satom-installer` | `/etc/sudoers.d/satom-builder`, `satom-builder-installer` |
| `/var/log/satom-install.log`, `/var/log/satom-setup.log` | `/var/log/satom-builder-install.log`, `/var/log/satom-builder-setup.log` |
| schema `satom.update-package/1`, product `satom` | `satom-builder.update-package/1`, `satom-builder` |
| key line type `satom-ed25519`, seed tag `satom-ed25519-seed` | `satom-builder-ed25519`, `satom-builder-ed25519-seed` |
| feed schema `satom.upgrade-feed/1`, `satom-update-<v>.tar.gz` | `satom-builder.upgrade-feed/1`, `satom-builder-update-<v>.tar.gz` |
| env `FM_APP_DIR`, `FM_APP_USER`, `SATOM_TRUST_DIR`, `SATOM_DEPLOY_SRC` | `SATOM_BUILDER_APP_DIR`, `SATOM_BUILDER_APP_USER`, `SATOM_BUILDER_TRUST_DIR`, `SATOM_BUILDER_DEPLOY_SRC` |
| gunicorn `127.0.0.1:8000`, `wsgi:app` | `127.0.0.1:8100`, `satom_builder.wsgi:app` |
| PKI in `/opt/satom/pki` | `/etc/satom-builder/pki` (root-only; the Builder has no certificate manager) |
| bundle `satom-offline-<v>-<os>.tar.gz` / `satom-installer/` | `satom-builder-offline-<v>-<os>.tar.gz` / `satom-builder-installer/` |

## Files

| Builder file | SATOM source | What was taken | What changed |
|---|---|---|---|
| `deploy/update_package.py` | `deploy/update_package.py` | Whole module: pure-Python Ed25519 (RFC 8032) verify/sign, trust store (`root_owned_problem`, `trust_store_problem`, `load_trust_store`, `parse_public_key`, `format_public_key`, `key_fingerprint`), safe extraction (`_safe_members`, `_refuse_links_on_disk`, `extract_package`, `extract_app_tree`, size ceiling), manifest (`build_manifest`, `dump_manifest`, `read_manifest`), verification (`verify_signature*`, `verify_contents`, `verify_package`), versions, wheel deltas (`wheel_key`, `select_wheels`, `read_package_wheelset`) | Renames; docstring; API-pack trust lanes (`PACK_LANES`, `PACK_KEY_DIR`, `TRUST_PURPOSES`) removed; `key_id()` added; `extract_app_tree` reports normalised paths (`./x` made every tracked file look dropped) |
| `deploy/sign_update_package.py` | `deploy/sign_update_package.py` | Whole tool: `genkey` (encrypted PKCS#8), `sign`, `verify`, `--passphrase-file` | Renames; prints the key id; refreshes a sibling `.sha256` after signing |
| `deploy/upgrade_feed.py` | `deploy/upgrade_feed.py` | HTTPS-only fetcher, redirect guard, Basic auth only to the feed host (unredirected), size+sha256-verified atomic download, GitHub-mirror rewrite, error hints | Renames; NO default feed URL; `api_pack` / `knowledge_feed` keys removed; 200 MB ceiling |
| `deploy/satom_builder_cli/release_trust.py` | `deploy/satom_cli/release_trust.py` | Whole module: anchor detection, root-only mirror, `fetch` through `upload-pack` as the owner, `verify-commit`, `newest_signed`, `resolve` (signed/pinned/held), `stage_deploy` | Renames only |
| `deploy/satom_builder_cli/cmd_release.py` | `deploy/satom_cli/cmd_release.py` | `DeploySource` gate, `_key_problem`, `_pubkey`, the `sign-release` commit-tree flow | Rewritten without SATOM's CLI framework (`Ctx`, `Result`); `sign-release` signs any repository's HEAD and **never pushes**; no primary/standby role |
| `deploy/self_update_runner.py` | `deploy/self_update_runner.py` | Helpers: `_read_request`, `_open_dir_nofollow`, `_write_nofollow`, `_app_ids`, `_link_below`, `_app_user_from_tree`, `_units_in`/`UNIT_FILES`/`NONROOT_UNITS`/`enforce_unit_user`/`refresh_units` + armed registry, `_load_update_package` (sibling loading), `runner_integrity_problem`, `adopt_tree`, `_untracked_set`/`_new_untracked`, `_chown_tree`, `_venv_tag`, `_make_wheelhouse`, `_pip_dry_run_ok`, `dependency_preflight`, `Status.begin/step`, `_reject`; skeleton of `package_change` | Status shape is contract section 9 (`status`: queued/running/done/failed/rolled-back, `from_version`/`to_version`); request validation (`validate_request`) is new and strict; kinds are `package`, `rollback`, `service` only (no git updates, pip, OS packages, HA, Postgres); SQLite backup through the backup API (new); venv and root-side snapshots per rollback point (new; SATOM restores from `pip freeze`); root-side refresh ALWAYS from the verified package (SATOM: only with a trust anchor); `/healthz` must report the new version; `preserve_local_commits` reduced to parking uncommitted edits (a node has no upstream); lock file; release keys from a verified package are added to the trust store |
| `deploy/install-runner.sh` | `deploy/install-runner.sh` | Root-owned copy, system interpreter picker, verifier self-test, drop-in `10-root-copy.conf`, ownership verification, converge hook | Renames; no `/usr/local/sbin` helper copies; creates the root-only rollback/state dirs |
| `deploy/install-cli.sh` | `deploy/install-cli.sh` | Root-owned copy, interpreter picker, shebang stamping, `/usr/bin` alias | Renames; shebang runs `python -I` |
| `deploy/satom-builder-cli-launcher` | `deploy/satom-cli-launcher` | Launcher | Renames |
| `deploy/upgrade-converge.sh` | `deploy/upgrade-converge.sh` | Install units an older runner did not know, drop-in, arm new path/timer units | Reduced: no console role, no scheduled-action seeding; creates the queue dirs |
| `deploy/tls-bootstrap.sh` | `deploy/tls-bootstrap.sh` | `ensure_pki` (internal CA, leaf SAN of every name, `"source":"imported"` protection, reuse when valid), `_cert_covers`, `write_vhost`, `import_cert` | Renames; PKI under `/etc/satom-builder/pki` (root-only); `listen ... ssl http2` instead of `http2 on;` (distro nginx predates it); IPv6 listeners only when the kernel has IPv6; `_cert_covers` checks the IP too; no `--resolver` (no container path); body limit 200M |
| `deploy/satom-builder.service`, `satom-builder-updater.{path,service}` | `deploy/satom.service`, `deploy/satom-updater.{path,service}` | Unit shapes, DirectoryNotEmpty queue, oneshot 1800 s | Template declares the service account (SATOM: `User=root` + drop-in); runner unit never points into the app tree |
| `deploy/satom-builder-installer.sudoers` | `deploy/satom-installer.sudoers` | Installer-account rule | Renames; `--dry-run` instead of `--authorize-peer` |
| `deploy/nginx-vhost.conf` | `deploy/nginx-vhost.conf` | Sample vhost | Redirect inside `location /` with the ACME location (SATOM's sample used a server-level return) |
| `installers/install-satom-builder.sh` | `installers/install-satom.sh` | `ask`/`ask_secret`/`_ask_die` [LOUD-READ], `--print-sudoers`, distro layer (`PKG_MGR`, `REQUIRED_PKGS`, `pkg_*`), bundle detection per family, `bundle_select_debs`/`bundle_install_debs` [BUNDLE-DEBS], SUSE/RHEL offline installs, `bundle_upgrade_abi_libs` [ABI-OFFLINE], `preflight` and its `pf_*` probes, `pick_python`, `py_runtime_broken`, served names [SERVED-NAMES], service account, tree adoption [ADOPT-TREE], runtime sudoers + visudo, unit drop-in, CLI/runner install, trust store seeding, vhost with default_server self-correction, SELinux/firewall, guarded nginx restart + readiness [NGINX-START], health check | No Postgres, cluster, ACME client, metrics store or API packs; questions also via `SETUP_*` (`--answers` parsed, never sourced; `--yes`); `detect_os` from the guided setup; `.env` keys merged (never regenerated); `manage migrate`/`keygen-publisher`/`create-admin` (0600 password file)/`check`; vhost written only through `tls-bootstrap.sh`; the stock default site is disabled only when unmodified (and restored by `--uninstall`); `--dry-run`; `--uninstall [--purge]`. Fixes found by installing on bare images, NOT in SATOM's copy: offline `.deb`s go through bounded `dpkg -i` passes (one pass breaks on Pre-Depends) and never a fix-broken apt run (which removed packages offline); "installed" means dpkg status `ii` (an `rc` package was skipped); the egress probe is never a bare call under `set -e`; the SSH client is optional (SUSE FIPS conflict) |
| `installers/satom-builder-setup.sh` | `installers/satom-setup.sh` | Native path: argument handling, `ask`/`ask_choice`/`ask_yn`, `detect_os`, requirements, sibling installer / extracted bundle / bundle tarball + `.sha256`, `on_error` trap, summary | No Docker path; answers passed to the installer (no stdin answer stream); bundle name must match the OS |
| `installers/build-offline-bundle{,-rhel,-suse}.sh` | `installers/build-offline-bundle{,-rhel,-suse}.sh` | Closure download per family, Debian alternative-provider exclusion + closure list, RHEL `createrepo_c`, SUSE empty-root download, wheels, `.sha256` with the basename | Renames; `BUNDLE_OS`; wheels from the shipped `app.tar.gz`'s requirements; no lego / VictoriaMetrics; Debian builder installs its own build tools and reads os-release in a subshell (sourcing it overwrote `VERSION`) |
| `installers/build-update-package.sh` | `installers/build-update-package.sh` | `git archive HEAD` or `APP_TARBALL`+`COMMIT`, cross-platform `pip download`, `select_wheels`, manifest | Renames; several Python versions (`WHEEL_PYTHON_VERSIONS`); opt-in release gate (`RELEASE_TRUST_SIGNERS`); checks the tree carries the root-side files (listing written to a file: `tar -t | grep -q` fails under pipefail) |
| `tests/test_installer_*.py`, `test_tls_*.py`, `test_units_*.py`, `test_bundles_*.py`, `test_update_*.py`, `test_runner_*.py` | `tests/test_installer_{loud_read,exec_bits,nginx_start,container_findings}.py`, `test_tls_by_default.py`, `test_unit_distribution.py`, `test_offline_bundles.py`, `test_update_package.py`, `test_update_wheels_delta.py`, `test_upgrade_feed.py`, `test_migrations_idempotent.py` | The guards and their method (assert on artefacts; run extracted functions) | Ported to the Builder's files; new guards for the request validation, contract status shape, SQLite backup and release trust |
| `LICENSE` | `LICENSE` | Elastic License 2.0 text, verbatim | Header names SATOM Builder |
