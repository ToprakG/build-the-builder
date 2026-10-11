"""Static checks for a Freya workflow, against the bank's tool API.

Standard library only. Every check cites the kit doc it comes from (wf = docs/workflow-format.md,
tc = docs/tools-and-code.md). Calibration rule: `examples/base-agent.json` is accepted by the platform, so it lints
with 0 errors against `tools/openapi.json`. If a check flags it, the check is wrong.

    lint(workflow, api_summary, call_vars=None, unused_tools=()) -> {"errors": [...], "warnings": [...]}

Each issue is {"path": "$.nodes.x.transitions[0]", "msg": "..."}.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

from builder.agentic.tools.openapi_summary import given_call_vars, infer_roles, is_write

TOP_KEYS = {"initial_node", "nodes", "system_prompt", "global_nodes", "test_variables", "context_variables", "tools",
            "nodePositions"}  # wf "Top-level keys"
NODE_TYPES = {"conversation", "api_request", "code", "end_call"}  # wf "Node types" (the others are not used here)
DETERMINISTIC = {"equals", "not_equals", "greater_than", "less_than", "contains", "is_set", "is_not_set"}
NEEDS_OPERAND = {"equals", "not_equals", "greater_than", "less_than", "contains"}  # wf "Condition operators"
OPERATORS = DETERMINISTIC | {"always", "matches_intent"}
API_CALL, CODE_EXEC, END_CALL = "__function__:api_call", "__function__:code_exec", "__function__:end_call"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
AUTO_SUFFIXES = ("_success_count", "_failure_count", "_status_code")  # tc "Success and failure counters"


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, path, msg):
        self.errors.append({"path": path, "msg": msg})

    def warn(self, path, msg):
        self.warnings.append({"path": path, "msg": msg})

    def as_dict(self):
        return {"errors": self.errors, "warnings": self.warnings}


# --- small accessors -------------------------------------------------------------------------------------------


def _fn(tool) -> dict:
    return tool.get("function", {}) if isinstance(tool, dict) else {}


def _handler(tool) -> str:
    return _fn(tool).get("handler", "")


def _props(tool) -> dict:
    return ((_fn(tool).get("parameters") or {}).get("properties")) or {}


def _code_of(tool) -> str | None:
    code = _props(tool).get("code")
    return code.get("default") if isinstance(code, dict) else None


def _node_type(node: dict, is_global: bool) -> str:
    return node.get("node_type") or ("conversation" if is_global else "")


def _all_nodes(wf):
    """(path, id, node, is_global) for every node and global node."""
    for key, is_global in (("nodes", False), ("global_nodes", True)):
        group = wf.get(key) or {}
        if isinstance(group, dict):
            for nid, node in group.items():
                if isinstance(node, dict):
                    yield f"$.{key}.{nid}", nid, node, is_global


def _tools_of(wf):
    """(path, owner, tool, placement) for every tool object in the workflow. owner is a node id or 'workflow'."""
    for path, nid, node, _ in _all_nodes(wf):
        for i, t in enumerate(node.get("tools") or []):
            yield f"{path}.tools[{i}]", nid, t, "tools"
        for i, t in enumerate(node.get("pre_actions") or []):
            yield f"{path}.pre_actions[{i}]", nid, t, "pre_actions"
        term = node.get("terminal")
        if isinstance(term, dict):
            yield f"{path}.terminal", nid, term, "terminal"
    for i, t in enumerate(wf.get("tools") or []):
        yield f"$.tools[{i}]", "workflow", t, "workflow"


def _walk_conditions(cond, path):
    """Yield (path, leaf condition) for every leaf in a condition tree."""
    if not isinstance(cond, dict):
        return
    if "conditions" in cond:
        for i, c in enumerate(cond.get("conditions") or []):
            yield from _walk_conditions(c, f"{path}.conditions[{i}]")
    else:
        yield path, cond


def edges(wf) -> dict[str, list[str]]:
    """Graph edges: transition targets, passthrough_default_transition, max_turn_transition (wf "Transitions")."""
    out = {}
    for _, nid, node, _ in _all_nodes(wf):
        targets = [t.get("target_node") for t in node.get("transitions") or [] if isinstance(t, dict)]
        targets += [node.get("passthrough_default_transition"), node.get("max_turn_transition")]
        out[nid] = [t for t in targets if isinstance(t, str) and t]
    return out


def is_terminal(node: dict) -> bool:
    """An end_call node, or a conversation node that offers an end_call tool (how base-agent.json ends calls)."""
    if node.get("node_type") == "end_call":
        return True
    return any(_handler(t) == END_CALL for t in node.get("tools") or [])


# --- set variables ---------------------------------------------------------------------------------------------


def _code_writes(code: str) -> set[str]:
    names = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None
    if tree is not None:
        for n in ast.walk(tree):
            targets = []
            if isinstance(n, ast.Assign):
                targets = n.targets
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                targets = [n.target]
            for t in targets:
                for sub in ast.walk(t):
                    if (isinstance(sub, ast.Subscript) and isinstance(sub.value, ast.Name) and sub.value.id == "vars"):
                        key = sub.slice
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            names.add(key.value)
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "vars" and n.func.attr in ("update", "setdefault")):
                for a in n.args[:1]:
                    if isinstance(a, ast.Dict):
                        names |= {k.value for k in a.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                        names.add(a.value)
                names |= {kw.arg for kw in n.keywords if kw.arg}
    # Fallback for snippets that do not parse, or that write keys from a loop: a literal key after vars[
    names |= set(re.findall(r"vars\[\s*['\"]([A-Za-z_]\w*)['\"]\s*\]\s*=(?!=)", code))
    return names


def set_variables(wf, call_vars=None) -> tuple[set[str], tuple[str, ...]]:
    """Exact names that something sets, and prefixes of automatic keys (wf "Variables", tc "Where a result lands")."""
    names = set(call_vars or []) | set((wf.get("test_variables") or {}).keys())
    prefixes = ["system.", "call."]
    for _, nid, node, _ in _all_nodes(wf):
        names |= {f.get("name") for f in node.get("extraction_schema") or [] if isinstance(f, dict) and f.get("name")}
        names |= {m.get("variable") for m in node.get("response_mapping") or [] if isinstance(m, dict) and m.get("variable")}
        names |= {f"{nid}.current_turns", f"{nid}.total_turns"}
        if node.get("pre_actions"):
            prefixes.append(f"{nid}_response.")
            names |= {nid + s for s in AUTO_SUFFIXES}
    for _, owner, tool, placement in _tools_of(wf):
        name = _fn(tool).get("name")
        if not name:
            continue
        names |= {m.get("variable") for m in tool.get("response_mapping") or [] if isinstance(m, dict) and m.get("variable")}
        if placement != "terminal":
            prefixes += [f"{name}_response.", f"{name}_args."]
            names |= {name + s for s in AUTO_SUFFIXES}
        for code in (_code_of(tool), tool.get("pre_process_code"), tool.get("post_process_code")):
            if isinstance(code, str):
                names |= _code_writes(code)
    return names, tuple(prefixes)


def _is_set(var: str, names, prefixes) -> bool:
    var = var.split(":", 1)[0].strip()  # {{amount:json}}
    return var in names or var.startswith(prefixes)


# --- the checks ------------------------------------------------------------------------------------------------


def _check_structure(wf, r: Report):
    for k in wf:
        if k == "agent_config":
            r.err("$.agent_config", "agent_config must be left out (wf 'Top-level keys': omit it)")
        elif k not in TOP_KEYS:
            r.err(f"$.{k}", f"unknown top-level key {k!r}")
    if not isinstance(wf.get("nodes"), dict) or not wf.get("nodes"):
        r.err("$.nodes", "nodes must be a non-empty object keyed by node id")
    if "global_nodes" in wf and not isinstance(wf["global_nodes"], dict):
        r.err("$.global_nodes", "global_nodes must be an object keyed by node id")
    nulls = []

    def find(o, p):
        if o is None:
            nulls.append(p)
        elif isinstance(o, dict):
            for k, v in o.items():
                find(v, f"{p}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                find(v, f"{p}[{i}]")

    find(wf, "$")
    if nulls:  # wf says null fails validation, yet base-agent.json carries nulls and is accepted: a warning
        r.warn(nulls[0], f"{len(nulls)} null value(s); leave optional fields out instead: " + ", ".join(nulls[:5]))


def _check_graph(wf, r: Report):
    nodes, gnodes = wf.get("nodes") or {}, wf.get("global_nodes") or {}
    known = set(nodes) | set(gnodes)
    init = wf.get("initial_node")
    if init not in nodes:
        r.err("$.initial_node", f"initial_node {init!r} is not a node")
    graph = edges(wf)
    for path, nid, node, is_global in _all_nodes(wf):
        for i, t in enumerate(node.get("transitions") or []):
            tgt = t.get("target_node") if isinstance(t, dict) else None
            if tgt == "__previous__":
                if not is_global:  # wf "Transitions": __previous__ is allowed in global nodes
                    r.err(f"{path}.transitions[{i}]", "__previous__ is only allowed in global nodes")
            elif tgt not in known:
                r.err(f"{path}.transitions[{i}]", f"target_node {tgt!r} does not exist")
        for key in ("passthrough_default_transition", "max_turn_transition"):
            tgt = node.get(key)
            if tgt and tgt != "__previous__" and tgt not in known:
                r.err(f"{path}.{key}", f"{key} {tgt!r} does not exist")
            if tgt == "__previous__" and not is_global:
                r.err(f"{path}.{key}", "__previous__ is only allowed in global nodes")
        if node.get("node_type") == "end_call":  # wf "End-call nodes"
            if not isinstance(node.get("terminal"), dict):
                r.err(f"{path}.terminal", "end_call node needs a terminal object")
            if node.get("transitions"):
                r.err(f"{path}.transitions", "end_call node must have no transitions")
    # reachability: from initial_node, plus every global node (it can fire anywhere)
    seen, todo = set(), [init, *gnodes]
    while todo:
        n = todo.pop()
        if n in seen or n not in known:
            continue
        seen.add(n)
        todo += graph.get(n, [])
    for nid in nodes:
        if nid not in seen:
            r.err(f"$.nodes.{nid}", "node is not reachable from initial_node")
    # from every node a terminal is reachable (wf "Design rules": every path must reach an end_call node)
    terminals = {nid for nid, n in nodes.items() if is_terminal(n)}
    if not terminals:
        r.err("$.nodes", "no terminal: add an end_call node or an end_call tool")
        return
    reach_terminal = set(terminals)
    changed = True
    while changed:
        changed = False
        for nid in nodes:
            if nid not in reach_terminal and any(t in reach_terminal for t in graph.get(nid, [])):
                reach_terminal.add(nid)
                changed = True
    for nid in nodes:
        if nid not in reach_terminal:
            r.err(f"$.nodes.{nid}", "no terminal is reachable from this node")


def _check_node_types(wf, r: Report):
    for path, nid, node, is_global in _all_nodes(wf):
        ntype = _node_type(node, is_global)
        if ntype not in NODE_TYPES:
            r.err(f"{path}.node_type", f"node_type {ntype!r} is not one of {sorted(NODE_TYPES)}")
        if node.get("name") not in (None, nid):
            r.warn(f"{path}.name", "use the node key as its name (wf 'Node fields')")
        if node.get("tools") and ntype != "conversation":  # wf "Node types": the platform strips them
            r.err(f"{path}.tools", f"tools on a {ntype} node are dropped by the platform; only conversation nodes keep them")
        if node.get("pre_actions") and ntype == "conversation":
            r.err(f"{path}.pre_actions", "pre_actions on a conversation node are dropped; use an api_request or code node")
        for i, t in enumerate(node.get("pre_actions") or []):
            if isinstance(t, dict) and t.get("response_mapping"):  # tc "response_mapping" placement
                r.err(f"{path}.pre_actions[{i}].response_mapping",
                      "response_mapping inside a pre_action is ignored; put it at node level")
        if ntype == "code":
            if not any(_handler(t) == CODE_EXEC for t in node.get("pre_actions") or []):
                r.err(f"{path}.pre_actions", "code node needs a code_exec pre_action")
        if ntype in ("api_request", "code"):  # wf "Passthrough nodes"
            if node.get("passthrough") is not True:
                r.err(f"{path}.passthrough", f"{ntype} node must be passthrough: true")
            if not node.get("passthrough_default_transition"):
                r.err(f"{path}.passthrough_default_transition", "passthrough node needs passthrough_default_transition")
            trs = node.get("transitions") or []
            if not trs or (trs[-1].get("condition") or {}).get("operator") != "always":
                r.err(f"{path}.transitions", "passthrough node must end with an always transition (or it deadlocks)")
        elif node.get("passthrough") is True:
            trs = node.get("transitions") or []
            if not trs or (trs[-1].get("condition") or {}).get("operator") != "always":
                r.err(f"{path}.transitions", "passthrough node must end with an always transition (or it deadlocks)")


def _check_condition(cond, path, r: Report, names, prefixes, top=True):
    if not isinstance(cond, dict):
        r.err(path, "condition must be an object")
        return
    if "conditions" in cond:  # a group (wf "Condition shapes")
        logic = cond.get("logic")
        if logic not in ("or", "and"):
            r.err(path, f"group logic must be 'or' or 'and', got {logic!r}")
        if top and logic == "and":
            r.err(path, "never put logic 'and' at the top level; nest it inside an 'or' group")
        if not cond.get("conditions"):
            r.err(path, "empty condition group")
        for i, c in enumerate(cond.get("conditions") or []):
            _check_condition(c, f"{path}.conditions[{i}]", r, names, prefixes, top=False)
        # booleans arrive as "true" or "True" (wf "Do not route on a boolean")
        tested = {}
        for _, leaf in _walk_conditions(cond, path):
            if leaf.get("operator") == "equals" and str(leaf.get("operand")) in ("true", "True", "false", "False"):
                tested.setdefault((leaf.get("variable"), str(leaf.get("operand")).lower()), set()).add(str(leaf.get("operand")))
        for (var, low), spellings in tested.items():
            if len(spellings) == 1:
                r.warn(path, f"{var} is tested as {next(iter(spellings))!r} only; test both {low!r} and {low.title()!r}")
        return
    op = cond.get("operator")
    if op not in OPERATORS:
        r.err(path, f"unknown operator {op!r}")
        return
    if op == "always":
        return
    if op == "matches_intent":
        if not cond.get("description"):
            r.err(path, "matches_intent needs a description")
        return
    if top:
        r.warn(path, "bare variable condition; the working builder wraps variable checks in an 'or' group")
    if not cond.get("variable"):
        r.err(path, f"{op} needs a variable")
    elif not _is_set(cond["variable"], names, prefixes):
        r.warn(path, f"nothing sets variable {cond['variable']!r}; this branch can never fire")
    if op in NEEDS_OPERAND and "operand" not in cond:
        r.err(path, f"{op} needs an operand")
    if top and op in ("equals",) and str(cond.get("operand")) in ("true", "True"):
        r.warn(path, "a boolean tested with one spelling; test both 'true' and 'True' in an or group")


def _check_conditions(wf, r: Report, names, prefixes):
    for path, nid, node, is_global in _all_nodes(wf):
        for i, t in enumerate(node.get("transitions") or []):
            if not isinstance(t, dict):
                r.err(f"{path}.transitions[{i}]", "transition must be an object")
                continue
            if "condition" not in t:
                r.err(f"{path}.transitions[{i}]", "transition needs a condition")
                continue
            _check_condition(t["condition"], f"{path}.transitions[{i}].condition", r, names, prefixes)
        if is_global:
            if "condition" not in node:
                r.err(f"{path}.condition", "global node needs a condition (wf 'Global nodes')")
            else:
                _check_condition(node["condition"], f"{path}.condition", r, names, prefixes)


def _check_extraction(wf, r: Report):
    for path, nid, node, _ in _all_nodes(wf):
        for i, f in enumerate(node.get("extraction_schema") or []):
            p = f"{path}.extraction_schema[{i}]"
            if not isinstance(f, dict) or not f.get("name"):
                r.err(p, "extraction field needs a name")
                continue
            if "min_confidence" not in f:  # wf "Extraction schema": set it on every variable
                r.err(p, f"{f['name']}: set min_confidence (default 0.95 silently discards values)")
            if f.get("type") == "date":
                r.warn(p, f"{f['name']}: type 'date' resolves relative dates against the real clock, not {{{{now}}}}; "
                          "extract a string and resolve it in a code node from vars['now']")


def _check_api_tool(path, owner, tool, api, r: Report, names, prefixes, call_vars, placement="tools"):
    fn = _fn(tool)
    name = fn.get("name")
    props = _props(tool)
    spec = api["tools"].get(name)
    if spec is None:
        r.err(f"{path}.function.name", f"{name!r} is not a tool of the tool API ({', '.join(api['tools'])})")
    for key in ("url", "method"):  # tc "How a parameter gets its value": default bakes, const does not
        leaf = props.get(key)
        if not isinstance(leaf, dict) or "default" not in leaf:
            r.err(f"{path}.function.parameters.properties.{key}",
                  f"{key} needs a 'default'" + (" (const does not bake)" if isinstance(leaf, dict) and "const" in leaf else ""))
    headers = props.get("headers")
    if isinstance(headers, dict):
        for h, leaf in (headers.get("properties") or {}).items():
            if not isinstance(leaf, dict) or "default" not in leaf:
                r.err(f"{path}.function.parameters.properties.headers.{h}", "header needs a 'default'")
    if spec is None:
        return
    url = (props.get("url") or {}).get("default")
    if isinstance(url, str) and url.rstrip("/") != spec["url"]:
        r.err(f"{path}.function.parameters.properties.url", f"url must be {spec['url']} (servers[0].url + path), got {url}")
    method = (props.get("method") or {}).get("default")
    if isinstance(method, str) and method.upper() != spec["method"]:
        r.err(f"{path}.function.parameters.properties.method", f"method must be {spec['method']}")
    body = props.get("body")
    bpath = f"{path}.function.parameters.properties.body"
    if not isinstance(body, dict):
        if spec["request"]:
            r.err(bpath, "body with body.properties is missing")
        return
    if "default" in body and not body.get("properties"):  # tc "api_call body field rules"
        r.err(bpath, "declare each body field as a leaf under body.properties, not one body.default object")
    leaves = body.get("properties") or {}
    for field, leaf in leaves.items():
        lp = f"{bpath}.properties.{field}"
        if field not in spec["request"]:
            r.err(lp, f"{field!r} is not a request field of {name} ({', '.join(spec['request'])})")
            continue
        if not isinstance(leaf, dict) or "default" not in leaf:
            r.err(lp, f"{field} needs its own 'default'" + (" (const does not bake)" if isinstance(leaf, dict) and "const" in leaf else ""))
            continue
        api_enum = spec["request"][field].get("enum")
        default = leaf.get("default")
        if api_enum is not None:
            if isinstance(default, str) and not PLACEHOLDER.search(default) and default not in api_enum:
                r.err(lp, f"{field} default {default!r} is not in the API enum {api_enum}")
            if "enum" in leaf and not set(leaf["enum"]) <= set(api_enum):
                r.err(lp, f"{field} enum {sorted(set(leaf['enum']) - set(api_enum))} not in the API enum {api_enum}")
        m = PLACEHOLDER.fullmatch(default.strip()) if isinstance(default, str) else None
        var = m.group(1).split(":")[0] if m else None
        if field in call_vars and default != "{{" + field + "}}":
            r.err(lp, f"{field} is a call variable; bake it with default '{{{{{field}}}}}'")
        model_facing = placement == "tools"  # engine-run calls (pre_actions, lifecycle) bake on purpose
        if model_facing and var and leaf.get("description") and var not in call_vars and _is_set(var, names, prefixes):
            r.warn(lp, f"model-filled {field} uses {{{{{var}}}}}, which another step sets: it will be baked and the model never sees it")
    missing = [f for f in spec["required"] if f not in leaves]
    if missing:
        r.err(bpath, f"required field(s) of {name} missing from body.properties: {missing}")
    if is_write(spec, name, api) and (tool.get("config") or {}).get("maxRetries", 0) > 0:  # tc "Retries and timeouts"
        r.warn(f"{path}.config.maxRetries", f"{name} writes data; maxRetries > 0 can record it twice")


def _literal(node) -> bool:
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False


def _try_wrapped(tree: ast.Module) -> bool:
    """Imports, docstrings, function definitions and literal assignments (to a name or to vars['x']) cannot raise,
    so they may sit outside the try; nothing else."""
    rest = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.Expr, ast.FunctionDef))
            and not (isinstance(n, ast.Assign) and _literal(n.value))]
    return all(isinstance(n, ast.Try) for n in rest)  # nothing left that can raise counts as wrapped


def _check_snippet(path, code, r: Report, kind):
    if not isinstance(code, str) or not code.strip():
        return None
    if "{{" in code or "}}" in code:  # tc "Rules for snippets"
        r.err(path, "no '{{' or '}}' in code; read values from vars (a nested dict literal ending in }} counts)")
    try:
        tree = ast.parse(code)
        compile(code, path, "exec")
    except SyntaxError as e:
        r.err(path, f"{kind} does not compile: {e.msg} (line {e.lineno})")
        return None
    if not _try_wrapped(tree):
        r.warn(path, f"{kind} is not wrapped in try/except; a raising snippet's effect on the call is not documented")
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    if kind == "code node" and "vars" not in used:
        r.warn(path, "code node does not read or write vars; its output cannot reach the workflow")
    return tree


def _check_list_mappings(wf, api, r: Report):
    """A mapped list stayed unset in a practice run (10 October); only flattened keys held it."""
    def check(path, mappings, tool_names, owner):
        for i, m in enumerate(mappings or []):
            field = str((m or {}).get("path", "")).split(".")
            if len(field) == 2 and field[0] == "data":
                for name in tool_names:
                    spec = (api["tools"].get(name) or {}).get("response", {}).get(field[1])
                    if spec and spec.get("type") == "array":
                        r.warn(f"{path}.response_mapping[{i}]",
                               f"maps the list {m['path']!r}; a mapped list stayed unset on the platform. Read "
                               f"'{owner}_response.{m['path']}.<i>.<field>' with list_from_vars instead")

    for path, nid, node, _ in _all_nodes(wf):
        names = [_fn(t).get("name") for t in node.get("pre_actions") or [] if _handler(t) == API_CALL]
        check(path, node.get("response_mapping"), names, nid)
    for path, owner, tool, placement in _tools_of(wf):
        if placement == "tools" and isinstance(tool, dict):
            check(path, tool.get("response_mapping"), [_fn(tool).get("name")], _fn(tool).get("name"))


def _snippets(wf):
    """(path, kind, code) for every code node snippet and hook."""
    for path, owner, tool, placement in _tools_of(wf):
        if not isinstance(tool, dict):
            continue
        code = _code_of(tool)
        if placement == "pre_actions" and _handler(tool) == CODE_EXEC and isinstance(code, str):
            yield f"{path}.function.parameters.properties.code.default", "code node", code
        for hook in ("pre_process_code", "post_process_code"):
            if isinstance(tool.get(hook), str) and tool[hook].strip():
                yield f"{path}.{hook}", hook, tool[hook]


def _list_mapping_targets(wf, api) -> dict:
    """Variable name -> flattened prefix, for every mapping of an API list field."""
    out = {}
    for _, nid, node, _ in _all_nodes(wf):
        names = [_fn(t).get("name") for t in node.get("pre_actions") or [] if _handler(t) == API_CALL]
        for m in node.get("response_mapping") or []:
            parts = str((m or {}).get("path", "")).split(".")
            for name in names:
                spec = (api["tools"].get(name) or {}).get("response", {}).get(parts[-1]) if len(parts) == 2 else None
                if spec and spec.get("type") == "array" and m.get("variable"):
                    out[m["variable"]] = f"{nid}_response.{m['path']}"
    return out


def _check_platform_facts(wf, api, r: Report):
    """Behaviour seen in practice runs on Freya (reference/platform-facts.md), checked where the JSON shows it."""
    lists = _list_mapping_targets(wf, api)
    for path, kind, code in _snippets(wf):
        # Fact 1: a mapped list stays unset; code must rebuild it from the flattened keys.
        for var, prefix in lists.items():
            reads = re.search(r"vars(\.get\(|\[)\s*['\"]" + re.escape(var) + r"['\"]", code)
            if reads and "_response.data." not in code and "list_from_vars" not in code:
                r.err(path, f"reads the mapped list {var!r}, which stays unset on the platform; rebuild it from "
                            f"'{prefix}.<i>.<field>' with list_from_vars")
        # Fact 2: several calls of one tool in one message; <tool>_args keeps one call's values.
        if kind == "post_process_code" and re.search(r"_args\.", code):
            r.warn(path, "reads <tool>_args in a post-hook: with several calls in one message it holds only one call's "
                         "values. Count successes, or queue ids from the pre-hook's config['body']")
    # Fact 10: a model tool call on entry made the platform speak a filler line; engine-run writes avoid it.
    for path, nid, node, _ in _all_nodes(wf):
        on_enter = node.get("on_enter") or {}
        prompt_text = str(on_enter.get("prompt") or "") if isinstance(on_enter, dict) and on_enter.get("type") == "llm" else ""
        for t in node.get("tools") or []:
            name = _fn(t).get("name")
            if name and _handler(t) == API_CALL and name in prompt_text:
                r.warn(f"{path}.on_enter.prompt", f"the entry prompt makes the model call {name}; if code can decide the "
                                                  "values, send it from an api_request node instead (platform-facts.md #10)")
    # Fact 3: a completion flag can stay false; the agent then says goodbye until the turn cap. Safety net: end_call.
    nodes = wf.get("nodes") or {}
    graph = {nid: [t.get("target_node") for t in n.get("transitions") or [] if isinstance(t, dict)]
             + [n.get("passthrough_default_transition")] for nid, n in nodes.items()}  # the turn cap does not count
    for nid, node in nodes.items():
        if node.get("node_type") != "conversation" or is_terminal(node):
            continue
        seen, todo, finishes = set(), list(graph.get(nid, [])), False
        while todo and not finishes:
            m = todo.pop()
            if m in seen or m not in nodes:
                continue
            seen.add(m)
            if nodes[m].get("node_type") == "end_call":
                finishes = True
            elif nodes[m].get("node_type") in ("code", "api_request"):
                todo += graph.get(m, [])
        if finishes:
            r.warn(f"$.nodes.{nid}.tools", "the call can finish from this node but it has no end_call tool; add one "
                                           "(message_mode none) so a goodbye always ends the call")


def _check_tools(wf, api, r: Report, names, prefixes, call_vars):
    for path, owner, tool, placement in _tools_of(wf):
        if not isinstance(tool, dict):
            r.err(path, "tool must be an object")
            continue
        handler = _handler(tool)
        if handler == API_CALL:
            _check_api_tool(path, owner, tool, api, r, names, prefixes, call_vars, placement)
        for hook in ("pre_process_code", "post_process_code"):
            if tool.get(hook):
                if handler != API_CALL:  # tc "Pre and post Python hooks": stripped from other handlers
                    r.err(f"{path}.{hook}", f"{hook} on a {handler or 'missing'} handler is removed by the platform")
                else:
                    _check_snippet(f"{path}.{hook}", tool[hook], r, "hook")
        if handler == CODE_EXEC and placement == "pre_actions":
            code = _code_of(tool)
            if not isinstance(code, str):
                r.err(f"{path}.function.parameters.properties.code", "code_exec needs code as a baked 'default' string")
            else:
                _check_snippet(f"{path}.function.parameters.properties.code.default", code, r, "code node")


PROMPT_FIELDS = ("task_message", "loop_prompt", "role_message", "routing_prompt")


def _check_prompts(wf, r: Report, names, prefixes):
    def scan(path, text, spoken):
        if not isinstance(text, str):
            return
        for var in PLACEHOLDER.findall(text):
            if not _is_set(var, names, prefixes):
                msg = f"{{{{{var}}}}} is never set"
                if spoken:  # wf "{{var}} substitution": a fixed spoken line says it aloud
                    r.err(path, msg + "; this fixed line would say it aloud")
                else:
                    r.warn(path, msg + "; the model would see the literal placeholder")

    scan("$.system_prompt", wf.get("system_prompt"), False)
    node_ids = set(wf.get("nodes") or {}) | set(wf.get("global_nodes") or {})
    for path, nid, node, is_global in _all_nodes(wf):
        for f in PROMPT_FIELDS:
            scan(f"{path}.{f}", node.get(f), False)
        on_enter = node.get("on_enter") or {}
        if isinstance(on_enter, dict):
            scan(f"{path}.on_enter.text", on_enter.get("text"), on_enter.get("type") == "tts")
            scan(f"{path}.on_enter.prompt", on_enter.get("prompt"), False)
        for i, t in enumerate(node.get("transitions") or []):
            if isinstance(t, dict):
                scan(f"{path}.transitions[{i}].description", t.get("description"), False)
        term = node.get("terminal")
        if isinstance(term, dict):
            spoken = (_props(term).get("spoken_message") or {}).get("default")
            scan(f"{path}.terminal.spoken_message", spoken, True)
        task = node.get("task_message") or ""
        for other in node_ids - {nid}:  # wf "Prompts": do not name other nodes
            if "_" in other and re.search(rf"\b{re.escape(other)}\b", task):
                r.warn(f"{path}.task_message", f"names node {other!r}; the model does not know about nodes")
        if _node_type(node, is_global) == "conversation":
            prompt_text = " ".join(str(node.get(f) or "") for f in ("task_message", "loop_prompt")) + " " + str(wf.get("system_prompt") or "")
            for t in node.get("tools") or []:
                tname = _fn(t).get("name")
                if tname and _handler(t) != END_CALL and tname not in prompt_text:
                    r.warn(f"{path}.task_message", f"the prompt does not name tool {tname!r}; say when to call it (tc 'Tool descriptions')")


def _offers(node, name) -> bool:
    return any(_fn(t).get("name") == name for t in (node.get("tools") or []) + (node.get("pre_actions") or []))


def _close_name(api, policy) -> str | None:
    """The closing operationId: the policy's role=close entry, else the one inferred from the API."""
    if isinstance(policy, dict):
        for name, spec in (policy.get("tools") or {}).items():
            if isinstance(spec, dict) and spec.get("role") == "close" and name in (api.get("tools") or {}):
                return name
    return infer_roles(api).get("close")


def _check_closing(wf, api, r: Report, unused_tools, policy=None):
    nodes = wf.get("nodes") or {}
    close = _close_name(api, policy)
    if not close:
        for t in wf.get("tools") or []:
            if "end_of_call" in ((t.get("config") or {}).get("alwaysRunsAt") or []) and _fn(t).get("name"):
                close = _fn(t).get("name")
                break
    lifecycle = [t for t in wf.get("tools") or [] if close and _fn(t).get("name") == close
                 and "end_of_call" in ((t.get("config") or {}).get("alwaysRunsAt") or [])]
    offering = set()
    if close:
        offering = {nid for nid, n in nodes.items() if _offers(n, close)} | {
            nid for nid, n in (wf.get("global_nodes") or {}).items() if _offers(n, close)}
    if not close:
        pass
    elif lifecycle:
        if offering:
            r.warn("$.tools", f"{close} runs at end_of_call and is also offered in {sorted(offering)}: it can run twice")
    elif close in api["tools"] and close not in unused_tools:
        graph = edges(wf)
        seen, todo = set(), [wf.get("initial_node")]
        while todo:  # walk the graph avoiding close_call nodes; reaching a terminal means a path without close_call
            n = todo.pop()
            if n in seen or n not in nodes or n in offering:
                continue
            seen.add(n)
            if is_terminal(nodes[n]):
                r.err(f"$.nodes.{n}", f"a path reaches this terminal without {close}; "
                                      "run it at end_of_call or on every path")
                break
            todo += graph.get(n, [])
    for nid, n in nodes.items():  # after the closing tool as a pre_action, only a terminal may follow
        if close and any(_fn(t).get("name") == close for t in n.get("pre_actions") or []):
            graph = edges(wf)
            seen, todo = set(), list(graph.get(nid, []))
            while todo:
                m = todo.pop()
                if m in seen or m not in nodes:
                    continue
                seen.add(m)
                if nodes[m].get("node_type") != "end_call":
                    r.err(f"$.nodes.{nid}", f"after {close} only an end_call node may follow; {m!r} is reachable")
                    break
                todo += graph.get(m, [])
    used = {_fn(t).get("name") for _, _, t, _ in _tools_of(wf)}
    for t in api["tools"]:
        if t not in used and t not in unused_tools:
            r.warn("$", f"tool {t!r} is not used anywhere; mark it unused in policy.json if the brief never needs it")


def lint(wf, api_summary: dict, call_vars=None, unused_tools=(), policy=None) -> dict:
    r = Report()
    if not isinstance(wf, dict):
        r.err("$", "workflow must be a JSON object")
        return r.as_dict()
    call_vars = set(call_vars or []) | set((wf.get("test_variables") or {}).keys()) | given_call_vars(api_summary)
    names, prefixes = set_variables(wf, call_vars)
    _check_structure(wf, r)
    if not isinstance(wf.get("nodes"), dict) or not wf.get("nodes"):
        return r.as_dict()
    _check_graph(wf, r)
    _check_node_types(wf, r)
    _check_conditions(wf, r, names, prefixes)
    _check_extraction(wf, r)
    _check_tools(wf, api_summary, r, names, prefixes, call_vars)
    _check_list_mappings(wf, api_summary, r)
    _check_platform_facts(wf, api_summary, r)
    _check_prompts(wf, r, names, prefixes)
    _check_closing(wf, api_summary, r, set(unused_tools or ()), policy)
    return r.as_dict()


def policy_hints(policy: dict | None) -> tuple[list[str], list[str]]:
    """Call variables and unused tools declared in policy.json (see reference/policy-schema.md)."""
    if not isinstance(policy, dict):
        return [], []
    return list(policy.get("call_variables") or []), list(policy.get("unused_tools") or [])


def lint_file(workflow_path, api_summary: dict, policy_path=None, last_good_path=None) -> dict:
    """Lint a file. On 0 errors, copy it to last_good_path. Returns the report plus counts."""
    p = Path(workflow_path)
    if not p.exists():
        return {"errors": [{"path": "$", "msg": f"{p.name} does not exist yet"}], "warnings": []}
    try:
        wf = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"errors": [{"path": "$", "msg": f"invalid JSON: {e}"}], "warnings": []}
    policy = None
    if policy_path and Path(policy_path).exists():
        try:
            policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            policy = None
    call_vars, unused = policy_hints(policy)
    report = lint(wf, api_summary, call_vars, unused, policy)
    if not report["errors"] and last_good_path:
        shutil.copyfile(p, last_good_path)
        report["saved_last_good"] = True
    return report


def render(report: dict, limit: int = 60) -> str:
    lines = [f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)"]
    for kind in ("errors", "warnings"):
        items = report[kind]
        for it in items[:limit]:
            lines.append(f"{kind[:-1].upper()} {it['path']}: {it['msg']}")
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more {kind}")
    return "\n".join(lines)
