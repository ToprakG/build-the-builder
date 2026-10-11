"""api_call tool objects built from the tool API (docs/tools-and-code.md "Tool function shape", "body field rules").

- url/method/headers are baked with `default`, never `const`.
- every body field is a leaf under body.properties with its own default; call variables are baked as {{name}}.
- a model-filled field uses default "{{<field name>}}" (no variable of that name is ever set), with type, enum and
  description on the leaf.
- writes get maxRetries 0; reads get the brief's retry count.
"""

from __future__ import annotations

READS = ("verify_identity", "get_account")


def api_tool(api, name: str, body: dict, *, description: str = "", pre: str | None = None, post: str | None = None,
             retries: int = 0, timeout_ms: int = 12000, config_extra: dict | None = None,
             response_mapping: list | None = None) -> dict:
    """`body`: {field: leaf dict}. Fields the API requires but `body` lacks get a literal '' default."""
    spec = api.tools[name]
    req = api.request(name)
    leaves = {}
    for field, leaf in body.items():
        if field not in req:
            continue
        leaves[field] = leaf
    for field in spec.get("required") or []:
        if field not in leaves:
            leaves[field] = {"type": req.get(field, {}).get("type", "string"), "default": ""}
    tool = {
        "type": "function",
        "config": {"timeoutMs": timeout_ms, "maxRetries": retries if name in READS else 0, **(config_extra or {})},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description or spec.get("summary", "") or name,
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
    if response_mapping:
        tool["response_mapping"] = response_mapping
    if pre:
        tool["pre_process_code"] = pre
    if post:
        tool["post_process_code"] = post
    return tool


def baked(api, tool: str, field: str, value: str) -> dict:
    return {"type": _json_type(api.field_type(tool, field)), "default": value}


def model_leaf(api, tool: str, field: str, description: str, enum: list | None = None) -> dict:
    leaf = {"type": _json_type(api.field_type(tool, field)), "description": description, "default": "{{" + field + "}}"}
    api_enum = api.enum(tool, field)
    if enum is not None:
        enum = [e for e in enum if api_enum is None or e in api_enum]
        if enum:
            leaf["enum"] = enum
    elif api_enum:
        leaf["enum"] = api_enum
    return leaf


def customer_leaf(api, tool: str) -> dict:
    return {"customer_id": {"type": "string", "default": "{{customer_id}}"}} if "customer_id" in api.request(tool) else {}


def _json_type(t: str) -> str:
    return t if t in ("string", "number", "integer", "boolean") else "string"
