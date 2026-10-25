"""The Builder update queue, web side (contract section 9).

The web app only WRITES requests (``data/update-requests/<id>.json``) and
READS statuses (``data/update-status/<id>.json``). The root runner, started by
``satom-builder-updater.path`` (DirectoryNotEmpty), does everything else.
A request is written to a temporary file outside the watched directory and
renamed into it, so the runner never sees half a file.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("package", "rollback", "service")
UPLOAD_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".zip")
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_ID = re.compile(r"^[0-9a-f]{32}$")


def dirs(data_dir) -> dict[str, Path]:
    d = Path(data_dir)
    return {"uploads": d / "update-uploads", "requests": d / "update-requests",
            "status": d / "update-status"}


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_upload_name(filename: str) -> str:
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _SAFE.sub("_", base).strip("._") or "update"
    if not base.lower().endswith(UPLOAD_SUFFIXES):
        raise ValueError("An update package is a .tar, .tar.gz, .tgz or .zip file.")
    return base[:120]


def save_upload(data_dir, filename: str, stream, max_bytes: int) -> str:
    """Stream an upload into data/update-uploads/. -> the stored file name."""
    name = f"{uuid.uuid4().hex[:12]}-{safe_upload_name(filename)}"
    up = dirs(data_dir)["uploads"]
    up.mkdir(parents=True, exist_ok=True)
    dest = up / name
    tmp = up / f".{name}.part"
    size = 0
    try:
        with open(tmp, "wb") as fh:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"The update package is larger than "
                                     f"{max_bytes // (1024 * 1024)} MB.")
                fh.write(chunk)
        if size == 0:
            raise ValueError("The file is empty.")
        os.chmod(tmp, 0o640)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return name


def enqueue(data_dir, *, kind: str, requested_by: str, upload_name: str = "",
            allow_downgrade: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown update request kind.")
    if kind == "package":
        if not upload_name or "/" in upload_name or upload_name.startswith("."):
            raise ValueError("A package request needs an uploaded file.")
        if not (dirs(data_dir)["uploads"] / upload_name).is_file():
            raise ValueError("The uploaded file is gone.")
        path = f"data/update-uploads/{upload_name}"
    else:
        path = ""
    req = {"id": uuid.uuid4().hex, "kind": kind, "path": path,
           "requested_by": requested_by, "at": iso_now(),
           "allow_downgrade": bool(allow_downgrade)}
    d = dirs(data_dir)
    d["requests"].mkdir(parents=True, exist_ok=True)
    tmp = Path(data_dir) / f".update-request-{req['id']}.json.tmp"
    tmp.write_text(json.dumps(req, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o640)
    os.replace(tmp, d["requests"] / f"{req['id']}.json")
    return req


def pending(data_dir) -> list[dict]:
    out = []
    rq = dirs(data_dir)["requests"]
    if rq.is_dir():
        for p in sorted(rq.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return out


def statuses(data_dir, limit: int = 50) -> list[dict]:
    st = dirs(data_dir)["status"]
    if not st.is_dir():
        return []
    files = sorted(st.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for p in files[:limit]:
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(j, dict) or not _ID.match(str(j.get("id", ""))):
            continue
        steps = j.get("steps") if isinstance(j.get("steps"), list) else []
        out.append({"id": j["id"], "status": str(j.get("status", "")),
                    "from_version": str(j.get("from_version", "") or ""),
                    "to_version": str(j.get("to_version", "") or ""),
                    "steps": [{"name": str(s.get("name", "")), "ok": bool(s.get("ok")),
                               "detail": str(s.get("detail", ""))[:4000],
                               "at": str(s.get("at", ""))}
                              for s in steps if isinstance(s, dict)]})
    return out
