"""The assistant chat: conversations about one project, kept in SQLite.

* A conversation belongs to one project and one author. Its messages, their
  status and the drafts the model proposed are rows (``chat_messages``), so
  closing the page loses nothing: a reply that is being written goes on in a
  server thread, and the page picks it up again. A restart while a reply
  runs marks it failed with a sentence saying so (``fail_stale``).
* Files: images (PNG, JPEG, GIF, WebP, sniffed from their bytes, never their
  name) go to the model as images -- only a model that reads images can use
  them -- and text files (UTF-8: code, JSON, CSV, YAML, logs...) go into the
  prompt under "Attached files", cut to :data:`TEXT_IN_PROMPT` each.
* A reply runs the same loop as Generate (``ai_loop.run`` in chat mode):
  the model gets the author guide, the target summary, the CURRENT saved
  draft, the conversation so far and the new message; its draft is checked by
  the rules pack and repaired up to the round limit. The proposed draft is
  stored on the reply and reaches the project only when the author applies
  it (a normal save: the previous draft is kept as a revision).

Never sent: data rows, secrets (``ai_loop`` builds the prompt from the same
allow-lists as Generate) and other authors' conversations.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback

from . import ai_loop, db
from .package import sniff_image
from .progress import Progress
from .secrets_store import redact

MAX_IMAGE = 5 * 1024 * 1024
MAX_TEXT_FILE = 512 * 1024
MAX_FILES_PER_CHAT = 30
MAX_FILES_PER_MESSAGE = 8
MAX_IMAGES_PER_MESSAGE = 4
TEXT_IN_PROMPT = 64 * 1024
HISTORY_MESSAGES = 12
HISTORY_CHARS = 4000
MAX_MESSAGE = 8000
TEXT_SUFFIXES = (".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".log",
                 ".py", ".js", ".css", ".html", ".jinja", ".j2", ".sql", ".ini", ".conf",
                 ".cfg", ".toml", ".sh")


class ChatError(ValueError):
    def __init__(self, message: str, code: str = "bad_request", status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


def classify(name: str, data: bytes) -> tuple[str, str]:
    """-> (kind, mime) of an uploaded file. Raises ChatError."""
    mime = sniff_image(data)
    if mime:
        if len(data) > MAX_IMAGE:
            raise ChatError("An image may be at most 5 MB.", "too_large", 413)
        return "image", mime
    low = (name or "").lower()
    if not low.endswith(TEXT_SUFFIXES):
        raise ChatError("Attach an image (PNG, JPEG, GIF, WebP) or a text file ("
                        + ", ".join(TEXT_SUFFIXES) + ").", "bad_file", 415)
    if len(data) > MAX_TEXT_FILE:
        raise ChatError("A text file may be at most 512 KB.", "too_large", 413)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise ChatError("The text file is not UTF-8.", "bad_file", 415) from None
    if b"\x00" in data:
        raise ChatError("The text file holds binary data.", "bad_file", 415)
    return "text", "text/plain; charset=utf-8"


def clean_name(name: str) -> str:
    n = (name or "file").replace("\\", "/").rsplit("/", 1)[-1].strip()
    n = "".join(ch for ch in n if ch.isprintable())[:120]
    return n or "file"


def add_file(conn, chat_id: int, name: str, data: bytes) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM chat_files WHERE chat_id = ?",
                         (chat_id,)).fetchone()[0]
    if count >= MAX_FILES_PER_CHAT:
        raise ChatError(f"A conversation holds at most {MAX_FILES_PER_CHAT} files.", "too_many", 409)
    kind, mime = classify(name, data)
    sha = hashlib.sha256(data).hexdigest()
    cur = conn.execute("INSERT INTO chat_files (chat_id, name, mime, kind, size, sha256, data, "
                       "created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (chat_id, clean_name(name), mime, kind, len(data), sha, data, db.now()))
    return {"id": cur.lastrowid, "name": clean_name(name), "mime": mime, "kind": kind,
            "size": len(data)}


def file_meta(row) -> dict:
    return {"id": row["id"], "name": row["name"], "mime": row["mime"], "kind": row["kind"],
            "size": row["size"]}


def message_answer(row, files: dict | None = None, progress: dict | None = None) -> dict:
    ids = db.loads(row["files"], []) or []
    return {"id": row["id"], "role": row["role"], "content": row["content"],
            "progress": progress,
            "files": [files[i] for i in ids if files and i in files] if files else ids,
            "status": row["status"], "rounds": db.loads(row["rounds"], []) or [],
            "has_draft": row["draft"] is not None, "applied_at": row["applied_at"],
            "error": row["error"], "profile": row["profile"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def chat_answer(conn, chat) -> dict:
    files = {r["id"]: file_meta(r) for r in conn.execute(
        "SELECT id, name, mime, kind, size FROM chat_files WHERE chat_id = ?", (chat["id"],))}
    msgs = conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id",
                        (chat["id"],)).fetchall()
    progress = {r["message_id"]: ai_loop.with_now(db.loads(r["data"], None)) for r in conn.execute(
        "SELECT p.message_id, p.data FROM chat_progress p JOIN chat_messages m "
        "ON m.id = p.message_id WHERE m.chat_id = ?", (chat["id"],))}
    return {"id": chat["id"], "project_id": chat["project_id"], "title": chat["title"],
            "profile_id": chat["profile_id"], "created_at": chat["created_at"],
            "updated_at": chat["updated_at"], "files": list(files.values()),
            "messages": [message_answer(m, files, progress.get(m["id"])) for m in msgs],
            "running": any(m["status"] == "running" for m in msgs)}


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------
def conversation_text(history: list, new_text: str, text_files: list) -> str:
    """The author's side of the prompt: the conversation so far (last
    :data:`HISTORY_MESSAGES`, each cut), the new message and its text files."""
    parts = []
    past = [m for m in history if m["status"] == "done" or m["role"] == "user"][-HISTORY_MESSAGES:]
    if past:
        parts.append("## The conversation so far\n")
        for m in past:
            who = "Author" if m["role"] == "user" else "You (assistant)"
            text = (m["content"] or "").strip()
            if len(text) > HISTORY_CHARS:
                text = text[:HISTORY_CHARS] + " [...]"
            if m["role"] == "assistant" and m["applied_at"]:
                text += " [the author applied this draft]"
            parts.append(f"**{who}:** {text}\n")
    parts.append("## The author's new message\n\n" + new_text.strip() + "\n")
    if text_files:
        parts.append("## Attached files\n")
        for f in text_files:
            body = bytes(f["data"]).decode("utf-8", "replace")
            cut = len(body) > TEXT_IN_PROMPT
            body = body[:TEXT_IN_PROMPT]
            parts.append(f"### {f['name']}{' (cut)' if cut else ''}\n\n````\n{body}\n````\n")
    return "\n".join(parts)


# --------------------------------------------------------------------------
# replies
# --------------------------------------------------------------------------
def start_reply(app, *, chat_id: int, user_id: int, text: str, file_ids: list, adapter,
                pack, snap: dict, project: dict, assets: list, max_rounds: int,
                profile_label: str, test_schema: str = "", render_check=None) -> dict:
    """Store the author's message and a running reply, then write the reply
    in a thread. -> {"user": id, "reply": id}."""
    with db.session(app.config["DB_PATH"]) as conn:
        history = conn.execute("SELECT * FROM chat_messages WHERE chat_id = ? ORDER BY id",
                               (chat_id,)).fetchall()
        files = conn.execute("SELECT * FROM chat_files WHERE chat_id = ? AND id IN (%s)"
                             % ",".join("?" * len(file_ids)), (chat_id, *file_ids)
                             ).fetchall() if file_ids else []
        t = db.now()
        mu = conn.execute("INSERT INTO chat_messages (chat_id, role, content, files, status, "
                          "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                          (chat_id, "user", text, db.dumps([f["id"] for f in files]), "done", t, t)
                          ).lastrowid
        mr = conn.execute("INSERT INTO chat_messages (chat_id, role, content, status, profile, "
                          "runner, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                          (chat_id, "assistant", "", "running", profile_label, runner_id(), t, t)
                          ).lastrowid
        title = conn.execute("SELECT title FROM chats WHERE id = ?", (chat_id,)).fetchone()["title"]
        conn.execute("UPDATE chats SET updated_at = ?, title = ? WHERE id = ?",
                     (t, title or text.strip().splitlines()[0][:80], chat_id))
    text_files = [dict(f) for f in files if f["kind"] == "text"]
    author_text = conversation_text([dict(h) for h in history], text, text_files)
    if test_schema:
        author_text += ("\n## The project's test database\n\n" + test_schema + "\n\nThe "
                        "author runs read-only SQLite SELECTs over it in the Database tab: "
                        "when asked about the data, answer with a query they can run.\n")
    images = [{"mime": f["mime"], "data": bytes(f["data"])} for f in files if f["kind"] == "image"]

    def work():
        with db.session(app.config["DB_PATH"]) as conn:
            def on_round(rounds):
                conn.execute("UPDATE chat_messages SET rounds = ?, updated_at = ? WHERE id = ?",
                             (db.dumps(rounds), db.now(), mr))
            pg = Progress(ai_loop.progress_saver(conn, "chat_progress", mr))
            if text_files or images:
                pg.step(f"Attached: {len(text_files)} text file(s) in the prompt, "
                        f"{len(images)} image(s) sent as images", "ok")
            try:
                res = ai_loop.run(adapter, pack, snap, project, author_text, assets,
                                  max_rounds=max_rounds, on_round=on_round,
                                  min_satom=str(snap.get("satom_version") or ""), chat=True,
                                  images=images or None, progress=pg,
                                  render_check=render_check)
                draft = res.get("project")
                note = res.get("message") or ""
                if not note:
                    note = ("Here is a new draft." if draft else "No change.")
                if draft and not res.get("ok"):
                    last = (res.get("rounds") or [{}])[-1]
                    note += (f"\n\n{len(last.get('problems') or [])} problem(s) are left after "
                             "the repair rounds: they are listed under the reply.")
                conn.execute("UPDATE chat_messages SET status = 'done', content = ?, rounds = ?, "
                             "draft = ?, updated_at = ? WHERE id = ?",
                             (note, db.dumps(res.get("rounds") or []),
                              db.dumps(draft) if draft else None, db.now(), mr))
            except Exception as exc:  # noqa: BLE001 -- shown on the reply, never lost
                msg = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
                if type(exc).__name__ not in ("AIError", "RulesError", "SatomError", "NotReady"):
                    app.logger.error("chat reply %s failed: %s", mr,
                                     redact(traceback.format_exc()[-2000:]))
                pg.step("Failed: " + redact(msg)[:300], "bad")
                conn.execute("UPDATE chat_messages SET status = 'failed', error = ?, "
                             "updated_at = ? WHERE id = ?", (redact(msg)[:2000], db.now(), mr))
            conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (db.now(), chat_id))

    th = threading.Thread(target=work, name=f"chat-{mr}", daemon=True)
    th.start()
    app.extensions.setdefault("satom_builder_threads", {})[f"chat-{mr}"] = th
    return {"user": mu, "reply": mr}


STALE_AFTER = 2 * 3600


def runner_id() -> str:
    """The process writing a reply: its pid and start time (a pid is reused)."""
    return "%d:%s" % (os.getpid(), _start_time(os.getpid()))


def _start_time(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            return fh.read().rsplit(b")", 1)[1].split()[19].decode()
    except (OSError, IndexError):
        return ""


def _alive(runner: str) -> bool:
    try:
        pid, started = runner.split(":", 1)
        return bool(started) and _start_time(int(pid)) == started
    except ValueError:
        return False


def fail_stale(conn) -> int:
    """A reply still 'running' whose process is gone (a restart, a worker that
    died) or that ran longer than :data:`STALE_AFTER` is failed, saying so."""
    n = 0
    for r in conn.execute("SELECT id, runner, updated_at FROM chat_messages "
                          "WHERE status = 'running'").fetchall():
        if _alive(r["runner"]) and db.now() - r["updated_at"] < STALE_AFTER:
            continue
        conn.execute("UPDATE chat_messages SET status = 'failed', error = ?, updated_at = ? "
                     "WHERE id = ? AND status = 'running'",
                     ("The Builder restarted while this reply was being written: send the "
                      "message again.", db.now(), r["id"]))
        n += 1
    return n


def draft_of(row) -> dict | None:
    d = db.loads(row["draft"], None)
    return d if isinstance(d, dict) else None


def history_json(answer: dict) -> str:
    return json.dumps(answer, ensure_ascii=False)
