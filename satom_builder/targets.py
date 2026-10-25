"""SATOM targets, per-author tokens, the target snapshot cache and the rules
pack that matches a target.

The snapshot (contract section 2) is cached per (target, author) for
``SATOM_BUILDER_SNAPSHOT_TTL`` seconds: it is fetched with that author's
token, so it reflects that author's RBAC and ADOM.
"""
from __future__ import annotations

from flask import current_app

from . import db, rules
from .satom_client import SatomClient, SatomError
from .secrets_store import decrypt, encrypt

#: test hook: an httpx transport used by every client (a fake SATOM)
TRANSPORT_KEY = "SATOM_TRANSPORT"


def get_target(conn, tid):
    if tid is None:
        return None
    return conn.execute("SELECT * FROM targets WHERE id = ?", (tid,)).fetchone()


def list_targets(conn):
    return conn.execute("SELECT * FROM targets ORDER BY name").fetchall()


def token_for(conn, user_id: int, target_id: int) -> str:
    row = conn.execute("SELECT token_enc FROM tokens WHERE user_id = ? AND target_id = ?",
                       (user_id, target_id)).fetchone()
    return decrypt(row["token_enc"]) if row else ""


def has_token(conn, user_id: int, target_id: int) -> bool:
    return conn.execute("SELECT 1 FROM tokens WHERE user_id = ? AND target_id = ?",
                        (user_id, target_id)).fetchone() is not None


def set_token(conn, user_id: int, target_id: int, token: str) -> None:
    conn.execute("INSERT INTO tokens (user_id, target_id, token_enc, updated_at) "
                 "VALUES (?,?,?,?) ON CONFLICT(user_id, target_id) DO UPDATE SET "
                 "token_enc = excluded.token_enc, updated_at = excluded.updated_at",
                 (user_id, target_id, encrypt(token), db.now()))
    conn.execute("DELETE FROM snapshots WHERE user_id = ? AND target_id = ?",
                 (user_id, target_id))


def clear_token(conn, user_id: int, target_id: int) -> None:
    conn.execute("DELETE FROM tokens WHERE user_id = ? AND target_id = ?", (user_id, target_id))
    conn.execute("DELETE FROM snapshots WHERE user_id = ? AND target_id = ?",
                 (user_id, target_id))


def client(conn, target_row, user_id: int) -> SatomClient:
    cfg = current_app.config
    return SatomClient(target_row["base_url"], token_for(conn, user_id, target_row["id"]),
                       ca_pem=target_row["ca_pem"], verify_tls=bool(target_row["verify_tls"]),
                       timeout=float(cfg["SATOM_BUILDER_SATOM_TIMEOUT"]),
                       transport=cfg.get(TRANSPORT_KEY))


def forget_snapshots(conn, target_id: int) -> None:
    """Drop every author's cached snapshot of a target: after something that
    changes what the target answers (a publisher registered on it), a cached
    one would make the local check disagree with SATOM."""
    conn.execute("DELETE FROM snapshots WHERE target_id = ?", (target_id,))


def snapshot(conn, target_row, user_id: int, refresh: bool = False) -> dict:
    """The cached target snapshot, refetched when older than the TTL."""
    ttl = float(current_app.config["SATOM_BUILDER_SNAPSHOT_TTL"])
    if not refresh:
        row = conn.execute("SELECT fetched_at, data FROM snapshots WHERE target_id = ? "
                           "AND user_id = ?", (target_row["id"], user_id)).fetchone()
        if row and db.now() - row["fetched_at"] < ttl:
            snap = db.loads(row["data"])
            if isinstance(snap, dict):
                return snap
    with client(conn, target_row, user_id) as c:
        snap = c.target()
    conn.execute("INSERT INTO snapshots (target_id, user_id, fetched_at, data) VALUES "
                 "(?,?,?,?) ON CONFLICT(target_id, user_id) DO UPDATE SET "
                 "fetched_at = excluded.fetched_at, data = excluded.data",
                 (target_row["id"], user_id, db.now(), db.dumps(snap)))
    return snap


def rules_pack(conn, target_row, user_id: int, snap: dict) -> rules.RulesPack:
    """The verified rules pack for this target's version, downloaded once."""
    cfg = current_app.config
    data_dir = cfg["SATOM_BUILDER_DATA"]
    timeout = float(cfg["SATOM_BUILDER_RULES_TIMEOUT"])
    sha = str(snap.get("rules_sha256") or "").lower()
    version = str(snap.get("satom_version") or "")
    if not sha:
        raise rules.RulesError("The target snapshot names no rules pack (rules_sha256).")
    if not rules.have(data_dir, sha):
        with client(conn, target_row, user_id) as c:
            data, header_sha = c.rules_pack()
        if header_sha != sha:
            raise rules.RulesError(
                f"SATOM sent rules pack {header_sha[:12]} but its target snapshot names "
                f"{sha[:12]}: refused.")
        rules.store(data_dir, data, header_sha, version)
    return rules.load(data_dir, sha, version, timeout)


class NotReady(Exception):
    """A precondition for talking to SATOM is missing (target, token...)."""

    def __init__(self, message: str, code: str = "not_ready", status: int = 409):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def context_for_project(conn, project_row, user, refresh: bool = False):
    """-> (target_row, snapshot). Raises NotReady / SatomError."""
    if project_row["target_id"] is None:
        raise NotReady("Pick a SATOM target for this project first.", "no_target")
    t = get_target(conn, project_row["target_id"])
    if t is None:
        raise NotReady("The project's SATOM target no longer exists.", "no_target")
    if not has_token(conn, user.id, t["id"]):
        raise NotReady(f"You have no SATOM token for {t['name']} yet: add one under "
                       "My tokens.", "no_token")
    return t, snapshot(conn, t, user.id, refresh=refresh)


__all__ = ["SatomError", "NotReady", "client", "snapshot", "rules_pack",
           "context_for_project"]
