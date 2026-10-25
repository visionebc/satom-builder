# Offline update packages

A SATOM Builder update is a **signed package**: application code plus the
Python wheels a node may be missing. It applies with no network, from the
Updates page or the operator CLI, through a root runner that verifies it first
and rolls back on failure. The rules are SATOM's, renamed; nothing (key,
schema, trust store, path) is shared with SATOM.

## 1. The short version

```bash
# build (any machine with a checkout; it holds no secret)
bash installers/build-update-package.sh
#   -> dist/satom-builder-update-<version>.tar.gz (+ .sha256)

# sign (the machine where the release key lives -- never a customer node)
python3 deploy/sign_update_package.py sign dist/satom-builder-update-<version>.tar.gz \
    --key satom-builder-release.key --passphrase-file satom-builder-release.passphrase
#   (signing rewrites the tarball and refreshes its .sha256)

# apply (on the node)
#   web : Updates page -> upload the package
#   cli : sudo satom-builder update apply satom-builder-update-<version>.tar.gz
```

### 1.1 The upgrade feed

A node can download the newest package itself, but only when an operator asks
(an on-premise product does not phone home). The built-in feed is the
`satom-builder-update-latest.json` asset of the newest GitHub release:

```bash
sudo satom-builder update fetch          # dry run: what would be downloaded
sudo satom-builder update fetch --yes    # download it; then: satom-builder update apply <path>
# an internal mirror instead:
sudo satom-builder update fetch --feed https://downloads.example.com/satom-builder-update-latest.json
```

The feed (schema `satom-builder.upgrade-feed/1`) is an address book: HTTPS only
(redirects included), the download lands under its name only when its size and
sha256 match the feed, and the package is then applied exactly like an
uploaded one -- **the signature is still checked** by the runner. A forged feed
can at most point at a package no trusted key signed, and that package is
refused. `SATOM_BUILDER_UPGRADE_FEED` replaces the built-in feed (a proxy
that cannot reach github.com, or an internal mirror).

## 2. Why it is signed, and what that buys

### The private key never touches a customer node

Building and signing are separate steps. The build host holds no secret; the
signer runs where the encrypted key lives. The key is an Ed25519 key stored as
**encrypted PKCS#8**; unattended signing reads the passphrase from a 0600 file
(`--passphrase-file`), never from the command line.

### The public key is public, and that is not a weakness

The release key's public half ships in the code (`deploy/update-keys/`) and the
installer copies it into the trust store. A public key can only verify.

| Release key | |
|---|---|
| File | `deploy/update-keys/satom-builder-release-99e9f047.pub` |
| Key id (sha256 of the raw key) | `99e9f047ec320b5799bad156842cb1bdcf357a8e1a2c3df5f1c0b8dac423ab91` |
| Fingerprint | `SHA256:menwR+wyC1eZutFWhCyxvc81eo4aLD318cC42sQjq5E` |

### The trust store is root-owned and outside the application tree

`/etc/satom-builder/update-keys/` -- and every directory above it -- must be
owned by root and not group/world writable. The runner checks the whole parent
chain **before** the signature: a trust store the web worker could write would
let it add its own key and sign its own package.

## 3. What is in a package

```text
satom-builder-update-<version>/
    manifest.json   the signed document
    manifest.sig    base64 Ed25519 signature over manifest.json's exact bytes
    app.tar.gz      `git archive HEAD` of the release (never a working tree)
    wheels/*.whl    cp311 wheels for manylinux_2_28 / manylinux2014 x86_64
```

`manifest.json` (schema `satom-builder.update-package/1`, product
`satom-builder`) carries `version`, `commit`, `built_at`, `python_tags`,
`min_from_version`, `requirements`, the sha256 and size of **every other
file**, and `wheelset` (the full resolved wheel set), `wheels_mode`
(`full`/`delta`) and `wheels_baseline`. It deliberately names no host, path or
operator. Signing the manifest covers the whole package; an unlisted extra file
is a refusal.

**Wheel deltas.** With `WHEEL_BASELINE=<json>` (`{"<version>": ["name==ver",
...]}`, the `wheelset` of the published packages) the package ships only the
wheels not every baseline release already has, and `min_from_version` rises to
the oldest baseline release. A node older than that is refused by the version
rules before anything is touched; it applies an earlier full package first.

Online-only nodes on Python 3.12/3.13 (Ubuntu 24.04, Debian 13) need a package
built with `WHEEL_PYTHON_VERSIONS="3.11 3.12 3.13"`; a package whose wheels do
not match the node's venv is refused (`python_tags`).

## 4. Managing the trust store

```bash
ls -l /etc/satom-builder/update-keys/            # root:root, 0644 files, 0755 directory
sudo install -o root -g root -m 0644 my-fork.pub /etc/satom-builder/update-keys/
sudo satom-builder status                        # lists the fingerprints in use
```

Key files are one line, shaped like `authorized_keys`:
`satom-builder-ed25519 <base64 raw key> <comment>`. A SATOM key
(`satom-ed25519 ...`) is not accepted.

### Rotating a key

Ship the new public key in `deploy/update-keys/` of a release signed with the
**old** key: applying that verified package adds it to the trust store (the
runner only ever ADDS keys, and only from a verified package). Then sign later
releases with the new one. Remove the old `.pub` only
when no node needs a package it signed.

## 5. Preflight (nothing changes until all of it passes)

1. **Runner integrity** -- the runner's own code (`/usr/local/lib/satom-builder-runner`)
   is root-owned all the way up; otherwise every request is refused.
2. **Signature** -- trust store sanity, then Ed25519 over `manifest.json`.
3. **Hashes** -- every file against the signed manifest.
4. **Version rules** -- a package older than (or equal to) the installed
   version is refused unless the request explicitly allows a downgrade
   (`allow_downgrade`); `min_from_version` above this node's version is
   refused; `python_tags` must include the venv's interpreter; and a
   dependency preflight (`pip install --dry-run --no-index`) proves every
   requirement is installed or shipped.

## 6. Applying

The web app (unprivileged) and the CLI only enqueue
`data/update-requests/<id>.json`:

```json
{"id": "<uuid hex>", "kind": "package", "path": "data/update-uploads/<file>",
 "requested_by": "<user>", "at": "<iso>", "allow_downgrade": false}
```

`satom-builder-updater.path` (DirectoryNotEmpty) starts the root runner, which
re-validates the request (the id must equal the file name, the path must be a
plain file name under `data/update-uploads/`, booleans must be booleans,
unknown fields are refused) and writes `data/update-status/<id>.json`:

```json
{"id": "...", "status": "queued|running|done|failed|rolled-back",
 "steps": [{"name": "...", "ok": true, "detail": "...", "at": "..."}],
 "from_version": "0.1.0", "to_version": "0.2.0"}
```

After the preflight (steps 1-4 above):

5. **Database backup** -- every SQLite file in `data/` is copied with the
   sqlite3 backup API (a consistent snapshot even while the app writes; never
   a `cp` of a live file), as the service account, into a root-only rollback
   point in `/var/lib/satom-builder-rollback/<id>/`.
6. **pip freeze** -- recorded, plus a copy of the venv (so rollback needs no
   network) and of the root-side files (runner, CLI, units).
7. **Park local changes** -- uncommitted edits in the tree go to
   `refs/backup/pre-update-*`.
8. **Install the tree** -- `app.tar.gz` is laid over the tree (links and
   traversal refused), files the new revision dropped are removed, ownership
   goes back to the service account.
9. **pip --no-index** -- only from the package's wheels.
10. **`manage migrate`** -- fatal on failure.
11. **Restart + `/healthz`** -- 200 **and** the version it reports is the new
    one. Before the restart, the units, the CLI and the runner are refreshed
    from a root-only copy of the **verified** package (never from the tree the
    service account owns).
12. **Commit** -- the deployed revision is recorded in git, staging exactly
    the files the package wrote or removed.

### 6.1 Database changes

Migrations are idempotent and additive. A failure from step 8 on rolls back
the code tree, the venv and the root-side files -- **never the database**: data
written since the backup may matter, and only the operator can decide. The
backup stays in the rollback point.

## 7. Rollback and downgrades

```bash
sudo satom-builder rollback                 # dry run: says what it will do
sudo satom-builder rollback --yes           # code + venv back to before the last update
sudo satom-builder rollback --restore-db --yes   # ...and the database copy taken then
```

A rollback request (`"kind": "rollback"`, optional `"point"` and
`"restore_db"`) returns to the state recorded before the last applied update:
tree (git reset to the recorded revision), venv (the saved copy) and root-side
files; the database only with `--restore-db` (data written since is lost). It is
refused when the node's version is not the one that update installed. The
newest three rollback points are kept.

When an apply FAILED (it rolled its code and venv back by itself) or was
already rolled back, its point still holds the database copy taken before it:

```bash
sudo satom-builder rollback --point <id> --restore-db --yes
```

restores only that database (the code is already at the point's starting
version, which must be the version the node runs).

A **downgrade** with a package (an older signed package) needs
`allow_downgrade: true` (`satom-builder update apply --allow-downgrade`).

## 8. The runner privilege boundary

The runner runs as root, so it never runs code the service account can write:

- it is executed from `/usr/local/lib/satom-builder-runner` (root-owned) with a
  system interpreter, via the drop-in `10-root-copy.conf`;
  `deploy/install-runner.sh` installs that copy after a self-test of the
  signature verifier;
- the verifier is loaded from the runner's own directory, never from the app tree;
- requests and status files are read and written without following links;
- git runs as the service account, never as root in its repository;
- root-side files are refreshed only from a root-only copy of a verified package.

## 9. Release trust for `deploy/` changes

`deploy/` is what root runs on every node, so changes to it are attested with
an SSH ed25519 **release-trust key** (principal `satom-builder-release`):

| Release-trust key | |
|---|---|
| Public half | `deploy/release-trust/satom-builder-release.allowed_signers` |
| Fingerprint | `SHA256:eip9a/dZZZ3lK1Z75C/hw4cOYDAUT2PV7hdStF5JAtg` |

- **At build time**: `RELEASE_TRUST_SIGNERS=deploy/release-trust/satom-builder-release.allowed_signers
  bash installers/build-update-package.sh` refuses a HEAD whose `deploy/`
  differs from the newest commit signed by a release key.
- **To attest**: `sudo satom-builder sign-release --repo <checkout>` (dry run,
  then `--yes`) adds one signed, empty commit on top of HEAD with the
  root-only key, after showing the `deploy/` changes since the last signed
  release. It never pushes.
- **On a node (opt-in)**: a root-owned
  `/etc/satom-builder/release-trust.allowed_signers` (copy the file above)
  makes `satom-builder reinstall cli|runner` install only a `deploy/` a release
  key signed. Package applies are already gated by the package signature.

Signatures are checked in a root-only mirror
(`/var/lib/satom-builder-release/mirror.git`), never by running git as root in
a repository another account owns.

## 10. Building and signing

### Build

```bash
bash installers/build-update-package.sh
# from a payload that is not a checkout (both or neither):
APP_TARBALL=/path/app.tar.gz COMMIT=<full sha> bash installers/build-update-package.sh
# delta against published packages:
WHEEL_BASELINE=baseline.json bash installers/build-update-package.sh
```

### Create a signing key (once)

```bash
install -d -m 0700 ~/.satom-builder-release
( umask 077; openssl rand -base64 36 > ~/.satom-builder-release/satom-builder-release.passphrase )
python3 deploy/sign_update_package.py genkey --out ~/.satom-builder-release/satom-builder-release \
    --comment "SATOM Builder release key" \
    --passphrase-file ~/.satom-builder-release/satom-builder-release.passphrase
```

Publish the `.pub` as `deploy/update-keys/satom-builder-release-<keyid8>.pub`;
back the `.key` and the passphrase up offline, separately.

### Sign and verify

```bash
python3 deploy/sign_update_package.py sign dist/satom-builder-update-<v>.tar.gz \
    --key ~/.satom-builder-release/satom-builder-release.key \
    --passphrase-file ~/.satom-builder-release/satom-builder-release.passphrase
python3 deploy/sign_update_package.py verify dist/satom-builder-update-<v>.tar.gz \
    --pub deploy/update-keys/satom-builder-release-99e9f047.pub
```

Signing rewrites the tarball; a `.sha256` next to it is rewritten too.

## 11. Known limits

- Wheels are built for CPython 3.11 by default; see section 3 for 3.12/3.13 nodes.
- The upload ceiling is 200 MB (nginx `client_max_body_size`).
- A rollback point holds a full venv copy; the newest three are kept.
- The database is never rolled back automatically.
