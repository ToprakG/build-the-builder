"""Headless Claude Code (Agent SDK) sessions for the audit and the patcher, in an isolated temp workspace.

Workspace (tempfile.mkdtemp, outside the repo):
    input/brief-N.<ext>, input/tools.openapi.json
    current/policy.json, current/workflow.json (read-only), current/zones.json
    report/ (patcher only: bugs.json and failures/<case>-r<rep>.json)
    out/    (the only writable folder)
MCP tools (server "tcc", no path arguments, bound to the workspace): zones(), preview_patches(), time_left().
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import stat
import tempfile
import time
from pathlib import Path

from builder.template_cc import compiler, guard, patches, policy_check, zones
from builder.template_cc.trace import short

SERVER = "tcc"
FILE_TOOLS = ["Read", "Glob", "Grep", "Write"]
DENIED = ["Bash", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "Skill"]
TOOL_SPECS = {
    "zones": ("List every patch zone: id, locked, budget (max_chars), current text and purpose.", {}),
    "preview_patches": ("Read out/patches.json (or out/policy_patches.json in the audit), apply it in memory and "
                        "return schema errors, the lint result, growth and a diff. Writes nothing.", {}),
    "time_left": ("Seconds left for this session.", {}),
    "run_sims": ("Apply out/patches.json in memory and run hard simulations of that candidate agent: an LLM customer "
                 "(DeepSeek) with hard talking styles, the bank ledger scored, the conversation judged by the decision "
                 "model. Returns pass/fail per case with the failure reasons. Slow: about 2-4 minutes per case; "
                 "n is 1 to 4.", {"n": int}),
}


def make_workspace(briefs, api_path: Path, policy: dict, workflow: dict | None = None, book: zones.ZoneBook | None = None,
                   report: dict | None = None, failures: list | None = None) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="tcc-ws-"))
    (ws / "input").mkdir()
    (ws / "current").mkdir()
    (ws / "out").mkdir()
    for b in briefs:
        (ws / "input" / b.name).write_text(b.text, encoding="utf-8")
    shutil.copyfile(api_path, ws / "input" / "tools.openapi.json")
    (ws / "current" / "policy.json").write_text(json.dumps(policy, indent=1, ensure_ascii=False), encoding="utf-8")
    if workflow is not None:
        p = ws / "current" / "workflow.json"
        p.write_text(compiler.dump(workflow), encoding="utf-8")
        p.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    if book is not None:
        (ws / "current" / "zones.json").write_text(json.dumps(zones.listing(book), indent=1, ensure_ascii=False), encoding="utf-8")
    if report is not None:
        (ws / "report").mkdir()
        (ws / "report" / "bugs.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
        (ws / "report" / "failures").mkdir()
        for f in failures or []:
            safe = {k: f.get(k) for k in ("case_id", "rep", "failed_reasons", "transcript", "tool_calls")}
            name = f"{f.get('case_id')}-r{f.get('rep', 1)}.json".replace("/", "_")
            (ws / "report" / "failures" / name).write_text(json.dumps(safe, indent=1, ensure_ascii=False), encoding="utf-8")
    return ws


def remove_workspace(ws: Path):
    for p in Path(ws).rglob("*"):
        try:
            p.chmod(0o700 if p.is_dir() else 0o600)
        except OSError:
            pass
    shutil.rmtree(ws, ignore_errors=True)


class BoundTools:
    """The MCP tools, bound to one workspace and its in-memory state. No path parameters anywhere."""

    def __init__(self, ws: Path, mode: str, deadline: float, policy: dict, api, brief_text: str,
                 workflow: dict | None = None, book: zones.ZoneBook | None = None, on_event=None, sims_fn=None):
        self.ws, self.mode, self.deadline = Path(ws), mode, deadline
        self.policy, self.api, self.brief_text = policy, api, brief_text
        self.workflow, self.book = workflow, book
        self.on_event = on_event or (lambda *a, **k: None)
        self.sims_fn = sims_fn  # (workflow, n) -> report text; None when no simulator is connected

    def call(self, name: str, args: dict | None = None) -> str:
        try:
            fn = getattr(self, "t_" + name)
            out = fn(**{k: v for k, v in (args or {}).items() if k in fn.__code__.co_varnames})
            self.on_event("mcp", tool=name, result=short(out, 200))
            return out
        except Exception as e:  # noqa: BLE001 - a tool error is text for the model
            return f"tool error: {e!r}"

    def t_time_left(self) -> str:
        return f"{max(0, int(self.deadline - time.time()))} seconds left"

    def t_run_sims(self, n: int = 2) -> str:
        if self.mode != "patch" or self.sims_fn is None:
            return "run_sims is not available in this session"
        n = max(1, min(4, int(n or 2)))
        path = self.ws / "out" / "patches.json"
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        wf, book, applied, failed = patches.apply_all(self.workflow, self.book, items if isinstance(items, list) else [])
        head = (f"candidate = current agent + {len(applied)} patch(es) from out/patches.json"
                + (f" ({len(failed)} not applied)" if failed else ""))
        return head + "\n" + self.sims_fn(wf, n)

    def t_zones(self) -> str:
        if self.book is None:
            return "no zones in this session"
        return json.dumps(zones.listing(self.book), ensure_ascii=False, indent=1)

    def t_preview_patches(self) -> str:
        name = "policy_patches.json" if self.mode == "audit" else "patches.json"
        path = self.ws / "out" / name
        if not path.exists():
            return f"out/{name} does not exist yet"
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return f"invalid JSON: {e}"
        if not isinstance(items, list):
            return "the file must hold a JSON list"
        if self.mode == "audit":
            return self._preview_policy(items)
        return self._preview_prompt(items)

    def _preview_policy(self, items) -> str:
        lines, pol = [], self.policy
        for p in items:
            errs = patches.policy_shape_errors(p)
            if not errs:
                try:
                    pol = patches.apply_policy(pol, p)
                except patches.PatchError as e:
                    errs = [str(e)]
                if not errs and not policy_check.quote_in(p.get("brief_quote", ""), self.brief_text):
                    errs.append("brief_quote is not found in the brief")
            lines.append(f"{p.get('id')}: {'ok' if not errs else '; '.join(errs)}")
        viol = policy_check.check(pol, self.api, self.brief_text) if self.api is not None else []
        lines.append(f"policy_check after all patches: {len(viol)} violation(s)")
        lines += [f"  {v['path']}: {v['msg']}" for v in viol[:20]]
        return "\n".join(lines)

    def _preview_prompt(self, items) -> str:
        wf, book, applied, failed = patches.apply_all(self.workflow, self.book, items)
        lines = [f"{p.get('id')}: applied" for p in applied]
        lines += [f"{p.get('id') if isinstance(p, dict) else '?'}: NOT applied: {why}" for p, why in failed]
        for p in items:
            if isinstance(p, dict):
                errs = patches.shape_errors(p)
                if not policy_check.quote_in(p.get("brief_quote", ""), self.brief_text):
                    errs.append("brief_quote is not found in the brief")
                if errs:
                    lines.append(f"{p.get('id')}: {'; '.join(errs)}")
        report = compiler.lint(wf, self.policy, self.api)
        lines.append(f"lint: {len(report['errors'])} error(s)")
        lines += [f"  {e['path']}: {e['msg']}" for e in report["errors"][:10]]
        lines.append(f"growth: {patches.growth(self.book, book):+.1%} (limit +{zones.MAX_GROWTH:.0%} per iteration)")
        lines.append(patches.diff(self.book, book)[:6000])
        return "\n".join(lines)


def tool_names() -> list[str]:
    return [f"mcp__{SERVER}__{n}" for n in TOOL_SPECS]


def build_server(bound: BoundTools):
    from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: PLC0415

    def make(name, description, schema):
        @tool(name, description, schema)
        async def handler(args):
            return {"content": [{"type": "text", "text": bound.call(name, args)}]}

        return handler

    return create_sdk_mcp_server(SERVER, "1.0.0", [make(n, d, s) for n, (d, s) in TOOL_SPECS.items()])


def sdk_options(ws: Path, model: str, system_prompt: str, hook, server, max_turns: int, max_usd: float | None):
    from claude_agent_sdk import ClaudeAgentOptions, HookMatcher  # noqa: PLC0415

    return ClaudeAgentOptions(
        cwd=str(ws), model=model, tools=list(FILE_TOOLS), allowed_tools=FILE_TOOLS + tool_names(),
        disallowed_tools=DENIED, setting_sources=["project"], permission_mode="dontAsk", max_turns=max_turns,
        max_budget_usd=max_usd, env={"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[hook])]}, mcp_servers={SERVER: server},
        strict_mcp_config=True, system_prompt=system_prompt)


def reap_children(grace_s: float = 3.0) -> int:
    """After a timeout: SIGTERM, then SIGKILL, every CLI child the SDK still tracks (SDK_NOTES.md)."""
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli as sc  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return 0
    pids = [getattr(p, "pid", None) for p in list(getattr(sc, "_ACTIVE_CHILDREN", set()) or [])]
    pids = [p for p in pids if p]
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    end = time.monotonic() + grace_s
    alive = list(pids)
    while alive and time.monotonic() < end:
        for pid in list(alive):
            try:
                done, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                done = pid
            if done:
                alive.remove(pid)
        time.sleep(0.1)
    for pid in alive:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return len(pids)


def _trace_message(trace, msg, purpose: str, state: dict):
    name = type(msg).__name__
    if name == "AssistantMessage":
        state["turns"] += 1
        bits = []
        for block in getattr(msg, "content", []) or []:
            b = type(block).__name__
            if b == "TextBlock":
                bits.append(short(block.text, 100))
            elif b == "ToolUseBlock":
                bits.append(f"{block.name}({short(block.input, 80)})")
        trace.model_call(getattr(msg, "model", "") or "claude-code", f"{purpose} turn {state['turns']}: " + " | ".join(bits))
    elif name == "ResultMessage":
        state["result"] = {"subtype": getattr(msg, "subtype", None), "turns": getattr(msg, "num_turns", None),
                           "cost_usd": getattr(msg, "total_cost_usd", None), "usage": getattr(msg, "usage", None),
                           "is_error": getattr(msg, "is_error", None)}
        cost = getattr(msg, "total_cost_usd", None)
        if cost:
            trace.cost += float(cost)
        trace.line(f"{purpose} result: {short(state['result'], 300)}")
    trace.event("sdk_message", purpose=purpose, message=name)


async def _consume(query_fn, prompt: str, options, trace, purpose: str, seconds: float, state: dict):
    import anyio  # noqa: PLC0415 - an SDK dependency; its cancel scope lets the SDK's shielded close() run

    async def consume():
        async for msg in query_fn(prompt=prompt, options=options):
            _trace_message(trace, msg, purpose, state)

    try:
        with anyio.fail_after(seconds):
            await consume()
    except TimeoutError:
        state["timed_out"] = True
    except Exception as e:  # noqa: BLE001
        state["error"] = repr(e)


def run(prompt: str, system_prompt: str, ws: Path, bound: BoundTools, model: str, trace, purpose: str, seconds: float,
        max_turns: int = 40, max_usd: float | None = None, query_fn=None, server=None) -> dict:
    """Run one headless session. Returns {turns, timed_out, error, result, reaped}."""
    state = {"turns": 0, "timed_out": False, "error": None, "result": None, "reaped": 0}
    if seconds < 20:
        state["error"] = "not enough time left"
        trace.warn(f"{purpose}: skipped, {seconds:.0f}s left")
        return state
    if query_fn is None:
        from claude_agent_sdk import query as query_fn  # noqa: PLC0415
    hook = guard.make_hook(ws, on_event=lambda kind, detail: trace.event(kind, purpose=purpose, detail=detail))
    if server is None:
        server = build_server(bound)
    options = sdk_options(ws, model, system_prompt, hook, server, max_turns, max_usd)
    trace.line(f"{purpose}: Claude Code session ({model}), {seconds:.0f}s, max_turns {max_turns}, workspace {ws}")
    asyncio.run(_consume(query_fn, prompt, options, trace, purpose, seconds, state))
    if state["timed_out"]:
        state["reaped"] = reap_children()
        trace.warn(f"{purpose}: timed out after {seconds:.0f}s; reaped {state['reaped']} CLI process(es)")
    if state["error"]:
        trace.warn(f"{purpose}: session error {state['error']}")
    return state
