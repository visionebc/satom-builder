"""Management CLI: ``python -m satom_builder.manage <command>`` (contract section 10).

Every command is idempotent.

  migrate                                   create / upgrade the SQLite schema
  create-admin --username U --password-file F
                                            create the admin, or reset its password
  keygen-publisher                          create the publisher key if missing; prints key_id
  check                                     config sanity; exit 0 when OK, 1 otherwise
  version                                   print the version
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as config_mod
from . import db


def _cfg() -> dict:
    return config_mod.load()


def cmd_migrate(args) -> int:
    cfg = _cfg()
    data = Path(cfg["SATOM_BUILDER_DATA"])
    data.mkdir(parents=True, exist_ok=True)
    before, after = db.migrate(db.path_in(data))
    print(f"schema version {before} -> {after}" if before != after
          else f"schema version {after} (up to date)")
    return 0


def cmd_create_admin(args) -> int:
    from werkzeug.security import generate_password_hash
    username = (args.username or "").strip()
    if not username or len(username) > 80:
        print("error: --username is required (at most 80 characters)", file=sys.stderr)
        return 1
    try:
        password = Path(args.password_file).read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        print(f"error: cannot read the password file: {exc.strerror}", file=sys.stderr)
        return 1
    if len(password) < 12:
        print("error: the password must be at least 12 characters", file=sys.stderr)
        return 1
    cfg = _cfg()
    path = db.path_in(cfg["SATOM_BUILDER_DATA"])
    db.migrate(path)
    with db.session(path) as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        h = generate_password_hash(password)
        if row:
            conn.execute("UPDATE users SET password_hash = ?, role = 'admin', source = 'local', "
                         "enabled = 1 WHERE id = ?", (h, row["id"]))
            action = "admin.password_reset"
            print(f"admin {username} exists: password reset, role admin, enabled")
        else:
            conn.execute("INSERT INTO users (username, password_hash, role, source, enabled, "
                         "created_at) VALUES (?, ?, 'admin', 'local', 1, ?)",
                         (username, h, db.now()))
            action = "admin.create"
            print(f"admin {username} created")
        conn.execute("INSERT INTO audit (at, username, action, object, detail, ip) "
                     "VALUES (?, 'manage', ?, ?, '{}', '')", (db.now(), action, username))
    return 0


def cmd_keygen_publisher(args) -> int:
    from .package import PackageError, keygen
    cfg = _cfg()
    try:
        kid, created = keygen(cfg["SATOM_BUILDER_PUBLISHER_KEY"])
    except (PackageError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(kid)
    if created:
        print(f"created {cfg['SATOM_BUILDER_PUBLISHER_KEY']}", file=sys.stderr)
    return 0


def cmd_check(args) -> int:
    cfg = _cfg()
    probs = config_mod.problems(cfg)
    if not probs:
        try:
            path = db.path_in(cfg["SATOM_BUILDER_DATA"])
            if path.exists():
                with db.session(path) as conn:
                    v = db.current_version(conn)
                if v < db.SCHEMA_VERSION:
                    probs.append(f"the database schema is at {v}; run migrate "
                                 f"(expected {db.SCHEMA_VERSION}).")
        except Exception as exc:  # noqa: BLE001
            probs.append(f"the database cannot be opened: {type(exc).__name__}")
    from .package import publisher_info
    pub = publisher_info(cfg["SATOM_BUILDER_PUBLISHER_KEY"])
    for p in probs:
        print(f"FAIL {p}")
    if pub["error"]:
        print(f"WARN {pub['error']}")
    elif not pub["present"]:
        print("WARN no publisher key yet (run keygen-publisher): packages are unsigned")
    if probs:
        return 1
    print("OK")
    return 0


def cmd_version(args) -> int:
    print(config_mod.version())
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m satom_builder.manage",
                                 description="SATOM Builder management commands.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create or upgrade the SQLite schema")
    p = sub.add_parser("create-admin", help="create the admin, or reset its password")
    p.add_argument("--username", required=True)
    p.add_argument("--password-file", required=True)
    sub.add_parser("keygen-publisher", help="create the publisher key if missing")
    sub.add_parser("check", help="config sanity (exit 0/1)")
    sub.add_parser("version", help="print the version")
    args = ap.parse_args(argv)
    return {"migrate": cmd_migrate, "create-admin": cmd_create_admin,
            "keygen-publisher": cmd_keygen_publisher, "check": cmd_check,
            "version": cmd_version}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
