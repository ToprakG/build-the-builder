"""PreToolUse guard for the T-CC Claude Code workspaces (own small copy; dedupe with builder/agentic later).

Read/Glob/Grep may touch only the workspace; Write only `out/`. Paths are resolved (symlinks included) before the
check. A path naming test material is denied even inside the workspace. Our MCP tools pass (they take no paths);
everything else is denied (permission_mode dontAsk refuses it as well).
"""

from __future__ import annotations

import os
from pathlib import Path

FORBIDDEN = ("testbed", "cases.json", "config.json", "oracle", "public-test-set", ".env", "agentforge")
FILE_TOOLS = {"Read": ("file_path",), "Write": ("file_path",), "Glob": ("path",), "Grep": ("path",)}
WRITE_TOOLS = {"Write"}
WILDCARDS = "*?[{"
MCP_PREFIX = "mcp__tcc__"


def forbidden(text: str) -> str | None:
    low = str(text).lower()
    return next((w for w in FORBIDDEN if w in low), None)


def inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve(arg: str, root: Path) -> Path:
    p = Path(os.path.expanduser(arg))
    return (p if p.is_absolute() else root / p).resolve()


def _glob_base(pattern: str) -> str:
    parts = []
    for part in Path(pattern).parts:
        if any(c in part for c in WILDCARDS):
            break
        parts.append(part)
    return str(Path(*parts)) if parts else "."


def check(tool_name: str, tool_input: dict, workspace: Path) -> tuple[bool, str]:
    root = Path(workspace).resolve()
    if tool_name.startswith(MCP_PREFIX):
        return True, "builder tool"
    if tool_name not in FILE_TOOLS:
        return False, f"{tool_name} is not available"
    tool_input = tool_input or {}
    args = [(k, tool_input[k]) for k in FILE_TOOLS[tool_name] if isinstance(tool_input.get(k), str) and tool_input[k]]
    if tool_name in ("Glob", "Grep"):
        for key in ("pattern", "glob"):
            value = tool_input.get(key)
            if not isinstance(value, str) or (tool_name == "Grep" and key == "pattern"):
                continue
            if ".." in Path(value).parts or os.path.isabs(value) or value.startswith("~"):
                return False, f"{key} {value!r} leaves the workspace"
            args.append((key, _glob_base(value)))
    if tool_name in ("Read", "Write") and not args:
        return False, "missing file_path"
    for key, value in args:
        bad = forbidden(value)
        if bad:
            return False, f"{key} names forbidden material ({bad})"
        resolved = _resolve(value, root)
        if not inside(resolved, root):
            return False, f"{key} {value!r} resolves outside the workspace"
        bad = forbidden(str(resolved.relative_to(root)))
        if bad:
            return False, f"{key} resolves to forbidden material ({bad})"
        if tool_name in WRITE_TOOLS and not inside(resolved, root / "out"):
            return False, "writes are allowed only under out/"
    return True, "ok"


def make_hook(workspace: Path, on_event=None):
    async def hook(input_data, tool_use_id, context):  # HookCallback (claude_agent_sdk types.py:594)
        name = input_data.get("tool_name", "")
        allowed, reason = check(name, input_data.get("tool_input") or {}, workspace)
        if not allowed:
            if on_event:
                on_event("denied", {"tool": name, "input": input_data.get("tool_input"), "reason": reason})
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                           "permissionDecisionReason": reason}}
        return {}

    return hook
