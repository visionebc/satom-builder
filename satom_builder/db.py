"""SQLite storage: stdlib ``sqlite3``, WAL, foreign keys, migrations in code.

Every connection is CLOSED by whoever opened it. ``with conn:`` in sqlite3
only commits or rolls back; it does NOT close the connection (a known fd-leak
trap). Use :func:`session` (opens, commits/rolls back, closes) or, inside a
request, :func:`get` (one connection per request, closed on teardown).
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from pathlib import Path

DB_NAME = "builder.sqlite3"

# Each migration runs once, in order, inside one transaction; the
# schema_version row is bumped in the same transaction. Statements are
# idempotent (IF NOT EXISTS) so a half-applied migration from an older build
# can never wedge the upgrade either.
MIGRATIONS: list[list[str]] = [
    # 1 -- initial schema
    [
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT,
            role TEXT NOT NULL CHECK (role IN ('admin','author')),
            source TEXT NOT NULL DEFAULT 'local' CHECK (source IN ('local','ldap','ad','radius')),
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            last_login REAL)""",
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            base_url TEXT NOT NULL,
            ca_pem TEXT NOT NULL DEFAULT '',
            verify_tls INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS tokens (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            token_enc TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, target_id))""",
        """CREATE TABLE IF NOT EXISTS snapshots (
            target_id INTEGER NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fetched_at REAL NOT NULL,
            data TEXT NOT NULL,
            PRIMARY KEY (target_id, user_id))""",
        """CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('view','widget','app')),
            slug TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '1.0.0',
            icon TEXT NOT NULL DEFAULT 'bi-puzzle',
            description TEXT NOT NULL DEFAULT '',
            target_id INTEGER REFERENCES targets(id) ON DELETE SET NULL,
            draft TEXT NOT NULL DEFAULT '{}',
            last_check TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner_id)",
        """CREATE TABLE IF NOT EXISTS project_revisions (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            at REAL NOT NULL,
            by_user TEXT NOT NULL,
            data TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS revisions_project ON project_revisions(project_id)",
        """CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            mime TEXT NOT NULL,
            size INTEGER NOT NULL,
            data BLOB NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE (project_id, sha256))""",
        """CREATE TABLE IF NOT EXISTS ai_profiles (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            adapter TEXT NOT NULL CHECK (adapter IN ('anthropic','openai_compatible')),
            base_url TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL,
            key_enc TEXT NOT NULL DEFAULT '',
            max_tokens INTEGER NOT NULL DEFAULT 64000,
            effort TEXT NOT NULL DEFAULT 'high',
            fallbacks INTEGER NOT NULL DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running','done','failed')),
            rounds TEXT NOT NULL DEFAULT '[]',
            error TEXT NOT NULL DEFAULT '',
            result TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY,
            at REAL NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            object TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}',
            ip TEXT NOT NULL DEFAULT '')""",
        "CREATE INDEX IF NOT EXISTS audit_at ON audit(at)",
        """CREATE TABLE IF NOT EXISTS login_failures (
            id INTEGER PRIMARY KEY,
            key TEXT NOT NULL,
            at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS login_failures_key ON login_failures(key, at)",
    ],
]

SCHEMA_VERSION = len(MIGRATIONS)


def path_in(data_dir: str | Path) -> Path:
    return Path(data_dir) / DB_NAME


def connect(db_path: str | Path) -> sqlite3.Connection:
    """A configured connection. The CALLER must close it."""
    conn = sqlite3.connect(str(db_path), timeout=15, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN ... COMMIT (ROLLBACK on error). Does not close ``conn``."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


@contextlib.contextmanager
def session(db_path: str | Path):
    """Open, yield, and ALWAYS close a connection (autocommit mode)."""
    with contextlib.closing(connect(db_path)) as conn:
        yield conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0] or 0)


def migrate(db_path: str | Path) -> tuple[int, int]:
    """Bring the schema up to date. Idempotent. -> (from_version, to_version)."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with session(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        start = current_version(conn)
        for n, stmts in enumerate(MIGRATIONS, start=1):
            if n <= start:
                continue
            with transaction(conn):
                for s in stmts:
                    conn.execute(s)
                conn.execute("DELETE FROM schema_version")
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (n,))
        return start, current_version(conn)


# --------------------------------------------------------------------------
# Flask glue: one connection per request, closed on teardown
# --------------------------------------------------------------------------
def get():
    from flask import current_app, g
    conn = g.get("_db")
    if conn is None:
        conn = g._db = connect(current_app.config["DB_PATH"])
    return conn


def close_request_db(_exc=None):
    from flask import g
    conn = g.pop("_db", None)
    if conn is not None:
        conn.close()


def app_session():
    """A fresh, self-closing connection for code outside a request (threads)."""
    from flask import current_app
    return session(current_app.config["DB_PATH"])


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def now() -> float:
    return time.time()


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(text, default=None):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def get_setting(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return loads(row["value"], default) if row else default


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, dumps(value)))
