"""Configuration: ``/opt/satom-builder/.env`` (contract section 10) plus the environment.

Precedence, lowest first: built-in defaults, the ``.env`` file, the process
environment, then the ``overrides`` given to :func:`load` (tests).
The ``.env`` path itself can be moved with ``SATOM_BUILDER_ENV``.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = "/opt/satom-builder/.env"

DEFAULTS = {
    "SECRET_KEY": "",
    "FERNET_KEY": "",
    "SATOM_BUILDER_DATA": "/opt/satom-builder/data",
    "SATOM_BUILDER_PUBLISHER_KEY": "/etc/satom-builder/publisher/publisher_ed25519.key",
    "SATOM_BUILDER_BIND": "127.0.0.1:8100",
    # optional knobs (not required by the installer)
    "SATOM_BUILDER_COOKIE_SECURE": "1",
    "SATOM_BUILDER_SNAPSHOT_TTL": "300",
    "SATOM_BUILDER_RULES_TIMEOUT": "30",
    "SATOM_BUILDER_SATOM_TIMEOUT": "60",
    "SATOM_BUILDER_UPDATE_MAX_MB": "1024",
    "SATOM_BUILDER_AI_TIMEOUT": "900",
}

#: Keys whose values are secrets: never logged, never rendered, never passed
#: to the rules subprocess.
SECRET_KEYS = ("SECRET_KEY", "FERNET_KEY")


def read_env_file(path: str | os.PathLike) -> dict[str, str]:
    """A minimal ``KEY=value`` parser (comments, blank lines, optional quotes)."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, IsADirectoryError):
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def load(overrides: dict | None = None) -> dict:
    cfg = dict(DEFAULTS)
    env_file = (overrides or {}).get("SATOM_BUILDER_ENV") or os.environ.get(
        "SATOM_BUILDER_ENV", DEFAULT_ENV_FILE)
    cfg.update(read_env_file(env_file))
    for k in list(DEFAULTS) + ["SATOM_BUILDER_ENV"]:
        if k in os.environ:
            cfg[k] = os.environ[k]
    cfg.update(overrides or {})
    cfg["SATOM_BUILDER_ENV"] = env_file
    return cfg


def problems(cfg: dict) -> list[str]:
    """Config sanity for ``manage check``: an empty list means OK."""
    out = []
    if len(cfg.get("SECRET_KEY") or "") < 32:
        out.append("SECRET_KEY is missing or shorter than 32 characters.")
    try:
        from cryptography.fernet import Fernet
        Fernet((cfg.get("FERNET_KEY") or "").encode())
    except Exception:
        out.append("FERNET_KEY is missing or is not a valid Fernet key.")
    data = Path(cfg.get("SATOM_BUILDER_DATA") or "")
    if not str(data) or not data.is_dir():
        out.append(f"SATOM_BUILDER_DATA ({data}) is not a directory.")
    elif not os.access(data, os.W_OK):
        out.append(f"SATOM_BUILDER_DATA ({data}) is not writable by this user.")
    for k in ("SATOM_BUILDER_SNAPSHOT_TTL", "SATOM_BUILDER_RULES_TIMEOUT",
              "SATOM_BUILDER_SATOM_TIMEOUT", "SATOM_BUILDER_UPDATE_MAX_MB",
              "SATOM_BUILDER_AI_TIMEOUT"):
        try:
            if int(cfg.get(k)) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            out.append(f"{k} must be a positive integer.")
    bind = cfg.get("SATOM_BUILDER_BIND") or ""
    if ":" not in bind:
        out.append("SATOM_BUILDER_BIND must be host:port.")
    return out
