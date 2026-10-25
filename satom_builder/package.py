"""``satom.plugin/1`` packages: build, sign, and read back (adopt).

Byte-compatible with SATOM's ``app/services/plugin_package.write_package``:

* ``plugin.json`` = ``json.dumps(manifest, indent=1, sort_keys=True,
  ensure_ascii=False)`` with ``format`` and the ``files`` sha256 map;
* members in the order ``plugin.json``, ``plugin.sig`` (when signed), then the
  files sorted by name; every member dated 1980-01-01, deflated, mode 0644;
* ``plugin.sig`` = ``json.dumps({"alg": "ed25519", "key_id": <sha256 hex of
  the raw public key>, "sig": <base64 Ed25519 over plugin.json's bytes>},
  indent=1)`` (contract section 8).

The manifest checks below are the package FORMAT (what a zip must look like
to be a package at all). The plugin RULES are SATOM's, in the rules pack.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "satom.plugin/1"
EXT = ".satomplugin"
KINDS = ("view", "widget", "app")          # never "code" (design section 2)
APP_FILE = "app.json"
CONTENT_FILES = ("body.jinja", "style.css", "script.js")
ASSET_RE = re.compile(r"^assets/([0-9a-f]{64})\.(png|jpg|gif|webp)$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
ICON_RE = re.compile(r"^bi-[a-z0-9-]{1,28}$")
MAX_PACKAGE = 10 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024
MAX_FILES = 200
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
EXT_MIME = {v: k for k, v in MIME_EXT.items()}


class PackageError(ValueError):
    """The package cannot be built or read; the message says why."""


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sniff_image(data: bytes) -> str:
    """Same sniffing as SATOM's ``studio_design.sniff``: bytes, never names."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def bump(version: str, part: str = "patch") -> str:
    if not VERSION_RE.match(version or ""):
        raise PackageError("The version must be X.Y.Z.")
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    elif part == "patch":
        patch += 1
    else:
        raise PackageError("Bump major, minor or patch.")
    if max(major, minor, patch) > 9999:
        raise PackageError("A version part cannot exceed 9999.")
    return f"{major}.{minor}.{patch}"


# --------------------------------------------------------------------------
# format checks (mirrors plugin_package.validate_manifest, minus kind code)
# --------------------------------------------------------------------------
def _member_ok(name: str, kind: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        return False
    if any(p in ("", ".", "..") for p in name.split("/")):
        return False
    if kind in ("view", "widget"):
        return name in CONTENT_FILES
    return name == APP_FILE or bool(ASSET_RE.match(name))


def validate_manifest(m) -> dict:
    if not isinstance(m, dict):
        raise PackageError("plugin.json is not an object.")
    if m.get("format") != FORMAT:
        raise PackageError(f"Unknown package format {m.get('format')!r}.")
    if not SLUG_RE.match(str(m.get("slug") or "")):
        raise PackageError("The slug must be lowercase letters, digits and dashes "
                           "(at most 80, starting with a letter or digit).")
    name = m.get("name")
    if not (isinstance(name, str) and name.strip() and len(name) <= 200):
        raise PackageError("The plugin has no name.")
    if not VERSION_RE.match(str(m.get("version") or "")):
        raise PackageError("The version must be X.Y.Z.")
    kind = m.get("kind")
    if kind == "code":
        raise PackageError("SATOM Builder does not build or adopt code plugins.")
    if kind not in KINDS:
        raise PackageError(f"Unknown plugin kind {kind!r}.")
    if not ICON_RE.match(str(m.get("icon") or "")):
        raise PackageError("The icon must be a Bootstrap icon class (bi-...).")
    if m.get("entry"):
        raise PackageError("'entry' is only for code plugins.")
    for k in ("datasets", "params"):
        if not isinstance(m.get(k, []), list):
            raise PackageError(f"'{k}' must be a list.")
    if kind == "app" and (m.get("datasets") or m.get("params")):
        raise PackageError("An app plugin carries no datasets or params.")
    if m.get("min_satom") and not VERSION_RE.match(str(m["min_satom"])):
        raise PackageError("min_satom must be X.Y.Z.")
    files = m.get("files")
    if not isinstance(files, dict) or not files:
        raise PackageError("plugin.json lists no files.")
    if len(files) > MAX_FILES:
        raise PackageError(f"Too many files ({len(files)} > {MAX_FILES}).")
    for fname, digest in files.items():
        if not _member_ok(fname, kind):
            raise PackageError(f"{fname!r} is not allowed in a {kind} plugin.")
        if not re.match(r"^[0-9a-f]{64}$", str(digest)):
            raise PackageError(f"{fname!r} has no sha256 in plugin.json.")
    if kind in ("view", "widget") and set(files) != set(CONTENT_FILES):
        raise PackageError(f"A {kind} plugin carries exactly {', '.join(CONTENT_FILES)}.")
    if kind == "app" and APP_FILE not in files:
        raise PackageError("An app plugin carries app.json.")
    return m


# --------------------------------------------------------------------------
# signing
# --------------------------------------------------------------------------
def key_id(raw_public: bytes) -> str:
    return sha256(raw_public)


def raw_public(private_key) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return private_key.public_key().public_bytes(serialization.Encoding.Raw,
                                                 serialization.PublicFormat.Raw)


def sign(manifest_bytes: bytes, private_key) -> bytes:
    sig = private_key.sign(manifest_bytes)
    return json.dumps({"alg": "ed25519", "key_id": key_id(raw_public(private_key)),
                       "sig": base64.b64encode(sig).decode()}, indent=1).encode()


def load_private_key(path: str | os.PathLike):
    """The installation's publisher key (PKCS#8 PEM), or None when absent or
    unreadable."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    try:
        pem = Path(path).read_bytes()
    except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
        return None
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise PackageError("The publisher key is not an Ed25519 key.")
    return key


def publisher_info(path) -> dict:
    """-> {"present", "key_id", "public_key" (base64 raw), "error"}."""
    try:
        key = load_private_key(path)
    except Exception as exc:  # noqa: BLE001 - shown on the Publisher page
        return {"present": False, "key_id": "", "public_key": "",
                "error": f"The publisher key cannot be read: {type(exc).__name__}."}
    if key is None:
        return {"present": False, "key_id": "", "public_key": "", "error": ""}
    raw = raw_public(key)
    return {"present": True, "key_id": key_id(raw),
            "public_key": base64.b64encode(raw).decode(), "error": ""}


def keygen(path: str | os.PathLike, group: str = "satom-builder") -> tuple[str, bool]:
    """Create the publisher key if missing (idempotent). -> (key_id, created).
    PKCS#8 PEM, mode 0640, root:<group> when run as root and the group exists.
    The directory is created if missing; its owner and mode are the installer's."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    p = Path(path)
    existing = load_private_key(p) if p.exists() else None
    if existing is not None:
        return key_id(raw_public(existing)), False
    if p.exists():
        raise PackageError(f"{p} exists but cannot be read as a key; not overwritten.")
    p.parent.mkdir(parents=True, exist_ok=True)
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    os.chmod(p, 0o640)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            import grp
            gid = grp.getgrnam(group).gr_gid
            os.chown(p, 0, gid)
        except KeyError:
            pass
    return key_id(raw_public(key)), True


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------
def write_package(manifest: dict, files: dict[str, bytes], private_key=None) -> bytes:
    m = dict(manifest)
    m["format"] = FORMAT
    m["files"] = {n: sha256(b) for n, b in sorted(files.items())}
    validate_manifest(m)
    for n, b in files.items():
        if len(b) > MAX_FILE:
            raise PackageError(f"{n} is larger than 2 MB.")
    if sum(len(b) for b in files.values()) > MAX_TOTAL:
        raise PackageError("The package would expand to more than 10 MB.")
    mbytes = json.dumps(m, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
    members = [("plugin.json", mbytes)]
    if private_key is not None:
        members.append(("plugin.sig", sign(mbytes, private_key)))
    members += sorted(files.items())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            zi = zipfile.ZipInfo(name, ZIP_EPOCH)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, data)
    out = buf.getvalue()
    if len(out) > MAX_PACKAGE:
        raise PackageError(f"The package is {len(out)} bytes (10 MB at most).")
    return out


def app_body_bytes(body: dict) -> bytes:
    """app.json exactly as SATOM's export writes it."""
    return json.dumps(body, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")


def build_from_project(project: dict, assets: list[dict], *, private_key=None,
                       built_at: str | None = None, min_satom: str = "") -> tuple[str, bytes]:
    """``project`` is the section-11 shape; ``assets`` = [{"sha256","mime","data"}].
    -> (file name, package bytes)."""
    kind = project.get("kind")
    manifest = {
        "slug": project.get("slug") or "",
        "name": project.get("name") or "",
        "version": project.get("version") or "1.0.0",
        "kind": kind,
        "icon": project.get("icon") or "bi-puzzle",
        "description": (project.get("description") or "")[:500],
        "datasets": list(project.get("datasets") or []) if kind != "app" else [],
        "params": list(project.get("params") or []) if kind != "app" else [],
        "built_at": built_at or utc_now(),
    }
    if VERSION_RE.match(min_satom or ""):
        manifest["min_satom"] = min_satom
    files: dict[str, bytes] = {}
    if kind in ("view", "widget"):
        src = project.get("files") or {}
        for n in CONTENT_FILES:
            v = src.get(n) or ""
            if not isinstance(v, str):
                raise PackageError(f"{n} must be text.")
            files[n] = v.encode("utf-8")
    elif kind == "app":
        body = project.get("app")
        if not isinstance(body, dict):
            body = {}
        body = dict(body)
        app = dict(body.get("app") or {})
        # Only fills what is ABSENT, from the project's own fields; it never
        # changes a value the author wrote.
        for k, v in (("slug", manifest["slug"]), ("name", manifest["name"]),
                     ("icon", manifest["icon"]), ("description", manifest["description"])):
            app.setdefault(k, v)
        body["app"] = app
        files[APP_FILE] = app_body_bytes(body)
        for a in assets:
            ext = MIME_EXT.get(a["mime"])
            if ext:
                files[f"assets/{a['sha256']}.{ext}"] = bytes(a["data"])
    else:
        raise PackageError(f"Unknown plugin kind {kind!r}.")
    return f"{manifest['slug']}-{manifest['version']}{EXT}", write_package(
        manifest, files, private_key)


# --------------------------------------------------------------------------
# read (adopt an existing package into a project)
# --------------------------------------------------------------------------
def read_for_adopt(data: bytes) -> dict:
    """Parse a ``.satomplugin`` without trusting it: format checks and member
    hashes only. A signature is NOT verified and is dropped (an adopted
    package is re-signed by this installation when it is built again).
    -> {"manifest", "files", "assets": [{"sha256","mime","data"}], "signed"}."""
    if not data:
        raise PackageError("The file is empty.")
    if len(data) > MAX_PACKAGE:
        raise PackageError("The file is larger than 10 MB.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("This is not a SATOM plugin package.")
    infos = z.infolist()
    names = [i.filename for i in infos]
    if len(names) != len(set(names)):
        raise PackageError("The package has a duplicate member.")
    if "plugin.json" not in names:
        raise PackageError("plugin.json is missing: not a SATOM plugin package.")
    if sum(i.file_size for i in infos) > MAX_TOTAL:
        raise PackageError("The package expands to more than 10 MB.")
    if any(i.file_size > MAX_FILE for i in infos):
        raise PackageError("A member is larger than 2 MB.")
    try:
        manifest = json.loads(z.read("plugin.json").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise PackageError("plugin.json is not valid JSON.")
    validate_manifest(manifest)
    allowed = set(manifest["files"]) | {"plugin.json", "plugin.sig"}
    extra = sorted(n for n in names if n not in allowed)
    if extra:
        raise PackageError("Members not listed in plugin.json: " + ", ".join(extra[:5]))
    files: dict[str, bytes] = {}
    for fname, digest in manifest["files"].items():
        if fname not in names:
            raise PackageError(f"{fname} is listed but missing.")
        b = z.read(fname)
        if sha256(b) != digest:
            raise PackageError(f"{fname} does not match its sha256 in plugin.json.")
        files[fname] = b
    assets = []
    if manifest["kind"] == "app":
        try:
            body = json.loads(files[APP_FILE].decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PackageError("app.json is not valid JSON.")
        if not isinstance(body, dict) or not isinstance(body.get("app"), dict):
            raise PackageError("app.json has no 'app' object.")
        for fname, b in files.items():
            m = ASSET_RE.match(fname)
            if m:
                mime = sniff_image(b)
                if not mime or MIME_EXT[mime] != m.group(2) or sha256(b) != m.group(1):
                    raise PackageError(f"{fname} does not match its name.")
                assets.append({"sha256": m.group(1), "mime": mime, "data": b})
    else:
        for n in CONTENT_FILES:
            try:
                files[n].decode("utf-8")
            except UnicodeDecodeError:
                raise PackageError(f"{n} is not UTF-8 text.")
    return {"manifest": manifest, "files": files, "assets": assets,
            "signed": "plugin.sig" in names}
