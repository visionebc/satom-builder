"""The rules pack: SATOM's own validation code, pinned and run in a subprocess.

Contract section 3.1 ("Pinning", Builder side):

* the zip is stored under ``data/rules/<sha256>.zip`` (+ an unpacked,
  read-only directory ``data/rules/<sha256>/``);
* before EVERY load the zip's sha256 is checked against the expected digest
  (``X-Satom-Rules-Sha256`` at download, the target snapshot's
  ``rules_sha256`` afterwards) and every member's hash against ``rules.json``;
* a pack whose ``satom_version`` differs from the target's is refused;
* the pack runs in a SUBPROCESS: ``python -I -B -c <runner>``, ``sys.path`` =
  the unpacked pack + the standard library + the venv's site-packages, an
  environment that holds none of the Builder's secrets, ``cwd`` = the pack,
  a timeout (30 s by default), JSON over stdin/stdout and size caps.

A pack that fails any of these checks is not loaded, and with no pack no
local validation is possible: the caller gets a :class:`RulesError` that says
why. Nothing is ever re-implemented here.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import site
import stat
import subprocess
import sys
import sysconfig
import tempfile
import zipfile
from pathlib import Path

FORMAT = "satom.rules-pack/1"
ENTRY = "satom_rules"
MAX_ZIP = 32 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_UNPACKED = 64 * 1024 * 1024
MAX_STDIN = 24 * 1024 * 1024
MAX_STDOUT = 16 * 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# The program the subprocess runs. It reads ONE JSON request on stdin and
# writes ONE JSON answer on stdout. argv[1] = pack dir, argv[2:] = the extra
# sys.path entries (stdlib + site-packages) the parent computed.
RUNNER = r"""
import sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
except Exception:
    pass
_pack, _extra = sys.argv[1], sys.argv[2:]
sys.path[:] = [_pack] + _extra
import base64, json, io
_out = sys.stdout
sys.stdout = io.StringIO()          # a print() in the pack must not corrupt the answer
def _answer(obj):
    _out.write(json.dumps(obj, ensure_ascii=False, default=str))
    _out.flush()
try:
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    import satom_rules as R
    op = req.get("op")
    if op == "check":
        res = R.check(base64.b64decode(req["package"]), req["target"], req.get("policy", "builder"))
    elif op == "schema":
        res = R.schema(req["target"])
    elif op == "author_guide":
        res = R.author_guide(req["target"])
    elif op == "info":
        res = {"check_version": getattr(R, "CHECK_VERSION", None),
               "policies": list(getattr(R, "POLICIES", ()))}
    else:
        raise ValueError("unknown op %r" % (op,))
    _answer({"ok": True, "result": res})
except BaseException as exc:
    _answer({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
"""

#: The only environment variables the subprocess gets.
_SAFE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
             "LC_ALL": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}


class RulesError(Exception):
    """The pack cannot be used; the message says why, in plain English."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _member_ok(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    return all(p not in ("", ".", "..") for p in name.split("/"))


def inspect_zip(data: bytes, expected_sha: str) -> dict:
    """Check a pack zip against its digest and its own ``rules.json``.
    -> the parsed ``rules.json``. Raises :class:`RulesError`."""
    expected_sha = (expected_sha or "").strip().lower()
    if not _SHA_RE.match(expected_sha):
        raise RulesError("The expected rules pack digest is not a sha256.")
    if len(data) > MAX_ZIP:
        raise RulesError("The rules pack is larger than 32 MB.")
    actual = sha256(data)
    if actual != expected_sha:
        raise RulesError(f"The rules pack does not match its digest (expected "
                         f"{expected_sha[:12]}, got {actual[:12]}): refused.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise RulesError("The rules pack is not a zip file.")
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise RulesError("The rules pack has too many members.")
    names = [i.filename for i in infos]
    if len(names) != len(set(names)):
        raise RulesError("The rules pack has a duplicate member.")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise RulesError("The rules pack expands to more than 64 MB.")
    if "rules.json" not in names:
        raise RulesError("The rules pack has no rules.json.")
    try:
        meta = json.loads(z.read("rules.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise RulesError("rules.json is not valid JSON.")
    if not isinstance(meta, dict) or meta.get("format") != FORMAT:
        raise RulesError(f"Unknown rules pack format (this Builder reads {FORMAT}).")
    if meta.get("entry") != ENTRY:
        raise RulesError(f"The rules pack entry must be {ENTRY!r}.")
    files = meta.get("files")
    if not isinstance(files, dict) or not files:
        raise RulesError("rules.json lists no files.")
    for n in names:
        if not _member_ok(n):
            raise RulesError(f"The rules pack member {n!r} has an unsafe name.")
        if n != "rules.json" and n not in files:
            raise RulesError(f"The rules pack member {n} is not listed in rules.json.")
    for n, digest in files.items():
        if not _member_ok(n) or not str(n).startswith(ENTRY + "/"):
            raise RulesError(f"rules.json lists an unexpected file {n!r}.")
        if n not in names:
            raise RulesError(f"{n} is listed in rules.json but missing.")
        if not _SHA_RE.match(str(digest)) or sha256(z.read(n)) != digest:
            raise RulesError(f"{n} does not match its sha256 in rules.json: refused.")
    if f"{ENTRY}/__init__.py" not in files:
        raise RulesError(f"{ENTRY}/__init__.py is missing from the rules pack.")
    if not isinstance(meta.get("satom_version"), str) or not meta["satom_version"]:
        raise RulesError("rules.json carries no satom_version.")
    return meta


def _readonly_tree(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for f in filenames:
            os.chmod(os.path.join(dirpath, f), 0o444)
        os.chmod(dirpath, 0o555)


def _writable_tree(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)


class RulesPack:
    """A verified, unpacked rules pack. Construct with :func:`load`."""

    def __init__(self, sha: str, directory: Path, meta: dict, timeout: float):
        self.sha = sha
        self.dir = directory
        self.meta = meta
        self.satom_version = meta["satom_version"]
        self.timeout = timeout

    # -- the pack's API (contract section 3.2) ---------------------------
    def check(self, package: bytes, target: dict, policy: str = "builder") -> dict:
        res = self._run({"op": "check", "policy": policy, "target": target,
                         "package": base64.b64encode(package).decode("ascii")})
        if not isinstance(res, dict) or "ok" not in res or "problems" not in res:
            raise RulesError("The rules pack's check() answered in an unknown shape.")
        return res

    def schema(self, target: dict) -> dict:
        res = self._run({"op": "schema", "target": target})
        if not isinstance(res, dict):
            raise RulesError("The rules pack's schema() did not return an object.")
        return res

    def author_guide(self, target: dict) -> str:
        res = self._run({"op": "author_guide", "target": target})
        if not isinstance(res, str):
            raise RulesError("The rules pack's author_guide() did not return text.")
        return res

    def info(self) -> dict:
        return self._run({"op": "info"})

    # -- subprocess -------------------------------------------------------
    def _run(self, request: dict):
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        if len(payload) > MAX_STDIN:
            raise RulesError("The request to the rules pack is too large.")
        cmd = [sys.executable, "-I", "-B", "-c", RUNNER, str(self.dir)] + child_sys_path()
        try:
            proc = subprocess.run(cmd, input=payload, capture_output=True,
                                  timeout=self.timeout, cwd=str(self.dir),
                                  env=dict(_SAFE_ENV), close_fds=True)
        except subprocess.TimeoutExpired:
            raise RulesError(f"The rules pack did not answer within "
                             f"{int(self.timeout)} s: stopped.") from None
        out = proc.stdout
        if len(out) > MAX_STDOUT:
            raise RulesError("The rules pack's answer is too large.")
        try:
            ans = json.loads(out.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            tail = proc.stderr.decode("utf-8", "replace")[-400:].strip()
            raise RulesError("The rules pack crashed" + (f": {tail}" if tail else "."))
        if not isinstance(ans, dict) or not ans.get("ok"):
            err = ans.get("error") if isinstance(ans, dict) else "unknown error"
            raise RulesError(f"The rules pack failed: {str(err)[:600]}")
        return ans.get("result")


def child_sys_path() -> list[str]:
    """The standard library and the venv's site-packages: nothing else (no
    cwd, no user site, not the Builder's own source tree)."""
    paths = []
    for key in ("stdlib", "platstdlib"):
        p = sysconfig.get_paths().get(key)
        if p and p not in paths:
            paths.append(p)
    dyn = os.path.join(sysconfig.get_paths()["platstdlib"], "lib-dynload")
    if os.path.isdir(dyn) and dyn not in paths:
        paths.append(dyn)
    for p in site.getsitepackages():
        if os.path.isdir(p) and p not in paths:
            paths.append(p)
    builder_src = str(Path(__file__).resolve().parent.parent)
    return [p for p in paths if os.path.abspath(p) != builder_src]


# --------------------------------------------------------------------------
# store
# --------------------------------------------------------------------------
def rules_dir(data_dir) -> Path:
    return Path(data_dir) / "rules"


def store(data_dir, data: bytes, header_sha: str, target_version: str) -> str:
    """Verify a freshly downloaded pack and store it. -> its sha256."""
    meta = inspect_zip(data, header_sha)
    if meta["satom_version"] != target_version:
        raise RulesError(f"The rules pack is for SATOM {meta['satom_version']} but the "
                         f"target runs {target_version}: refused.")
    sha = header_sha.lower()
    root = rules_dir(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    zpath = root / f"{sha}.zip"
    tmp = root / f".{sha}.zip.tmp"
    tmp.write_bytes(data)
    os.chmod(tmp, 0o444)
    os.replace(tmp, zpath)
    _unpack(root, sha, data)
    return sha


def _unpack(root: Path, sha: str, data: bytes) -> Path:
    final = root / sha
    if final.is_dir():
        _writable_tree(final)
        shutil.rmtree(final)
    staging = Path(tempfile.mkdtemp(prefix=f".{sha[:12]}-", dir=root))
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if not _member_ok(info.filename):
                    raise RulesError("unsafe member name")
                dest = staging / info.filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(info))
        _readonly_tree(staging)
        os.chmod(staging, 0o755)
        os.replace(staging, final)
        os.chmod(final, 0o555)
    except BaseException:
        if staging.exists():
            _writable_tree(staging)
            shutil.rmtree(staging, ignore_errors=True)
        raise
    return final


def _verify_tree(directory: Path, meta: dict) -> None:
    listed = set(meta["files"]) | {"rules.json"}
    for dirpath, dirnames, filenames in os.walk(directory):
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), directory).replace(os.sep, "/")
            if rel not in listed:
                raise RulesError(f"The unpacked rules pack holds an unlisted file {rel}.")
    for n, digest in meta["files"].items():
        p = directory / n
        try:
            if sha256(p.read_bytes()) != digest:
                raise RulesError(f"The unpacked {n} no longer matches rules.json.")
        except FileNotFoundError:
            raise RulesError(f"The unpacked rules pack lost {n}.") from None


def have(data_dir, sha: str) -> bool:
    sha = (sha or "").lower()
    return bool(_SHA_RE.match(sha)) and (rules_dir(data_dir) / f"{sha}.zip").is_file()


def load(data_dir, sha: str, target_version: str, timeout: float = 30.0) -> RulesPack:
    """Load a stored pack, re-verifying everything first."""
    sha = (sha or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise RulesError("The target names no valid rules pack digest.")
    root = rules_dir(data_dir)
    zpath = root / f"{sha}.zip"
    try:
        data = zpath.read_bytes()
    except FileNotFoundError:
        raise RulesError("The target's rules pack has not been downloaded yet.") from None
    meta = inspect_zip(data, sha)
    if meta["satom_version"] != target_version:
        raise RulesError(f"The rules pack is for SATOM {meta['satom_version']} but the "
                         f"target runs {target_version}: refused.")
    directory = root / sha
    try:
        if not directory.is_dir():
            raise RulesError("missing")
        _verify_tree(directory, meta)
        if bytes(Path(directory / "rules.json").read_bytes()) != zipfile.ZipFile(
                io.BytesIO(data)).read("rules.json"):
            raise RulesError("rules.json differs")
    except RulesError:
        directory = _unpack(root, sha, data)   # re-create from the verified zip
        _verify_tree(directory, meta)
    st = directory.stat()
    if st.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        os.chmod(directory, 0o555)
    return RulesPack(sha, directory, meta, timeout)
