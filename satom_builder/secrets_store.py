"""Secrets at rest (Fernet, ``FERNET_KEY``) and redaction.

Secrets the Builder holds: AI provider keys, per-author SATOM tokens and the
directory bind password / RADIUS shared secret. They are encrypted in SQLite,
WRITE-ONLY in the UI (a page only ever says "set" or "not set") and redacted
from every error and log line.

Every plaintext that :func:`decrypt` returns is remembered (process-wide) so
the logging filter and :func:`redact` can scrub it even from messages that a
third-party library built.
"""
from __future__ import annotations

import logging
import re
import threading

from cryptography.fernet import Fernet, InvalidToken

_lock = threading.Lock()
_known: set[str] = set()
_fernet_cache: dict[str, Fernet] = {}

#: Shapes that are always scrubbed, whatever their value.
_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s'\"]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(sk-ant-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]+"),
    re.compile(r"(sk-[A-Za-z0-9]{2})[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)((?:x-api-key|api[_-]?key|password|secret|token)\s*[\"']?\s*[:=]\s*[\"']?)[^\s'\",}]{4,}"),
]
MASK = "[redacted]"


class SecretError(ValueError):
    pass


def _fernet(key: str) -> Fernet:
    f = _fernet_cache.get(key)
    if f is None:
        try:
            f = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise SecretError("FERNET_KEY is not a valid Fernet key.") from exc
        _fernet_cache[key] = f
    return f


def _key() -> str:
    from flask import current_app
    return current_app.config["FERNET_KEY"]


def remember(value: str) -> None:
    if value and len(value) >= 4:
        with _lock:
            _known.add(value)


def encrypt(plaintext: str, key: str | None = None) -> str:
    if not plaintext:
        return ""
    remember(plaintext)
    return _fernet(key or _key()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str, key: str | None = None) -> str:
    if not token:
        return ""
    try:
        value = _fernet(key or _key()).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretError("A stored secret cannot be decrypted (was FERNET_KEY "
                          "changed?). Set it again.") from exc
    remember(value)
    return value


def redact(text, extra=()) -> str:
    """Scrub every known secret, the given ``extra`` values and secret-looking
    shapes (Bearer headers, API keys) from ``text``."""
    if text is None:
        return ""
    s = str(text)
    with _lock:
        values = set(_known)
    values.update(v for v in extra if v)
    for v in sorted(values, key=len, reverse=True):
        if len(v) >= 4 and v in s:
            s = s.replace(v, MASK)
    for pat in _PATTERNS:
        s = pat.sub(lambda m: m.group(1) + MASK, s)
    return s


class RedactingFilter(logging.Filter):
    """Installed on the root logger's handlers: no record leaves with a secret."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        if record.exc_info and record.exc_info[1] is not None:
            # the traceback text is formatted later; pre-format and scrub it
            import traceback
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def install_log_filter() -> None:
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
