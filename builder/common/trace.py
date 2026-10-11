"""The build trace the kit asks for: every SDK message (messages.jsonl) and a readable trace.txt.

trace.txt holds the start and end time, one line per model call (model + purpose), every tool call with a short
result, what was created or updated on Freya, every self-check (lint, coverage, validate) and the totals.
"""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

NO_SIMULATIONS = "Simulations: none. This builder runs no simulations; it checks the workflow offline (lint, coverage, snippets) and with a validate dry run."


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def short(value, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class Trace:
    def __init__(self, directory: Path, header: dict):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = (self.dir / "messages.jsonl").open("w", encoding="utf-8")
        self.lines: list[str] = []
        self.started = _now()
        self.header = header
        self.write_json({"type": "start", "time": self.started, **header})
        self.line(f"start {self.started}")
        for k, v in header.items():
            self.line(f"  {k}: {short(v, 300)}")
        self.closed = False

    def write_json(self, obj: dict):
        self.jsonl.write(json.dumps(obj, default=str, ensure_ascii=False) + "\n")
        self.jsonl.flush()

    def line(self, text: str):
        self.lines.append(f"{time.strftime('%H:%M:%S')} {text}")

    def message(self, msg):
        """Record one SDK message (any dataclass) and its readable lines."""
        name = type(msg).__name__
        try:
            payload = dataclasses.asdict(msg)
        except TypeError:
            payload = {"repr": repr(msg)}
        self.write_json({"type": name, **payload})
        if name == "AssistantMessage":
            for block in getattr(msg, "content", []) or []:
                bname = type(block).__name__
                if bname == "TextBlock":
                    self.line(f"model {msg.model}: {short(block.text)}")
                elif bname == "ToolUseBlock":
                    self.line(f"model {msg.model}: tool {block.name} {short(block.input, 120)}")
                elif bname == "ThinkingBlock":
                    self.line(f"model {msg.model}: thinking")
        elif name == "UserMessage":
            content = getattr(msg, "content", None)
            for block in content if isinstance(content, list) else []:
                if type(block).__name__ == "ToolResultBlock":
                    flag = " ERROR" if getattr(block, "is_error", False) else ""
                    self.line(f"  result{flag}: {short(block.content, 200)}")
        elif name == "ResultMessage":
            self.line(f"result: {msg.subtype}, {msg.num_turns} turns, {msg.duration_ms / 1000:.0f} s, "
                      f"cost ${msg.total_cost_usd or 0:.2f}, usage {short(msg.usage, 200)}")
        elif name == "SystemMessage":
            self.line(f"system: {msg.subtype}")

    def event(self, kind: str, detail):
        """A builder-side event: tool, denial, check, freya, finalize, error."""
        self.write_json({"type": "event", "kind": kind, "time": _now(), "detail": detail})
        self.line(f"{kind}: {short(detail, 300)}")

    def close(self, summary: dict):
        if self.closed:
            return
        self.closed = True
        ended = _now()
        self.write_json({"type": "end", "time": ended, **summary})
        self.jsonl.close()
        self.line(NO_SIMULATIONS)
        for k, v in summary.items():
            self.line(f"  {k}: {short(v, 300)}")
        self.line(f"end {ended}")
        (self.dir / "trace.txt").write_text("\n".join(self.lines) + "\n", encoding="utf-8")
