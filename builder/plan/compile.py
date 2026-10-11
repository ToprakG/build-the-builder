"""Compile a procedure plan into a workflow. Deterministic: the same plan and tool API give the same JSON.

A tool step is an api_request node, then a code node that derives counts, selections and spoken amounts.
An ask step extracts the customer's answer and branches on it. A say step speaks a template.
"""

from __future__ import annotations

import json
import re

from builder.agentic.tools.openapi_summary import summarize
from builder.plan.money import ROWS_SOURCE, render_money
from builder.plan.schema import NAME, PlanError, _branch_when, _line_text, complete, validate

SLUG = re.compile(r"[^A-Za-z0-9_]+")


def _summary(api: dict) -> dict:
    if isinstance(api, dict) and "openapi" in api:
        return summarize(api)
    return api or {}


def _slug(text: str) -> str:
    s = SLUG.sub("_", str(text or "").strip()).strip("_")
    if not s:
        s = "step"
    if s[0].isdigit():
        s = "n_" + s
    return s


def _lookup(response: dict, path: str) -> dict | None:
    """The schema node at path. A leading 'data.' is the response envelope, not a body field."""
    parts = [p for p in str(path or "").split(".") if p and not p.isdigit()]
    if parts and parts[0] == "data":
        parts = parts[1:]
    cur = None
    fields = response or {}
    for part in parts:
        cur = fields.get(part) if isinstance(fields, dict) else None
        if not isinstance(cur, dict):
            return None
        if cur.get("type") == "array":
            items = cur.get("items") or {}
            fields = items.get("properties") or items if isinstance(items, dict) else {}
        elif cur.get("properties"):
            fields = cur["properties"]
        else:
            fields = {}
    return cur


def _is_list(spec: dict, path: str, output: dict) -> bool:
    if output.get("list") is True:
        return True
    found = _lookup((spec or {}).get("response") or {}, path)
    return bool(found and found.get("type") == "array")


def _json_type(spec_type: str) -> str:
    return spec_type if spec_type in ("string", "number", "integer", "boolean") else "string"


def _leaf(field_spec: dict, default):
    kind = _json_type((field_spec or {}).get("type") or "string")
    if isinstance(default, str) and default.startswith("{{") and default.endswith("}}"):
        return {"type": kind, "default": default}
    if kind == "integer" and isinstance(default, str) and default.lstrip("-").isdigit():
        default = int(default)
    if kind == "number" and isinstance(default, str):
        try:
            default = float(default) if "." in default else int(default)
        except ValueError:
            pass
    return {"type": kind, "default": default}


def _api_tool(spec: dict, name: str, inputs: dict) -> dict:
    request = spec.get("request") or {}
    leaves = {}
    for field, source in (inputs or {}).items():
        if field not in request:
            continue
        if isinstance(source, dict) and "from" in source:
            default = "{{" + str(source["from"]) + "}}"
        elif isinstance(source, dict) and "literal" in source:
            default = source["literal"]
        else:
            default = source
        leaves[field] = _leaf(request[field], default)
    for field in spec.get("required") or []:
        if field not in leaves and field in request:
            leaves[field] = _leaf(request[field], "")
    return {
        "type": "function",
        "config": {"timeoutMs": 12000, "maxRetries": 0},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": spec.get("summary") or name,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": spec["url"]},
                    "method": {"type": "string", "default": spec["method"]},
                    "headers": {"type": "object", "properties": {
                        "Content-Type": {"type": "string", "default": "application/json"}}},
                    "body": {"type": "object", "properties": leaves,
                             "required": [f for f in (spec.get("required") or []) if f in leaves]},
                },
            },
        },
    }


def _numeric_targets(plan: dict) -> set[str]:
    found = set()
    for step in plan.get("steps") or []:
        for op in list(step.get("before") or []) + list(step.get("after") or []):
            if isinstance(op, dict) and op.get("op") in ("count", "unique") and op.get("into"):
                found.add(str(op["into"]))
    return found


def _prefix(op: dict, step_tool: str | None) -> str:
    tool = op.get("tool") or step_tool or ""
    return f"{tool}_response.{op.get('list_path')}"


def _init_and_body(ops: list, step_tool: str | None) -> tuple[list[str], list[str], bool, bool]:
    """Assignments that sit outside the try (literals) and the body inside it."""
    init, body = [], []
    needs_rows, needs_money = False, False
    for op in ops:
        kind = op.get("op")
        into = str(op.get("into") or "")
        if kind in ("count", "unique", "join", "at", "select"):
            needs_rows = True
        if kind == "money":
            needs_money = True
        if kind == "count":
            init.append(f"vars[{into!r}] = '0'")
            init.append(f"vars[{into + '_n'!r}] = 0")
            body.append(f"n = len(_rows({_prefix(op, step_tool)!r}))")
            body.append(f"vars[{into!r}] = str(n)")
            body.append(f"vars[{into + '_n'!r}] = n")
        elif kind == "unique":
            init.append(f"vars[{into!r}] = '0'")
            init.append(f"vars[{into + '_n'!r}] = 0")
            body.append("seen = []")
            body.append(f"for row in _rows({_prefix(op, step_tool)!r}):")
            body.append(f"    val = str(_cell(row, {op.get('field')!r}) or '')")
            body.append("    if val not in seen:")
            body.append("        seen.append(val)")
            body.append("n = len(seen)")
            body.append(f"vars[{into!r}] = str(n)")
            body.append(f"vars[{into + '_n'!r}] = n")
        elif kind == "join":
            init.append(f"vars[{into!r}] = ''")
            sep = op.get("sep") if op.get("sep") is not None else ", "
            body.append("parts = []")
            body.append(f"for row in _rows({_prefix(op, step_tool)!r}):")
            body.append(f"    val = str(_cell(row, {op.get('field')!r}) or '').strip()")
            body.append("    if val:")
            body.append("        parts.append(val)")
            body.append(f"vars[{into!r}] = {sep!r}.join(parts)")
        elif kind == "at":
            init.append(f"vars[{into!r}] = ''")
            if op.get("index_var"):
                body.append(f"idx_text = str(vars.get({op['index_var']!r}) or '0')")
                body.append("idx = int(idx_text) if idx_text.lstrip('-').isdigit() else 0")
            else:
                body.append(f"idx = {int(op.get('index') or 0)}")
            body.append(f"rows = _rows({_prefix(op, step_tool)!r})")
            body.append("item = rows[idx] if 0 <= idx < len(rows) else None")
            body.append(f"vars[{into!r}] = '' if item is None else str(_cell(item, {op.get('field')!r}) or '')")
        elif kind == "select":
            prefix_name = str(op.get("into"))
            init.append(f"vars[{prefix_name + '_found'!r}] = 'no'")
            for field in op.get("fields") or []:
                init.append(f"vars[{prefix_name + '_' + str(field)!r}] = ''")
            body.append(f"rows = _rows({_prefix(op, step_tool)!r})")
            body.append("found = None")
            body.append(f"want = str(vars.get({op.get('where_var')!r}) or '')")
            body.append("for row in rows:")
            body.append(f"    if str(_cell(row, {op.get('where_field')!r}) or '') == want:")
            body.append("        found = row")
            body.append("        break")
            body.append(f"vars[{prefix_name + '_found'!r}] = 'yes' if found is not None else 'no'")
            for field in op.get("fields") or []:
                target = prefix_name + "_" + str(field)
                body.append(
                    f"vars[{target!r}] = '' if found is None else str(_cell(found, {str(field)!r}) or '')")
        elif kind == "money":
            init.append(f"vars[{into!r}] = ''")
            if op.get("currency_from"):
                code = f"vars.get({op['currency_from']!r})"
            else:
                code = repr(str(op.get("currency") or ""))
            body.append(f"vars[{into!r}] = speak_amount(vars.get({op.get('from')!r}), {code})")
        elif kind == "copy":
            init.append(f"vars[{into!r}] = ''")
            body.append(f"vars[{into!r}] = str(vars.get({op.get('from')!r}) or '')")
        elif kind == "first":
            init.append(f"vars[{into!r}] = ''")
            sources = op.get("from") or []
            if isinstance(sources, str):
                sources = [sources]
            body.append("picked = ''")
            body.append(f"for key in {tuple(sources)!r}:")
            body.append("    val = str(vars.get(key) or '').strip()")
            body.append("    if val:")
            body.append("        picked = val")
            body.append("        break")
            body.append(f"vars[{into!r}] = picked")
        elif kind == "flag":
            init.append(f"vars[{into!r}] = 'no'")
            body.append(f"raw = str(vars.get({op.get('from')!r}) or '').strip()")
            body.append("empty = raw == '' or raw.lower() in ('none', 'null')")
            if op.get("zero_is_empty"):
                body.append("if not empty:")
                body.append("    try:")
                body.append("        empty = float(raw.replace(',', '.')) == 0")
                body.append("    except Exception:")
                body.append("        empty = False")
            body.append(f"vars[{into!r}] = 'no' if empty else 'yes'")
    return init, body, needs_rows, needs_money


def _snippet(ops: list, step_tool: str | None, money: dict) -> str:
    init, body, needs_rows, needs_money = _init_and_body(ops, step_tool)
    init.insert(0, "vars['plan_ready'] = 'yes'")
    parts = []
    if needs_rows:
        parts.append(ROWS_SOURCE.rstrip("\n"))
    if needs_money:
        parts.append(render_money(money).rstrip("\n"))
    parts.extend(init)
    indented = "\n".join("    " + line for line in body) if body else "    pass"
    parts.append("try:\n" + indented + "\nexcept Exception:\n    pass")
    return "\n".join(parts) + "\n"


def _code_node(nid: str, code: str, transitions: list) -> dict:
    default = transitions[-1]["target_node"]
    return {
        "name": nid, "node_type": "code", "passthrough": True, "passthrough_default_transition": default,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {
            "name": "code_exec", "handler": "__function__:code_exec",
            "parameters": {"type": "object", "properties": {
                "code": {"type": "string", "default": code},
                "timeout_ms": {"type": "number", "default": 10000}}}}}],
        "transitions": transitions,
    }


def _eq_group(variable: str, op: str, value) -> dict:
    """Variable checks sit in an or-group. A numeric value is tested as text and as a number."""
    leaves = [{"variable": variable, "operator": op, "operand": value}]
    if op == "equals" and isinstance(value, str) and value.lstrip("-").isdigit():
        leaves.append({"variable": variable, "operator": op, "operand": int(value)})
    if op == "equals" and isinstance(value, int) and not isinstance(value, bool):
        leaves.append({"variable": variable, "operator": op, "operand": str(value)})
    return {"logic": "or", "conditions": leaves}


def _condition(when: dict | None, call_id: str, tool: str | None, numeric: set[str]) -> dict | None:
    if when is None:
        return {"operator": "always"}
    variable = str(when.get("variable") or "")
    op = when.get("op") or "equals"
    value = when.get("value")
    if variable == "@status":
        variable = f"{tool}_status_code" if tool else f"{call_id}_status_code"
    elif variable == "@failure":
        op = "greater_than"
        value = 0
        variable = f"{tool}_failure_count" if tool else f"{call_id}_failure_count"
    elif op in ("greater_than", "less_than") and variable in numeric:
        variable = variable + "_n"
        if isinstance(value, str) and value.lstrip("-").isdigit():
            value = int(value)
    if op in ("is_set", "is_not_set"):
        return {"logic": "or", "conditions": [{"variable": variable, "operator": op}]}
    return _eq_group(variable, op, value)


def _transitions(step: dict, resolve, call_id: str, tool: str | None, numeric: set[str], default: str) -> list:
    """Conditional branches, then one always. A conditional that lands on the same node as always is dropped,
    because the engine merges same-target transitions and an unconditional merge would skip the ones above it."""
    default_target = default
    for branch in step.get("branches") or []:
        if isinstance(branch, dict) and branch.get("goto") and _branch_when(branch) is None:
            default_target = resolve(branch["goto"], step)
    out = []
    for branch in step.get("branches") or []:
        if not isinstance(branch, dict) or not branch.get("goto"):
            continue
        when = _branch_when(branch)
        if when is None:
            continue
        target = resolve(branch["goto"], step)
        if target == default_target:
            continue
        cond = _condition(when, call_id, tool, numeric)
        out.append({"target_node": target, "description": branch.get("description") or "branch", "condition": cond})
    out.append({"target_node": default_target, "description": "continue", "condition": {"operator": "always"}})
    return out


def _mapping(spec: dict, outputs: list) -> list:
    mapped = []
    for output in outputs or []:
        if not isinstance(output, dict) or not output.get("path") or not output.get("variable"):
            continue
        if not NAME.match(str(output["variable"])):
            continue
        if _is_list(spec, output["path"], output):
            continue
        mapped.append({"path": output["path"], "variable": output["variable"]})
    return mapped


def _task(step: dict, line: str) -> str:
    if step.get("task"):
        return str(step["task"])
    if line:
        return "Say exactly this, and nothing else:\n" + line
    return "Wait for the caller. Do not invent figures, dates, or reasons."


def _end_node(nid: str, line: str) -> dict:
    props = {"message_mode": {"type": "string", "default": "custom" if line else "none"}}
    if line:
        props["spoken_message"] = {"type": "string", "default": line}
    return {
        "name": nid, "node_type": "end_call", "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {
            "name": "end_" + nid, "handler": "__function__:end_call",
            "parameters": {"type": "object", "properties": props}}},
    }


def _say_or_ask(nid: str, step: dict, line: str, transitions: list, passthrough_to: str | None) -> dict:
    node = {
        "name": nid, "node_type": "conversation", "task_message": _task(step, line),
        "on_enter": {"type": "tts", "text": line} if line else {"type": "silent"},
        "transitions": transitions,
    }
    if passthrough_to:
        node["passthrough"] = True
        node["passthrough_default_transition"] = passthrough_to
    if step.get("kind") == "ask":
        schema = []
        for field in step.get("extract") or []:
            item = {
                "name": field["name"], "type": "string",
                "description": field.get("description") or field["name"],
                "min_confidence": 0.6 if field.get("enum") else 0.4,
            }
            if field.get("enum"):
                item["enum_values"] = list(field["enum"])
            schema.append(item)
        if schema:
            node["extraction_schema"] = schema
        if step.get("max_turns"):
            node["max_node_turns"] = int(step["max_turns"])
            node["max_turn_transition"] = transitions[-1]["target_node"]
    return node


def _system(plan: dict) -> str:
    parts = []
    if str(plan.get("system") or "").strip():
        parts.append(str(plan["system"]).strip())
    else:
        parts.append("Follow the spoken lines. Do not invent figures, dates, or reasons.")
    clock = plan.get("clock")
    if clock:
        parts.append(
            f"The current date and time is {{{{{clock}}}}}. Every date decision uses {clock}. Never use the system clock.")
    if plan.get("language"):
        parts.append(f"Speak in the language of the spoken lines. The language code is {plan['language']}.")
    return "\n\n".join(parts)


class _Graph:
    def __init__(self, plan: dict, api: dict):
        self.plan = plan
        self.api = api
        self.tools = api.get("tools") or {}
        self.steps = {s["id"]: s for s in plan["steps"]}
        self.numeric = _numeric_targets(plan)
        self.nodes: dict[str, dict] = {}

    def entry(self, step_id: str) -> str:
        step = self.steps[step_id]
        sid = _slug(step_id)
        if step.get("before"):
            return sid + "_before"
        if step.get("kind") in ("tool", "handoff"):
            return sid + "_call"
        return sid

    def resolve(self, goto: str, step: dict) -> str:
        """A step's own ask loops back to the question, not to the code that prepared it."""
        if goto == step.get("id") and step.get("kind") == "ask":
            return _slug(goto)
        return self.entry(goto)

    def default_of(self, step: dict) -> str:
        if step.get("else"):
            return self.resolve(step["else"], step)
        if step.get("then"):
            return self.entry(step["then"])
        for branch in step.get("branches") or []:
            if isinstance(branch, dict) and _branch_when(branch) is None and branch.get("goto"):
                return self.resolve(branch["goto"], step)
        for other in self.plan["steps"]:
            if other.get("kind") == "end":
                return self.entry(other["id"])
        return _slug(step["id"])

    def emit_code(self, nid: str, ops: list, step: dict, nxt: str):
        # The code node itself has one way out. The step's branches live on the node after it.
        tr = [{"target_node": nxt, "description": "continue", "condition": {"operator": "always"}}]
        self.nodes[nid] = _code_node(nid, _snippet(ops, step.get("tool"), self.plan.get("money") or {}), tr)

    def emit_tool(self, step: dict):
        sid = _slug(step["id"])
        call, after = sid + "_call", sid + "_after"
        spec = self.tools[step["tool"]]
        default = self.default_of(step)
        if step.get("before"):
            self.emit_code(sid + "_before", step["before"], step, call)
        mapping = _mapping(spec, step.get("outputs") or [])
        node = {
            "name": call, "node_type": "api_request", "passthrough": True,
            "passthrough_default_transition": after, "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True,
            "pre_actions": [_api_tool(spec, step["tool"], step.get("inputs") or {})],
            "transitions": [{"target_node": after, "description": "continue",
                             "condition": {"operator": "always"}}],
        }
        if mapping:
            node["response_mapping"] = mapping
        self.nodes[call] = node
        branches = _transitions(step, self.resolve, call, step["tool"], self.numeric, default)
        self.emit_code(after, step.get("after") or [], step, branches[-1]["target_node"])
        # emit_code wrote a single always. Replace it with the real branches. The last one stays unconditional.
        self.nodes[after]["transitions"] = branches
        self.nodes[after]["passthrough_default_transition"] = branches[-1]["target_node"]

    def emit_say(self, step: dict):
        sid = _slug(step["id"])
        line = _line_text(self.plan, step)
        default = self.default_of(step)
        if step.get("before"):
            self.emit_code(sid + "_before", step["before"], step, sid)
        if step.get("branches"):
            transitions = _transitions(step, self.resolve, sid, step.get("tool"), self.numeric, default)
            self.nodes[sid] = _say_or_ask(sid, step, line, transitions, None)
        else:
            transitions = [{"target_node": default, "description": "continue",
                            "condition": {"operator": "always"}}]
            self.nodes[sid] = _say_or_ask(sid, step, line, transitions, default)

    def emit_ask(self, step: dict):
        sid = _slug(step["id"])
        line = _line_text(self.plan, step)
        if step.get("before"):
            self.emit_code(sid + "_before", step["before"], step, sid)
        default = self.default_of(step)
        on_max = step.get("on_max")
        transitions = _transitions(step, self.resolve, sid, step.get("tool"), self.numeric, default)
        if on_max:
            # the turn cap is stored on the node; the transitions still need their own else
            pass
        node = _say_or_ask(sid, step, line, transitions, None)
        if on_max:
            node["max_turn_transition"] = self.entry(on_max)
        self.nodes[sid] = node

    def emit_end(self, step: dict):
        sid = _slug(step["id"])
        self.nodes[sid] = _end_node(sid, _line_text(self.plan, step))

    def build(self) -> dict:
        for step in self.plan["steps"]:
            kind = step.get("kind")
            if kind in ("tool", "handoff"):
                self.emit_tool(step)
            elif kind == "say":
                self.emit_say(step)
            elif kind == "ask":
                self.emit_ask(step)
            elif kind == "end":
                self.emit_end(step)
        ends = [nid for nid, node in self.nodes.items() if node.get("node_type") == "end_call"]
        blocked = [nid for nid, node in self.nodes.items() if node.get("node_type") != "conversation"]
        globals_ = {}
        for item in self.plan.get("globals") or []:
            gid = "g_" + _slug(item.get("id") or "interrupt")
            target = self.entry(item["goto"])
            exclude = list(dict.fromkeys([*blocked, *ends, *(item.get("exclude") or [])]))
            node = {
                "name": gid, "node_type": "conversation",
                "description": item["description"],
                "condition": {"operator": "matches_intent", "description": item["description"]},
                "task_message": item.get("task") or "Continue.",
                "on_enter": {"type": "silent"},
                "passthrough": True, "passthrough_default_transition": target,
                "excluded_nodes": exclude,
                "transitions": [{"target_node": target, "description": "continue",
                                 "condition": {"operator": "always"}}],
            }
            if item.get("line") or item.get("text"):
                node["on_enter"] = {"type": "tts", "text": _line_text(self.plan, item)}
            globals_[gid] = node
        wf = {
            "initial_node": self.entry(self.plan["steps"][0]["id"]),
            "system_prompt": _system(self.plan),
            "nodes": self.nodes,
        }
        if globals_:
            wf["global_nodes"] = globals_
        return wf


def compile_plan(plan: dict, api: dict) -> dict:
    summary = _summary(api)
    plan = complete(plan)
    errors = validate(plan, summary)
    if errors:
        raise PlanError(errors)
    missing = [s.get("tool") for s in plan["steps"]
               if s.get("kind") in ("tool", "handoff") and s.get("tool") not in (summary.get("tools") or {})]
    if missing:
        raise PlanError([f"unknown tool {name}" for name in missing])
    return _Graph(plan, summary).build()


def used_tools(plan: dict) -> set[str]:
    return {s.get("tool") for s in (plan.get("steps") or []) if isinstance(s, dict) and s.get("tool")}


def dump(wf: dict) -> str:
    return json.dumps(wf, indent=1, ensure_ascii=False) + "\n"
