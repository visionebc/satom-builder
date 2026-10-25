#!/usr/bin/env python3
"""SATOM Builder privileged update runner (runs as ROOT).

Triggered by ``satom-builder-updater.path`` whenever
``data/update-requests/`` becomes non-empty. The web application (unprivileged)
and the operator CLI only ENQUEUE: they drop a request JSON there and read the
status JSON this runner writes to ``data/update-status/``. Everything that
decides whether an update is trustworthy happens HERE, as root, from a
root-owned copy of this file (``/usr/local/lib/satom-builder-runner``, put
there by ``deploy/install-runner.sh``).

Request kinds (contract section 9):

* ``package``  -- apply a signed offline update package from data/update-uploads;
* ``rollback`` -- return to the state a previous package apply recorded
                  (code tree + venv; the database only when the operator asks);
* ``service``  -- restart the web service (``restart``) or the web service and
                  nginx (``restart-all``).

The package apply, in order (each step only makes sense once the previous one
passed):

     1. hardened runner      its own code is root-owned, all the way up
     2. signature            Ed25519 over manifest.json, root-owned trust store
     3. hashes               every file the signed manifest lists
     4. version rules        downgrade needs allow_downgrade; min_from_version;
                             python tag; dependency preflight
     5. database backup      every SQLite file in data/, through the sqlite3
                             backup API (never a cp of a live file)
     6. pip freeze           plus a copy of the venv, so rollback works offline
     7. park local changes   commits and uncommitted edits -> refs/backup/*
     8. install tree         app.tar.gz over the tree, dropped files removed,
                             ownership handed back to the service account
     9. pip --no-index       only from the package's wheels
    10. manage migrate       FATAL on failure
    11. restart + /healthz   200, and the version it reports is the new one
    12. commit               the deployed revision is recorded in git

Any failure from step 8 on rolls the tree, the venv and the root-side files
back. The DATABASE is never restored automatically: migrations are idempotent
and additive, and only the operator can judge whether data written since the
backup may be thrown away (``satom-builder rollback --restore-db``).

Most helpers below are vendored from SATOM ``deploy/self_update_runner.py``
(see deploy/VENDORED.md). STANDARD LIBRARY ONLY, and never an import of the
application: the runner must work when the venv is broken.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

APP = Path(os.environ.get("SATOM_BUILDER_APP_DIR", "/opt/satom-builder"))
DATA = APP / "data"
REQ = DATA / "update-requests"
STA = DATA / "update-status"
UPLOADS = DATA / "update-uploads"
VENV = APP / "venv" / "bin"
SERVICE = "satom-builder.service"
UPDATER_UNIT = "satom-builder-updater.service"
SYSTEMD_DIR = Path(os.environ.get("SATOM_BUILDER_SYSTEMD_DIR", "/etc/systemd/system"))
TRUST_DIR = os.environ.get("SATOM_BUILDER_TRUST_DIR", "/etc/satom-builder/update-keys")
RUNNER_LIB = Path("/usr/local/lib/satom-builder-runner")
CLI_LIB = Path("/usr/local/lib/satom-builder-cli")
CLI_BIN = Path("/usr/local/sbin/satom-builder")
RB_DIR = Path(os.environ.get("SATOM_BUILDER_ROLLBACK_DIR", "/var/lib/satom-builder-rollback"))
STATE_DIR = Path(os.environ.get("SATOM_BUILDER_RUNNER_STATE", "/var/lib/satom-builder-runner"))
ARMED_REGISTRY = STATE_DIR / "armed-units.json"
LOCK_FILE = Path(os.environ.get("SATOM_BUILDER_RUNNER_LOCK", "/run/satom-builder-runner.lock"))
HEALTH_URL = os.environ.get("SATOM_BUILDER_HEALTH_URL", "http://127.0.0.1:8100/healthz")
HEALTH_TIMEOUT = int(os.environ.get("SATOM_BUILDER_HEALTH_TIMEOUT", "90"))
#: Rollback points kept (each holds a venv copy, so this bounds disk use).
RB_KEEP = int(os.environ.get("SATOM_BUILDER_ROLLBACK_KEEP", "3"))

KINDS = ("package", "rollback", "service")
SERVICE_ACTIONS = ("restart", "restart-all")

# [RUNNER-INPUT] The queue is written by the service account, so every request
# value that reaches a file name, a path or a command line is re-checked here,
# as root. Copied, never imported from the application.
UID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,200}$")
_POINT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_PRINTABLE_RE = re.compile(r"^[\x20-\x7e]*$")
#: Package file names, same rule as update_package.PKG_NAME_RE (re-checked
#: again with the verifier's own copy once it is loaded).
_PKG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.tar\.gz$")
MAX_REQUEST_BYTES = 64 * 1024
_REQUEST_KEYS = {"id", "kind", "path", "requested_by", "at", "allow_downgrade",
                 "point", "restore_db", "action"}

GIT_IDENTITY = ("-c", "user.name=SATOM Builder", "-c", "user.email=satom-builder@localhost")
DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


class RequestError(ValueError):
    """A request the runner refuses to act on."""


class Refused(RuntimeError):
    """Refused BEFORE anything on the node was changed."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg):
    """One line to the journal (stderr of satom-builder-updater.service)."""
    print("satom-builder-updater: %s" % msg, file=sys.stderr, flush=True)


def _app_user_from_tree():
    """Owner of the app tree = service account (vendored). An env var gets
    forgotten on a new node; the directory's owner does not."""
    try:
        import pwd
        return pwd.getpwuid(APP.stat().st_uid).pw_name
    except Exception:
        return "root"


APP_USER = os.environ.get("SATOM_BUILDER_APP_USER") or _app_user_from_tree()


def _app_ids():
    """(uid, gid) of the service account when running as root, else None."""
    if not APP_USER or APP_USER == "root" or os.geteuid() != 0:
        return None
    try:
        import pwd
        pw = pwd.getpwnam(APP_USER)
        return pw.pw_uid, pw.pw_gid
    except KeyError:
        return None


def run(cmd, timeout=600, user=None, cwd=None, env=None, input_=None):
    if user and user != "root" and os.geteuid() == 0:
        cmd = ["runuser", "-u", user, "--"] + list(cmd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd, env=env, input=input_)


def _git_env():
    """git runs as the SERVICE ACCOUNT, in its own repository, with no system
    config and the account's own HOME: root never executes the account's git
    configuration (hooks, fsmonitor, filters)."""
    return {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": str(APP),
            "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def git(*a, timeout=600):
    return run(["git", "-C", str(APP), "-c", "safe.directory=%s" % APP] + list(a),
               timeout=timeout, user=APP_USER, cwd="/", env=_git_env())


def app_env():
    """Environment for ``python -m satom_builder.manage``: .env plus a minimal
    base. The file is root:<account> 0640; it is parsed, never sourced."""
    env = {"PATH": "%s:/usr/sbin:/usr/bin:/sbin:/bin" % VENV, "HOME": str(APP),
           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    try:
        text = (APP / ".env").read_text(encoding="utf-8")
    except OSError:
        return env
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_LINE_RE.match(line)
        if not m:
            continue
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        env[m.group(1)] = val
    return env


def manage(*args, timeout=600):
    return run([str(VENV / "python"), "-m", "satom_builder.manage"] + list(args),
               timeout=timeout, user=APP_USER, cwd=str(APP), env=app_env())


def _current_version():
    try:
        return (APP / "VERSION").read_text().strip()
    except OSError:
        return ""


def _read_request(req_path):
    """The request JSON, read without following a link planted in the queue
    and with a size ceiling (vendored, size limit added)."""
    fd = os.open(str(req_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise RequestError("%s is not a regular file" % req_path)
        if st.st_size > MAX_REQUEST_BYTES:
            raise RequestError("request is %d bytes, over the %d-byte limit"
                               % (st.st_size, MAX_REQUEST_BYTES))
        raw = fh.read(MAX_REQUEST_BYTES + 1)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RequestError("request is not valid JSON: %s" % exc)


def _open_dir_nofollow(dirpath):
    """O_DIRECTORY fd of ``dirpath``, never through a symlink below APP
    (vendored). Each component below APP is opened relative to its parent with
    O_NOFOLLOW (created when missing)."""
    dirpath = Path(dirpath)
    try:
        rel = dirpath.relative_to(APP)
        fd = os.open(str(APP), os.O_RDONLY | os.O_DIRECTORY)
    except ValueError:
        dirpath.parent.mkdir(parents=True, exist_ok=True)
        rel = Path(dirpath.name)
        fd = os.open(str(dirpath.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel.parts:
            if part in (".", ".."):
                raise ValueError("refusing a '%s' path component" % part)
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                nfd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, 0o750, dir_fd=fd)
                nfd = os.open(part, flags, dir_fd=fd)
                ids = _app_ids()
                if ids:
                    os.fchown(nfd, ids[0], ids[1])
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_nofollow(dirpath, name, text):
    """Write ``dirpath/name`` as root, safely (vendored): the directory is
    reached without links, the content goes to a fresh O_EXCL|O_NOFOLLOW temp
    file and an atomic rename replaces the NAME (a link there is replaced,
    never followed). The file is handed to the service account."""
    if not isinstance(name, str) or not _FILE_NAME_RE.fullmatch(name):
        raise ValueError("refusing to write a file named %r" % (name,))
    dfd = _open_dir_nofollow(dirpath)
    tmp = ".%s.%s.tmp" % (name, secrets.token_hex(6))
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o640, dir_fd=dfd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.fchmod(fh.fileno(), 0o640)
                ids = _app_ids()
                if ids:
                    os.fchown(fh.fileno(), ids[0], ids[1])
                fh.write(text)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def _link_below(base, rel):
    """True when ``base/rel`` cannot be used as root (vendored): an absolute or
    ``..`` path, or an existing component below ``base`` that is a symlink."""
    rel = Path(rel)
    if rel.is_absolute() or ".." in rel.parts:
        return True
    cur = Path(base)
    for part in rel.parts:
        cur = cur / part
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return False


def _svc_active(unit):
    try:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                           text=True, timeout=30)
        return (r.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def health_ok(timeout=HEALTH_TIMEOUT, expect_version=None):
    """(ok, detail): /healthz answers 200 with status ok -- and, when asked,
    reports ``expect_version``, which proves the NEW code is what serves."""
    deadline = time.time() + timeout
    last = "no answer"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=5) as r:
                code = getattr(r, "status", r.getcode())
                body = r.read(4096).decode("utf-8", "replace")
            if code == 200:
                try:
                    doc = json.loads(body)
                except ValueError:
                    doc = {}
                ver = str(doc.get("version") or "")
                if expect_version and ver != expect_version:
                    last = "200 but version %r (expected %s)" % (ver, expect_version)
                else:
                    return True, "200 OK, version %s" % (ver or "?")
            else:
                last = "HTTP %s" % code
        except Exception as exc:  # noqa: BLE001
            last = str(exc)[:200]
        time.sleep(3)
    return False, last


# ---------------------------------------------------------------------------
# status file -- contract section 9
# ---------------------------------------------------------------------------
class Status:
    """``data/update-status/<id>.json``::

        {"id", "status": "queued|running|done|failed|rolled-back",
         "steps": [{"name", "ok", "detail", "at"}], "from_version", "to_version"}

    plus informative extras (kind, requested_by, current step, error, times).
    """

    def __init__(self, uid, req, kind):
        if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
            raise ValueError("refusing request id %r" % (uid,))
        self.name = uid + ".json"
        req = req if isinstance(req, dict) else {}
        by = req.get("requested_by")
        self.d = {
            "id": uid, "status": "running", "kind": kind, "steps": [],
            "from_version": _current_version(), "to_version": "",
            "requested_by": by if isinstance(by, str) and len(by) <= 120 else "",
            "started_at": now(), "updated_at": now(), "error": "",
        }
        self.flush()

    def begin(self, name):
        """Announce the step that is RUNNING now (vendored): the slow ones take
        minutes and an operator must be able to tell working from hung."""
        self.d["current"] = {"name": name, "since": now()}
        self.d["updated_at"] = now()
        self.flush()

    def step(self, name, ok=True, detail="", warn=False):
        self.d.pop("current", None)
        rec = {"name": name, "ok": bool(ok),
               "detail": (detail or "").strip()[-500:], "at": now()}
        if warn:
            rec["warn"] = True
        self.d["steps"].append(rec)
        self.d["updated_at"] = now()
        self.flush()

    def finish(self, status, **extra):
        assert status in ("done", "failed", "rolled-back"), status
        self.d.pop("current", None)
        self.d["status"] = status
        self.d.update(extra)
        self.d["finished_at"] = self.d["updated_at"] = now()
        self.flush()

    def flush(self):
        _write_nofollow(STA, self.name, json.dumps(self.d, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------
def validate_request(raw, stem):
    """The request, re-validated as root. Raises RequestError.

    ``id`` must equal the file's own name: a request cannot write (or
    overwrite) the status of another one.
    """
    if not isinstance(raw, dict):
        raise RequestError("a request must be a JSON object")
    unknown = sorted(set(raw) - _REQUEST_KEYS)
    if unknown:
        raise RequestError("unknown request field(s): %s" % ", ".join(unknown[:5]))
    uid = raw.get("id")
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
        raise RequestError("invalid request id %r" % (uid,))
    if uid != stem:
        raise RequestError("request id %r does not match its file name %r" % (uid, stem))
    kind = raw.get("kind")
    if kind not in KINDS:
        raise RequestError("unknown request kind %r" % (kind,))
    by = raw.get("requested_by", "")
    if not isinstance(by, str) or len(by) > 120 or not _PRINTABLE_RE.match(by):
        raise RequestError("requested_by must be printable text of at most 120 characters")
    at = raw.get("at", "")
    if not isinstance(at, str) or len(at) > 40 or not _PRINTABLE_RE.match(at):
        raise RequestError("at must be a short timestamp string")
    for flag in ("allow_downgrade", "restore_db"):
        if flag in raw and not isinstance(raw[flag], bool):
            raise RequestError("%s must be true or false, not %r" % (flag, raw[flag]))
    out = {"id": uid, "kind": kind, "requested_by": by, "at": at,
           "allow_downgrade": bool(raw.get("allow_downgrade", False)),
           "restore_db": bool(raw.get("restore_db", False))}
    if kind == "package":
        path = raw.get("path")
        if not isinstance(path, str):
            raise RequestError("a package request needs a path")
        prefix = "data/update-uploads/"
        if not path.startswith(prefix):
            raise RequestError("the package must be staged in %s" % prefix)
        name = path[len(prefix):]
        if not _PKG_NAME_RE.fullmatch(name) or "/" in name:
            raise RequestError("%r is not an acceptable package file name" % name)
        out["package"] = name
    elif "path" in raw and raw.get("path") not in ("", None):
        raise RequestError("a %s request takes no path" % kind)
    if kind == "rollback":
        point = raw.get("point", "")
        if point not in ("", None) and (not isinstance(point, str)
                                        or not _POINT_RE.fullmatch(point)):
            raise RequestError("invalid rollback point %r" % (point,))
        out["point"] = point or ""
    if kind == "service":
        action = raw.get("action", "restart")
        if action not in SERVICE_ACTIONS:
            raise RequestError("service action must be one of %s" % ", ".join(SERVICE_ACTIONS))
        out["action"] = action
    return out


# ---------------------------------------------------------------------------
# the runner's own trust (vendored)
# ---------------------------------------------------------------------------
def _load_sibling(filename, modname):
    """Load a module that sits NEXT TO THIS FILE -- never from APP/deploy: when
    the runner is properly installed both live in the root-owned copy."""
    import importlib.util
    here = Path(__file__).resolve().parent
    for path in (here / filename, here / "satom_builder_cli" / filename):
        if path.is_file():
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("%s is not installed next to the runner (%s)" % (filename, here))


def _load_update_package():
    return _load_sibling("update_package.py", "satom_builder_update_package")


def runner_integrity_problem():
    """Why this runner must not be trusted to make a trust decision, or None.

    Running as root out of a directory the service account can write means the
    account chooses the code root executes; signature verification performed
    by that code proves nothing. Every request kind is refused until
    ``install-runner.sh`` has installed the root-owned copy.
    """
    here = Path(__file__).resolve().parent
    try:
        up = _load_update_package()
    except Exception as exc:  # noqa: BLE001
        return "cannot load the signature verifier: %s" % exc
    problem = up.root_owned_problem(here, "*.py")
    if problem:
        return ("%s -- run 'bash %s/deploy/install-runner.sh' as root to install "
                "the hardened copy" % (problem, APP))
    return None


# ---------------------------------------------------------------------------
# git baseline (vendored)
# ---------------------------------------------------------------------------
def adopt_tree():
    """(ok, detail). An application tree that is not a git checkout becomes
    one with a single baseline commit of the release files (the shipped
    .gitignore keeps data/, venv/, .env and state/ out). A no-op on a checkout."""
    if (APP / ".git").exists():
        return True, "already a git checkout"
    for args in (("init", "-q"), ("add", "-A"),
                 GIT_IDENTITY + ("commit", "-q", "-m",
                                 "baseline: SATOM Builder %s as installed"
                                 % (_current_version() or "?"))):
        r = git(*args, timeout=300)
        if r.returncode != 0:
            return False, "git %s failed: %s" % (args[-1] if args[0] == "-c" else args[0],
                                                 (r.stderr or r.stdout or "")[-300:])
    n = len((git("ls-files", timeout=120).stdout or "").splitlines())
    return True, "baseline commit of %d tracked file(s)" % n


def _untracked_set():
    out = git("ls-files", "--others", "--exclude-standard", timeout=120).stdout or ""
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _new_untracked(before):
    """Untracked, non-ignored paths that appeared since ``before``: rollback
    removes only these, never the operator's own untracked files."""
    return sorted(_untracked_set() - before)


def preserve_local_commits(snapshot, st):
    """Park uncommitted tracked edits under ``refs/backup/`` before the tree is
    replaced (vendored, simplified: an installed node has no upstream, so the
    commit history itself is never at risk -- only edits not yet committed).

    Returns True when something was preserved, None when there was nothing to
    preserve, False when it was needed but FAILED or could not be evaluated
    (the caller aborts: a guard that cannot do its job aborts the operation).
    """
    s = git("status", "--porcelain", "--untracked-files=no", timeout=60)
    if s.returncode != 0:
        st.step("park local changes", False,
                "git status failed (rc=%d) -- cannot tell whether the tree is "
                "dirty: %s" % (s.returncode, (s.stderr or "")[:200]))
        return False
    if not (s.stdout or "").strip():
        st.step("park local changes", True, "nothing to park (tree clean at %s)" % snapshot[:12])
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    c = git("stash", "create", "pre-update %s" % stamp, timeout=120)
    sha = (c.stdout or "").strip()
    if c.returncode != 0 or not sha:
        st.step("park local changes", False, "git stash create failed: %s"
                % (c.stderr or "")[:200])
        return False
    ref = "refs/backup/pre-update-%s" % stamp
    u = git("update-ref", ref, sha, timeout=60)
    st.step("park local changes", u.returncode == 0,
            ("%s -> %s (recover: git stash apply %s)" % (ref, sha[:12], ref))
            if u.returncode == 0 else (u.stderr or "update-ref failed"))
    return u.returncode == 0


def _chown_tree():
    """Give the tree back to the service account (vendored). ``.env`` stays
    root:<account> 0640, set through an O_NOFOLLOW fd."""
    if not APP_USER or APP_USER == "root":
        return True, "running un-deprivileged; nothing to hand back"
    r = subprocess.run(["chown", "-R", "%s:%s" % (APP_USER, APP_USER), str(APP)],
                       capture_output=True, text=True, timeout=600)
    try:
        import grp
        fd = os.open(str(APP / ".env"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.fchown(fd, 0, grp.getgrnam(APP_USER).gr_gid)
                os.fchmod(fd, 0o640)
        finally:
            os.close(fd)
    except (OSError, KeyError):
        pass
    return r.returncode == 0, (r.stderr or "tree owned by %s" % APP_USER)[:200]


# ---------------------------------------------------------------------------
# dependencies (vendored)
# ---------------------------------------------------------------------------
PIP_DRY_RUN_SINCE = (22, 2)
_PIP_MISSING_RES = (
    re.compile(r"No matching distribution found for ([^\s;]+)"),
    re.compile(r"Could not find a version that satisfies the requirement ([^\s;]+)"),
)
_REQ_MEMBERS = ("requirements.txt", "./requirements.txt")


def _venv_tag():
    r = run([str(VENV / "python"), "-c",
             "import sys;print('cp%d%d' % sys.version_info[:2])"],
            timeout=30, user=APP_USER)
    return (r.stdout or "").strip()


def _make_wheelhouse(pkg_dir, up):
    """Root-owned, world-READABLE copy of the package's wheels plus the NEW
    requirements.txt read straight out of the verified app.tar.gz. pip runs as
    the service account, which cannot traverse the 0700 root staging dir."""
    house = Path(tempfile.mkdtemp(prefix="satom-builder-wheels-", dir="/var/tmp"))
    os.chmod(house, 0o755)
    wdir = house / "wheels"
    wdir.mkdir()
    os.chmod(wdir, 0o755)
    for w in sorted((Path(pkg_dir) / "wheels").glob("*.whl")):
        if not w.is_file() or w.is_symlink():
            continue
        shutil.copyfile(w, wdir / w.name)
        os.chmod(wdir / w.name, 0o644)
    req = None
    with tarfile.open(Path(pkg_dir) / "app.tar.gz", "r:gz") as tf:
        for name in _REQ_MEMBERS:
            try:
                m = tf.getmember(name)
            except KeyError:
                continue
            if m.isfile() and m.size < 1024 * 1024:
                req = tf.extractfile(m).read()
            break
    if req is None:
        raise up.PackageError("app.tar.gz has no requirements.txt")
    (house / "requirements.txt").write_bytes(req)
    os.chmod(house / "requirements.txt", 0o644)
    return house


def _pip_dry_run_ok():
    r = run([str(VENV / "python"), "-m", "pip", "--version"], timeout=60, user=APP_USER)
    m = re.search(r"pip (\d+)\.(\d+)", r.stdout or "")
    if not m:
        return False, "pip (version unknown)"
    return (int(m.group(1)), int(m.group(2))) >= PIP_DRY_RUN_SINCE, m.group(0)


def dependency_preflight(st, wheelhouse, manifest, cur):
    """Refuse, before anything changes, a package whose requirements this
    node's venv cannot meet from what is installed plus what is shipped."""
    st.begin("dependency preflight")
    shipped = len(list((Path(wheelhouse) / "wheels").glob("*.whl")))
    mode = manifest.get("wheels_mode") or "full"
    supported, pipv = _pip_dry_run_ok()
    if not supported:
        st.step("dependency preflight", True,
                "skipped: %s has no --dry-run (22.2+); %s package, %d wheel(s)"
                % (pipv, mode, shipped))
        return
    p = run([str(VENV / "pip"), "install", "--dry-run", "--quiet", "--no-index",
             "--find-links", str(Path(wheelhouse) / "wheels"),
             "-r", str(Path(wheelhouse) / "requirements.txt")],
            timeout=600, user=APP_USER, cwd="/")
    if p.returncode == 0:
        st.step("dependency preflight", True,
                "every requirement is installed or shipped (%s package, %d wheel(s))"
                % (mode, shipped))
        return
    out = (p.stderr or "") + "\n" + (p.stdout or "")
    missing = sorted({m.group(1) for rx in _PIP_MISSING_RES for m in rx.finditer(out)})
    msg = ("dependencies cannot be met offline: %s neither installed in this venv "
           "nor shipped in the package (%s, %d wheel(s)). Nothing was changed. "
           "Apply an earlier full update package or one built with a "
           "min_from_version at or below %s, then retry"
           % (", ".join(missing) or "(see pip output)", mode, shipped, cur))
    st.step("dependency preflight", False, msg if missing else (msg + " | " + out.strip()[-300:]))
    raise Refused(msg)


# ---------------------------------------------------------------------------
# SQLite backup -- through the backup API, as the service account
# ---------------------------------------------------------------------------
_SQLITE_BACKUP = r"""
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:%s?mode=ro" % src, uri=True, timeout=30)
d = sqlite3.connect(dst)
with d:
    s.backup(d)
ok = d.execute("PRAGMA integrity_check").fetchone()[0]
d.close(); s.close()
if ok != "ok":
    sys.exit("integrity_check on the copy: %s" % ok)
print("ok")
"""


def data_dir():
    d = app_env().get("SATOM_BUILDER_DATA") or str(DATA)
    p = Path(d)
    return p if p.is_absolute() else DATA


def find_databases():
    """Every SQLite file directly in data/ (regular files only, never a link)."""
    d = data_dir()
    out = []
    try:
        entries = sorted(os.scandir(d), key=lambda e: e.name)
    except OSError:
        return out
    for e in entries:
        if not e.name.endswith(DB_SUFFIXES) or not _FILE_NAME_RE.fullmatch(e.name):
            continue
        try:
            st_ = os.lstat(e.path)
        except OSError:
            continue
        if stat.S_ISREG(st_.st_mode):
            out.append(Path(e.path))
    return out


def backup_databases(point_dir, st):
    """Copy every SQLite database into ``point_dir/db/``.

    The copy is made with ``sqlite3.Connection.backup`` -- a consistent
    snapshot even while the app writes, which ``cp`` of a live file (plus its
    -wal) is not -- and runs AS THE SERVICE ACCOUNT, so root never parses a
    file that account controls and never creates a root-owned -shm next to
    the live database. Root then moves the result into the root-only point.
    """
    dbs = find_databases()
    st.begin("database backup")
    if not dbs:
        st.step("database backup", True, "no SQLite database in %s yet" % data_dir())
        return []
    work = Path(tempfile.mkdtemp(prefix="satom-builder-dbbak-", dir="/var/tmp"))
    ids = _app_ids()
    try:
        if ids:
            os.chown(work, ids[0], ids[1])
        os.chmod(work, 0o700)
        dest = Path(point_dir) / "db"
        dest.mkdir(mode=0o700, exist_ok=True)
        saved = []
        for db in dbs:
            tmp = work / (db.name + ".bak")
            r = run([sys.executable, "-I", "-c", _SQLITE_BACKUP, str(db), str(tmp)],
                    timeout=1200, user=APP_USER, cwd="/")
            if r.returncode != 0 or not tmp.is_file():
                raise Refused("database backup of %s failed: %s"
                              % (db.name, (r.stderr or r.stdout or "")[-300:]))
            final = dest / (db.name + ".bak")
            shutil.copyfile(tmp, final)
            os.chmod(final, 0o600)
            saved.append({"name": db.name, "backup": final.name,
                          "size": final.stat().st_size})
        st.step("database backup", True, "%d database(s) copied with the sqlite3 backup "
                "API: %s" % (len(saved), ", ".join(s["name"] for s in saved)))
        return saved
    finally:
        shutil.rmtree(work, ignore_errors=True)


def restore_databases(point_dir, saved, st):
    """Put the backed-up databases back (operator rollback with restore_db).
    The service is stopped by the caller."""
    d = data_dir()
    ids = _app_ids()
    for rec in saved:
        name = rec.get("name", "")
        if not _FILE_NAME_RE.fullmatch(name) or not name.endswith(DB_SUFFIXES):
            raise RuntimeError("refusing to restore a database named %r" % name)
        src = Path(point_dir) / "db" / (name + ".bak")
        target = d / name
        if _link_below(d, name):
            raise RuntimeError("refusing to restore over a symlink: %s" % target)
        for side in ("-wal", "-shm", "-journal"):
            p = Path(str(target) + side)
            if p.is_symlink() or p.exists():
                p.unlink()
        tmp = d / (".%s.restore.%s" % (name, secrets.token_hex(4)))
        shutil.copyfile(src, tmp)
        if ids:
            os.chown(tmp, ids[0], ids[1])
        os.chmod(tmp, 0o640)
        os.replace(tmp, target)
    st.step("restore database", True, "%d database(s) restored from the backup taken "
            "before the update" % len(saved))


# ---------------------------------------------------------------------------
# rollback points (root-only, outside the app tree)
# ---------------------------------------------------------------------------
ROOT_SIDE_PATHS = (RUNNER_LIB, CLI_LIB, CLI_BIN)


def _rb_root():
    RB_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RB_DIR, 0o700)
    return RB_DIR


def _rb_point_dir(point_id):
    if not _POINT_RE.fullmatch(point_id or ""):
        raise RuntimeError("invalid rollback point id %r" % (point_id,))
    return _rb_root() / point_id


def _rb_save_manifest(d, man):
    tmp = Path(d) / ".point.json.tmp"
    tmp.write_text(json.dumps(man, indent=2, sort_keys=True))
    os.chmod(tmp, 0o600)
    os.replace(tmp, Path(d) / "point.json")


def _rb_manifest(d):
    return json.loads((Path(d) / "point.json").read_text())


def rollback_points():
    """[point manifest] newest first."""
    out = []
    if not RB_DIR.is_dir():
        return out
    for d in RB_DIR.iterdir():
        if d.is_dir() and _POINT_RE.fullmatch(d.name) and (d / "point.json").is_file():
            try:
                man = _rb_manifest(d)
            except (OSError, ValueError):
                continue
            man["_dir"] = str(d)
            out.append(man)
    out.sort(key=lambda m: m.get("created_at", ""), reverse=True)
    return out


def _unit_files_installed():
    out = []
    for pat in ("satom-builder*.service", "satom-builder*.path", "satom-builder*.timer"):
        out.extend(sorted(SYSTEMD_DIR.glob(pat)))
    return out


def snapshot_root_side(point_dir):
    """tar of the root-owned copies (runner, CLI, units + drop-ins) as they are
    BEFORE the update, so a rollback never has to trust the app tree to put
    root-side files back."""
    out = Path(point_dir) / "root-side.tar"
    with tarfile.open(out, "w") as tf:
        for p in list(ROOT_SIDE_PATHS) + _unit_files_installed():
            if p.exists() or p.is_symlink():
                tf.add(str(p), arcname=str(p).lstrip("/"))
        for p in _unit_files_installed():
            dd = Path(str(p) + ".d")
            if dd.is_dir():
                tf.add(str(dd), arcname=str(dd).lstrip("/"))
    os.chmod(out, 0o600)
    return out


def restore_root_side(point_dir, st):
    tar = Path(point_dir) / "root-side.tar"
    if not tar.is_file():
        st.step("restore root-side files", False, "no root-side snapshot in the point")
        return False
    for p in (RUNNER_LIB, CLI_LIB):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
    r = subprocess.run(["tar", "-x", "-p", "-f", str(tar), "-C", "/"],
                       capture_output=True, text=True, timeout=300)
    subprocess.run(["systemctl", "daemon-reload"], timeout=120)
    st.step("restore root-side files", r.returncode == 0,
            (r.stderr or "runner, CLI and units as before the update").strip()[-300:])
    return r.returncode == 0


def snapshot_venv(point_dir, st):
    st.begin("snapshot venv")
    src = APP / "venv"
    if not src.is_dir() or src.is_symlink():
        raise Refused("%s is not a directory; refusing to update without a way back" % src)
    r = subprocess.run(["cp", "-a", str(src), str(Path(point_dir) / "venv")],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise Refused("could not copy the venv for rollback: %s" % (r.stderr or "")[-300:])
    st.step("snapshot venv", True, "venv copied to the rollback point")


def restore_venv(point_dir, st):
    saved = Path(point_dir) / "venv"
    if not saved.is_dir():
        st.step("restore venv", False, "no venv copy in the rollback point")
        return False
    live = APP / "venv"
    if live.is_symlink():
        live.unlink()
    elif live.exists():
        shutil.rmtree(live)
    r = subprocess.run(["cp", "-a", str(saved), str(live)], capture_output=True,
                       text=True, timeout=1800)
    if r.returncode == 0 and APP_USER and APP_USER != "root":
        subprocess.run(["chown", "-R", "%s:%s" % (APP_USER, APP_USER), str(live)],
                       timeout=600)
    st.step("restore venv", r.returncode == 0, (r.stderr or "venv as before the update")[-300:])
    return r.returncode == 0


def prune_points(keep=RB_KEEP):
    for man in rollback_points()[keep:]:
        shutil.rmtree(man["_dir"], ignore_errors=True)


# ---------------------------------------------------------------------------
# units (vendored: the list is DERIVED from deploy/, never hand-written)
# ---------------------------------------------------------------------------
UNIT_DROPIN = """# Generated by SATOM Builder (installer / update runner). Do NOT edit by hand.
# Lives in a drop-in rather than in the unit because every update re-copies the
# deploy/ templates: the drop-in survives that.
[Service]
User=%s
Group=%s
"""


def _units_in(deploy_dir):
    names = set()
    for pat in ("*.service", "*.timer", "*.path"):
        try:
            names.update(p.name for p in Path(deploy_dir).glob(pat))
        except OSError:
            pass
    return tuple(sorted(names))


UNIT_FILES = _units_in(APP / "deploy")


def _nonroot_units(names):
    """Every .service except the privileged runner itself."""
    return tuple(sorted({u for u in names if u.endswith(".service")} - {UPDATER_UNIT}))


NONROOT_UNITS = _nonroot_units(UNIT_FILES)


def enforce_unit_user(user, names=None):
    if not user or user == "root":
        return
    for unit in (NONROOT_UNITS if names is None else _nonroot_units(names)):
        p = SYSTEMD_DIR / unit
        if not p.exists():
            continue
        d = Path(str(p) + ".d")
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / "10-app-user.conf").write_text(UNIT_DROPIN % (user, user))
        except OSError:
            pass


def _armed_registry():
    try:
        data = json.loads(ARMED_REGISTRY.read_text())
        return set(data) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def _remember_armed(units):
    if not units:
        return
    try:
        ARMED_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(ARMED_REGISTRY.parent), 0o700)
        ARMED_REGISTRY.write_text(json.dumps(sorted(_armed_registry() | set(units))))
    except OSError:
        pass


def refresh_units(deploy_dir, st=None):
    """Copy every unit template of ``deploy_dir``, pin the service account,
    reload systemd and ARM (once) a .path/.timer that arrived for the first
    time. An already installed unit keeps the enabled state the operator gave it."""
    names = _units_in(deploy_dir)
    fresh = []
    for unit in names:
        src = Path(deploy_dir) / unit
        dst = SYSTEMD_DIR / unit
        is_new = not dst.exists()
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o644)
        if is_new and unit.endswith((".path", ".timer")):
            fresh.append(unit)
    enforce_unit_user(APP_USER, names)
    subprocess.run(["systemctl", "daemon-reload"], timeout=120)
    armed = []
    for unit in fresh:
        p = subprocess.run(["systemctl", "enable", "--now", unit],
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            armed.append(unit)
        if st is not None:
            st.step("arm new unit %s" % unit, p.returncode == 0,
                    (p.stderr or p.stdout or "enabled").strip()[-300:])
    _remember_armed(armed)
    if st is not None:
        st.step("refresh units", True, "%d unit template(s) from the verified package"
                % len(names))
    return names


def seed_trust_store(stage_deploy, st):
    """Install the release keys a VERIFIED package ships (key rotation: the new
    public key travels in a release signed with the old one). Keys are only
    ever ADDED; removing a key is an operator act."""
    up = _load_update_package()
    added = []
    for k in sorted(Path(stage_deploy, "update-keys").glob("*.pub")):
        if not _FILE_NAME_RE.fullmatch(k.name) or k.is_symlink() or not k.is_file():
            continue
        try:
            up.parse_public_key(k.read_text())
        except Exception:  # noqa: BLE001 -- a malformed key is skipped, never installed
            continue
        dst = Path(TRUST_DIR) / k.name
        if dst.exists() and dst.read_bytes() == k.read_bytes():
            continue
        shutil.copyfile(k, dst)
        os.chmod(dst, 0o644)
        added.append(k.name)
    st.step("trust store", True, ("added %s" % ", ".join(added)) if added
            else "release keys already present")


def refresh_root_side(stage_deploy, st):
    """Units, the operator CLI and this runner, from a ROOT-ONLY copy of the
    already verified package -- never from the service-account-owned tree that
    was just overwritten (a post-verification edit cannot change what root runs)."""
    refresh_units(stage_deploy, st)
    seed_trust_store(stage_deploy, st)
    env = dict(os.environ, SATOM_BUILDER_APP_DIR=str(APP),
               SATOM_BUILDER_DEPLOY_SRC=str(stage_deploy))
    for script, label in (("install-cli.sh", "operator CLI"),
                          ("install-runner.sh", "update runner")):
        sp = Path(stage_deploy) / script
        if not sp.exists():
            st.step("refresh %s" % label, False, "%s is missing from the package" % script)
            raise RuntimeError("the package has no deploy/%s" % script)
        st.begin("refresh %s" % label)
        cr = subprocess.run(["bash", str(sp)], capture_output=True, text=True,
                            timeout=300, env=env)
        st.step("refresh %s" % label, cr.returncode == 0,
                (cr.stdout or cr.stderr or "").strip()[-300:])
        if cr.returncode != 0:
            raise RuntimeError("deploy/%s failed: %s" % (script, (cr.stderr or "")[-300:]))


def restart_service(st, expect_version):
    st.begin("restart service")
    r = subprocess.run(["systemctl", "restart", SERVICE], capture_output=True,
                       text=True, timeout=180)
    st.step("restart service", r.returncode == 0, (r.stderr or SERVICE).strip()[-300:])
    if r.returncode != 0:
        raise RuntimeError("systemctl restart %s failed" % SERVICE)
    st.begin("health check")
    ok, detail = health_ok(expect_version=expect_version)
    st.step("health check", ok, detail)
    if not ok:
        raise RuntimeError("health check failed: %s" % detail)


# ---------------------------------------------------------------------------
# kind: package
# ---------------------------------------------------------------------------
def package_change(req):
    uid = req["id"]
    st = Status(uid, req, "package")
    st.d["package"] = req["package"]
    cur = _current_version()
    stage = wheelhouse = stage_root = None
    snapshot = None
    point_dir = None
    untracked_before = set()
    touched = False
    root_touched = False
    try:
        # -- 1. hardened runner -------------------------------------------------
        problem = runner_integrity_problem()
        if problem:
            st.step("runner integrity", False, problem)
            raise Refused("refusing to verify a package from a runner the service "
                          "account can rewrite")
        st.step("runner integrity", True, "running root-owned from %s"
                % Path(__file__).resolve().parent)
        up = _load_update_package()

        name = req["package"]
        if not up.PKG_NAME_RE.match(name):
            raise Refused("%r is not an acceptable package name" % name)
        if _link_below(APP, Path("data") / "update-uploads" / name):
            raise Refused("the upload path contains a symbolic link")
        pkg_file = UPLOADS / name
        try:
            info = os.lstat(pkg_file)
        except OSError:
            raise Refused("%s is not staged on this node" % name)
        if not stat.S_ISREG(info.st_mode):
            raise Refused("%s is not a regular file" % name)
        st.step("locate package", True, "%s (%d bytes)" % (name, info.st_size))

        # Extract ONCE into root-only space: the upload dir is writable by the
        # service account, so anything verified in place could be swapped.
        stage = Path(tempfile.mkdtemp(prefix="satom-builder-pkg-", dir="/var/tmp"))
        os.chmod(stage, 0o700)
        st.begin("extract to root-only staging")
        pkg_dir = up.extract_package(pkg_file, stage)
        st.step("extract to root-only staging", True, str(stage))

        # -- 2. signature -------------------------------------------------------
        st.begin("verify signature")
        problem = up.trust_store_problem(TRUST_DIR)
        if problem:
            st.step("verify signature", False, "trust store is not safe to use: %s" % problem)
            raise Refused("trust store is not safe to use: %s" % problem)
        key = up.verify_signature(pkg_dir, TRUST_DIR)
        manifest = up.read_manifest(pkg_dir)
        if manifest.get("schema") != up.SCHEMA:
            raise Refused("unsupported package schema %r (this node speaks %s)"
                          % (manifest.get("schema"), up.SCHEMA))
        if manifest.get("product") != up.PRODUCT:
            raise Refused("package is for product %r, not %s"
                          % (manifest.get("product"), up.PRODUCT))
        st.step("verify signature", True, "signed by %s (%s)"
                % (key["fingerprint"], key["comment"] or key["name"]))

        # -- 3. hashes ----------------------------------------------------------
        problems = up.verify_contents(pkg_dir, manifest)
        if problems:
            st.step("verify file hashes", False, "; ".join(problems[:5]))
            raise Refused("package contents do not match the signed manifest: %s"
                          % "; ".join(problems[:5]))
        st.step("verify file hashes", True, "%d file(s) match the signed manifest"
                % len(manifest.get("files") or {}))

        # -- 4. version rules ---------------------------------------------------
        new = str(manifest.get("version") or "")
        if not up.VERSION_RE.match(new):
            raise Refused("the manifest carries no usable version (%r)" % new)
        st.d["to_version"] = new
        cmp_ = up.compare_versions(new, cur) if cur else 1
        if cmp_ < 0 and not req["allow_downgrade"]:
            raise Refused("package %s is OLDER than the installed %s and the request "
                          "did not allow a downgrade" % (new, cur))
        if cmp_ == 0 and not req["allow_downgrade"]:
            raise Refused("version %s is already installed (re-applying it needs "
                          "allow_downgrade)" % new)
        min_from = str(manifest.get("min_from_version") or "")
        if min_from and cur and up.compare_versions(cur, min_from) < 0:
            raise Refused("package requires %s or newer; this node runs %s. Apply an "
                          "earlier update package first" % (min_from, cur))
        tags = list(manifest.get("python_tags") or [])
        mine = _venv_tag()
        if tags and "*" not in tags and mine and mine not in tags:
            raise Refused("package carries wheels for %s; this venv is %s"
                          % (", ".join(tags), mine))
        st.step("version rules", True, "%s -> %s%s" % (cur or "?", new,
                " (DOWNGRADE, explicitly allowed)" if cmp_ < 0 else
                " (re-apply, explicitly allowed)" if cmp_ == 0 else ""))
        wheelhouse = _make_wheelhouse(pkg_dir, up)
        dependency_preflight(st, wheelhouse, manifest, cur)

        # -- 5. database backup (point created first; it is root-only) ----------
        ok_adopt, adopt_detail = adopt_tree()
        if adopt_detail != "already a git checkout" or not ok_adopt:
            st.step("adopt the installed tree into git", ok_adopt, adopt_detail)
        if not ok_adopt:
            raise Refused("the application tree is not a git checkout and could not "
                          "be adopted: %s" % adopt_detail)
        head = git("rev-parse", "HEAD")
        snapshot = (head.stdout or "").strip()
        if head.returncode != 0 or not _SHA_RE.fullmatch(snapshot):
            raise Refused("cannot read the current revision: %s" % (head.stderr or "")[-200:])
        point_dir = _rb_point_dir(uid)
        point_dir.mkdir(mode=0o700)
        point = {"id": uid, "created_at": now(), "state": "prepared",
                 "from_version": cur, "to_version": new, "snapshot": snapshot,
                 "package": name, "databases": []}
        _rb_save_manifest(point_dir, point)
        point["databases"] = backup_databases(point_dir, st)
        _rb_save_manifest(point_dir, point)

        # -- 6. pip freeze + venv copy -----------------------------------------
        st.begin("freeze current dependencies")
        fr = run([str(VENV / "pip"), "freeze"], timeout=180, user=APP_USER, cwd="/")
        if fr.returncode != 0:
            raise Refused("pip freeze failed: %s" % (fr.stderr or "")[-200:])
        (point_dir / "pip-freeze.txt").write_text(fr.stdout)
        st.step("freeze current dependencies", True, "%d requirement line(s) recorded"
                % len(fr.stdout.splitlines()))
        snapshot_venv(point_dir, st)
        snapshot_root_side(point_dir)

        # -- 7. park local changes ---------------------------------------------
        untracked_before = _untracked_set()
        if preserve_local_commits(snapshot, st) is False:
            raise Refused("refusing to replace the tree: local changes could not be parked")

        # -- 8. install tree + chown -------------------------------------------
        app_tar = pkg_dir / "app.tar.gz"
        tracked_now = {ln.strip() for ln in (git("ls-files", timeout=120).stdout or "").splitlines()
                       if ln.strip()}
        touched = True
        st.begin("install application tree")
        written = up.extract_app_tree(app_tar, APP)
        st.step("install application tree", True, "%d file(s) from %s" % (len(written), name))
        stale = sorted(tracked_now - set(written))
        if tracked_now and len(stale) > max(20, len(tracked_now) // 2):
            raise RuntimeError("refusing to remove %d of %d tracked files: the package's "
                               "app.tar.gz does not look like this product's tree"
                               % (len(stale), len(tracked_now)))
        for rel in stale:
            if _link_below(APP, Path(rel).parent):
                continue
            try:
                (APP / rel).unlink()
            except OSError:
                pass
        if stale:
            st.step("remove %d file(s) the new revision dropped" % len(stale), True,
                    ", ".join(stale[:8]))
        cok, cdetail = _chown_tree()
        st.step("restore tree ownership", cok, cdetail)

        # -- 9. pip --no-index -------------------------------------------------
        st.begin("pip install (offline, from the package)")
        p = run([str(VENV / "pip"), "install", "-q", "--no-index", "--find-links",
                 str(wheelhouse / "wheels"), "-r", str(APP / "requirements.txt")],
                timeout=1800, user=APP_USER, cwd="/")
        st.step("pip install (offline, from the package)", p.returncode == 0,
                (p.stderr or p.stdout or "")[-400:])
        if p.returncode != 0:
            raise RuntimeError("offline pip install failed")

        # -- 10. migrations (fatal) ------------------------------------------------
        st.begin("manage migrate")
        m = manage("migrate", timeout=900)
        st.step("manage migrate", m.returncode == 0, (m.stdout or m.stderr or "")[-300:])
        if m.returncode != 0:
            raise RuntimeError("manage migrate failed -- the code and the venv are rolled "
                               "back; the database is NOT (its backup is in rollback "
                               "point %s: 'satom-builder rollback --point %s "
                               "--restore-db')" % (uid, uid))

        # root-side files from a root-only copy of the VERIFIED package
        stage_root = Path(tempfile.mkdtemp(prefix="satom-builder-rel-", dir="/var/tmp"))
        os.chmod(stage_root, 0o700)
        up.extract_app_tree(app_tar, stage_root)
        root_touched = True
        refresh_root_side(stage_root / "deploy", st)

        # -- 11. restart + /healthz --------------------------------------------
        restart_service(st, new)

        # -- 12. record the deployed revision ---------------------------------
        paths = sorted(set(written) | set(stale))
        for i in range(0, len(paths), 200):
            git("add", "--", *paths[i:i + 200], timeout=120)
        c = git(*(GIT_IDENTITY + ("commit", "-q", "-m",
                                  "apply update package %s (%s) by %s"
                                  % (new, (manifest.get("commit") or "")[:12],
                                     req["requested_by"] or "?"))))
        st.step("record deployed revision", True,
                (c.stdout or c.stderr or "committed").strip()[-200:] or "committed")
        point["state"] = "applied"
        point["applied_at"] = now()
        _rb_save_manifest(point_dir, point)
        prune_points()
        st.finish("done", to_version=new, signed_by=key["fingerprint"],
                  rollback_point=uid, downgrade=bool(cmp_ < 0))
    except Exception as exc:  # noqa: BLE001
        st.step("ERROR", False, str(exc)[:400])
        if not touched:
            if point_dir is not None:
                shutil.rmtree(point_dir, ignore_errors=True)
            st.finish("failed", error=str(exc)[:400], rolled_back=False)
            return
        recovered = _rollback_after_failure(st, snapshot, point_dir, untracked_before,
                                            root_touched)
        if point_dir is not None:
            try:
                man = _rb_manifest(point_dir)
                man["state"] = "failed"
                _rb_save_manifest(point_dir, man)
            except (OSError, ValueError):
                pass
        st.finish("rolled-back" if recovered else "failed", error=str(exc)[:400],
                  rolled_back=True, recovered=recovered, to_version=st.d.get("to_version", ""))
    finally:
        for d in (stage, wheelhouse, stage_root):
            if d:
                shutil.rmtree(d, ignore_errors=True)


def _rollback_after_failure(st, snapshot, point_dir, untracked_before, root_touched):
    """Code tree, venv and root-side files back to the snapshot. Never the DB."""
    try:
        st.begin("rollback to %s" % (snapshot or "?")[:12])
        r = git("reset", "-q", "--hard", snapshot)
        st.step("restore application tree", r.returncode == 0,
                (r.stderr or "tree at %s" % snapshot[:12]).strip()[-300:])
        stray = _new_untracked(untracked_before)
        for rel in stray:
            if _link_below(APP, Path(rel).parent):
                continue
            target = APP / rel
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
            except OSError:
                pass
        if stray:
            st.step("remove %d file(s) the package added" % len(stray), True,
                    ", ".join(stray[:8]))
        restore_venv(point_dir, st)
        if root_touched:
            restore_root_side(point_dir, st)
        _chown_tree()
        st.step("database", True, "NOT restored automatically (backup kept in the "
                "rollback point)", warn=True)
        subprocess.run(["systemctl", "restart", SERVICE], timeout=180)
        ok, detail = health_ok()
        st.step("rollback health check", ok, detail if ok else
                "STILL UNHEALTHY after rollback: %s" % detail)
        return ok
    except Exception as exc:  # noqa: BLE001
        st.step("rollback", False, "rollback error: %s" % str(exc)[:300])
        return False


# ---------------------------------------------------------------------------
# kind: rollback (operator-requested)
# ---------------------------------------------------------------------------
def rollback_job(req):
    uid = req["id"]
    st = Status(uid, req, "rollback")
    cur = _current_version()
    stopped = False
    try:
        problem = runner_integrity_problem()
        if problem:
            st.step("runner integrity", False, problem)
            raise Refused("refusing to roll back from a runner the service account "
                          "can rewrite")
        st.step("runner integrity", True, "running root-owned")
        everything = rollback_points()
        if req["point"]:
            points = [p for p in everything if p.get("id") == req["point"]]
            if not points:
                raise Refused("no rollback point %r" % req["point"])
        else:
            points = [p for p in everything if p.get("state") == "applied"]
            if not points:
                raise Refused("there is no rollback point: no update package has been "
                              "applied on this node since it was installed")
        point = points[0]
        pdir = Path(point["_dir"])
        if point.get("state") in ("failed", "rolled-back"):
            # The code is already back at from_version (the failed apply rolled
            # itself back, or an earlier rollback did): only the DATABASE copy
            # taken before that update can still be restored.
            if not req["restore_db"]:
                raise Refused("rollback point %s is %s: its code is already rolled back; "
                              "only --restore-db applies to it" % (point["id"], point["state"]))
            if point.get("from_version") != cur:
                raise Refused("rollback point %s holds the database of %s, but this node "
                              "runs %s" % (point["id"], point.get("from_version"), cur))
            st.d["to_version"] = cur
            st.step("select rollback point", True, "%s (%s): database copy taken before "
                    "%s -> %s" % (point["id"], point["state"], point.get("from_version"),
                                  point.get("to_version")))
            st.begin("stop service")
            stopped = True
            subprocess.run(["systemctl", "stop", SERVICE], timeout=180)
            st.step("stop service", True, SERVICE)
            restore_databases(pdir, point.get("databases") or [], st)
            restart_service(st, cur or None)
            man = _rb_manifest(pdir)
            man["db_restored_at"] = now()
            _rb_save_manifest(pdir, man)
            st.finish("done", to_version=cur, rollback_point=point["id"])
            return
        if point.get("state") != "applied":
            raise Refused("rollback point %s is %s" % (point["id"], point.get("state")))
        if point.get("to_version") != cur:
            raise Refused("rollback point %s returns %s -> %s, but this node runs %s; "
                          "roll back the newer update first"
                          % (point["id"], point.get("to_version"), point.get("from_version"),
                             cur))
        snap = point.get("snapshot") or ""
        if not _SHA_RE.fullmatch(snap):
            raise Refused("the rollback point has no usable revision")
        st.d["to_version"] = point.get("from_version", "")
        st.step("select rollback point", True, "%s: %s -> %s%s" % (
            point["id"], cur, point.get("from_version"),
            " (database too)" if req["restore_db"] else " (database kept)"))

        st.begin("stop service")
        stopped = True
        subprocess.run(["systemctl", "stop", SERVICE], timeout=180)
        st.step("stop service", True, SERVICE)
        r = git("reset", "-q", "--hard", snap)
        st.step("restore application tree", r.returncode == 0,
                (r.stderr or "tree at %s" % snap[:12]).strip()[-300:])
        if r.returncode != 0:
            raise RuntimeError("git reset to %s failed" % snap[:12])
        if not restore_venv(pdir, st):
            raise RuntimeError("the venv could not be restored")
        restore_root_side(pdir, st)
        if req["restore_db"]:
            restore_databases(pdir, point.get("databases") or [], st)
        else:
            st.step("database", True, "kept as it is (pass --restore-db to put back the "
                    "copy taken before the update)", warn=True)
        _chown_tree()
        restart_service(st, point.get("from_version") or None)
        point_man = _rb_manifest(pdir)
        point_man["state"] = "rolled-back"
        point_man["rolled_back_at"] = now()
        _rb_save_manifest(pdir, point_man)
        st.finish("done", to_version=point.get("from_version", ""), rollback_point=point["id"])
    except Exception as exc:  # noqa: BLE001
        st.step("ERROR", False, str(exc)[:400])
        if stopped and _svc_active(SERVICE) != "active":
            try:
                subprocess.run(["systemctl", "start", SERVICE], timeout=180)
            except Exception:  # noqa: BLE001 -- the status must still be written
                pass
        st.finish("failed", error=str(exc)[:400])


# ---------------------------------------------------------------------------
# kind: service
# ---------------------------------------------------------------------------
def service_action(req):
    st = Status(req["id"], req, "service")
    st.d["to_version"] = st.d["from_version"]
    try:
        problem = runner_integrity_problem()
        if problem:
            st.step("runner integrity", False, problem)
            raise Refused("refusing to act from a runner the service account can rewrite")
        restart_service(st, None)
        if req["action"] == "restart-all":
            st.begin("nginx")
            t = subprocess.run(["nginx", "-t"], capture_output=True, text=True, timeout=60)
            if t.returncode != 0:
                st.step("nginx -t", False, (t.stderr or "")[-300:])
                raise RuntimeError("nginx -t failed: not reloading a broken configuration")
            r = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True,
                               text=True, timeout=60)
            st.step("reload nginx", r.returncode == 0, (r.stderr or "reloaded").strip()[-300:])
            if r.returncode != 0:
                raise RuntimeError("systemctl reload nginx failed")
        st.finish("done")
    except Exception as exc:  # noqa: BLE001
        st.step("ERROR", False, str(exc)[:400])
        st.finish("failed", error=str(exc)[:400])


# ---------------------------------------------------------------------------
# queue
# ---------------------------------------------------------------------------
_HANDLERS = {"package": package_change, "rollback": rollback_job, "service": service_action}


def _reject(req_path, uid, reason):
    """Dequeue a request the runner will not act on, and say why: in the
    journal always, in its status file when its id is one we can write."""
    _log("rejected request %s: %s" % (os.path.basename(str(req_path)), reason))
    if isinstance(uid, str) and UID_RE.fullmatch(uid):
        try:
            st = Status(uid, {}, "invalid")
            st.step("validate request", False, reason)
            st.finish("failed", error=reason)
        except Exception:  # noqa: BLE001
            pass


def _drain_junk():
    """Anything in the queue that is not a regular ``*.json`` would keep the
    .path unit firing for ever: drop it (a link is removed, never followed)."""
    try:
        entries = list(os.scandir(REQ))
    except OSError:
        return
    for e in entries:
        try:
            st_ = os.lstat(e.path)
        except OSError:
            continue
        if e.name.endswith(".json") and stat.S_ISREG(st_.st_mode):
            continue
        if stat.S_ISDIR(st_.st_mode):
            _log("queue holds a directory %s; leaving it" % e.name)
            continue
        _log("dropping non-request entry %s" % e.name)
        try:
            os.unlink(e.path)
        except OSError:
            pass


def handle(req_path):
    req_path = Path(req_path)
    stem = req_path.name[:-len(".json")] if req_path.name.endswith(".json") else req_path.name
    try:
        raw = _read_request(req_path)
    except (OSError, RequestError) as exc:
        try:
            os.unlink(req_path)
        except OSError:
            pass
        _reject(req_path, stem, "unreadable request: %s" % exc)
        return
    try:
        os.unlink(req_path)        # claimed: never processed twice
    except OSError:
        pass
    try:
        req = validate_request(raw, stem)
    except RequestError as exc:
        _reject(req_path, stem, str(exc))
        return
    _log("processing %s request %s" % (req["kind"], req["id"]))
    _HANDLERS[req["kind"]](req)


def main(argv=None):
    if os.geteuid() != 0:
        print("satom-builder-updater: must run as root", file=sys.stderr)
        return 3
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        _drain_junk()
        while True:
            try:
                pending = sorted((p for p in REQ.glob("*.json")),
                                 key=lambda p: (p.lstat().st_mtime, p.name))
            except OSError:
                pending = []
            if not pending:
                break
            for rp in pending:
                try:
                    handle(rp)
                except Exception as exc:  # noqa: BLE001 -- never crash-loop
                    _log("dropped request %s: %s" % (rp.name, exc))
                    try:
                        os.unlink(rp)
                    except OSError:
                        pass
            _drain_junk()
    return 0


if __name__ == "__main__":
    sys.exit(main())
