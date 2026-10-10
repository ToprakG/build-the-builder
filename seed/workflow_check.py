"""Static checks for a workflow, plus a local run of every code node and hook.

    python3 seed/workflow_check.py

Builds Brief A, Brief B, and the seven fake-brief configs, then checks each workflow
without calling Freya. Code nodes run as code_exec would: a dict named vars.
Hooks run with vars plus config (pre) or result (post). The sample call is one
customer, two products when the brief allows more than one, on 2026-10-14 at 11:00.
A snippet that swallows an error fails the check, because the sample is valid and
the success-path variables will not match.
"""
import ast
import copy
import datetime
import json
import pathlib
import signal
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seed import datefns as D

NOW = "2026-10-14T11:00"
TODAY = datetime.date(2026, 10, 14)
NODE_TYPES = {"conversation", "api_request", "code", "end_call"}
ACTION_TYPES = {"api_request", "code"}
OPERATORS = {"always", "equals", "greater_than", "is_set", "matches_intent"}
ALLOWED_IMPORTS = {"json", "re", "datetime"}

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "enumerate": enumerate,
    "float": float, "int": int, "isinstance": isinstance, "len": len, "list": list,
    "max": max, "min": min, "range": range, "round": round, "set": set, "str": str,
    "tuple": tuple, "Exception": Exception, "True": True, "False": False, "None": None,
}


class SnippetTimeout(BaseException):
    pass


def _limited_import(name, *args, **kwargs):
    if name not in ALLOWED_IMPORTS:
        raise ImportError(name)
    return __import__(name, *args, **kwargs)


def iter_configs():
    """Brief A, Brief B, then each testbed fake brief. Yields (name, policy, tools path)."""
    from seed.policy_ab import POLICY_A, POLICY_B
    yield "brief-a", POLICY_A, ROOT / "tools" / "openapi.json"
    yield "brief-b", POLICY_B, ROOT / "tools" / "openapi.json"
    for folder in sorted((ROOT / "testbed" / "fake_briefs").iterdir()):
        yield folder.name, json.loads((folder / "config.json").read_text()), folder / "tools.openapi.json"


def iter_snippets(workflow):
    """Yield (kind, where, code) for every code body and every pre/post hook."""
    groups = [("node", workflow.get("nodes") or {}), ("global", workflow.get("global_nodes") or {})]
    for scope, nodes in groups:
        for name, node in nodes.items():
            for action in node.get("pre_actions") or []:
                function = action.get("function") or {}
                if function.get("handler") == "__function__:code_exec":
                    code = ((function.get("parameters") or {}).get("properties") or {}).get("code") or {}
                    yield "code", f"{scope}:{name}", code.get("default") or ""
            for tool in (node.get("tools") or []) + (node.get("pre_actions") or []):
                tool_name = (tool.get("function") or {}).get("name") or "?"
                for key in ("pre_process_code", "post_process_code"):
                    source = tool.get(key)
                    if source and str(source).strip():
                        yield key, f"{scope}:{name}.{tool_name}", source
    for tool in workflow.get("tools") or []:
        tool_name = (tool.get("function") or {}).get("name") or "?"
        for key in ("pre_process_code", "post_process_code"):
            source = tool.get(key)
            if source and str(source).strip():
                yield key, f"workflow:{tool_name}", source


def _walk_nulls(value, path, problems):
    if value is None:
        problems.append(f"{path} is null")
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_nulls(item, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_nulls(item, f"{path}[{index}]", problems)


def _walk_conditions(condition, path, problems):
    if not isinstance(condition, dict):
        problems.append(f"{path} condition is not an object")
        return
    if "logic" in condition:
        if condition["logic"] not in ("and", "or"):
            problems.append(f"{path} has logic {condition['logic']!r}")
        for index, child in enumerate(condition.get("conditions") or []):
            _walk_conditions(child, f"{path}[{index}]", problems)
        return
    operator = condition.get("operator")
    if operator not in OPERATORS:
        problems.append(f"{path} has operator {operator!r}")


def _check_tool(tool, where, problems):
    function = tool.get("function") or {}
    handler = function.get("handler")
    if handler not in ("__function__:api_call", "__function__:code_exec", "__function__:end_call"):
        problems.append(f"{where} has handler {handler!r}")
    if tool.get("pre_process_code") or tool.get("post_process_code"):
        if handler != "__function__:api_call":
            problems.append(f"{where} has a hook on handler {handler!r}")
    for key in ("pre_process_code", "post_process_code"):
        source = tool.get(key)
        if not source or not str(source).strip():
            continue
        if "{{" in source or "}}" in source:
            problems.append(f"{where} {key} contains a template brace")
        try:
            ast.parse(source)
        except SyntaxError as error:
            problems.append(f"{where} {key} syntax error: {error}")
    parameters = (function.get("parameters") or {}).get("properties") or {}
    body = (parameters.get("body") or {}).get("properties") or {}
    if handler == "__function__:api_call":
        for field, leaf in body.items():
            if not isinstance(leaf, dict) or "default" not in leaf:
                problems.append(f"{where} body.{field} has no default")
            if isinstance(leaf, dict) and "const" in leaf:
                problems.append(f"{where} body.{field} uses const")
    for mapping in tool.get("response_mapping") or []:
        path = mapping.get("path") or ""
        if not str(path).startswith("data."):
            problems.append(f"{where} response_mapping path {path!r} does not start with data.")


def _check_node(name, node, workflow, scope, problems):
    where = f"{scope}:{name}"
    if node.get("name") != name:
        problems.append(f"{where} name is {node.get('name')!r}")
    node_type = node.get("node_type")
    if node_type not in NODE_TYPES:
        problems.append(f"{where} has node_type {node_type!r}")
    nodes = workflow.get("nodes") or {}
    globals_ = workflow.get("global_nodes") or {}
    for transition in node.get("transitions") or []:
        target = transition.get("target_node")
        if target not in nodes and target not in globals_ and target != "__previous__":
            problems.append(f"{where} transitions to missing node {target!r}")
        if not transition.get("description"):
            problems.append(f"{where} has a transition without a description")
        _walk_conditions(transition.get("condition"), f"{where} transition", problems)
    default_target = node.get("passthrough_default_transition")
    if default_target and default_target not in nodes and default_target not in globals_ and default_target != "__previous__":
        problems.append(f"{where} passthrough_default_transition {default_target!r} is missing")
    if node.get("passthrough"):
        transitions = node.get("transitions") or []
        if not default_target:
            problems.append(f"{where} is passthrough without passthrough_default_transition")
        if not transitions or (transitions[-1].get("condition") or {}).get("operator") != "always":
            problems.append(f"{where} passthrough node does not end with an always transition")
    if node_type in ACTION_TYPES and not node.get("passthrough"):
        problems.append(f"{where} {node_type} node is not passthrough")
    if node_type == "conversation" and node.get("pre_actions"):
        problems.append(f"{where} conversation node has pre_actions")
    if node_type != "conversation" and node.get("tools"):
        problems.append(f"{where} {node_type} node has tools")
    if node_type not in ACTION_TYPES and node.get("pre_actions"):
        problems.append(f"{where} {node_type} node has pre_actions")
    if node_type == "conversation" and not (node.get("task_message") or "").strip():
        problems.append(f"{where} conversation node has no task_message")
    if node_type == "end_call":
        terminal = (node.get("terminal") or {}).get("function") or {}
        if terminal.get("handler") != "__function__:end_call":
            problems.append(f"{where} end_call node has no end_call terminal")
    for mapping in node.get("response_mapping") or []:
        path = mapping.get("path") or ""
        if not str(path).startswith("data."):
            problems.append(f"{where} response_mapping path {path!r} does not start with data.")
    for action in node.get("pre_actions") or []:
        function = action.get("function") or {}
        if function.get("handler") == "__function__:code_exec":
            code = ((function.get("parameters") or {}).get("properties") or {}).get("code") or {}
            source = code.get("default") or ""
            if "{{" in source or "}}" in source:
                problems.append(f"{where} code contains a template brace")
            try:
                ast.parse(source)
            except SyntaxError as error:
                problems.append(f"{where} code syntax error: {error}")
        _check_tool(action, where, problems)
    for tool in node.get("tools") or []:
        _check_tool(tool, f"{where}.{(tool.get('function') or {}).get('name')}", problems)


def static_problems(workflow):
    problems = []
    _walk_nulls(workflow, "workflow", problems)
    nodes = workflow.get("nodes") or {}
    initial = workflow.get("initial_node")
    if initial not in nodes:
        problems.append(f"initial_node {initial!r} is not a node")
    for name, node in nodes.items():
        _check_node(name, node, workflow, "node", problems)
    for name, node in (workflow.get("global_nodes") or {}).items():
        _check_node(name, node, workflow, "global", problems)
    for tool in workflow.get("tools") or []:
        _check_tool(tool, f"workflow.{(tool.get('function') or {}).get('name')}", problems)
    return problems


def _label(policy, product):
    fields = policy["account_fields"]
    type_value = product.get(fields["type"])
    for product_type in policy["product_types"].values():
        if product_type["enum"] == type_value:
            label = product_type["label"]
            last4 = product_type.get("last4_field")
            if last4:
                label = label.replace("{last4}", str(product.get(last4) or ""))
            return label
    return str(type_value)


def _facts_for(policy, product):
    fields = policy["account_fields"]
    floors = {item["enum"]: item["floor_field"] for item in policy["product_types"].values()}
    facts = D.product_facts(
        product, TODAY, policy["bands"], policy["holidays"],
        fields["type"], floors, fields["past_due"], fields["days_late"],
    )
    facts = dict(facts)
    facts["product_id"] = product.get(fields["product_id"])
    facts["label"] = _label(policy, product)
    return facts


def _sample_products(policy, count):
    fields = policy["account_fields"]
    types = list(policy["product_types"].values())
    amounts = [(420.0, 126.0, 6), (200.0, 50.0, 20)]
    products = []
    for index in range(count):
        product_type = types[index % len(types)]
        full, floor, days_late = amounts[index % len(amounts)]
        product = {
            fields["product_id"]: policy.get("product_id_format", "p{n}").format(n=index + 1),
            fields["type"]: product_type["enum"],
            fields["past_due"]: full,
            fields["days_late"]: days_late,
            product_type["floor_field"]: floor,
        }
        if product_type.get("last4_field"):
            product[product_type["last4_field"]] = "4821"
        products.append(product)
    return products


def _callback_sample(policy):
    window = policy["callback"]
    holidays = policy["holidays"]
    if window["same_day"]:
        day = TODAY
        candidates = ["15:00", "12:00", window["end"]]
    else:
        day = D.next_business_day(TODAY, holidays)
        candidates = [window["start"], "15:00", window["end"]]
    for time_value in candidates:
        ok, _reason = D.callback_ok(day.isoformat(), time_value, NOW, holidays, window["start"], window["end"], window["same_day"])
        if ok:
            return day.isoformat(), time_value
    raise RuntimeError(f"no valid sample callback for {policy.get('id')}")


def sample_vars(policy):
    """One valid mid-call variable table for this policy."""
    count = 2 if policy.get("max_products", 1) > 1 else 1
    products = _sample_products(policy, count)
    facts = [_facts_for(policy, product) for product in products]
    first = facts[0]
    callback_date, callback_time = _callback_sample(policy)
    question = policy["identity"]["questions"][0]
    field_name = policy["tools"]["verify_identity"]["field"]
    promise = policy["tools"]["create_promise"]
    note = policy["tools"]["add_note"]
    amount = int(round(first["full"] * 100)) if promise.get("amount_unit") == "cents" else first["full"]
    variables = {
        "customer_id": "c_test", "first_name": "Dana", "last_name": "Reyes", "title": "Ms", "now": NOW,
        "products": products,
        "products_json": json.dumps(facts),
        "promises_json": "[]",
        "notes_json": "[]",
        "attempts_used_json": "{}",
        "current_product_index": 0,
        "current_product_id": first["product_id"],
        "current_label": first["label"],
        "current_floor": first["floor"],
        "current_full": first["full"],
        "current_deadline": first["deadline"],
        "current_min_date": first["min_date"],
        "current_attempt1_date": first["attempt1_date"],
        "current_attempts_allowed": first["attempts_allowed"],
        "current_band": first["band"],
        "current_attempt_number": 1,
        "opening_asks": 0,
        "callback_date": callback_date,
        "callback_time": callback_time,
        "turn_signal": "restructuring",
        "transfer_outcome_key": "tr_restructuring",
        f"verify_identity_args.{field_name}": question,
        f"create_promise_args.{promise['date']}": first["min_date"],
        f"create_promise_args.{promise['amount']}": amount,
        f"add_note_args.{note['reason']}": policy["note_map"]["refused"],
    }
    return variables, facts


def _on_alarm(_signum, _frame):
    raise SnippetTimeout()


def _exec_snippet(code, variables, extra=None):
    namespace = {"__builtins__": {**_SAFE_BUILTINS, "__import__": _limited_import}, "vars": variables}
    if extra:
        namespace.update(extra)
    previous = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        exec(code, namespace)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _run(code, variables, extra=None):
    try:
        ast.parse(code)
    except SyntaxError as error:
        return f"syntax error: {error}"
    try:
        _exec_snippet(code, variables, extra)
    except SnippetTimeout:
        return "timed out"
    except Exception as error:
        return f"raised {type(error).__name__}: {error}"
    return None


def _expect(problems, where, condition, message):
    if not condition:
        problems.append(f"{where}: {message}")


def run_problems(workflow, policy):
    """Run every snippet on a copy of the sample. Return problems, not an exception."""
    problems = []
    base, facts = sample_vars(policy)
    snippets = {(kind, where): code for kind, where, code in iter_snippets(workflow)}
    required = [
        ("code", "node:init"), ("code", "node:opening_bump"), ("code", "node:third_party_check"),
        ("code", "node:identity_decline_bump"), ("code", "node:compute_products"),
        ("code", "node:negotiate_advance"), ("code", "node:negotiate_bump_attempt"),
        ("code", "node:finish_call"), ("code", "node:prepare_transfer"),
        ("code", "node:close_transferred"), ("code", "node:closing"),
        ("post_process_code", "node:identity.verify_identity"),
        ("pre_process_code", "node:negotiate.create_promise"),
        ("post_process_code", "node:negotiate.create_promise"),
        ("post_process_code", "node:negotiate.add_note"),
    ]
    if policy.get("notes_on_restructuring"):
        required.append(("code", "node:note_before_transfer_check"))
    for key in required:
        if key not in snippets:
            problems.append(f"missing {key[0]} {key[1]}")
    promise = policy["tools"]["create_promise"]
    first = facts[0]
    cents = promise.get("amount_unit") == "cents"

    def run(kind, where, variables=None, extra=None):
        code = snippets.get((kind, where))
        if code is None:
            return None
        values = copy.deepcopy(base if variables is None else variables)
        error = _run(code, values, extra)
        if error:
            problems.append(f"{where} {kind}: {error}")
            return None
        return values

    ran = run("code", "node:init")
    if ran is not None:
        _expect(problems, "init", ran.get("today") == TODAY.isoformat(), f"today {ran.get('today')!r}")
        expected_next = D.next_business_day(TODAY, policy["holidays"]).isoformat()
        _expect(problems, "init", ran.get("next_business_day") == expected_next, f"next_business_day {ran.get('next_business_day')!r}, expected {expected_next}")

    ran = run("code", "node:opening_bump")
    if ran is not None:
        _expect(problems, "opening_bump", ran.get("opening_asks") == 1 and ran.get("opening_exhausted") == "no",
                f"asks {ran.get('opening_asks')!r} exhausted {ran.get('opening_exhausted')!r}")

    ran = run("code", "node:third_party_check")
    if ran is not None:
        _expect(problems, "third_party_check", ran.get("callback_valid") == "yes", f"callback_valid {ran.get('callback_valid')!r}")
        if policy["tools"]["schedule_callback"]["mode"] == "datetime":
            combined = f"{base['callback_date']}T{base['callback_time']}"
            _expect(problems, "third_party_check", ran.get("callback_at_combined") == combined, f"callback_at_combined {ran.get('callback_at_combined')!r}")

    result = {"status": "success", "data": {"ok": True, "verified": True}, "message": "ok", "status_code": 200, "header": {"Content-Type": "application/json"}}
    ran = run("post_process_code", "node:identity.verify_identity", extra={"result": result})
    if ran is not None:
        question = policy["identity"]["questions"][0]
        _expect(problems, "verify_identity post", ran.get("identity_passed_" + question) == "yes", f"passed flag {ran.get('identity_passed_' + question)!r}")
        _expect(problems, "verify_identity post", ran.get("identity_failed") == "no", f"identity_failed {ran.get('identity_failed')!r}")
        _expect(problems, "verify_identity post", bool(result["data"].get("what_to_do_next")), "what_to_do_next was not set")
        _expect(problems, "verify_identity post", "header" not in result, "header was left on the result")

    ran = run("code", "node:identity_decline_bump")
    if ran is not None:
        _expect(problems, "identity_decline_bump", ran.get("identity_decline_count") == 1 and ran.get("identity_declined_exhausted") == "no",
                f"count {ran.get('identity_decline_count')!r} exhausted {ran.get('identity_declined_exhausted')!r}")

    ran = run("code", "node:compute_products")
    if ran is not None:
        loaded = json.loads(ran.get("products_json") or "[]")
        _expect(problems, "compute_products", ran.get("product_count") == len(facts), f"product_count {ran.get('product_count')!r}, expected {len(facts)}")
        _expect(problems, "compute_products", loaded and loaded[0].get("deadline") == first["deadline"] and loaded[0].get("product_id") == first["product_id"],
                f"first product {loaded[:1]!r}, expected deadline {first['deadline']} id {first['product_id']}")
        _expect(problems, "compute_products", ran.get("current_label") == first["label"], f"label {ran.get('current_label')!r}, expected {first['label']!r}")
        _expect(problems, "compute_products", "Apologize" not in str(ran.get("current_instruction")), "took the account-load fallback")

    amount_in = int(round(first["full"] * 100)) if cents else first["full"]
    config = {"url": "https://bank.example/create_promise", "method": "POST", "headers": {}, "body": {promise["date"]: first["min_date"], promise["amount"]: amount_in}}
    ran = run("pre_process_code", "node:negotiate.create_promise", extra={"config": config})
    if ran is not None:
        _ok, date_out, amount_out, reason = D.validate_promise(
            first["min_date"], first["full"], first["min_date"], first["deadline"], first["floor"], first["full"], policy["holidays"])
        expected_amount = int(round(amount_out * 100)) if cents else amount_out
        _expect(problems, "create_promise pre", config["body"].get(promise["date"]) == date_out, f"date {config['body'].get(promise['date'])!r}, expected {date_out}")
        _expect(problems, "create_promise pre", config["body"].get(promise["amount"]) == expected_amount, f"amount {config['body'].get(promise['amount'])!r}, expected {expected_amount}")
        _expect(problems, "create_promise pre", ran.get("promise_correction_reason") == reason, f"reason {ran.get('promise_correction_reason')!r}, expected {reason!r}")

    result = {"status": "success", "data": {"ok": True}, "message": "ok", "status_code": 200, "header": {}}
    ran = run("post_process_code", "node:negotiate.create_promise", extra={"result": result})
    if ran is not None:
        recorded = json.loads(ran.get("promises_json") or "[]")
        _expect(problems, "create_promise post", ran.get("advance_reason") == "promised" and len(recorded) == 1, f"advance {ran.get('advance_reason')!r} promises {recorded!r}")
        if recorded:
            _expect(problems, "create_promise post", recorded[0].get("product_id") == first["product_id"] and round(float(recorded[0].get("amount")), 2) == round(first["full"], 2),
                    f"recorded {recorded[0]!r}")
        _expect(problems, "create_promise post", bool(result["data"].get("what_to_do_next")), "what_to_do_next was not set")

    result = {"status": "success", "data": {"ok": True}, "message": "ok", "status_code": 200, "header": {}}
    ran = run("post_process_code", "node:negotiate.add_note", extra={"result": result})
    if ran is not None:
        notes = json.loads(ran.get("notes_json") or "[]")
        sent = policy["note_map"]["refused"]
        canonical = {value: key for key, value in policy["note_map"].items()}.get(sent, sent)
        _expect(problems, "add_note post", ran.get("advance_reason") == "noted" and notes and notes[0].get("reason") == canonical,
                f"advance {ran.get('advance_reason')!r} notes {notes!r}, expected reason {canonical!r}")

    ran = run("code", "node:negotiate_advance")
    if ran is not None:
        _expect(problems, "negotiate_advance", ran.get("current_product_index") == 1, f"index {ran.get('current_product_index')!r}")
        if len(facts) == 1:
            _expect(problems, "negotiate_advance", str(ran.get("current_instruction") or "").startswith("All products are resolved"),
                    f"instruction {ran.get('current_instruction')!r}")
        else:
            _expect(problems, "negotiate_advance", ran.get("current_product_id") == facts[1]["product_id"],
                    f"product {ran.get('current_product_id')!r}, expected {facts[1]['product_id']!r}")

    ran = run("code", "node:negotiate_bump_attempt", variables={**base, "current_instruction": "UNSET"})
    if ran is not None:
        used = json.loads(ran.get("attempts_used_json") or "{}")
        _expect(problems, "negotiate_bump_attempt", used.get(first["product_id"]) == 1, f"attempts {used!r}")
        _expect(problems, "negotiate_bump_attempt", ran.get("current_instruction") not in (None, "", "UNSET"), "instruction was not written")

    ran = run("code", "node:prepare_transfer", variables={**base, "transfer_queue": "UNSET"})
    if ran is not None:
        _expect(problems, "prepare_transfer", ran.get("transfer_queue") == policy["queue_map"]["restructuring"],
                f"queue {ran.get('transfer_queue')!r}, expected {policy['queue_map']['restructuring']!r}")
        _expect(problems, "prepare_transfer", ran.get("transfer_outcome_key") == "tr_restructuring", f"key {ran.get('transfer_outcome_key')!r}")

    ran = run("code", "node:note_before_transfer_check", variables={**base, "note_before_transfer_done": "UNSET"})
    if ran is not None:
        _expect(problems, "note_before_transfer_check", ran.get("note_before_transfer_done") == "no", f"done {ran.get('note_before_transfer_done')!r}")
        _expect(problems, "note_before_transfer_check", ran.get("current_product_id") == first["product_id"], f"product {ran.get('current_product_id')!r}")

    ran = run("code", "node:close_transferred", variables={**base, "closevar_value": "UNSET"})
    if ran is not None:
        value, reason = policy["outcome_map"]["tr_restructuring"]
        _expect(problems, "close_transferred", ran.get("closevar_value") == value and ran.get("closevar_reason") == (reason or ""),
                f"value {ran.get('closevar_value')!r} reason {ran.get('closevar_reason')!r}")

    promised = [{"product_id": first["product_id"], "amount": first["full"], "date": first["min_date"]}]
    ran = run("code", "node:closing", variables={**base, "promises_json": json.dumps(promised), "closevar_value": "UNSET"})
    if ran is not None:
        value, reason = policy["outcome_map"]["promise"]
        _expect(problems, "closing", ran.get("closevar_value") == value and ran.get("closevar_reason") == (reason or ""),
                f"value {ran.get('closevar_value')!r} reason {ran.get('closevar_reason')!r}")

    checked = {
        ("code", "node:init"), ("code", "node:opening_bump"), ("code", "node:third_party_check"),
        ("post_process_code", "node:identity.verify_identity"), ("code", "node:identity_decline_bump"),
        ("code", "node:compute_products"), ("pre_process_code", "node:negotiate.create_promise"),
        ("post_process_code", "node:negotiate.create_promise"), ("post_process_code", "node:negotiate.add_note"),
        ("code", "node:negotiate_advance"), ("code", "node:negotiate_bump_attempt"),
        ("code", "node:prepare_transfer"), ("code", "node:note_before_transfer_check"),
        ("code", "node:close_transferred"), ("code", "node:closing"),
    }
    for (kind, where), code in snippets.items():
        if (kind, where) in checked:
            continue
        values = copy.deepcopy(base)
        error = _run(code, values)
        if error:
            problems.append(f"{where} {kind}: {error}")
    return problems


def check_workflow(workflow, policy):
    return static_problems(workflow) + run_problems(workflow, policy)


def main():
    from seed.graph import build_workflow
    all_ok = True
    for name, policy, tools_path in iter_configs():
        tools_api = json.loads(tools_path.read_text())
        try:
            workflow = build_workflow(policy, tools_api)
        except Exception as error:
            print(f"FAIL {name}: build_workflow raised {type(error).__name__}: {error}")
            all_ok = False
            continue
        problems = check_workflow(workflow, policy)
        snippets = list(iter_snippets(workflow))
        if problems:
            print(f"FAIL {name}: {len(problems)} problem(s)")
            for problem in problems[:20]:
                print("   -", problem)
            if len(problems) > 20:
                print(f"   - ... {len(problems) - 20} more")
            all_ok = False
        else:
            print(f"PASS {name}: static and runtime OK ({len(workflow['nodes'])} nodes, {len(snippets)} snippets)")
    print()
    print("ALL 9 CONFIGS OK" if all_ok else "SOME CONFIGS FAILED")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
