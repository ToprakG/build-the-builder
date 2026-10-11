"""Test helpers: invented account data per policy, and in-process execution of compiled snippets."""

import copy
import json

from builder.agentic.tools.snippets import build_config, find_tool
from builder.agentic.tools.lint import _code_of


def fake_products(policy: dict, n: int, days=(5, 30, 12), past_due=(412.5, 300.0, 99.99), floors=(137.5, 75.0, 25.0)):
    af, types = policy["account_fields"], list(policy["product_types"].values())
    fmt = policy.get("product_id_format") or "p{n}"
    out = []
    for i in range(n):
        t = types[i % len(types)]
        row = {af["product_id"]: fmt.replace("{n}", str(i + 1)), af["type"]: t["enum"], af["past_due"]: past_due[i],
               af["days_late"]: days[i], t["floor_field"]: floors[i]}
        if t.get("last4_field"):
            row[t["last4_field"]] = "4321"
        out.append(row)
    return out


def call_vars(policy: dict, now: str) -> dict:
    v = {k: "Sample" for k in policy.get("call_variables") or []}
    v.update(customer_id="c_test", now=now)
    return v


def run_code(wf: dict, node_id: str, vars: dict) -> dict:
    """Run a code node in process; returns the new variable table. Raises if the snippet itself raises."""
    code = _code_of(find_tool(wf, node_id, None))
    ns = {"vars": copy.deepcopy(vars)}
    exec(compile(code, node_id, "exec"), ns)
    return ns["vars"]


def run_pre(wf: dict, node_id: str, tool: str, vars: dict, model_args: dict) -> tuple[dict, dict]:
    t = find_tool(wf, node_id, tool)
    cfg = build_config(t, {**vars, **model_args})
    ns = {"vars": copy.deepcopy(vars), "config": cfg}
    exec(compile(t["pre_process_code"], node_id + "/pre", "exec"), ns)
    return ns["vars"], ns["config"]


def run_post(wf: dict, node_id: str, tool: str, vars: dict, data: dict, status="success", code=200) -> tuple[dict, dict]:
    t = find_tool(wf, node_id, tool)
    result = {"status": status, "data": copy.deepcopy(data), "message": "", "status_code": code, "header": {"x": "y"}}
    ns = {"vars": copy.deepcopy(vars), "result": result}
    exec(compile(t["post_process_code"], node_id + "/post", "exec"), ns)
    return ns["vars"], ns["result"]


def after_setup_and_facts(wf, policy, now, n):
    v = run_code(wf, "setup", call_vars(policy, now))
    v["products_raw"] = fake_products(policy, n)
    return run_code(wf, "facts", v)


def j(v, key):
    x = v.get(key)
    return json.loads(x) if isinstance(x, str) else x
