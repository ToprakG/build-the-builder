"""Arguments, slot name, input reading, output directories and model settings of a T-CC build."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent


class InputError(ValueError):
    pass


def is_tool_api(path: Path) -> bool:
    """The tool API is the one argument whose JSON has a top-level `openapi` key."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and "openapi" in data


def split_inputs(paths) -> tuple[list[Path], Path]:
    paths = [Path(p) for p in paths]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise InputError(f"not a file: {', '.join(missing)}")
    apis = [p for p in paths if is_tool_api(p)]
    if len(apis) != 1:
        raise InputError(f"need exactly one tool API file (JSON with a top-level 'openapi' key), got {len(apis)}")
    briefs = [p for p in paths if p != apis[0]]
    if not briefs:
        raise InputError("need at least one brief file")
    return briefs, apis[0]


def slot_name(briefs) -> str:
    first = Path(briefs[0])
    stem = first.parent.name if first.stem.lower() == "brief" and first.parent.name else first.stem
    return f"tcc-{stem}"


@dataclass
class Brief:
    name: str  # brief-1.md, brief-2.eml, ...
    source: str  # original path
    text: str


def read_briefs(paths) -> list[Brief]:
    out = []
    for i, p in enumerate(paths, 1):
        p = Path(p)
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise InputError(f"{p} is not UTF-8 text") from e
        out.append(Brief(f"brief-{i}{p.suffix or '.txt'}", str(p), text))
    return out


def brief_text(briefs: list[Brief]) -> str:
    """All brief material in order. Later material overrides earlier material."""
    if len(briefs) == 1:
        return briefs[0].text
    parts = [f"===== {b.name} (part {i} of {len(briefs)}; later parts override earlier ones) =====\n{b.text}"
             for i, b in enumerate(briefs, 1)]
    return "\n\n".join(parts)


@dataclass
class Models:
    extract: str = "claude_cli:sonnet"  # the local Claude Code CLI (the Claude subscription); no API key needed
    analyze: str = "claude_cli:sonnet"
    patch: str = "sonnet"  # Claude Code (Agent SDK) model alias
    judge: str = "claude_cli:sonnet"  # LocalDecisionJudge (DECISION_MODEL)
    judge_threshold: float = 0.5  # vendor decision model: p(yes) threshold
    judge_votes: int = 1  # LocalDecisionJudge majority votes
    judge_vendor_model: str = ""  # empty = the vendor's own default
    audit_budget_usd: float = 3.0
    patch_budget_usd: float = 3.0
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | None = None, env=os.environ) -> "Models":
        m = cls()
        for attr, var in (("extract", "TCC_EXTRACT_MODEL"), ("analyze", "TCC_ANALYZE_MODEL"),
                          ("patch", "TCC_PATCH_MODEL"), ("judge", "DECISION_MODEL")):
            if env.get(var):
                setattr(m, attr, env[var])
        if path:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(m, k) and k != "extra":
                    setattr(m, k, type(getattr(m, k))(v))
                else:
                    m.extra[k] = v
        return m


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="python3 -m builder.template_cc.run",
                                 description="T-CC builder: brief(s) + tool API -> Freya workflow")
    ap.add_argument("inputs", nargs="+", help="brief files (any extension) and exactly one tool API JSON")
    ap.add_argument("--save", choices=("freya", "none"), default="freya")
    ap.add_argument("--slot")
    ap.add_argument("--max-minutes", type=float, default=25.0)
    ap.add_argument("--audit", choices=("on", "off"), default="on")
    ap.add_argument("--self-test", choices=("none", "local"), default="none")
    ap.add_argument("--max-iters", type=int, default=3)
    ap.add_argument("--models")
    ap.add_argument("--out")
    ap.add_argument("--close-form", choices=("lifecycle", "node"), default="lifecycle")
    ap.add_argument("--keep-workspace", action="store_true")
    ap.add_argument("--self-test-cases", type=int, default=24, help="cases testgen makes for the self-test loop")
    ap.add_argument("--self-test-reps", type=int, default=1, help="runs per case in the self-test loop")
    ap.add_argument("--unit-gate", choices=("on", "off"), default="on", help="Freya unit tests as a gate before chats")
    ap.add_argument("--gate-p", type=float, default=0.1, help="sign-test p threshold of the gate")
    ap.add_argument("--features", default=None, help="compiler fixes: all (default, or env BUILDER_FEATURES), none, f1,f3, all,-f2")
    ap.add_argument("--policy", help="skip extraction: compile from this policy.json (checked deterministically)")
    return ap.parse_args(argv)


def out_dir(args, slot: str) -> Path:
    d = Path(args.out) if args.out else REPO / "out" / "template_cc" / slot
    (d / "patches").mkdir(parents=True, exist_ok=True)
    return d
