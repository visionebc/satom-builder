"""``satom-builder`` -- the operator CLI. Small on purpose.

    satom-builder status                      services, version, health, trust, last update
    satom-builder version
    satom-builder restart-all                 restart the web service, reload nginx (root)
    satom-builder update apply <pkg> [--allow-downgrade] [--no-wait] [--timeout S]
    satom-builder update status [ID] [--json]
    satom-builder update fetch [--feed URL] [--ca-file F] [--yes]
    satom-builder rollback [--point ID] [--restore-db] [--yes] [--no-wait]
    satom-builder sign-release [--repo PATH] [--yes]
    satom-builder reinstall cli|runner

Updates and rollbacks are ENQUEUED (data/update-requests/) and executed by the
privileged runner behind satom-builder-updater.path -- the same path the web
console uses, so there is exactly one implementation of an update.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(os.environ.get("SATOM_BUILDER_APP_DIR", "/opt/satom-builder"))
DATA = APP_DIR / "data"
REQ = DATA / "update-requests"
STA = DATA / "update-status"
UPLOADS = DATA / "update-uploads"
RUNNER_LIB = Path(os.environ.get("SATOM_BUILDER_RUNNER_LIB", "/usr/local/lib/satom-builder-runner"))
TRUST_DIR = Path(os.environ.get("SATOM_BUILDER_TRUST_DIR", "/etc/satom-builder/update-keys"))
HEALTH_URL = os.environ.get("SATOM_BUILDER_HEALTH_URL", "http://127.0.0.1:8100/healthz")
UNITS = ("satom-builder.service", "satom-builder-updater.path", "nginx.service")
PKG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.tar\.gz$")
POINT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_ROOT = 0, 1, 2, 3


def _is_root():
    return os.geteuid() == 0


def _need_root(what):
    if not _is_root():
        print("satom-builder %s: must run as root (sudo satom-builder %s)" % (what, what),
              file=sys.stderr)
        return True
    return False


def _app_user():
    try:
        return pwd.getpwuid(APP_DIR.stat().st_uid).pw_name
    except (KeyError, OSError):
        return "root"


def _version():
    try:
        return (APP_DIR / "VERSION").read_text().strip()
    except OSError:
        return "?"


def _load_root_module(filename, modname):
    """A module from the ROOT-OWNED runner copy only -- never from the app tree:
    this CLI runs as root."""
    path = RUNNER_LIB / filename
    if not path.is_file():
        raise RuntimeError("%s is not installed (run: satom-builder reinstall runner)" % path)
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _systemctl(*args, timeout=120):
    p = subprocess.run(["systemctl"] + list(args), capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _health(timeout=5):
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
            return getattr(r, "status", r.getcode()), body
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:200]


def _statuses():
    out = []
    try:
        for p in STA.glob("*.json"):
            try:
                d = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(d, dict):
                d["_mtime"] = p.stat().st_mtime
                out.append(d)
    except OSError:
        pass
    out.sort(key=lambda d: d.get("_mtime", 0), reverse=True)
    return out


# ---------------------------------------------------------------------------
# status / version
# ---------------------------------------------------------------------------
def cmd_version(args):
    print("SATOM Builder %s" % _version())
    return EXIT_OK


def cmd_status(args):
    worst = EXIT_OK
    print("SATOM Builder %s  (%s)" % (_version(), APP_DIR))
    for unit in UNITS:
        rc, state, _ = _systemctl("is-active", unit, timeout=30)
        print("  %-30s %s" % (unit, state or "unknown"))
        if unit != "satom-builder-updater.path" and state != "active":
            worst = EXIT_FAIL
    code, body = _health()
    print("  %-30s %s" % ("health", ("%s %s" % (code, body.strip()[:80])) if code else
                          "no answer (%s)" % body))
    if code != 200:
        worst = EXIT_FAIL
    try:
        up = _load_root_module("update_package.py", "sb_cli_update_package")
        problem = up.root_owned_problem(RUNNER_LIB, "*.py")
        print("  %-30s %s" % ("update runner", "hardened (%s)" % RUNNER_LIB if not problem
                              else "NOT hardened: %s" % problem))
        tproblem = up.trust_store_problem(str(TRUST_DIR))
        keys = up.load_trust_store(str(TRUST_DIR))
        print("  %-30s %s" % ("trust store", ("%d key(s): %s" % (len(keys), ", ".join(
            k["fingerprint"] for k in keys))) if not tproblem else "UNSAFE: %s" % tproblem))
        if problem or tproblem:
            worst = EXIT_FAIL
    except Exception as exc:  # noqa: BLE001
        print("  %-30s %s" % ("update runner", "MISSING: %s" % exc))
        worst = EXIT_FAIL
    anchor = Path("/etc/satom-builder/release-trust.allowed_signers")
    print("  %-30s %s" % ("release-trust anchor", "present (enforcement ON)"
                          if anchor.is_file() else "absent (enforcement OFF)"))
    last = _statuses()[:1]
    if last:
        d = last[0]
        print("  %-30s %s %s %s -> %s (%s)" % ("last update job", d.get("kind"), d.get("status"),
                                             d.get("from_version") or "?",
                                             d.get("to_version") or "?", d.get("id")))
    return worst


# ---------------------------------------------------------------------------
# restart-all
# ---------------------------------------------------------------------------
def cmd_restart_all(args):
    if _need_root("restart-all"):
        return EXIT_ROOT
    rc, _, err = _systemctl("restart", "satom-builder.service", timeout=180)
    print("restart satom-builder.service: %s" % ("ok" if rc == 0 else "FAILED %s" % err))
    t = subprocess.run(["nginx", "-t"], capture_output=True, text=True, timeout=60)
    if t.returncode != 0:
        print("nginx -t FAILED -- not reloading:\n%s" % (t.stderr or "")[-500:])
        return EXIT_FAIL
    rc2, _, err2 = _systemctl("reload", "nginx", timeout=60)
    print("reload nginx: %s" % ("ok" if rc2 == 0 else "FAILED %s" % err2))
    for _ in range(30):
        code, body = _health()
        if code == 200:
            print("health: 200 %s" % body.strip()[:80])
            return EXIT_OK if rc == 0 and rc2 == 0 else EXIT_FAIL
        time.sleep(2)
    print("health: no 200 within 60 s")
    return EXIT_FAIL


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------
def _chown_app(path):
    user = _app_user()
    if user == "root":
        return
    try:
        pw = pwd.getpwnam(user)
        os.chown(path, pw.pw_uid, pw.pw_gid)
    except (KeyError, OSError):
        pass


def enqueue(req):
    """Write ``data/update-requests/<id>.json`` atomically (temp + rename)."""
    REQ.mkdir(parents=True, exist_ok=True)
    tmp = REQ / (".%s.tmp" % req["id"])
    with open(tmp, "x", encoding="utf-8") as fh:
        json.dump(req, fh, indent=2)
    os.chmod(tmp, 0o640)
    _chown_app(tmp)
    final = REQ / ("%s.json" % req["id"])
    os.rename(tmp, final)
    return final


def new_request(kind, **fields):
    req = {"id": uuid.uuid4().hex, "kind": kind,
           "requested_by": "cli:%s" % (os.environ.get("SUDO_USER") or "root"),
           "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    req.update(fields)
    return req


def _print_status(d, seen=0):
    steps = d.get("steps") or []
    for s in steps[seen:]:
        print("  %s %-44s %s" % ("ok  " if s.get("ok") else "FAIL", s.get("name", "")[:44],
                                 (s.get("detail") or "")[:160]))
    return len(steps)


def wait_for(uid, timeout):
    path = STA / ("%s.json" % uid)
    deadline = time.time() + timeout
    seen = 0
    shown_current = None
    while time.time() < deadline:
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            time.sleep(2)
            continue
        seen = _print_status(d, seen)
        cur = (d.get("current") or {}).get("name")
        if cur and cur != shown_current:
            print("  ...  %s" % cur)
            shown_current = cur
        if d.get("status") in ("done", "failed", "rolled-back"):
            print("result: %s (%s -> %s)%s" % (d["status"], d.get("from_version") or "?",
                                              d.get("to_version") or "?",
                                              (" -- " + d["error"]) if d.get("error") else ""))
            return EXIT_OK if d["status"] == "done" else EXIT_FAIL
        time.sleep(2)
    print("still running after %d s; follow it with: satom-builder update status %s"
          % (timeout, uid))
    return EXIT_FAIL


def cmd_update_apply(args):
    if _need_root("update apply"):
        return EXIT_ROOT
    src = Path(args.package)
    if not src.is_file():
        print("no such file: %s" % src, file=sys.stderr)
        return EXIT_USAGE
    if not PKG_RE.match(src.name):
        print("%r is not an update package file name" % src.name, file=sys.stderr)
        return EXIT_USAGE
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dst = UPLOADS / src.name
    if src.resolve() != dst.resolve():
        tmp = UPLOADS / (".%s.part" % src.name)
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o640)
        _chown_app(tmp)
        os.replace(tmp, dst)
    req = new_request("package", path="data/update-uploads/%s" % src.name,
                      allow_downgrade=bool(args.allow_downgrade))
    enqueue(req)
    print("queued update %s (%s)" % (req["id"], src.name))
    if args.no_wait:
        print("follow it with: satom-builder update status %s" % req["id"])
        return EXIT_OK
    return wait_for(req["id"], args.timeout)


def cmd_update_status(args):
    items = _statuses()
    if args.id:
        items = [d for d in items if d.get("id") == args.id]
        if not items:
            print("no status for %s" % args.id, file=sys.stderr)
            return EXIT_FAIL
    if not items:
        print("no update jobs recorded yet")
        return EXIT_OK
    d = items[0]
    d.pop("_mtime", None)
    if args.json:
        print(json.dumps(d, indent=2, sort_keys=True))
        return EXIT_OK
    print("%s %s: %s  (%s -> %s)" % (d.get("kind"), d.get("id"), d.get("status"),
                                     d.get("from_version") or "?", d.get("to_version") or "?"))
    _print_status(d)
    if d.get("current"):
        print("  ...  %s (since %s)" % (d["current"].get("name"), d["current"].get("since")))
    if d.get("error"):
        print("error: %s" % d["error"])
    return EXIT_OK if d.get("status") in ("done", "running", "queued") else EXIT_FAIL


def cmd_update_fetch(args):
    if _need_root("update fetch"):
        return EXIT_ROOT
    try:
        feed_mod = _load_root_module("upgrade_feed.py", "sb_cli_upgrade_feed")
        up = _load_root_module("update_package.py", "sb_cli_update_package")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_FAIL
    if args.ca_file:
        feed_mod.CA_FILE_PROVIDER = lambda: args.ca_file
    try:
        doc = feed_mod.fetch_feed(args.feed or "")
    except feed_mod.FeedError as exc:
        print("feed: %s" % exc, file=sys.stderr)
        return EXIT_FAIL
    ver = str(doc.get("version") or "")
    cur = _version()
    pkg = doc["package"]
    print("feed %s: version %s (%s, %d bytes); this node runs %s"
          % (doc.get("_feed_url"), ver, pkg["name"], pkg["size"], cur))
    if cur != "?" and up.compare_versions(ver, cur) <= 0:
        print("up to date: nothing to download")
        return EXIT_OK
    if not args.yes:
        print("dry run: re-run with --yes to download it into %s (it is NOT applied)" % UPLOADS)
        return EXIT_USAGE
    path = feed_mod.download_package(doc, UPLOADS)
    os.chmod(path, 0o640)
    _chown_app(path)
    print("downloaded %s (size and sha256 match the feed)" % path)
    print("the SIGNATURE is checked when it is applied: satom-builder update apply %s" % path)
    return EXIT_OK


def cmd_rollback(args):
    if _need_root("rollback"):
        return EXIT_ROOT
    if args.point and not POINT_RE.match(args.point):
        print("invalid rollback point %r" % args.point, file=sys.stderr)
        return EXIT_USAGE
    if not args.yes:
        print("rollback (dry run): the code tree and the venv go back to the state "
              "recorded before the last applied update%s." % (
                  " AND the database is replaced by the copy taken then (data written "
                  "since is LOST)" if args.restore_db else "; the database is kept"))
        print("re-run with --yes to queue it")
        return EXIT_USAGE
    fields = {"restore_db": bool(args.restore_db)}
    if args.point:
        fields["point"] = args.point
    req = new_request("rollback", **fields)
    enqueue(req)
    print("queued rollback %s" % req["id"])
    if args.no_wait:
        return EXIT_OK
    return wait_for(req["id"], args.timeout)


# ---------------------------------------------------------------------------
# release trust
# ---------------------------------------------------------------------------
def cmd_sign_release(args):
    from . import cmd_release
    rc, lines = cmd_release.sign_release(Path(args.repo or APP_DIR), yes=args.yes)
    print("\n".join(lines))
    return rc


def cmd_reinstall(args):
    if _need_root("reinstall"):
        return EXIT_ROOT
    from . import cmd_release
    script = {"cli": "install-cli.sh", "runner": "install-runner.sh"}[args.what]
    with cmd_release.DeploySource(APP_DIR) as src:
        if src.refusal is not None:
            print("\n".join(src.refusal), file=sys.stderr)
            return EXIT_FAIL
        path = src.dir / script
        if not path.is_file():
            print("missing %s" % path, file=sys.stderr)
            return EXIT_FAIL
        env = src.env or dict(os.environ, SATOM_BUILDER_APP_DIR=str(APP_DIR))
        p = subprocess.run(["bash", str(path)], capture_output=True, text=True,
                           timeout=300, env=env)
        sys.stdout.write(p.stdout or "")
        sys.stderr.write(p.stderr or "")
        if src.note:
            print(src.note)
        return EXIT_OK if p.returncode == 0 else EXIT_FAIL


# ---------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(prog="satom-builder",
                                 description="SATOM Builder operator CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version", help="print the installed version").set_defaults(func=cmd_version)
    sub.add_parser("status", help="services, health, trust, last update").set_defaults(
        func=cmd_status)
    sub.add_parser("restart-all", help="restart the web service and reload nginx").set_defaults(
        func=cmd_restart_all)

    upd = sub.add_parser("update", help="offline update packages")
    us = upd.add_subparsers(dest="update_cmd", required=True)
    a = us.add_parser("apply", help="queue a signed update package")
    a.add_argument("package")
    a.add_argument("--allow-downgrade", action="store_true")
    a.add_argument("--no-wait", action="store_true")
    a.add_argument("--timeout", type=int, default=1800)
    a.set_defaults(func=cmd_update_apply)
    s = us.add_parser("status", help="the newest (or the given) update job")
    s.add_argument("id", nargs="?")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_update_status)
    f = us.add_parser("fetch", help="download the newest package an HTTPS feed names")
    f.add_argument("--feed", default="")
    f.add_argument("--ca-file", default="")
    f.add_argument("--yes", action="store_true")
    f.set_defaults(func=cmd_update_fetch)

    r = sub.add_parser("rollback", help="undo the last applied update")
    r.add_argument("--point", default="")
    r.add_argument("--restore-db", action="store_true")
    r.add_argument("--yes", action="store_true")
    r.add_argument("--no-wait", action="store_true")
    r.add_argument("--timeout", type=int, default=1800)
    r.set_defaults(func=cmd_rollback)

    sr = sub.add_parser("sign-release", help="sign HEAD with the release-trust key")
    sr.add_argument("--repo", default="")
    sr.add_argument("--yes", action="store_true")
    sr.set_defaults(func=cmd_sign_release)

    ri = sub.add_parser("reinstall", help="reinstall the root-owned CLI or runner copy")
    ri.add_argument("what", choices=("cli", "runner"))
    ri.set_defaults(func=cmd_reinstall)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
