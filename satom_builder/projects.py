"""Projects: the draft of one plugin, in the section-11 shape.

A project row holds the manifest fields (name, kind, slug, version, icon,
description, target) in columns and the rest of the draft as JSON:
``{"files": {"body.jinja","style.css","script.js"}, "datasets": [],
"params": [], "app": {...app.json body...}}``. Images live in ``assets``.
"""
from __future__ import annotations

import json
import re

from . import db
from .package import (ICON_RE, MIME_EXT, SLUG_RE, VERSION_RE, PackageError, sha256,
                      sniff_image)

KINDS = ("view", "widget", "app")
CONTENT_FILES = ("body.jinja", "style.css", "script.js")
MAX_BODY = 8 * 1024 * 1024
MAX_TEXT_FILE = 2 * 1024 * 1024
MAX_APP_JSON = 2 * 1024 * 1024
MAX_LIST = 100
MAX_ASSETS = 12
ASSET_MAX = 512 * 1024
MAX_DEPTH = 40
SECRET_FIELDS = ("password", "secret", "token", "api_key", "client_secret")

EDITABLE = ("name", "kind", "slug", "version", "icon", "description", "target_id",
            "files", "datasets", "params", "app", "adom")
#: an ADOM key as SATOM names them ('' = Auto: from the datasets it reads)
ADOM_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
READ_ONLY = ("id", "assets", "updated_at", "last_check", "local_check", "created_at",
             "owner", "where")


class ShapeError(ValueError):
    def __init__(self, message: str, code: str = "bad_shape"):
        super().__init__(message)
        self.message = message
        self.code = code


def empty_draft(kind: str) -> dict:
    d = {"files": {n: "" for n in CONTENT_FILES}, "datasets": [], "params": [], "app": {}}
    if kind == "app":
        d["app"] = {"app": {"fields": [], "graph": {}, "actions": [], "design": {}},
                    "stores": [], "connections": [], "apis": []}
    return d


def _depth(obj, n=0) -> int:
    if n > MAX_DEPTH:
        return n
    if isinstance(obj, dict):
        return max([_depth(v, n + 1) for v in obj.values()] or [n])
    if isinstance(obj, list):
        return max([_depth(v, n + 1) for v in obj] or [n])
    return n


def validate_patch(body, conn=None) -> dict:
    """Server-side shape validation of a PUT body (every key optional).
    -> the clean patch. Raises :class:`ShapeError`."""
    if not isinstance(body, dict):
        raise ShapeError("The body must be a JSON object.")
    unknown = [k for k in body if k not in EDITABLE and k not in READ_ONLY]
    if unknown:
        raise ShapeError(f"Unknown key(s): {', '.join(sorted(unknown)[:5])}.", "unknown_key")
    out: dict = {}
    if "name" in body:
        v = body["name"]
        if not isinstance(v, str) or not v.strip() or len(v) > 200:
            raise ShapeError("name must be 1 to 200 characters.")
        out["name"] = v.strip()
    if "kind" in body:
        if body["kind"] not in KINDS:
            raise ShapeError("kind must be view, widget or app.")
        out["kind"] = body["kind"]
    if "slug" in body:
        if not isinstance(body["slug"], str) or not SLUG_RE.match(body["slug"]):
            raise ShapeError("slug must be lowercase letters, digits and dashes "
                             "(at most 80, starting with a letter or digit).")
        out["slug"] = body["slug"]
    if "version" in body:
        if not isinstance(body["version"], str) or not VERSION_RE.match(body["version"]):
            raise ShapeError("version must be X.Y.Z.")
        out["version"] = body["version"]
    if "icon" in body:
        if not isinstance(body["icon"], str) or not ICON_RE.match(body["icon"]):
            raise ShapeError("icon must be a Bootstrap icon class (bi-...).")
        out["icon"] = body["icon"]
    if "description" in body:
        v = body["description"]
        if not isinstance(v, str) or len(v) > 500:
            raise ShapeError("description must be text of at most 500 characters.")
        out["description"] = v
    if "target_id" in body:
        v = body["target_id"]
        if v is not None and (not isinstance(v, int) or isinstance(v, bool)):
            raise ShapeError("target_id must be an integer or null.")
        if v is not None and conn is not None and conn.execute(
                "SELECT 1 FROM targets WHERE id = ?", (v,)).fetchone() is None:
            raise ShapeError("target_id names no SATOM target.", "no_target")
        out["target_id"] = v
    if "files" in body:
        v = body["files"]
        if not isinstance(v, dict) or any(k not in CONTENT_FILES for k in v):
            raise ShapeError("files holds only body.jinja, style.css and script.js.")
        for k, text in v.items():
            if not isinstance(text, str):
                raise ShapeError(f"files.{k} must be text.")
            if len(text.encode("utf-8")) > MAX_TEXT_FILE:
                raise ShapeError(f"files.{k} is larger than 2 MB.")
        out["files"] = {n: v.get(n, "") for n in CONTENT_FILES}
    if "datasets" in body:
        v = body["datasets"]
        if not isinstance(v, list) or len(v) > MAX_LIST or not all(
                isinstance(x, str) and 0 < len(x) <= 120 for x in v):
            raise ShapeError(f"datasets must be a list of at most {MAX_LIST} names.")
        out["datasets"] = v
    if "params" in body:
        v = body["params"]
        if not isinstance(v, list) or len(v) > MAX_LIST or not all(
                isinstance(x, dict) for x in v):
            raise ShapeError(f"params must be a list of at most {MAX_LIST} objects.")
        if len(json.dumps(v)) > 256 * 1024:
            raise ShapeError("params is too large.")
        out["params"] = v
    if "adom" in body:
        v = body["adom"]
        if v is None:
            v = ""
        if not isinstance(v, str) or (v and not ADOM_RE.match(v)):
            raise ShapeError("adom must be '' (Auto: from the datasets it reads) or an ADOM "
                             "key such as global or fortigate.")
        out["adom"] = v
    if "app" in body:
        v = body["app"]
        if not isinstance(v, dict):
            raise ShapeError("app must be an object (the app.json body).")
        if v and not isinstance(v.get("app", {}), dict):
            raise ShapeError("app.app must be an object.")
        for k in ("stores", "connections", "apis", "scripts"):
            if k in v and not isinstance(v[k], list):
                raise ShapeError(f"app.{k} must be a list.")
        for c in v.get("connections") or []:
            if isinstance(c, dict) and any(c.get(k) for k in SECRET_FIELDS):
                raise ShapeError(f"Connection {c.get('slug', '?')!r} carries a secret. The "
                                 "Builder never stores secrets: SATOM asks for them when the "
                                 "package is imported.", "secret_in_draft")
        if len(json.dumps(v, ensure_ascii=False).encode("utf-8")) > MAX_APP_JSON:
            raise ShapeError("app is larger than 2 MB.")
        if _depth(v) > MAX_DEPTH:
            raise ShapeError("app is nested too deeply.")
        out["app"] = v
    return out


# --------------------------------------------------------------------------
# rows <-> section-11 shape
# --------------------------------------------------------------------------
def get_row(conn, pid: int):
    return conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()


def may_access(user, row) -> bool:
    return row is not None and (user.is_admin or row["owner_id"] == user.id)


def asset_list(conn, pid: int) -> list[dict]:
    return [{"name": r["name"], "sha256": r["sha256"], "size": r["size"]}
            for r in conn.execute("SELECT name, sha256, size FROM assets WHERE project_id = ? "
                                  "ORDER BY id", (pid,))]


def asset_blobs(conn, pid: int) -> list[dict]:
    return [{"sha256": r["sha256"], "mime": r["mime"], "data": bytes(r["data"])}
            for r in conn.execute("SELECT sha256, mime, data FROM assets WHERE project_id = ? "
                                  "ORDER BY id", (pid,))]


def to_api(conn, row) -> dict:
    draft = db.loads(row["draft"], {}) or {}
    files = draft.get("files") or {}
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "slug": row["slug"],
        "version": row["version"], "icon": row["icon"], "description": row["description"],
        "target_id": row["target_id"],
        "files": {n: files.get(n, "") for n in CONTENT_FILES},
        "datasets": draft.get("datasets") or [],
        "params": draft.get("params") or [],
        "app": draft.get("app") or {},
        "adom": str(draft.get("adom") or ""),
        "assets": asset_list(conn, row["id"]),
        "updated_at": row["updated_at"],
        "last_check": db.loads(row["last_check"]),
    }


def create(conn, owner_id: int, *, name: str, kind: str, slug: str, version: str = "1.0.0",
           icon: str = "bi-puzzle", description: str = "", target_id=None,
           draft: dict | None = None) -> int:
    patch = validate_patch({"name": name, "kind": kind, "slug": slug, "version": version,
                            "icon": icon, "description": description,
                            "target_id": target_id}, conn)
    t = db.now()
    cur = conn.execute(
        "INSERT INTO projects (owner_id, name, kind, slug, version, icon, description, "
        "target_id, draft, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, patch["name"], patch["kind"], patch["slug"], patch["version"],
         patch["icon"], patch["description"], patch["target_id"],
         db.dumps(draft if draft is not None else empty_draft(kind)), t, t))
    return cur.lastrowid


def apply_patch(conn, row, patch: dict, by_user: str) -> None:
    """Save a validated patch; the previous draft is kept as a revision."""
    draft = db.loads(row["draft"], {}) or {}
    cols = {k: patch[k] for k in ("name", "kind", "slug", "version", "icon", "description",
                                  "target_id") if k in patch}
    for k in ("files", "datasets", "params", "app", "adom"):
        if k in patch:
            draft[k] = patch[k]
    conn.execute("INSERT INTO project_revisions (project_id, at, by_user, data) "
                 "VALUES (?,?,?,?)", (row["id"], db.now(), by_user,
                                       db.dumps({k: row[k] for k in row.keys()
                                                 if k not in ("last_check",)})))
    conn.execute("DELETE FROM project_revisions WHERE project_id = ? AND id NOT IN "
                 "(SELECT id FROM project_revisions WHERE project_id = ? "
                 "ORDER BY id DESC LIMIT 20)", (row["id"], row["id"]))
    sets = ", ".join(f"{k} = ?" for k in cols)
    args = list(cols.values())
    conn.execute(f"UPDATE projects SET {sets + ', ' if sets else ''}draft = ?, "
                 "updated_at = ? WHERE id = ?", args + [db.dumps(draft), db.now(), row["id"]])


def set_last_check(conn, pid: int, source: str, result) -> None:
    conn.execute("UPDATE projects SET last_check = ? WHERE id = ?",
                 (db.dumps({"source": source, "at": db.now(), "result": result})
                  if result is not None else None, pid))


_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def add_asset(conn, pid: int, filename: str, data: bytes) -> dict:
    if not data:
        raise ShapeError("The file is empty.", "empty")
    if len(data) > ASSET_MAX:
        raise ShapeError("An image is at most 512 KiB.", "too_large")
    mime = sniff_image(data)
    if mime not in MIME_EXT:
        raise ShapeError("Only PNG, JPEG, GIF and WebP images are accepted (checked "
                         "from the bytes, not the name).", "not_image")
    digest = sha256(data)
    row = conn.execute("SELECT name FROM assets WHERE project_id = ? AND sha256 = ?",
                       (pid, digest)).fetchone()
    if row:
        return {"name": row["name"], "sha256": digest}
    n = conn.execute("SELECT COUNT(*) FROM assets WHERE project_id = ?", (pid,)).fetchone()[0]
    if n >= MAX_ASSETS:
        raise ShapeError(f"A project holds at most {MAX_ASSETS} images: remove one first.",
                         "too_many")
    name = (_NAME_RE.sub("_", (filename or "").rsplit("/", 1)[-1]).strip() or
            f"image.{MIME_EXT[mime]}")[:120]
    conn.execute("INSERT INTO assets (project_id, name, sha256, mime, size, data, created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (pid, name, digest, mime, len(data), data, db.now()))
    conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (db.now(), pid))
    return {"name": name, "sha256": digest}


def adopt(conn, owner_id: int, parsed: dict, target_id=None) -> int:
    """Create a project from :func:`package.read_for_adopt` output."""
    m = parsed["manifest"]
    kind = m["kind"]
    draft = empty_draft(kind)
    if kind == "app":
        draft["app"] = json.loads(parsed["files"]["app.json"].decode("utf-8"))
    else:
        draft["files"] = {n: parsed["files"][n].decode("utf-8") for n in CONTENT_FILES}
        draft["datasets"] = [str(x) for x in m.get("datasets") or []][:MAX_LIST]
        draft["params"] = [x for x in m.get("params") or [] if isinstance(x, dict)][:MAX_LIST]
    try:
        validate_patch({k: draft[k] for k in ("files", "datasets", "params", "app")})
    except ShapeError as exc:
        raise PackageError(f"The package cannot be adopted: {exc.message}") from None
    pid = create(conn, owner_id, name=m["name"], kind=kind, slug=m["slug"],
                 version=m["version"], icon=m["icon"],
                 description=str(m.get("description") or "")[:500],
                 target_id=target_id, draft=draft)
    for a in parsed["assets"][:MAX_ASSETS]:
        add_asset(conn, pid, f"{a['sha256'][:12]}.{MIME_EXT[a['mime']]}", a["data"])
    return pid
