"""What an AI job is doing, for the page that follows it.

Generate and the assistant chat used to show "Generating…" for minutes with
nothing behind it. A :class:`Progress` keeps

* ``steps``: what the loop did, in order, each with its time and state
  (``run`` while it happens, then ``ok`` / ``warn`` / ``bad``): the prompt and
  its size, the model reading it, the answer read, the package checked with
  SATOM's rules, the problems found, the repair round;
* ``live``: the model call that is running -- its round, the model, the phase
  (``reading`` the prompt until the first piece arrives, ``thinking``,
  ``writing``, ``done``), the tokens and their rate, and the last
  :data:`TAIL` characters of the thinking and of the answer.

The adapters stream into it (``delta`` / ``usage``); ``save`` persists a
snapshot (SQLite row, polled by the page), at most once per ``every`` seconds
while text streams and at once on every step. Nothing of it is secret: it
holds what the model writes, never the prompt.
"""
from __future__ import annotations

import time

TAIL = 6000
MAX_STEPS = 120
STEP_TEXT = 400


class Progress:
    def __init__(self, save=None, *, every: float = 1.0, clock=time.time):
        self._save = save
        self._every = every
        self._clock = clock
        self._last = 0.0
        self.started = clock()
        self.steps: list[dict] = []
        self.live: dict = {}

    # -- steps -------------------------------------------------------------
    def step(self, text: str, state: str = "run", detail: str = "") -> None:
        """A new step; the one still running ends ``ok``."""
        self._close("ok")
        self.steps.append({"at": self._clock(), "text": str(text)[:STEP_TEXT], "state": state,
                           "detail": str(detail)[:2000]})
        del self.steps[:-MAX_STEPS]
        self.flush(force=True)

    def end(self, state: str = "ok", detail: str = "") -> None:
        """End the running step with *state* (and an optional detail)."""
        self._close(state, detail)
        self.flush(force=True)

    def _close(self, state: str, detail: str = "") -> None:
        if self.steps and self.steps[-1]["state"] == "run":
            s = self.steps[-1]
            s["state"], s["took"] = state, round(self._clock() - s["at"], 1)
            if detail:
                s["detail"] = str(detail)[:2000]

    # -- the model call ----------------------------------------------------
    def model(self, *, round_no: int, rounds: int, model: str) -> None:
        self.live = {"round": round_no, "rounds": rounds, "model": str(model)[:120],
                     "phase": "reading", "started": self._clock(), "first_at": None,
                     "ended": None, "pieces": 0, "tokens": None, "thinking_chars": 0,
                     "answer_chars": 0, "thinking": "", "answer": ""}
        self.flush(force=True)

    def delta(self, kind: str, text: str) -> None:
        """A piece the model wrote: ``thinking`` or ``answer``."""
        if not text or not self.live:
            return
        lv = self.live
        now = self._clock()
        if lv["first_at"] is None:
            lv["first_at"] = now
        key = "thinking" if kind == "thinking" else "answer"
        lv["phase"] = "thinking" if key == "thinking" else "writing"
        lv["pieces"] += 1
        lv[key + "_chars"] += len(text)
        lv[key] = (lv[key] + text)[-TAIL:]
        self.flush()

    def usage(self, tokens: int) -> None:
        if self.live:
            self.live["tokens"] = int(tokens)

    def model_done(self) -> None:
        if self.live:
            self.live["phase"], self.live["ended"] = "done", self._clock()
        self.flush(force=True)

    # -- persistence -------------------------------------------------------
    def snapshot(self) -> dict:
        now = self._clock()
        live = dict(self.live) if self.live else None
        if live:
            end = live["ended"] or now
            first = live["first_at"]
            live["elapsed"] = round(end - live["started"], 1)
            live["wait"] = round((first or end) - live["started"], 1)
            n = live["tokens"] or live["pieces"]
            live["estimated"] = live["tokens"] is None
            live["count"] = n
            live["rate"] = round(n / (end - first), 1) if first and end - first > 0.5 else None
        return {"started": self.started, "now": now, "steps": [dict(s) for s in self.steps],
                "live": live}

    def flush(self, force: bool = False) -> None:
        if self._save is None:
            return
        now = self._clock()
        if not force and now - self._last < self._every:
            return
        self._last = now
        try:
            self._save(self.snapshot())
        except Exception:  # noqa: BLE001 -- progress never breaks the job
            pass
