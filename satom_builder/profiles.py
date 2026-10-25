"""AI provider profiles (admin): name, adapter, base URL, model, key (Fernet,
write-only), max_tokens, effort (Anthropic), refusal fallbacks, enabled."""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import current_app

from . import db
from .ai_adapters import ADAPTERS, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, EFFORTS, make_adapter
from .secrets_store import decrypt, encrypt


class ProfileError(ValueError):
    pass


def list_profiles(conn, enabled_only: bool = False):
    q = "SELECT * FROM ai_profiles"
    if enabled_only:
        q += " WHERE enabled = 1"
    return conn.execute(q + " ORDER BY name").fetchall()


def get(conn, pid):
    return conn.execute("SELECT * FROM ai_profiles WHERE id = ?", (pid,)).fetchone()


def as_dict(row, with_key: bool = False) -> dict:
    d = {k: row[k] for k in ("id", "name", "adapter", "base_url", "model", "max_tokens",
                             "effort", "enabled")}
    d["fallbacks"] = bool(row["fallbacks"])
    d["enabled"] = bool(row["enabled"])
    d["key_set"] = bool(row["key_enc"])
    if with_key:
        d["key"] = decrypt(row["key_enc"]) if row["key_enc"] else ""
    return d


def adapter_for(row):
    cfg = current_app.config
    return make_adapter(as_dict(row, with_key=True),
                        anthropic_factory=cfg.get("ANTHROPIC_FACTORY"),
                        transport=cfg.get("AI_TRANSPORT"),
                        timeout=float(cfg["SATOM_BUILDER_AI_TIMEOUT"]))


def clean_form(form, existing=None) -> dict:
    name = (form.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ProfileError("The name must be 1 to 80 characters.")
    adapter = form.get("adapter") or ""
    if adapter not in ADAPTERS:
        raise ProfileError("Pick an adapter.")
    base_url = (form.get("base_url") or "").strip().rstrip("/")
    if base_url:
        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise ProfileError("The base URL must be an http(s) URL.")
        if parts.username or parts.password:
            raise ProfileError("The base URL must not carry credentials.")
    if adapter == "openai_compatible" and not base_url:
        raise ProfileError("An OpenAI-compatible profile needs a base URL.")
    model = (form.get("model") or "").strip() or (DEFAULT_MODEL if adapter == "anthropic" else "")
    if not model or len(model) > 120:
        raise ProfileError("The model must be 1 to 120 characters.")
    try:
        max_tokens = int(form.get("max_tokens") or DEFAULT_MAX_TOKENS)
    except ValueError:
        raise ProfileError("max tokens must be a number.") from None
    if not 256 <= max_tokens <= 128000:
        raise ProfileError("max tokens must be between 256 and 128000.")
    effort = form.get("effort") or "high"
    if effort not in EFFORTS:
        raise ProfileError("Unknown effort.")
    out = {"name": name, "adapter": adapter, "base_url": base_url, "model": model,
           "max_tokens": max_tokens, "effort": effort,
           "fallbacks": 1 if form.get("fallbacks") else 0,
           "enabled": 1 if form.get("enabled") else 0}
    if form.get("clear_key"):
        out["key_enc"] = ""
    elif form.get("key"):
        out["key_enc"] = encrypt(form.get("key").strip())
    elif existing is None:
        out["key_enc"] = ""
    return out


def save(conn, values: dict, pid=None) -> int:
    t = db.now()
    if pid is None:
        cols = list(values) + ["created_at", "updated_at"]
        cur = conn.execute(f"INSERT INTO ai_profiles ({', '.join(cols)}) VALUES "
                           f"({', '.join('?' for _ in cols)})", list(values.values()) + [t, t])
        return cur.lastrowid
    sets = ", ".join(f"{k} = ?" for k in values)
    conn.execute(f"UPDATE ai_profiles SET {sets}, updated_at = ? WHERE id = ?",
                 list(values.values()) + [t, pid])
    return pid
