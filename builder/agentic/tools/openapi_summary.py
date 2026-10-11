"""Summarise a bank's tool API file (OpenAPI 3) for the builder agent and for lint.

Standard library only. Tool names are whatever operationIds the file uses. A read is a GET or HEAD;
every other method writes. Roles (identity, account, record, note, callback, transfer, close) are
inferred from the operation's name, summary and shape, never from a fixed list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

READ_METHODS = frozenset({"GET", "HEAD"})
_ROLE_HINTS = {
    "close": re.compile(r"\bclose\b|end the call|end call", re.I),
    "identity": re.compile(r"ident|verif", re.I),
    "account": re.compile(r"account|product", re.I),
    "callback": re.compile(r"callback|schedule", re.I),
    "transfer": re.compile(r"transfer|handoff|queue", re.I),
    "record": re.compile(r"promise|payment|\brecord\b", re.I),
    "note": re.compile(r"\bnote\b", re.I),
}


def is_write(tool: dict, name: str | None = None, summary: dict | None = None) -> bool:
    """A GET or HEAD is a read. So is the identity or account role, even when the API uses POST.
    Every other method records something. The operationId does not matter."""
    if str((tool or {}).get("method") or "").upper() in READ_METHODS:
        return False
    if summary and name:
        roles = summary.get("roles") or infer_roles(summary)
        if name in (roles.get("identity"), roles.get("account")):
            return False
    return True


def _spaced(text: str) -> str:
    return str(text or "").replace("_", " ").replace("-", " ")


def _fields(spec: dict, side: str) -> dict:
    return (spec or {}).get(side) or {}


def _has_number(fields: dict) -> bool:
    return any(f.get("type") in ("number", "integer") and "enum" not in f for f in fields.values() if isinstance(f, dict))


def _has_when(fields: dict) -> bool:
    for name, f in fields.items():
        if not isinstance(f, dict):
            continue
        if f.get("format") in ("date", "date-time", "time"):
            return True
        if re.search(r"date|time", _spaced(name), re.I):
            return True
    return False


def _has_enum(fields: dict) -> bool:
    return any(isinstance(f, dict) and "enum" in f for f in fields.values())


def infer_roles(summary: dict) -> dict[str, str]:
    """role -> operationId. Highest score wins; one tool fills one role."""
    tools = (summary or {}).get("tools") or {}
    pairs = []
    for name, spec in tools.items():
        blob = _spaced(f"{name} {spec.get('summary', '')} {spec.get('path', '')}")
        named = _spaced(name)
        req, resp = _fields(spec, "request"), _fields(spec, "response")
        shape = {
            "identity": 3 if any(isinstance(f, dict) and f.get("type") == "boolean" and n.lower() not in ("ok", "error")
                                 for n, f in resp.items()) else 0,
            "account": 3 if any(isinstance(f, dict) and f.get("type") == "array" for f in resp.values()) else 0,
            "callback": 2 if _has_when(req) and not _has_number(req) else 0,
            "record": 3 if _has_number(req) and _has_when(req) else 0,
            "transfer": 2 if _has_enum(req) and not _has_when(req) and not _has_number(req) else 0,
            "close": 1 if _has_enum(req) and not _has_number(req) and not _has_when(req) else 0,
            "note": 1 if _has_enum(req) and not _has_number(req) and not _has_when(req) else 0,
        }
        for role, hint in _ROLE_HINTS.items():
            score = shape[role]
            if hint.search(named):
                score += 5
            elif hint.search(blob):
                score += 2
            if score >= 2:
                pairs.append((score, role, name))
    taken, roles = set(), {}
    for _score, role, name in sorted(pairs, reverse=True):
        if role in roles or name in taken:
            continue
        roles[role] = name
        taken.add(name)
    return roles


def _caller_field(name: str) -> bool:
    """A request field whose name is a caller identifier, whatever spelling the API uses."""
    low = re.sub(r"[^a-z0-9]", "", str(name).lower())
    return low.startswith("customer") or low in {"callerid", "callerref", "custid", "custno"}


def given_call_vars(summary: dict) -> set[str]:
    """Names that are already set because the tool API declares them.

    A caller-identifier field, and a field named ``now``, are treated as given only when an operation
    actually declares them. Nothing else is assumed: a brief that does not pass a caller id is not given one.
    """
    found = set()
    for spec in ((summary or {}).get("tools") or {}).values():
        for field in spec.get("request") or {}:
            if field == "now" or _caller_field(field):
                found.add(field)
    return found


def load(path: str | Path) -> dict:
    return summarize(json.loads(Path(path).read_text(encoding="utf-8")))


def _resolve(spec: dict, node, depth: int = 0):
    """Follow local `#/...` references; leave everything else as it is."""
    while isinstance(node, dict) and "$ref" in node and depth < 20:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target = spec
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
        node, depth = target, depth + 1
    return node if isinstance(node, dict) else {}


def _field(spec: dict, prop: dict, required: bool) -> dict:
    prop = _resolve(spec, prop)
    out = {"type": prop.get("type", "string"), "required": required}
    for key in ("enum", "description", "format", "pattern", "minimum", "maximum"):
        if key in prop:
            out[key] = prop[key]
    if prop.get("type") == "array":
        items = _resolve(spec, prop.get("items", {}))
        if items.get("type") == "object" or "properties" in items:
            req = set(items.get("required", []))
            out["items"] = {k: _field(spec, v, k in req) for k, v in items.get("properties", {}).items()}
        else:
            out["items"] = {"type": items.get("type", "string")}
    elif prop.get("type") == "object" and "properties" in prop:
        req = set(prop.get("required", []))
        out["properties"] = {k: _field(spec, v, k in req) for k, v in prop["properties"].items()}
    return out


def _schema_fields(spec: dict, schema: dict) -> dict:
    schema = _resolve(spec, schema)
    req = set(schema.get("required", []))
    return {k: _field(spec, v, k in req) for k, v in schema.get("properties", {}).items()}


def summarize(spec: dict) -> dict:
    servers = spec.get("servers") or [{}]
    base = str(servers[0].get("url", "")).rstrip("/")
    tools = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            body = _resolve(spec, op.get("requestBody", {}))
            schema = _resolve(spec, (body.get("content", {}).get("application/json", {}) or {}).get("schema", {}))
            responses, errors, response_fields = op.get("responses", {}) or {}, {}, {}
            for status, resp in responses.items():
                resp = _resolve(spec, resp)
                if str(status).startswith("2"):
                    rs = (resp.get("content", {}).get("application/json", {}) or {}).get("schema", {})
                    response_fields = _schema_fields(spec, rs)
                    if resp.get("description"):
                        errors[str(status)] = resp["description"]
                elif resp.get("description"):
                    errors[str(status)] = resp["description"]
            tools[name] = {
                "method": method.upper(),
                "path": path,
                "url": base + path,
                "summary": op.get("summary", "") or op.get("description", ""),
                "request": _schema_fields(spec, schema),
                "required": list(_resolve(spec, schema).get("required", [])),
                "response": response_fields,
                "statuses": errors,
            }
    summary = {"base_url": base, "title": (spec.get("info") or {}).get("title", ""), "tools": tools,
               "roles": {}, "missing_kit_tools": []}
    summary["roles"] = infer_roles(summary)
    # missing_kit_tools stays empty: callers may still read the key, and no fixed tool list is required.
    return summary


def _fmt_field(name: str, f: dict, indent: str = "    ") -> list[str]:
    bits = [f["type"] + ("" if not f.get("format") else f"({f['format']})"), "required" if f.get("required") else "optional"]
    if "enum" in f:
        bits.append("enum=" + "|".join(map(str, f["enum"])))
    line = f"{indent}{name}: {', '.join(bits)}"
    if f.get("description"):
        line += f" -- {f['description']}"
    lines = [line]
    for sub, sf in (f.get("items") or f.get("properties") or {}).items():
        if isinstance(sf, dict):  # an array of scalars has items={"type": "string"}: no dict values
            lines += _fmt_field(sub, sf, indent + "    ")
    return lines


def render(summary: dict, max_chars: int = 14000) -> str:
    """Compact text for the agent: one block per tool."""
    out = [f"base URL: {summary['base_url']}",
           "Tool names are the operationIds below. There is no required set."]
    roles = summary.get("roles") or infer_roles(summary)
    if roles:
        out.append("roles: " + ", ".join(f"{role}={name}" for role, name in sorted(roles.items())))
    for name, t in summary["tools"].items():
        out.append(f"\n{name}  {t['method']} {t['url']}")
        if t["summary"]:
            out.append(f"  summary: {t['summary']}")
        out.append("  request:")
        for fname, f in t["request"].items():
            out += _fmt_field(fname, f)
        if t["response"]:
            out.append("  response (data.*):")
            for fname, f in t["response"].items():
                out += _fmt_field(fname, f)
        for status, desc in t["statuses"].items():
            out.append(f"  {status}: {desc}")
    text = "\n".join(out)
    return text if len(text) <= max_chars else text[:max_chars] + "\n... (truncated)"


if __name__ == "__main__":
    import sys

    print(render(load(sys.argv[1])))
