"""Turn a Freya call into one text block. The Decisions API accepts user text only.

Tool calls sit inline with their arguments and results. A FACTS block at the end
is computed here, so the model checks words against those facts.
"""
import json
from dataclasses import dataclass, field

SKIP_TOOLS = {"end_call"}

@dataclass
class Turn:
    n: int
    kind: str
    text: str = ""
    name: str = ""
    args: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)

def _args(raw):
    if not isinstance(raw, dict):
        return {}
    body = raw.get("body") if isinstance(raw.get("body"), dict) else raw
    return {k: v for k, v in body.items() if k != "customer_id"}

def _result(event, raw):
    if event == "function_call_error":
        return {"ok": False, "error": raw if isinstance(raw, str) else json.dumps(raw, default=str)}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "empty result"}
    data = raw.get("data")
    if isinstance(data, dict):
        return data
    return {k: v for k, v in raw.items() if k not in ("header", "status_code", "message", "status")} or {"ok": False}

def parse_messages(messages):
    """Pair each tool start with its complete or error event. Number every spoken turn and tool."""
    pending, raw = [], []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role, text = message.get("role"), (message.get("message") or "").strip()
        if role in ("assistant", "user"):
            if text:
                raw.append({"kind": "agent" if role == "assistant" else "customer", "text": text})
            continue
        if role != "system" or not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        name, kind = event.get("function"), event.get("event")
        if name in SKIP_TOOLS:
            continue
        if kind == "function_call_start":
            pending.append({"name": name, "args": _args(event.get("args"))})
        elif kind in ("function_call_complete", "function_call_error"):
            slot = next((i for i, item in enumerate(pending) if item["name"] == name), None)
            args = pending.pop(slot)["args"] if slot is not None else {}
            payload = event.get("error") if kind == "function_call_error" else event.get("result")
            raw.append({"kind": "tool", "name": name or "", "args": args, "result": _result(kind, payload)})
    for item in pending:
        raw.append({"kind": "tool", "name": item["name"] or "", "args": item["args"], "result": {"ok": False, "error": "no result"}})
    turns = []
    for i, item in enumerate(raw, 1):
        turns.append(Turn(i, item["kind"], item.get("text", ""), item.get("name", ""), item.get("args") or {}, item.get("result") or {}))
    return turns

def variables_of(messages, case):
    if case and case.get("variables"):
        source = case["variables"]
        return {k: source[k] for k in ("title", "first_name", "last_name", "now") if k in source}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        table = (message.get("workflowSnapshot") or {}).get("variableTable") or {}
        if "last_name" not in table:
            continue
        found = {}
        for key in ("title", "first_name", "last_name", "now"):
            cell = table.get(key)
            if isinstance(cell, dict) and cell.get("value") not in (None, ""):
                found[key] = cell["value"]
        if found:
            return found
    return {}

def tools(turns, name=None):
    return [turn for turn in turns if turn.kind == "tool" and (name is None or turn.name == name)]

def verified_turn(turns):
    for turn in tools(turns, "verify_identity"):
        if turn.result.get("all_verified") is True:
            return turn.n
    return None

def products_of(turns):
    for turn in tools(turns, "get_account"):
        if turn.result.get("ok") is True and turn.result.get("products"):
            return turn.result["products"]
    return []

def product_type(turns, product_id):
    for product in products_of(turns):
        if product.get("product_id") == product_id:
            return product.get("type")
    return None

def fact_lines(turns):
    lines = []
    mark = verified_turn(turns)
    lines.append(f"identity verified at T{mark}" if mark else "identity not verified")
    verifies = tools(turns, "verify_identity")
    parts = []
    for turn in verifies:
        parts.append(f"T{turn.n} {turn.args.get('field')} verified={turn.result.get('verified')} wrong_answers_left={turn.result.get('wrong_answers_left')}")
    lines.append("verify_identity calls: " + str(len(verifies)) + ((" (" + "; ".join(parts) + ")") if parts else ""))
    accounts = tools(turns, "get_account")
    if accounts:
        bits = [f"T{turn.n} ok={turn.result.get('ok') is True}" + (f" error={turn.result.get('error')}" if turn.result.get("error") else "") for turn in accounts]
        lines.append("get_account calls: " + "; ".join(bits))
    else:
        lines.append("get_account calls: 0")
    promises = tools(turns, "create_promise")
    if promises:
        bits = []
        for turn in promises:
            state = "ok" if turn.result.get("ok") is True else "fail"
            bits.append(f"T{turn.n} {state} product={turn.args.get('product_id')} amount={turn.args.get('amount')} date={turn.args.get('date')}")
        lines.append(f"create_promise calls: {len(promises)} (" + "; ".join(bits) + ")")
    else:
        lines.append("create_promise calls: 0")
    notes = tools(turns, "add_note")
    if notes:
        lines.append("add_note calls: " + "; ".join(f"T{turn.n} product={turn.args.get('product_id')} reason={turn.args.get('reason')}" for turn in notes))
    callbacks = tools(turns, "schedule_callback")
    if callbacks:
        lines.append("schedule_callback calls: " + "; ".join(f"T{turn.n} date={turn.args.get('date')} time={turn.args.get('time')} ok={turn.result.get('ok') is True}" for turn in callbacks))
    transfers = tools(turns, "transfer_to_agent")
    if transfers:
        lines.append("transfer_to_agent calls: " + "; ".join(f"T{turn.n} queue={turn.args.get('queue')} ok={turn.result.get('ok') is not False}" for turn in transfers))
    closes = tools(turns, "close_call")
    if closes:
        last = closes[-1]
        lines.append(f"close_call at T{last.n} outcome={last.args.get('outcome')} reason={last.args.get('reason')}")
    else:
        lines.append("close_call: none")
    failed = [turn for turn in turns if turn.kind == "tool" and turn.result.get("ok") is False]
    if failed:
        lines.append("failed tool calls: " + "; ".join(f"T{turn.n} {turn.name} {turn.result.get('error')}" for turn in failed))
    else:
        lines.append("failed tool calls: none")
    return lines

def render(messages, case=None, facts=True):
    """One text block. Set facts=False for Judge B, which must not see the FACTS block."""
    turns = parse_messages(messages)
    variables = variables_of(messages, case)
    header = "CALL VARIABLES: " + " ".join(f"{key}={value}" for key, value in variables.items())
    body = []
    for turn in turns:
        if turn.kind == "agent":
            body.append(f"[T{turn.n} AGENT] {turn.text}")
        elif turn.kind == "customer":
            body.append(f"[T{turn.n} CUSTOMER] {turn.text}")
        else:
            body.append(f"[T{turn.n} TOOL {turn.name}] args={json.dumps(turn.args, default=str)} -> {json.dumps(turn.result, default=str)}")
    parts = [header, *body]
    if facts:
        parts.append("FACTS (computed by code):")
        parts.extend("- " + line for line in fact_lines(turns))
    return "\n".join(parts), turns
