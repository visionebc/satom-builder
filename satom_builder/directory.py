"""Directory sign-in settings (LDAP / Active Directory / RADIUS) and the glue
between the login form and the vendored engines in :mod:`directory_auth`.

Stored in ``settings['directory']``; the bind password and the RADIUS shared
secret are Fernet-encrypted and never rendered back.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import current_app

from . import db, directory_auth
from .secrets_store import decrypt, encrypt

MODES = ("off", "ldap", "ad", "radius")
DEFAULTS = {
    "mode": "off", "host": "", "port": "", "use_ssl": True, "start_tls": False,
    "tls_verify": True, "ca_pem": "", "base_dn": "", "bind_dn": "", "bind_password_enc": "",
    "user_attr": "", "user_filter": "", "ad_domain": "", "timeout": 8,
    "secret_enc": "", "nas_id": "satom-builder", "auto_create": True,
}


def load(conn) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(db.get_setting(conn, "directory", {}) or {})
    return cfg


def save(conn, cfg: dict) -> None:
    clean = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    db.set_setting(conn, "directory", clean)


def _ca_file(cfg: dict) -> str:
    pem = (cfg.get("ca_pem") or "").strip()
    if not pem:
        return ""
    p = Path(current_app.config["SATOM_BUILDER_DATA"]) / "directory-ca.pem"
    if not p.exists() or p.read_text(encoding="ascii", errors="replace") != pem + "\n":
        tmp = p.with_suffix(".tmp")
        tmp.write_text(pem + "\n", encoding="ascii")
        os.chmod(tmp, 0o640)
        os.replace(tmp, p)
    return str(p)


def engine_cfg(cfg: dict) -> dict:
    """The dict the vendored engines read (secrets decrypted, in memory only)."""
    out = {k: v for k, v in cfg.items() if not k.endswith("_enc") and k != "ca_pem"}
    out["kind"] = cfg["mode"]
    out["bind_password"] = decrypt(cfg.get("bind_password_enc") or "")
    out["secret"] = decrypt(cfg.get("secret_enc") or "")
    out["ca_file"] = _ca_file(cfg)
    if not out.get("port"):
        out.pop("port", None)
    return out


def authenticate(conn, username: str, password: str) -> tuple[bool, str, str]:
    """-> (ok, detail, source). Never raises."""
    cfg = load(conn)
    mode = cfg.get("mode", "off")
    if mode not in ("ldap", "ad", "radius"):
        return False, "Directory sign-in is off.", ""
    try:
        e = engine_cfg(cfg)
        if mode == "radius":
            ok, detail = directory_auth.radius_authenticate(e, username, password)
        else:
            ok, detail = directory_auth.ldap_authenticate(e, username, password)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}", mode
    return ok, detail, mode


def test(conn, username: str = "", password: str = "") -> tuple[bool, str]:
    cfg = load(conn)
    mode = cfg.get("mode", "off")
    if mode == "off":
        return False, "Directory sign-in is off: pick LDAP, Active Directory or RADIUS."
    e = engine_cfg(cfg)
    if mode == "radius":
        return directory_auth.radius_test(e, username, password)
    return directory_auth.ldap_test(e, username, password)


def set_secret_fields(cfg: dict, form) -> None:
    """Write-only secret fields: blank keeps the stored value, the 'clear'
    checkbox removes it."""
    for field, key in (("bind_password", "bind_password_enc"), ("secret", "secret_enc")):
        if form.get(f"clear_{field}"):
            cfg[key] = ""
        elif form.get(field):
            cfg[key] = encrypt(form.get(field))
