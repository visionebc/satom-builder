# SATOM Builder -- Installation manual

SATOM Builder is installed on its own server or VM, next to (never on) the
SATOM it builds plugins for. It talks to SATOM over the API only. The installer
follows the same rules as SATOM's: everything is asked first, a preflight lists
every blocker at once, it runs unattended from an answers file, it serves HTTPS
from the first boot, and re-running it keeps the data.

Updates are **not** done by re-running the installer: they are signed update
packages (see `docs/offline-update-packages.md`).

## 1. Requirements

### 1.1 Minimum hardware

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 1 core | 2 cores |
| RAM | 1 GB | 2 GB |
| Free disk on `/opt` | 1.5 GB | 5 GB (every update keeps a copy of the venv for rollback) |
| Free disk on `/var` | 1 GB | 2 GB (rollback points, logs) |

### 1.2 Software

| | Online install | Offline install (bundle) |
|---|---|---|
| Debian 12 | yes | yes (`debian12-amd64`) |
| Debian 13 | yes | no |
| Ubuntu 22.04 / 24.04 | yes | no |
| RHEL / Rocky / AlmaLinux 9 | yes | yes (`rhel9-x86_64`) |
| openSUSE Leap 15 / SLES 15 | yes | yes (`suse15-x86_64`) |

- **x86_64** and **systemd as PID 1** are required. Containers without systemd
  are not supported.
- **Python >= 3.10.** The installer picks `python3.13`, `python3.12`,
  `python3.11`, `python3.10` and only then `python3`; it never assumes
  `python3`. RHEL 9 gets `python3.11` (its `python3` is 3.9) and SUSE 15 gets
  `python311`.
- **No database server.** SATOM Builder keeps its own state in SQLite under
  `/opt/satom-builder/data/`.

### 1.3 Network / ports

| Port | Direction | Purpose |
|---|---|---|
| 443/tcp (configurable) | inbound | the web console (nginx, TLS) |
| 80/tcp | inbound | redirect to HTTPS and the ACME http-01 challenge |
| 127.0.0.1:8100 | loopback only | gunicorn (never published) |
| HTTPS to your SATOM | outbound | validate, rehearse, import (per-author token) |
| HTTPS to the AI provider you choose | outbound | only the prompt and the schema, never rows or secrets |
| HTTPS to PyPI and the distro mirrors | outbound, online install only | packages and wheels |

### 1.4 Packages that get installed (for prior approval)

| Family | Required | Optional (best effort) |
|---|---|---|
| Debian / Ubuntu | `python3 python3-venv python3-pip nginx openssl curl ca-certificates sudo git` | `openssh-client` |
| RHEL 9 | `python3.11 python3.11-pip nginx openssl curl ca-certificates sudo git` | `openssh-clients`, `policycoreutils-python-utils` |
| SUSE 15 | `python311 python311-pip nginx openssl curl ca-certificates sudo git` | `openssh` |

Only what is missing is installed. `git` records the installed tree so update
packages can roll back. The optional SSH package provides `ssh-keygen`, used
only by the opt-in release-trust check; every bundle ships it, but when it
cannot be installed (for example a SUSE image whose FIPS pattern pins another
openssh) the installer warns and carries on.

### 1.5 What the base image must already provide

`useradd`, `usermod`, `getent`, `install`, `runuser`, `tar`, `gzip`, `awk`,
`sed`, `grep`, `hostname`, `df`, `stat`, `find` (shadow, util-linux,
coreutils, tar). The preflight names any that are missing.

### 1.6 Checking beforehand without installing anything (`--preflight`)

```bash
sudo bash install-satom-builder.sh --preflight
```

It checks, without changing anything: root and write access to `/opt`,
`/etc`, `/etc/systemd/system`, `/var/log`, `/usr/local`; systemd as PID 1; the
OS and architecture; base utilities; `sudo`/`visudo`; Python; disk and RAM;
an existing installation; ports 80 and 8100; NTP; PyPI egress (online) or the
bundle's contents (offline). It lists **every** blocker at once and exits 1 if
there is any.

`sudo bash satom-builder-setup.sh --check` runs the guided setup's own checks
plus the same preflight.

## 2. Ways to install

Two scripts ship together in every release and every offline bundle:

- `satom-builder-setup.sh` -- the guided installer: checks the system, finds
  and verifies the offline bundle, then runs the installer.
- `install-satom-builder.sh` -- the installer itself: preflight, questions,
  installation.

Both accept `--answers FILE --yes` for unattended runs.

### 2.1 Online (with network)

From a release (the two scripts and the code) or a checkout of the
repository:

```bash
sudo bash installers/satom-builder-setup.sh
# or, without the guided wrapper:
sudo bash installers/install-satom-builder.sh
```

The code comes from, in this order: `SETUP_APP_TARBALL` (an `app.tar.gz`), the
checkout around the script (`git archive HEAD`), or `SETUP_GIT_URL` (asked;
there is no built-in address; the public repository is
`https://github.com/visionebc/satom-builder.git`).

### 2.2 Offline (no network)

Copy the bundle **and its `.sha256`** to the target, then:

```bash
sha256sum -c satom-builder-offline-0.1.6-debian12-amd64.tar.gz.sha256
tar xzf satom-builder-offline-0.1.6-debian12-amd64.tar.gz
cd satom-builder-installer
sudo bash satom-builder-setup.sh
```
A minimal RHEL or SUSE server may have no `tar`. Extract the bundle with Python instead —
`python3 -m tarfile -e satom-builder-offline-<version>-<os>.tar.gz .` — and run the setup from
the extracted folder: it installs `tar` from the bundle before anything else.


or let the guided installer verify and extract it:

```bash
sudo bash satom-builder-setup.sh --bundle satom-builder-offline-0.1.6-rhel9-x86_64.tar.gz
```

A bundle holds **one** family: `bundle/debs/` (Debian 12), `bundle/rpms/`
(RHEL 9) or `bundle/rpms-suse/` (SUSE 15), plus `bundle/wheels/`,
`bundle/app.tar.gz` and `bundle/BUNDLE_OS`. A bundle for another family, or
for another release of the same family, is a hard error before anything is
touched. To install a different OS, use the online install.

Debian: the bundle carries the full dependency closure (listed in
`bundle/debs-closure.txt`), but **base packages are never forced**: a package
already installed (status `ii`) at the bundle's version or newer is skipped,
and systemd, udev, PAM, libc and their libraries are never touched when any
version is installed -- the OS image owns those. The rest is installed in a few
`dpkg -i` passes (Pre-Depends need them); nothing is ever removed to "fix"
dependencies.

SUSE: zypper gets a repository directory of its own holding only the bundle;
nothing is registered on the system. RHEL: dnf reads `bundle/rpms` with
`--repofrompath` and every other repository disabled.

## 3. What the installer asks (in this order)

| # | Question | Variable | Default |
|---|---|---|---|
| 0 | Already installed: reinstall (data kept) or abort | `SETUP_EXISTING` | `abort` |
| 1 | IP address of this machine | `SETUP_IP` | detected |
| 2 | HTTPS port | `SETUP_PORT` | `443` |
| 3 | DNS name(s) the console is reached by | `SETUP_NAMES` | `hostname -f` |
| 4 | Repository URL (only when there is no bundle, tarball or checkout) | `SETUP_GIT_URL`, `SETUP_GIT_REF` | none, `main` |
| 5 | Administrator name | `SETUP_ADMIN_USER` | `admin` |
| 6 | Administrator password (12-1024 characters) | `SETUP_ADMIN_PASSWORD` | generated |
| 6b | On a re-run: set the admin password again? | `SETUP_RESET_ADMIN` | `n` |
| 7 | Certificate: `self` (internal CA) or `import` | `SETUP_CERT` (+ `SETUP_CERT_FILE`, `SETUP_KEY_FILE`, `SETUP_CHAIN_FILE`) | `self` |
| 8 | firewalld/ufw active: open the ports? | `SETUP_FIREWALL` | `y` |
| 9 | Continue? | `SETUP_CONFIRM` | `y` |

Other variables: `SETUP_SOURCE=online|offline`, `SETUP_APP_TARBALL`,
`SETUP_BUNDLE` (guided setup).

Rules that hold for every prompt:

- **No prompt fails silently.** If the input runs out (a pipe or here-doc
  shorter than the questions), the installer stops and names the prompt.
- With `--yes`, a missing answer that has no default stops the install and
  **names its variable** (for example `set SETUP_GIT_URL`).
- Yes/no answers accept `y`, `yes`, `1`, `true` (and `s`, `si`).
- The DNS names are asked because the vhost's `server_name` and the
  certificate's SAN are built from them; the short host name and the IP are
  added automatically.
- With `--yes` and no password, one is generated and saved to
  `/root/satom-builder-admin-password.txt` (0600). The password reaches the
  application through a 0600 file, never the command line, and never the logs.
- The logs, `/var/log/satom-builder-install.log` and
  `/var/log/satom-builder-setup.log` (0600), never contain a password, a token
  or a repository URL.

An answers file is plain `KEY=VALUE` lines. It is **parsed, never sourced**:
only `SETUP_*` and `SATOM_BUILDER_*` keys are accepted, and a value already in
the environment wins.

```bash
cat > answers.env <<'EOF'
SETUP_IP=192.0.2.10
SETUP_PORT=443
SETUP_NAMES="builder.example.com"
SETUP_ADMIN_USER=admin
SETUP_ADMIN_PASSWORD="a-long-password-of-yours"
SETUP_CERT=self
EOF
chmod 600 answers.env
sudo bash satom-builder-setup.sh --answers answers.env --yes
```

`--dry-run` asks (or reads) every answer, prints what would be done, and
changes nothing.

## 4. What goes where

The layout mirrors SATOM's decisions: the application directory **is** the code
tree (a git working tree with a baseline commit), and the node-local state
lives inside it in paths the shipped `.gitignore` excludes.

| Path | Owner / mode | What |
|---|---|---|
| `/opt/satom-builder/` | `satom-builder`, 0750 | the code tree (`satom_builder/`, `deploy/`, `installers/`, `VERSION`, `requirements.txt`); git baseline |
| `/opt/satom-builder/venv/` | `satom-builder` | Python virtual environment |
| `/opt/satom-builder/data/` | `satom-builder`, 0750 | SQLite database, `update-requests/`, `update-status/`, `update-uploads/` |
| `/opt/satom-builder/state/` | `satom-builder`, 0700 | node-local private state |
| `/opt/satom-builder/.env` | `root:satom-builder`, 0640 | `SECRET_KEY`, `FERNET_KEY`, `SATOM_BUILDER_DATA`, `SATOM_BUILDER_PUBLISHER_KEY`, `SATOM_BUILDER_BIND` -- the app only reads it; secrets are never regenerated on a re-run |
| `/etc/satom-builder/update-keys/` | `root`, 0755 | trust store for update packages |
| `/etc/satom-builder/publisher/publisher_ed25519.key` | `root:satom-builder`, 0640 | the installation's package-signing key (register its public half in SATOM -> Trusted publishers) |
| `/etc/satom-builder/pki/` | `root`, 0700 | internal CA and the node certificate (outside the app tree: the Builder has no certificate manager, so the service account never needs the key) |
| `/usr/local/lib/satom-builder-runner/` | `root`, 0755 | root-owned copy of the update runner and its verifier |
| `/usr/local/lib/satom-builder-cli/`, `/usr/local/sbin/satom-builder` | `root`, 0755 | root-owned operator CLI (`/usr/bin/satom-builder` links to it) |
| `/var/lib/satom-builder-rollback/` | `root`, 0700 | rollback points (venv copy, database backup, root-side files) |
| `/var/log/satom-builder/` | `satom-builder` | gunicorn access and error logs |

Units (templates in `deploy/`, copied verbatim):

- `satom-builder.service` -- gunicorn `satom_builder.wsgi:app` on
  `127.0.0.1:8100`, as `satom-builder` (pinned again by the drop-in
  `10-app-user.conf`, which survives every update's re-copy of the template);
- `satom-builder-updater.path` -- watches `data/update-requests/`;
- `satom-builder-updater.service` -- the root runner, oneshot, 1800 s, started
  from the root-owned copy (drop-in `10-root-copy.conf`).

The nginx vhost is `satom-builder.conf` in `sites-available/` (Debian/Ubuntu),
`vhosts.d/` (SUSE) or `conf.d/` (RHEL). It claims `default_server` only when no
other site on the host already does; the distro's own welcome site is disabled
only when it is the unmodified packaged one.

## 5. Permissions to request from the systems team

### Running the installer

Installing is root work (an account, packages, units, nginx). To avoid handing
over the root password, give a named account the right to run **one script at
one fixed, root-owned path**:

```bash
sudo bash install-satom-builder.sh --print-sudoers opsinstall > /etc/sudoers.d/satom-builder-installer
visudo -c
```

(`deploy/satom-builder-installer.sudoers` is the same rule as a file.) The
script must be root-owned and not writable by that account, or the rule is
equivalent to `NOPASSWD: ALL`. Remove the rule when the installation window
closes.

### What the application needs at runtime (`satom-builder`)

`/etc/sudoers.d/satom-builder` (validated with `visudo -cf` before it is
installed) allows exactly two commands: `nginx -t` and
`systemctl reload nginx`. No package manager, no generic `systemctl`: both are
root-equivalent. Everything else that needs root goes through the update
runner, which validates every request it reads.

### The operator CLI (`satom-builder`)

```text
sudo satom-builder status                     services, version, health, runner, trust store
sudo satom-builder update apply <package>     queue a signed update package and follow it
sudo satom-builder update status [ID]         the newest (or given) update job
sudo satom-builder update fetch [--feed URL]  download the newest package (default: the latest GitHub release)
sudo satom-builder rollback [--restore-db]    undo the last applied update
sudo satom-builder restart-all                restart the web service, reload nginx
sudo satom-builder reinstall cli|runner       reinstall the root-owned copies
sudo satom-builder sign-release               release trust (docs/offline-update-packages.md)
satom-builder version
```

It is a root-owned copy outside the app tree on purpose: a launcher that ran
code from the tree would let a compromised web worker rewrite what an operator
runs under sudo.

## 6. After installing

- Create the DNS name(s) you gave, pointing to the node's IP.
- Replace the internal certificate when you have a real one:

  ```bash
  sudo sh /opt/satom-builder/deploy/tls-bootstrap.sh import-cert \
      --pki /etc/satom-builder/pki --cert cert.pem --key key.pem [--chain chain.pem]
  sudo nginx -t && sudo systemctl reload nginx
  ```

  An imported certificate is marked `"source":"imported"` and no installer
  re-run ever reissues over it. (Or re-run the installer with `SETUP_CERT=import`.)
- Register the publisher key printed at the end of the install in SATOM ->
  Trusted publishers.
- Delete `/root/satom-builder-admin-password.txt` after the first login, if
  the password was generated.
- Day-one check: `sudo satom-builder status`.

### Re-running the installer

Re-running is idempotent: the code, `data/`, the `.env` secrets, the keys and
an imported certificate are kept. It re-applies packages, the venv's
requirements, the layout and permissions, units, the CLI and runner copies, the
vhost and the certificate (reissued only when a name was added or it is about
to expire).

A re-run never trusts what the service account can write. The units, the CLI,
the update runner, the release keys and the TLS helper are installed from the
release you run the installer FROM (keep the bundle or checkout), not from
`/opt/satom-builder/deploy`; the venv, `pip` and `manage` run as the service
account; `.env` is never executed as root. Run the installer from outside
`/opt/satom-builder` (it refuses otherwise). It stops, without changing
anything, when `.env` is not owned by root or when a path it writes in the
tree (`data/`, its queue directories, `state/`, `.env`, `data/.installed`, the
log directory) is a symbolic link: inspect it -- the service account may have
been compromised -- restore it (`.env` is `root:satom-builder 0640`) and re-run.

### Uninstall / revert

```bash
sudo bash install-satom-builder.sh --uninstall           # keeps data, keys, account
sudo bash install-satom-builder.sh --uninstall --purge   # deletes everything (asks for DELETE)
```

`--uninstall` stops and removes the units, the runtime sudoers rule, the vhost
and the root-owned CLI and runner copies. It keeps `/opt/satom-builder`
(database, `.env`), `/etc/satom-builder` (keys, certificates), the account and
the logs, so a later install reuses them. `--purge` also deletes those and the
service account. Firewall rules opened for the console are left in place.
Installed OS packages are never removed.

## 7. Support

- Install log: `/var/log/satom-builder-install.log`; setup log:
  `/var/log/satom-builder-setup.log`.
- Service: `journalctl -u satom-builder -n 100`; updates:
  `journalctl -u satom-builder-updater -n 100` and `sudo satom-builder update status`.
- Health: `curl -sk https://<name>/healthz` returns
  `{"status": "ok", "version": "..."}`.
