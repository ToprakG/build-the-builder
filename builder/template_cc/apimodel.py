"""What T-CC needs to know about a bank's tool API, read from the OpenAPI file (never from a brief or a config).

Wraps the A-agent `openapi_summary.summarize` (read-only import) and adds: enums per field, the account list field
and its item fields, and the roles of the identity-check response fields (verified, all verified, answers left,
hint).
"""

from __future__ import annotations

import json
from pathlib import Path

from builder.agentic.tools import openapi_summary

# The template compiler still speaks these operationIds. The agentic summary does not require them.
KIT_TOOLS = ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback",
             "transfer_to_agent", "close_call")


def _resolve(spec: dict, node, depth: int = 0):
    if depth > 12 or not isinstance(node, dict):
        return node if isinstance(node, dict) else {}
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        cur = spec
        for part in ref[2:].split("/"):
            cur = cur.get(part, {}) if isinstance(cur, dict) else {}
        return _resolve(spec, cur, depth + 1)
    return node


class ApiModel:
    def __init__(self, spec: dict):
        self.spec = spec
        self.summary = openapi_summary.summarize(spec)
        self.tools = self.summary["tools"]

    @classmethod
    def load(cls, path) -> "ApiModel":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def has(self, tool: str) -> bool:
        return tool in self.tools

    def request(self, tool: str) -> dict:
        return (self.tools.get(tool) or {}).get("request") or {}

    def response(self, tool: str) -> dict:
        return (self.tools.get(tool) or {}).get("response") or {}

    def enum(self, tool: str, field: str) -> list | None:
        f = self.request(tool).get(field) or {}
        return list(f["enum"]) if f.get("enum") else None

    def field_type(self, tool: str, field: str) -> str:
        return (self.request(tool).get(field) or {}).get("type", "string")

    def field_desc(self, tool: str, field: str) -> str:
        return (self.request(tool).get(field) or {}).get("description", "") or ""

    # --- get_account ---------------------------------------------------------------------------------------------
    def _operation(self, tool: str) -> dict:
        t = self.tools.get(tool)
        if not t:
            return {}
        item = (self.spec.get("paths") or {}).get(t["path"]) or {}
        return item.get(t["method"].lower()) or {}

    def account_list_field(self) -> str:
        """The array field of the get_account response (the product list)."""
        for name, f in self.response("get_account").items():
            if f.get("type") == "array":
                return name
        return "products"

    def account_item_fields(self) -> dict:
        """Item properties of the product list, if the API declares them: {name: {type, enum?}}."""
        op = self._operation("get_account")
        for status, resp in (op.get("responses") or {}).items():
            if not str(status).startswith("2"):
                continue
            resp = _resolve(self.spec, resp)
            schema = _resolve(self.spec, ((resp.get("content") or {}).get("application/json") or {}).get("schema") or {})
            arr = _resolve(self.spec, (schema.get("properties") or {}).get(self.account_list_field()) or {})
            items = _resolve(self.spec, arr.get("items") or {})
            out = {}
            for k, v in (items.get("properties") or {}).items():
                v = _resolve(self.spec, v)
                out[k] = {"type": v.get("type", ""), "enum": v.get("enum")}
            return out
        return {}

    # --- verify_identity response roles ----------------------------------------------------------------------------
    def identity_roles(self) -> dict:
        """Response field names by role, inferred from types and generic name parts. Missing roles are ''."""
        resp = self.response("verify_identity")
        roles = {"verified": "", "all": "", "left": "", "hint": "", "hint_values": []}
        for name, f in resp.items():
            low, typ = name.lower(), f.get("type")
            if typ == "boolean" and "all" in low:
                roles["all"] = roles["all"] or name
            elif typ == "boolean" and low != "ok" and "verif" in low:
                roles["verified"] = roles["verified"] or name
            elif typ == "integer" and ("left" in low or "remain" in low):
                roles["left"] = roles["left"] or name
            elif typ == "string" and f.get("enum"):
                roles["hint"] = roles["hint"] or name
                roles["hint_values"] = roles["hint_values"] or list(f["enum"])
        if not roles["verified"]:
            roles["verified"] = next((n for n, f in resp.items() if f.get("type") == "boolean" and n != "ok"
                                      and n != roles["all"]), "")
        return roles

    def render(self, max_chars: int = 14000) -> str:
        return openapi_summary.render(self.summary, max_chars)
