"""The two AI adapters (design section 2, decision 6).

* ``anthropic`` -- the official ``anthropic`` Python SDK, streaming
  (``client.messages.stream(...)``, its events to the progress sink, then
  ``.get_final_message()``). No sampling parameters
  (``temperature``/``top_p``/``top_k`` are a 400 since 2026-10-21), no
  assistant prefill; on the families whose thinking is on by default
  (``claude-opus-5-5``...) ``thinking={"type": "adaptive", "display":
  "summarized"}`` so the summary streams (the default "omitted" streams empty
  thinking), ``output_config={"effort": ...}``, the
  fixed system block marked ``cache_control``. Against Anthropic's
  first-party API, server-side refusal fallbacks are on by default
  (``client.beta.messages.stream(..., betas=["server-side-fallback-2026-07-01"],
  fallbacks="default")``); for any other base URL they are off.
* ``openai_compatible`` -- ``httpx`` POST ``{base_url}/chat/completions`` with
  ``stream: true`` (server-sent events; a server that answers in one JSON body
  is read too): OpenAI, Azure OpenAI, Mistral, Gemini's compatible endpoint,
  Ollama, vLLM, LM Studio. The Bearer key is optional (local servers need none).

Errors become :class:`AIError` with the provider's message and the key
redacted.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from .secrets_store import redact

ADAPTERS = ("anthropic", "openai_compatible")
DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 64000
EFFORTS = ("low", "medium", "high", "xhigh", "max")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FIRST_PARTY_HOSTS = ("api.anthropic.com",)
MAX_ANSWER = 4 * 1024 * 1024
# families whose thinking is on by default with its text "omitted": the
# request asks for display "summarized" (older models are left as they are)
SUMMARIZED_THINKING = ("claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5",
                       "claude-haiku-5")


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


class AIError(Exception):
    def __init__(self, message: str, code: str = "ai_error"):
        super().__init__(message)
        self.message = message
        self.code = code


def is_first_party(base_url: str) -> bool:
    if not base_url:
        return True
    host = (urlsplit(base_url).hostname or "").lower()
    return host in FIRST_PARTY_HOSTS


def _anthropic_event(event, sink) -> None:
    et = getattr(event, "type", "")
    if et == "content_block_delta":
        d = event.delta
        dt = getattr(d, "type", "")
        if dt == "thinking_delta" and getattr(d, "thinking", ""):
            sink.delta("thinking", d.thinking)
        elif dt == "text_delta" and getattr(d, "text", ""):
            sink.delta("answer", d.text)
    elif et == "message_delta":
        n = getattr(getattr(event, "usage", None), "output_tokens", None)
        if n:
            sink.usage(int(n))


class AnthropicAdapter:
    """``profile``: dict with model, key, base_url, max_tokens, effort, fallbacks."""

    def __init__(self, profile: dict, client_factory=None, timeout: float = 900.0):
        self.p = profile
        self.key = profile.get("key") or ""
        self.timeout = timeout
        if client_factory is None:
            import anthropic
            client_factory = anthropic.Anthropic
        self._factory = client_factory

    @property
    def uses_fallbacks(self) -> bool:
        return bool(self.p.get("fallbacks", True)) and is_first_party(self.p.get("base_url") or "")

    def make_client(self):
        if not self.key:
            raise AIError("This AI profile has no API key set.", "ai_no_key")
        kw = {"api_key": self.key, "timeout": self.timeout, "max_retries": 2}
        if self.p.get("base_url"):
            kw["base_url"] = self.p["base_url"]
        return self._factory(**kw)

    def request(self, system: str, user: str, *, max_tokens: int | None = None,
                effort: str | None = None, images: list | None = None) -> dict:
        """The exact keyword arguments sent to the SDK's ``stream()``.
        *images*: ``[{"mime", "data": bytes}]`` attached to the user turn."""
        content = user
        if images:
            content = [{"type": "image", "source": {"type": "base64", "media_type": im["mime"],
                                                    "data": _b64(im["data"])}} for im in images]
            content.append({"type": "text", "text": user})
        kw = {
            "model": self.p.get("model") or DEFAULT_MODEL,
            "max_tokens": int(max_tokens or self.p.get("max_tokens") or DEFAULT_MAX_TOKENS),
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": content}],
            "output_config": {"effort": effort or self.p.get("effort") or "high"},
        }
        if kw["model"].startswith(SUMMARIZED_THINKING):
            # thinking is on by default there and its text "omitted": ask for
            # the summary, so the author sees what the model is working on
            kw["thinking"] = {"type": "adaptive", "display": "summarized"}
        if self.uses_fallbacks:
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def complete(self, system: str, user: str, *, sink=None, **over) -> str:
        """*sink* (``delta(kind, text)``, ``usage(tokens)``) sees the thinking
        summary (``thinking``) and the answer (``answer``) as they stream."""
        import anthropic
        kw = self.request(system, user, **over)
        client = self.make_client()
        try:
            if "fallbacks" in kw:
                stream_cm = client.beta.messages.stream(**kw)
            else:
                stream_cm = client.messages.stream(**kw)
            with stream_cm as stream:
                if sink is not None:
                    for event in stream:
                        _anthropic_event(event, sink)
                msg = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise AIError("The AI provider rejected the API key of this profile.",
                          "ai_auth") from None
        except anthropic.PermissionDeniedError as exc:
            raise AIError("The API key may not use this model or feature: "
                          + self._clean(exc), "ai_forbidden") from None
        except anthropic.NotFoundError as exc:
            raise AIError(f"Model or endpoint not found ({kw['model']}): "
                          + self._clean(exc), "ai_not_found") from None
        except anthropic.RateLimitError as exc:
            retry = ""
            try:
                retry = exc.response.headers.get("retry-after") or ""
            except Exception:  # noqa: BLE001
                pass
            raise AIError("The AI provider is rate-limiting this key"
                          + (f"; retry after {retry} s." if retry else "; retry later."),
                          "ai_rate_limited") from None
        except anthropic.BadRequestError as exc:
            raise AIError("The AI provider refused the request: " + self._clean(exc),
                          "ai_bad_request") from None
        except anthropic.APIStatusError as exc:
            raise AIError(f"The AI provider failed (HTTP {exc.status_code}): "
                          + self._clean(exc), "ai_provider") from None
        except anthropic.APITimeoutError:
            raise AIError("The AI provider did not answer in time.", "ai_timeout") from None
        except anthropic.APIConnectionError as exc:
            raise AIError("Cannot reach the AI provider: " + self._clean(exc),
                          "ai_unreachable") from None
        finally:
            close = getattr(client, "close", None)
            if close:
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        return self._text(msg)

    def _clean(self, exc) -> str:
        msg = getattr(exc, "message", None) or str(exc)
        return redact(msg, extra=[self.key])[:800]

    @staticmethod
    def _text(msg) -> str:
        reason = getattr(msg, "stop_reason", None)
        if reason == "refusal":
            details = getattr(msg, "stop_details", None)
            cat = getattr(details, "category", None) if details is not None else None
            raise AIError("The model declined the request (refusal category: "
                          f"{cat or 'unspecified'}).", "ai_refusal")
        if reason == "max_tokens":
            raise AIError("The output was too long: raise max tokens in the AI profile.",
                          "ai_max_tokens")
        parts = [getattr(b, "text", "") for b in (getattr(msg, "content", None) or [])
                 if getattr(b, "type", "") == "text"]
        text = "".join(parts)
        if not text.strip():
            raise AIError("The model returned no text.", "ai_empty")
        return text


class OpenAICompatibleAdapter:
    def __init__(self, profile: dict, transport: httpx.BaseTransport | None = None,
                 timeout: float = 900.0):
        self.p = profile
        self.key = profile.get("key") or ""
        self.transport = transport
        self.timeout = timeout

    def url(self) -> str:
        base = (self.p.get("base_url") or "").rstrip("/")
        if not base:
            raise AIError("An OpenAI-compatible profile needs a base URL "
                          "(for example https://api.openai.com/v1 or http://127.0.0.1:11434/v1).",
                          "ai_no_url")
        return base + "/chat/completions"

    def request(self, system: str, user: str, *, max_tokens: int | None = None,
                effort: str | None = None, images: list | None = None) -> dict:
        content = user
        if images:
            # OpenAI's vision shape, which Ollama, vLLM and LM Studio accept for
            # a model that reads images; a text-only model refuses it.
            content = [{"type": "text", "text": user}] + [
                {"type": "image_url", "image_url": {
                    "url": "data:%s;base64,%s" % (im["mime"], _b64(im["data"]))}}
                for im in images]
        return {"model": self.p.get("model") or "",
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": content}],
                "max_tokens": int(max_tokens or self.p.get("max_tokens") or DEFAULT_MAX_TOKENS),
                "stream": True}

    def complete(self, system: str, user: str, *, sink=None, **over) -> str:
        """The answer text. *sink* (``delta(kind, text)``, ``usage(tokens)``)
        sees the reasoning (``thinking``) and the answer (``answer``) as they
        arrive. A server that ignores ``stream`` answers in one JSON body:
        read the same way, without live pieces."""
        body = self.request(system, user, **over)
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.key:
            headers["Authorization"] = "Bearer " + self.key
        kw = {"timeout": httpx.Timeout(self.timeout, connect=15.0)}
        if self.transport is not None:
            kw["transport"] = self.transport
        try:
            with httpx.Client(**kw) as c:
                with c.stream("POST", self.url(), json=body, headers=headers) as resp:
                    ctype = resp.headers.get("content-type", "")
                    if resp.status_code < 400 and "text/event-stream" in ctype:
                        return self._from_stream(resp, sink)
                    raw = b""
                    for chunk in resp.iter_bytes():
                        raw += chunk
                        if len(raw) > MAX_ANSWER:
                            raise AIError("The AI provider's answer is too large.", "ai_too_large")
        except httpx.TimeoutException:
            raise AIError("The AI provider did not answer in time.", "ai_timeout") from None
        except httpx.HTTPError as exc:
            raise AIError("Cannot reach the AI provider: " + self._clean(exc),
                          "ai_unreachable") from None
        return self._from_json(resp.status_code, raw)

    def _from_json(self, status: int, raw: bytes) -> str:
        try:
            j = json.loads(raw)
        except ValueError:
            j = None
        if status >= 400:
            self._raise_status(status, j, raw.decode("utf-8", "replace")[:300])
        try:
            choice = j["choices"][0]
            text = choice["message"]["content"]
        except (TypeError, KeyError, IndexError):
            raise AIError("The AI provider's answer has no choices[0].message.content.",
                          "ai_bad_answer") from None
        msg = choice.get("message") or {}
        reasoned = any(msg.get(k) for k in ("reasoning", "reasoning_content"))
        return self._finish(text, choice.get("finish_reason"), reasoned)

    def _from_stream(self, resp, sink) -> str:
        """Server-sent events: ``data: {chunk}`` lines, ``data: [DONE]`` last.
        The reasoning comes in ``delta.reasoning`` (Ollama: gpt-oss, qwen3) or
        ``delta.reasoning_content`` (vLLM, DeepSeek); a server without a
        reasoning parser writes it into the content inside ``<think>``."""
        answer, finish, size, reasoned = [], None, 0, False
        inline = _InlineThink()
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            if not isinstance(j, dict):
                continue
            if j.get("error"):
                self._raise_status(500, j, "")
            usage = j.get("usage")
            if sink is not None and isinstance(usage, dict) and usage.get("completion_tokens"):
                sink.usage(int(usage["completion_tokens"]))
            choices = j.get("choices") or []
            ch = choices[0] if choices and isinstance(choices[0], dict) else {}
            d = ch.get("delta") or {}
            r = d.get("reasoning") or d.get("reasoning_content") or ""
            t = d.get("content") or ""
            if isinstance(t, list):
                t = "".join(p.get("text", "") for p in t if isinstance(p, dict))
            size += len(r) + len(t)
            if size > MAX_ANSWER:
                raise AIError("The AI provider's answer is too large.", "ai_too_large")
            if r:
                reasoned = True
                if sink is not None:
                    sink.delta("thinking", r)
            if t:
                answer.append(t)
                if sink is not None:
                    sink.delta(inline.kind(t), t)
            if ch.get("finish_reason"):
                finish = ch["finish_reason"]
        return self._finish("".join(answer), finish, reasoned)

    def _raise_status(self, status: int, j, text: str):
        msg = ""
        if isinstance(j, dict):
            e = j.get("error")
            msg = (e.get("message") if isinstance(e, dict) else e) or j.get("message") or ""
        msg = self._clean(msg or text)
        if status == 401:
            raise AIError("The AI provider rejected the API key of this profile."
                          + (f" ({msg})" if msg else ""), "ai_auth")
        if status == 429:
            raise AIError("The AI provider is rate-limiting this key; retry later.",
                          "ai_rate_limited")
        raise AIError(f"The AI provider failed (HTTP {status}): {msg}", "ai_provider")

    @staticmethod
    def _finish(text, finish_reason, reasoned: bool) -> str:
        if finish_reason == "length":
            if not (text or "").strip() and reasoned:
                # Ollama, vLLM with a reasoning parser, DeepSeek's API: the
                # thinking comes in its own field and used the whole budget
                raise AIError("The model spent all of max tokens reasoning and wrote no answer: "
                              "raise max tokens in the AI profile.", "ai_max_tokens")
            raise AIError("The output was too long: raise max tokens in the AI profile.",
                          "ai_max_tokens")
        if isinstance(text, list):   # some servers return content parts
            text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
        if not isinstance(text, str) or not text.strip():
            raise AIError("The model returned no text.", "ai_empty")
        answer, _ = strip_reasoning(text)
        if not answer.strip():
            raise AIError("The model returned only its reasoning, no answer: raise max tokens "
                          "in the AI profile (reasoning models such as DeepSeek-R1 or Qwen3 "
                          "spend part of it thinking).", "ai_empty")
        return answer

    def _clean(self, exc) -> str:
        return redact(str(exc), extra=[self.key])[:800]


_THINK_OPEN = re.compile(r"^\s*<think\s*>", re.I)
_THINK_CLOSE = re.compile(r"</think\s*>", re.I)


class _InlineThink:
    """Live pieces of a content that opens with ``<think>``: ``thinking``
    until ``</think>``, then ``answer`` (what :func:`strip_reasoning` cuts)."""

    def __init__(self):
        self.head = ""
        self.state = None           # None: undecided, "thinking", "answer"

    def kind(self, piece: str) -> str:
        if self.state == "answer":
            return "answer"
        self.head = (self.head + piece)[-4096:] if self.state else self.head + piece
        if self.state is None:
            s = self.head.lstrip().lower()
            if not s or (len(s) < 6 and "<think".startswith(s)):
                return "thinking"
            self.state = "thinking" if s.startswith("<think") else "answer"
        if self.state == "thinking" and _THINK_CLOSE.search(self.head):
            self.state = "answer"
            return "thinking"
        return self.state


def strip_reasoning(text: str) -> tuple[str, bool]:
    """Drop the reasoning a self-hosted reasoning model (DeepSeek-R1, Qwen3,
    QwQ) writes before its answer when the server has no reasoning parser.

    ``<think>...</think>answer`` -> ``answer``; an unterminated ``<think>...``
    (the budget ran out while thinking) -> ``""``. DeepSeek-R1's chat template
    opens ``<think>`` inside the prompt, so its answer is ``...</think>answer``:
    that form is cut only when the text does not already start as an answer
    (``{`` or a code fence), so a JSON answer that mentions ``</think>`` as
    data is left alone. Returns (answer, whether reasoning was removed)."""
    m = _THINK_OPEN.match(text)
    if m:
        close = _THINK_CLOSE.search(text, m.end())
        return (text[close.end():] if close else ""), True
    head = text.lstrip()
    if not head.startswith(("{", "```")):
        close = _THINK_CLOSE.search(text)
        if close:
            return text[close.end():], True
    return text, False


def make_adapter(profile: dict, *, anthropic_factory=None, transport=None,
                 timeout: float = 900.0):
    if profile.get("adapter") == "anthropic":
        return AnthropicAdapter(profile, client_factory=anthropic_factory, timeout=timeout)
    if profile.get("adapter") == "openai_compatible":
        return OpenAICompatibleAdapter(profile, transport=transport, timeout=timeout)
    raise AIError(f"Unknown adapter {profile.get('adapter')!r}.", "ai_adapter")


def test_prompt(adapter) -> str:
    """The Test button: a tiny prompt with a small budget."""
    return adapter.complete("You are a connectivity check.",
                            "Reply with the single word: pong", max_tokens=1024,
                            effort="low")


def describe_request(adapter, system: str, user: str) -> str:
    """The request as JSON, for tests and the preview (no key inside)."""
    return json.dumps(adapter.request(system, user), ensure_ascii=False)
