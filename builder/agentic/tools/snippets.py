"""Run a workflow's code node or hook locally, the way the platform would (docs/tools-and-code.md).

- code node: gets `vars`
- pre-hook (`pre_process_code`): gets `vars` and `config` (url, method, headers, body, after {{var}} substitution)
- post-hook (`post_process_code`): gets `result` (the envelope {status, data, message, status_code, header}) and `vars`

The snippet runs in a separate `python -I` process with a timeout. `test_variables` are merged under the given vars.
Standard library only.
"""

from __future__ import annotations

import json
import subprocess
import sys

from builder.agentic.tools.lint import API_CALL, CODE_EXEC, PLACEHOLDER, _code_of, _fn, _props

TIMEOUT_S = 10
MAX_OUT = 6000

RUNNER = r"""
import contextlib, io, json, sys, traceback
data = json.loads(sys.stdin.read())
ns = {"vars": data["vars"]}
if data["kind"] == "pre_hook":
    ns["config"] = data["config"]
if data["kind"] == "post_hook":
    ns["result"] = data["result"]
before = json.dumps(data["vars"], sort_keys=True, default=str)
buf = io.StringIO()
error = None
try:
    with contextlib.redirect_stdout(buf):
        exec(compile(data["code"], "<snippet>", "exec"), ns)
except BaseException:
    error = traceback.format_exc(limit=3)
after = ns["vars"]
old = json.loads(before)
written = {k: v for k, v in after.items() if k not in old or old[k] != v} if isinstance(after, dict) else {}
out = {"vars_written": written, "stdout": buf.getvalue(), "exception": error}
if data["kind"] == "pre_hook":
    out["config"] = ns.get("config")
if data["kind"] == "post_hook":
    out["result"] = ns.get("result")
sys.stdout.write("\n@@RESULT@@" + json.dumps(out, default=str))
"""


def _substitute(value, variables):
    """Engine-style {{var}} substitution for an engine-run call. A field that is exactly one unresolved placeholder
    is dropped (returns _DROP); a mixed value keeps the literal placeholder (workflow-format.md)."""
    if not isinstance(value, str):
        return value
    m = PLACEHOLDER.fullmatch(value.strip())
    if m:
        name, _, fmt = m.group(1).partition(":")
        if name not in variables:
            return _DROP
        v = variables[name]
        return json.dumps(v) if fmt == "json" else v
    return PLACEHOLDER.sub(lambda mm: str(variables.get(mm.group(1).split(":")[0], mm.group(0))), value)


_DROP = object()


def build_config(tool: dict, variables: dict) -> dict:
    props = _props(tool)
    cfg = {"url": _substitute((props.get("url") or {}).get("default", ""), variables),
           "method": (props.get("method") or {}).get("default", "POST"), "headers": {}, "body": {}}
    for h, leaf in ((props.get("headers") or {}).get("properties") or {}).items():
        v = _substitute((leaf or {}).get("default"), variables)
        if v is not _DROP:
            cfg["headers"][h] = v
    for f, leaf in ((props.get("body") or {}).get("properties") or {}).items():
        v = _substitute((leaf or {}).get("default"), variables)
        if v is not _DROP:
            cfg["body"][f] = v
    if cfg["url"] is _DROP:
        cfg["url"] = ""
    return cfg


def find_tool(wf: dict, node_id: str, tool_name: str | None):
    """The tool object (or code_exec pre_action) addressed by node id and tool name. node_id 'workflow' = top level."""
    if node_id == "workflow":
        pool = wf.get("tools") or []
    else:
        node = (wf.get("nodes") or {}).get(node_id) or (wf.get("global_nodes") or {}).get(node_id)
        if node is None:
            raise KeyError(f"no node {node_id!r}")
        pool = (node.get("tools") or []) + (node.get("pre_actions") or [])
    for t in pool:
        if tool_name is None and _fn(t).get("handler") == CODE_EXEC:
            return t
        if tool_name is not None and _fn(t).get("name") == tool_name:
            return t
    raise KeyError(f"no tool {tool_name or 'code_exec'!r} in {node_id!r}")


def run_snippet(wf: dict, node_id: str, variables: dict | None = None, kind: str = "code", tool: str | None = None,
                result: dict | None = None, timeout: float = TIMEOUT_S, python: str | None = None) -> dict:
    if kind not in ("code", "pre_hook", "post_hook"):
        return {"error": "kind must be code, pre_hook or post_hook"}
    try:
        t = find_tool(wf, node_id, None if kind == "code" else tool)
    except KeyError as e:
        return {"error": str(e)}
    if kind == "code":
        code = _code_of(t)
    else:
        if _fn(t).get("handler") != API_CALL:
            return {"error": f"{tool} is not an api_call tool; the platform strips hooks from it"}
        code = t.get("pre_process_code" if kind == "pre_hook" else "post_process_code")
    if not isinstance(code, str) or not code.strip():
        return {"error": f"no {kind} snippet on {node_id}/{tool or 'code_exec'}"}
    merged = {**(wf.get("test_variables") or {}), **(variables or {})}
    payload = {"kind": kind, "code": code, "vars": merged}
    if kind == "pre_hook":
        payload["config"] = build_config(t, merged)
    if kind == "post_hook":
        is_envelope = isinstance(result, dict) and ("data" in result or "status" in result)
        payload["result"] = result if is_envelope else {  # a bare API body is wrapped like the platform does
            "status": "success", "data": result if result is not None else {}, "message": "Successfully executed POST request",
            "status_code": 200, "header": {"Content-Type": "application/json"}}
    try:
        proc = subprocess.run([python or sys.executable, "-I", "-c", RUNNER], input=json.dumps(payload, default=str),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout:g} s (the platform's code limit is timeout_ms, default 30 s; keep snippets fast)"}
    out, _, tail = proc.stdout.rpartition("\n@@RESULT@@")
    if not tail:
        return {"error": "runner crashed", "stderr": proc.stderr[-2000:]}
    res = json.loads(tail)
    if out:
        res["stdout"] = (out + res.get("stdout", ""))
    return res


def render(res: dict) -> str:
    text = json.dumps(res, indent=1, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n... (truncated)"


__all__ = ["run_snippet", "build_config", "find_tool", "render"]
