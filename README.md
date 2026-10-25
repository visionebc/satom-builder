# SATOM Builder

Customer-installed tool to author SATOM plugins and Plugin Studio apps (views, widgets, apps with forms, diagrams, data stores, connections, API Catalog versions, designs and sandboxed scripts), optionally with an AI model the user chooses. Every package is validated with the target SATOM's own rules before it can be imported.

- **Version:** see [`VERSION`](VERSION) · **License:** [Elastic License 2.0](LICENSE) · [Changelog](CHANGELOG.md)
- **Requires:** a SATOM server at **4.1.0 or later** (Builder API, rules pack, trusted publishers) and an API token of that SATOM for each author.

## What it guarantees

- **SATOM's rules, not a copy.** Builder downloads the rules pack of the exact SATOM it targets (pinned by sha256) and runs it locally; SATOM runs the same code again when the package is validated, rehearsed and imported.
- **Nothing that runs inside SATOM.** Builder authors `view`, `widget` and `app` packages only. Scripts get `inputs`, `result`, `log` and the connections they declare, with no network and no secrets.
- **No customer secrets in Builder.** Connections travel without credentials; SATOM asks for them on import and keeps them. Tests against real APIs run in SATOM, with SATOM's credentials.
- **Each author acts as themselves.** Builder talks to SATOM with the author's own SATOM token, so SATOM's roles, ADOMs and audit apply.
- **AI is optional and visible.** Two adapters (Anthropic, and any OpenAI-compatible server, local models included). Before a request is sent, Builder shows what leaves the server: never data rows, never secrets.

## Install

Download from the [latest release](https://github.com/visionebc/satom-builder/releases/latest):

| File | For |
|---|---|
| `satom-builder-offline-<version>-debian12-amd64.tar.gz` | Debian 12, no network needed |
| `satom-builder-offline-<version>-rhel9-x86_64.tar.gz` | RHEL / Rocky / Alma 9, no network needed |
| `satom-builder-offline-<version>-suse15-x86_64.tar.gz` | SUSE Linux Enterprise / openSUSE Leap 15 |
| `satom-builder-update-<version>.tar.gz` | signed update package for an installed node |

```bash
sha256sum -c satom-builder-offline-<version>-debian12-amd64.tar.gz.sha256
tar xzf satom-builder-offline-<version>-debian12-amd64.tar.gz
cd satom-builder-installer
sudo bash install-satom-builder.sh --preflight   # checks only, changes nothing
sudo bash satom-builder-setup.sh
```

Install it on its **own** server or VM, not on the SATOM server. The installer asks everything first and only then installs; `--answers FILE --yes` runs it unattended. Online install from a checkout: `sudo bash installers/satom-builder-setup.sh`. Full manual: [docs/INSTALL.md](docs/INSTALL.md).

## Update

Updates are signed packages (Ed25519) applied by a root runner with rollback:

```bash
sudo satom-builder update fetch          # dry run: what the latest release would download
sudo satom-builder update fetch --yes    # download it (size and sha256 checked)
sudo satom-builder update apply /path/printed/by/fetch   # signature checked, then applied
```

or upload `satom-builder-update-<version>.tar.gz` on the **Updates** page (administrators). Details: [docs/offline-update-packages.md](docs/offline-update-packages.md).

## Documentation

- [User guide](docs/user-guide.md) — projects, the editor, AI generation, tokens, publisher key, administration
- [Editor reference](docs/editor.md)
- [Installation manual](docs/INSTALL.md)
- [Update packages](docs/offline-update-packages.md)
- [SATOM ⇄ Builder API contract](docs/contract-satom-api.md)

## License

Source-available under the [Elastic License 2.0](LICENSE). You may use, modify and run SATOM Builder inside your own organisation, including in production and for commercial purposes. You may not provide it to third parties as a hosted or managed service; for that, contact **licensing@visionebc.com**. See [NOTICE](NOTICE) for code shared with SATOM and third-party components.
