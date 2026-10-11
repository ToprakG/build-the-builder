"""PreToolUse guard: defence in depth beyond the temp-dir workspace.

Read/Glob/Grep may touch only the workspace; Write/Edit only `out/`. Paths are resolved (symlinks included) before
the check. Any path naming test material is denied even inside the workspace. MCP tools of our server and the
skill tool pass; everything else is denied (permission_mode dontAsk refuses it too).
"""

from __future__ import annotations

import os
from pathlib import Path

FORBIDDEN = ("testbed", "cases.json", "config.json", "oracle", "public-test-set", ".env", "agentforge")
FILE_TOOLS = {"Read": ("file_path",), "Write": ("file_path",), "Edit": ("file_path",), "MultiEdit": ("file_path",),
              "Glob": ("path",), "Grep": ("path",)}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit"}
READ_ONLY_OUT = {"derived_unit_tests.json", "localsim_results.json", "last_good.workflow.json"}
WILDCARDS = "*?[{"


def _forbidden(text: str) -> str | None:
    low = text.lower()
    return next((w for w in FORBIDDEN if w in low), None)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(arg: str, root: Path) -> Path:
    p = Path(os.path.expanduser(arg))
    return (p if p.is_absolute() else root / p).resolve()


def _glob_base(pattern: str) -> str:
    """The literal directory part of a glob pattern, before the first wildcard."""
    parts = []
    for part in Path(pattern).parts:
        if any(c in part for c in WILDCARDS):
            break
        parts.append(part)
    return str(Path(*parts)) if parts else "."


def check(tool_name: str, tool_input: dict, workspace: Path, mcp_prefix: str = "mcp__fb__") -> tuple[bool, str]:
    """(allowed, reason)."""
    root = Path(workspace).resolve()
    if tool_name.startswith(mcp_prefix) or tool_name == "Skill":
        return True, "builder tool"
    if tool_name not in FILE_TOOLS:
        return False, f"{tool_name} is not available to the builder"
    tool_input = tool_input or {}
    args = [(k, tool_input[k]) for k in FILE_TOOLS[tool_name] if isinstance(tool_input.get(k), str) and tool_input[k]]
    if tool_name in ("Glob", "Grep"):
        for key in ("pattern", "glob"):
            value = tool_input.get(key)
            if not isinstance(value, str):
                continue
            if tool_name == "Grep" and key == "pattern":
                continue  # a regex over file contents, not a path
            if ".." in Path(value).parts or os.path.isabs(value) or value.startswith("~"):
                return False, f"{key} {value!r} leaves the workspace"
            args.append((key, _glob_base(value)))
    if tool_name in ("Read", "Write", "Edit", "MultiEdit") and not args:
        return False, "missing file_path"
    for key, value in args:
        bad = _forbidden(value)
        if bad:
            return False, f"{key} names forbidden material ({bad})"
        resolved = _resolve(value, root)
        if not _inside(resolved, root):
            return False, f"{key} {value!r} resolves outside the workspace"
        rel = str(resolved.relative_to(root))
        bad = _forbidden(rel)
        if bad:
            return False, f"{key} resolves to forbidden material ({bad})"
        if tool_name in WRITE_TOOLS and not _inside(resolved, root / "out"):
            return False, "writes are allowed only under out/"
        if tool_name in WRITE_TOOLS and resolved.name in READ_ONLY_OUT:
            return False, f"{resolved.name} is written by the builder's tools, not by hand"
    return True, "ok"


def make_hook(workspace: Path, on_event=None):
    """An SDK PreToolUse callback. on_event(kind, detail) logs allows of MCP tools and every denial."""

    async def hook(input_data, tool_use_id, context):  # signature: HookCallback (types.py:594)
        name = input_data.get("tool_name", "")
        allowed, reason = check(name, input_data.get("tool_input") or {}, workspace)
        if not allowed:
            if on_event:
                on_event("denied", {"tool": name, "input": input_data.get("tool_input"), "reason": reason})
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        return {}

    return hook
