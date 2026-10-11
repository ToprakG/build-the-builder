"""The build trace the kit asks for: trace.txt (readable) and events.jsonl (one JSON object per event).

It records the start and end time, every model call (model + one-line purpose, tokens and cost when known), every
deterministic check, what was created on Freya, every self-test run (or "no self-test"), and the totals.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def short(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Trace:
    def __init__(self, directory: Path, header: dict | None = None):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events = (self.dir / "events.jsonl").open("w", encoding="utf-8")
        self.lines: list[str] = []
        self.started = _now()
        self.t0 = time.time()
        self.tokens_in = self.tokens_out = 0
        self.cost = 0.0
        self.model_calls = 0
        self.closed = False
        self.event("start", time=self.started, **(header or {}))
        self.line(f"start {self.started}")
        for k, v in (header or {}).items():
            self.line(f"  {k}: {short(v, 300)}")

    # --- low level ---------------------------------------------------------------------------------------------
    def event(self, kind: str, **data):
        if self.closed:
            return
        self._events.write(json.dumps({"type": kind, "t": round(time.time() - self.t0, 3), **data},
                                      default=str, ensure_ascii=False) + "\n")
        self._events.flush()

    def line(self, text: str):
        self.lines.append(f"{time.strftime('%H:%M:%S')} {text}")
        self._flush_txt()

    def _flush_txt(self):
        (self.dir / "trace.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    # --- typed events --------------------------------------------------------------------------------------------
    def model_call(self, model: str, purpose: str, tokens_in: int | None = None, tokens_out: int | None = None,
                   cost_usd: float | None = None, seconds: float | None = None, error: str | None = None):
        self.model_calls += 1
        self.tokens_in += tokens_in or 0
        self.tokens_out += tokens_out or 0
        self.cost += cost_usd or 0.0
        bits = [f"model call {model}: {purpose}"]
        if tokens_in is not None or tokens_out is not None:
            bits.append(f"tokens in={tokens_in} out={tokens_out}")
        if cost_usd is not None:
            bits.append(f"cost=${cost_usd:.4f}")
        if seconds is not None:
            bits.append(f"{seconds:.1f}s")
        if error:
            bits.append(f"ERROR {short(error, 200)}")
        self.line(" | ".join(bits))
        self.event("model_call", model=model, purpose=purpose, tokens_in=tokens_in, tokens_out=tokens_out,
                   cost_usd=cost_usd, seconds=seconds, error=error)

    def check(self, name: str, ok: bool, detail="") -> None:
        self.line(f"check {name}: {'ok' if ok else 'FAIL'} {short(detail, 200) if detail else ''}".rstrip())
        self.event("check", name=name, ok=ok, detail=detail)

    def freya(self, action: str, detail) -> None:
        self.line(f"freya {action}: {short(detail, 300)}")
        self.event("freya", action=action, detail=detail)

    def selftest(self, detail) -> None:
        self.line(f"self-test: {short(detail, 300)}")
        self.event("selftest", detail=detail)

    def warn(self, text: str) -> None:
        self.line(f"WARNING {text}")
        self.event("warning", text=text)

    def close(self, status: str, **summary):
        if self.closed:
            return
        ended = _now()
        self.line(f"end {ended} status={status} wall={time.time() - self.t0:.1f}s model_calls={self.model_calls} "
                  f"tokens_in={self.tokens_in} tokens_out={self.tokens_out} cost=${self.cost:.4f}")
        self.event("end", time=ended, status=status, model_calls=self.model_calls, tokens_in=self.tokens_in,
                   tokens_out=self.tokens_out, cost_usd=self.cost, **summary)
        self.closed = True
        self._events.close()


class NullTrace(Trace):
    """A trace that writes nothing (tests and helpers)."""

    def __init__(self):  # noqa: D401 - no files
        self.lines, self.closed = [], False
        self.tokens_in = self.tokens_out = self.model_calls = 0
        self.cost, self.t0, self.events = 0.0, time.time(), []

    def event(self, kind, **data):
        self.events.append({"type": kind, **data})

    def line(self, text):
        self.lines.append(text)

    def _flush_txt(self):
        pass

    def close(self, status, **summary):
        self.closed = True
