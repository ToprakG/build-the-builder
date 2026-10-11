"""Extra builder tools for the banking-voice trial. They read only the workspace the builder already has."""

from __future__ import annotations

import json
import re

DIGIT_RUN = re.compile(r"\d{5,}")
CURRENCY_CODE = re.compile(r"\b(USD|EUR|GBP|TL)\b")

SPECS = {
    "ledger_digest": (
        "Compact view of input/conflict_ledger.json: each rule id, whether it is open, and the chosen sentence.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "speech_guard": (
        "Check a spoken line for a digit run of five or more and for currency codes USD, EUR, GBP, TL. "
        "Pass the text a code node produced.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
         "additionalProperties": False},
    ),
    "side_exit_check": (
        "Report conversation nodes in out/workflow.json that have a transition back to themselves.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
}


def ledger_digest(bound, args) -> str:
    path = bound.ws / "input" / "conflict_ledger.json"
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "input/conflict_ledger.json does not exist yet"
    rows = []
    for rule in ledger.get("rules") or []:
        chosen = rule.get("chosen") or rule.get("follow") or rule.get("sentence") or ""
        if isinstance(chosen, dict):
            chosen = chosen.get("sentence") or chosen.get("text") or ""
        rows.append({"id": rule.get("id"), "open": bool(rule.get("open")), "chosen": str(chosen)[:240]})
    return json.dumps({"rules": len(rows), "items": rows}, ensure_ascii=False)


def speech_guard(bound, args) -> str:
    text = str((args or {}).get("text") or "")
    hits = []
    if DIGIT_RUN.search(text):
        hits.append("digit run of 5 or more")
    codes = sorted(set(CURRENCY_CODE.findall(text)))
    if codes:
        hits.append("currency code: " + ", ".join(codes))
    return json.dumps({"ok": not hits, "hits": hits}, ensure_ascii=False)


def side_exit_check(bound, args) -> str:
    wf, err = bound._wf()
    if err:
        return err
    bad = []
    nodes = wf.get("nodes") or {}
    if isinstance(nodes, list):
        nodes = {n.get("id"): n for n in nodes if isinstance(n, dict)}
    for node_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("type") not in (None, "conversation"):
            if node.get("type") != "conversation":
                continue
        for edge in node.get("transitions") or []:
            target = edge.get("target") or edge.get("destination") or edge.get("to")
            if target == node_id:
                bad.append(node_id)
                break
    return json.dumps({"self_edges": bad, "ok": not bad}, ensure_ascii=False)


HANDLERS = {
    "ledger_digest": ledger_digest,
    "speech_guard": speech_guard,
    "side_exit_check": side_exit_check,
}


class Bound:
    """BoundTools plus the trial tools. `call` accepts the new names."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def call(self, name, args=None):
        if name in HANDLERS:
            try:
                return HANDLERS[name](self.inner, args or {})
            except Exception as e:  # noqa: BLE001
                return f"tool error: {e!r}"
        return self.inner.call(name, args)


def build_server(bound):
    from claude_agent_sdk import create_sdk_mcp_server, tool  # noqa: PLC0415

    from builder.agentic.mcp_server import SERVER, TOOL_SPECS

    specs = dict(TOOL_SPECS)
    specs.update(SPECS)

    def make(name, description, schema):
        @tool(name, description, schema)
        async def handler(args):
            text = bound.call(name, args)
            return {"content": [{"type": "text", "text": text}]}

        return handler

    return create_sdk_mcp_server(SERVER, "1.0.0", [make(n, d, s) for n, (d, s) in specs.items()])
