"""Inputs of a build: which argument is the tool API, the brief order, slot naming, the isolated workspace copy."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KIT_DOCS = ("workflow-format.md", "tools-and-code.md", "api.md")
SKILL_NAME = "build-phone-agent"
SKILL_DIR = REPO / "builder" / "agentic" / "skill" / SKILL_NAME
DEMO_FILES = (REPO / "tests" / "agentic" / "fixtures" / "pattern_demo.workflow.json",
              REPO / "tests" / "agentic" / "fixtures" / "pattern_demo.openapi.json")


class InputError(ValueError):
    pass


def is_tool_api(path: Path) -> bool:
    """A tool API file is JSON with a top-level `openapi` key, whatever its name or extension."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return False
    return isinstance(data, dict) and "openapi" in data


def split_inputs(paths) -> tuple[list[Path], Path]:
    """Positional arguments -> (brief files in the given order, the one tool API file)."""
    paths = [Path(p) for p in paths]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise InputError(f"not a file: {', '.join(missing)}")
    apis = [p for p in paths if is_tool_api(p)]
    if len(apis) != 1:
        raise InputError(f"need exactly one tool API file (JSON with a top-level 'openapi' key), got {len(apis)}: "
                         + ", ".join(map(str, apis)))
    briefs = [p for p in paths if p != apis[0]]
    if not briefs:
        raise InputError("need at least one brief file")
    for b in briefs:
        try:
            b.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise InputError(f"{b} is not UTF-8 text") from e
    return briefs, apis[0]


def slot_name(briefs, variant: str = "agentic") -> str:
    """`<variant>-<stem>` of the last brief, or of its folder when the stem is just `brief`."""
    last = Path(briefs[-1])
    stem = last.parent.name if last.stem.lower() == "brief" and last.parent.name else last.stem
    return f"{variant}-{stem}"


def build_workspace(ws: Path, briefs, api: Path, skill_dir: Path = SKILL_DIR, docs_dir: Path = REPO / "docs",
                    demo_files=DEMO_FILES) -> dict:
    """Fill an empty directory with exactly what the builder agent may see. Briefs are renamed brief-1.<ext>, ..."""
    ws = Path(ws)
    (ws / "input").mkdir(parents=True)
    copied = []
    for i, b in enumerate(briefs, 1):
        ext = Path(b).suffix.lower() or ".txt"
        dst = ws / "input" / f"brief-{i}{ext}"
        shutil.copyfile(b, dst)
        copied.append(dst)
    shutil.copyfile(api, ws / "input" / "tools.openapi.json")
    (ws / "docs").mkdir()
    for name in KIT_DOCS:
        shutil.copyfile(Path(docs_dir) / name, ws / "docs" / name)
    (ws / "examples").mkdir()
    for f in demo_files:
        shutil.copyfile(f, ws / "examples" / Path(f).name)
    shutil.copytree(skill_dir, ws / ".claude" / "skills" / Path(skill_dir).name,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (ws / "out").mkdir()
    return {"briefs": copied, "api": ws / "input" / "tools.openapi.json", "out": ws / "out"}
