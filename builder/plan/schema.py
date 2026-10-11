"""The procedure plan. A list of steps. Each step calls a tool, speaks a line, asks a question, or ends the call.

Branches read a tool result or the customer's answer. Spoken lines are templates. Money formatting is a table.
Nothing in this schema is specific to one product.
"""

from __future__ import annotations

import copy
import re

KINDS = ("tool", "handoff", "say", "ask", "end")
OPS = ("equals", "not_equals", "greater_than", "less_than", "contains", "is_set", "is_not_set")
DERIVE_OPS = ("count", "unique", "join", "at", "select", "money", "copy", "first", "flag")
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PlanError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _branch_when(branch: dict) -> dict | None:
    when = branch.get("when")
    if isinstance(when, dict):
        return when
    if branch.get("variable"):
        return {"variable": branch.get("variable"), "op": branch.get("op") or "equals", "value": branch.get("value")}
    return None


def _has_open_end(step: dict) -> bool:
    if step.get("else") or step.get("then"):
        return True
    for branch in step.get("branches") or []:
        if isinstance(branch, dict) and _branch_when(branch) is None and branch.get("goto"):
            return True
    return False


def complete(plan: dict) -> dict:
    """Fill structural defaults. Does not invent a spoken sentence or a tool argument."""
    plan = copy.deepcopy(plan if isinstance(plan, dict) else {})
    plan["language"] = plan.get("language") or "en"
    plan["system"] = plan.get("system") or ""
    plan["call_variables"] = list(plan.get("call_variables") or [])
    plan["lines"] = dict(plan.get("lines") or {})
    plan["globals"] = list(plan.get("globals") or [])
    money = dict(plan.get("money") or {})
    money.setdefault("unit", "major")
    money.setdefault("in_decimal", "either")
    money.setdefault("group_sep", "")
    money.setdefault("template", "{major} {name} {minor}")
    money.setdefault("zero", "{name} {absent}")
    money.setdefault("absent", "0")
    money.setdefault("negative", "-")
    money.setdefault("currencies", [])
    plan["money"] = money
    steps = [s for s in (plan.get("steps") or []) if isinstance(s, dict)]
    if not any(s.get("kind") == "end" for s in steps):
        steps.append({"id": "goodbye", "kind": "end"})
    end_id = next(s.get("id") for s in steps if s.get("kind") == "end")
    for step in steps:
        step.setdefault("before", [])
        step.setdefault("after", [])
        step.setdefault("branches", [])
        step.setdefault("outputs", [])
        step.setdefault("inputs", {})
        if step.get("kind") == "ask" and not step.get("else"):
            step["else"] = step.get("id")
        if step.get("kind") in ("tool", "handoff") and not _has_open_end(step):
            step["else"] = end_id
    plan["steps"] = steps
    return plan


def _line_text(plan: dict, step: dict) -> str:
    if step.get("text"):
        return str(step["text"])
    key = step.get("line")
    found = (plan.get("lines") or {}).get(key) if key else None
    if isinstance(found, dict):
        return str(found.get("text") or "")
    if isinstance(found, str):
        return found
    return ""


def validate(plan: dict, api: dict | None = None) -> list[str]:
    """Structural problems. A tool name or a field the API does not have is an error."""
    errors = []
    tools = ((api or {}).get("tools") or {}) if isinstance(api, dict) else {}
    steps = plan.get("steps") or []
    if not steps:
        errors.append("plan has no steps")
        return errors
    ids = []
    for step in steps:
        if not isinstance(step, dict):
            errors.append("a step is not an object")
            continue
        sid = step.get("id")
        if not isinstance(sid, str) or not NAME.match(sid):
            errors.append(f"step id {sid!r} must be a name of letters, digits and underscores")
            continue
        if sid in ids:
            errors.append(f"duplicate step id {sid}")
        ids.append(sid)
        if step.get("kind") not in KINDS:
            errors.append(f"{sid}: kind must be one of {', '.join(KINDS)}")
        for op in list(step.get("before") or []) + list(step.get("after") or []):
            if not isinstance(op, dict) or op.get("op") not in DERIVE_OPS:
                errors.append(f"{sid}: derive op must be one of {', '.join(DERIVE_OPS)}")
                continue
            if op.get("op") in ("count", "unique", "join", "at", "select") and not (op.get("tool") or step.get("tool")):
                errors.append(f"{sid}: {op.get('op')} needs the tool whose result it reads")
            into = op.get("into")
            if op.get("op") != "select" and into and not NAME.match(str(into)):
                errors.append(f"{sid}: derive target {into!r} is not a variable name")
            if op.get("op") == "select":
                if not NAME.match(str(op.get("into") or "")):
                    errors.append(f"{sid}: select needs an into prefix")
                for field in op.get("fields") or []:
                    if not NAME.match(str(field)):
                        errors.append(f"{sid}: select field {field!r} is not a name")
        if step.get("kind") in ("tool", "handoff"):
            name = step.get("tool")
            spec = tools.get(name) if name else None
            if tools and not spec:
                errors.append(f"{sid}: tool {name!r} is not in the tool API")
            elif spec:
                for field, source in (step.get("inputs") or {}).items():
                    if field not in (spec.get("request") or {}):
                        errors.append(f"{sid}: {field!r} is not a request field of {name}")
                    elif isinstance(source, dict) and "literal" in source:
                        enum = (spec["request"][field] or {}).get("enum")
                        if enum is not None and source["literal"] not in enum:
                            errors.append(f"{sid}: {field} value {source['literal']!r} is not in {enum}")
                missing = [f for f in (spec.get("required") or []) if f not in (step.get("inputs") or {})]
                if missing:
                    errors.append(f"{sid}: missing request fields {missing}")
        if step.get("kind") in ("say", "ask") and not _line_text(plan, step):
            errors.append(f"{sid}: needs a spoken line")
        if step.get("kind") == "ask":
            for field in step.get("extract") or []:
                if not isinstance(field, dict) or not NAME.match(str(field.get("name") or "")):
                    errors.append(f"{sid}: extract field needs a name")
        for branch in step.get("branches") or []:
            if not isinstance(branch, dict) or not branch.get("goto"):
                errors.append(f"{sid}: a branch needs a goto")
                continue
            when = _branch_when(branch)
            if when is not None and when.get("op", "equals") not in OPS:
                errors.append(f"{sid}: unknown branch op {when.get('op')!r}")
        for key in ("else", "then", "on_max"):
            if step.get(key) and step[key] not in ids and step[key] != step.get("id"):
                # ids may be incomplete mid-loop; checked again below
                pass
    known = set(ids)
    for step in steps:
        if not isinstance(step, dict):
            continue
        sid = step.get("id")
        for branch in step.get("branches") or []:
            if isinstance(branch, dict) and branch.get("goto") not in known:
                errors.append(f"{sid}: goto {branch.get('goto')!r} is not a step")
        for key in ("else", "then", "on_max"):
            if step.get(key) and step[key] not in known:
                errors.append(f"{sid}: {key} {step[key]!r} is not a step")
    for item in plan.get("globals") or []:
        if not isinstance(item, dict) or not item.get("description") or item.get("goto") not in known:
            errors.append("a global interrupt needs a description and a goto step")
    money = plan.get("money") or {}
    for piece in (money.get("template"), money.get("zero")):
        if isinstance(piece, str) and ("{{" in piece or "}}" in piece):
            errors.append("a money template uses single braces, like {major} {name} {minor}")
    return errors


EXTRACT_SCHEMA = {
    "type": "object",
    "required": ["language", "call_variables", "lines", "steps"],
    "properties": {
        "language": {"type": "string"},
        "system": {"type": "string"},
        "clock": {"type": "string"},
        "call_variables": {"type": "array", "items": {"type": "string"}},
        "money": {"type": "object"},
        "lines": {"type": "object"},
        "steps": {"type": "array"},
        "globals": {"type": "array"},
    },
}
