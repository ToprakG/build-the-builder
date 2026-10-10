"""build_workflow(policy, tools_api) -> workflow JSON.

Everything brief-specific (holiday dates, band thresholds, floor field names, sentences, the
outcome table) lives in a policy dict (see policy_ab.py). This file only knows the SHAPE of an
early-stage collections call: open -> who's on the line -> identity -> fetch the account -> settle
each product one at a time -> transfer or close. It reads every number and word it needs from
`policy`, and it reads every tool's fields and enums from `tools_api` (the brief's openapi.json),
never typing a field name or enum value by hand. Swap either argument for a final brief's and the
same function builds a correct workflow for it.

Every piece of arithmetic, counting and policy validation happens in a `code` node or a tool's
pre/post hook, built from seed/datefns.py's functions via inspect.getsource so the sandboxed copy
is byte-identical to the one seed/test_datefns.py tests. The dialogue model is never asked to do
date math, count attempts, or decide whether get_account may be called yet.
"""
import inspect
import json

from seed import datefns as D

# ---------------------------------------------------------------------------
# Code-node source assembly
# ---------------------------------------------------------------------------

_DATEFN_NAMES = [
    "parse_date", "parse_datetime", "is_business_day", "next_business_day",
    "on_or_after_business_day", "add_business_days", "band_for", "deadline_for",
    "floor_for", "last_business_day_of_week", "attempt1_proposal_date", "label_for",
    "product_facts", "validate_promise", "callback_ok", "outcome_for_call",
]


def _datefns_source():
    return "import datetime\n" + "\n".join(inspect.getsource(getattr(D, name)) for name in _DATEFN_NAMES)


def _guard_no_mustache(code):
    """The platform forbids a literal "{{" or "}}" inside a code node's source (tools-and-code.md).
    A nested dict/list literal can create "}}" by accident; this is the build-time check that
    catches it before the workflow is ever sent to the API."""
    if "{{" in code or "}}" in code:
        bad = [line for line in code.splitlines() if "{{" in line or "}}" in line]
        raise ValueError("generated code contains a template brace sequence:\n" + "\n".join(bad))
    return code


def _py_literal(value):
    """Render a plain-data value (str/int/float/bool/list/dict of those) as Python source,
    guaranteed not to contain "}}" by construction (json.dumps never emits a space, and we insert
    one between any two adjacent closing brackets/braces)."""
    text = json.dumps(value)
    text = text.replace("null", "None").replace("true", "True").replace("false", "False")
    out = []
    for i, ch in enumerate(text):
        out.append(ch)
        if ch in "}]" and i + 1 < len(text) and text[i + 1] in "}]":
            out.append(" ")
    return "".join(out)


def code_action(code, timeout_ms=10000):
    code = _guard_no_mustache(code)
    return {
        "type": "function",
        "function": {
            "name": "code_exec",
            "handler": "__function__:code_exec",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "default": code},
                    "timeout_ms": {"type": "number", "default": timeout_ms},
                },
            },
        },
    }


def code_node(name, body, next_node, extra_transitions=None, on_enter=None):
    """A passthrough `code` node: run `body` (python using `vars`), then go to `next_node`.
    `extra_transitions` are checked before the always-fallback to `next_node`."""
    transitions = list(extra_transitions or [])
    transitions.append({"target_node": next_node, "description": "always", "condition": {"operator": "always"}})
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": next_node,
        "on_enter": on_enter or {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [code_action(body)],
        "transitions": transitions,
    }


def say_then_node(name, text, next_node):
    """A fixed, model-free line (on_enter tts), then move on. Used for required sentences that do
    not depend on anything the model negotiated (sympathy, the app-or-branch line, goodbye)."""
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": next_node,
        "on_enter": {"type": "tts", "text": text},
        "disable_inactivity_monitor": True,
        "pre_actions": [code_action("pass")],
        "transitions": [{"target_node": next_node, "description": "always", "condition": {"operator": "always"}}],
    }


# ---------------------------------------------------------------------------
# Tool definitions, generated from tools_api (the brief's openapi.json)
# ---------------------------------------------------------------------------

def _op_schema(tools_api, name):
    op = tools_api["paths"]["/" + name]["post"]
    return op["requestBody"]["content"]["application/json"]["schema"]


def _leaf(spec, default_value):
    leaf = {"type": spec.get("type", "string")}
    if spec.get("description"):
        leaf["description"] = spec["description"]
    if spec.get("enum"):
        leaf["enum"] = spec["enum"]
    leaf["default"] = default_value
    return leaf


def build_tool(tools_api, name, description, baked=None, config=None):
    """One tool function, shaped from tools_api's schema for `/<name>`. `baked[field]` supplies a
    literal or `{{var}}` default that hides the field from the model (the engine fills it); every
    other field defaults to `{{field}}`, so the model sees and fills it. `customer_id` is always
    baked from the call variable of the same name, matching every tool in this API."""
    schema = _op_schema(tools_api, name)
    props = schema.get("properties", {})
    required = schema.get("required", [])
    baked = dict(baked or {})
    baked.setdefault("customer_id", "{{customer_id}}")
    base = tools_api["servers"][0]["url"]
    body_props = {}
    for field, spec in props.items():
        default_value = baked.get(field, "{{" + field + "}}")
        body_props[field] = _leaf(spec, default_value)
    return {
        "type": "function",
        "config": config or {"timeoutMs": 10000, "maxRetries": 0},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": f"{base}/{name}"},
                    "method": {"type": "string", "default": "POST"},
                    "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                    "body": {"type": "object", "properties": body_props, "required": required},
                },
            },
        },
    }


def pre_action_node(name, tool, next_node, response_mapping=None, on_enter=None, failure_target=None):
    """An `api_request` node: the engine fires `tool` on entry, then moves on. `failure_target`,
    if given, is tried first and catches a non-ok result via the tool's own success/failure
    counters (`<name>_failure_count`), before the unconditional fallback to `next_node`."""
    transitions = []
    if failure_target:
        transitions.append({
            "target_node": failure_target, "description": "the call failed",
            "condition": {"logic": "or", "conditions": [{"variable": f"{tool['function']['name']}_failure_count", "operator": "greater_than", "operand": 0}]},
        })
    transitions.append({"target_node": next_node, "description": "always", "condition": {"operator": "always"}})
    node = {
        "name": name,
        "node_type": "api_request",
        "passthrough": True,
        "passthrough_default_transition": next_node,
        "on_enter": on_enter or {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [tool],
        "transitions": transitions,
    }
    if response_mapping:
        node["response_mapping"] = response_mapping
    return node


# ---------------------------------------------------------------------------
# build_workflow
# ---------------------------------------------------------------------------

def build_workflow(policy, tools_api):
    holidays = policy["holidays"]
    bands = policy["bands"]
    floor_field = policy["floor_field_by_type"]
    sentences = policy["sentences"]
    limits = policy["limits"]
    identity_cfg = policy["identity"]
    transfer_map = policy["transfer_map"]
    outcomes = policy["outcomes"]
    note_reasons = policy["note_reasons"]
    paid_reason = policy["paid_note_reason"]
    forbidden = policy["forbidden_before_verification"]

    datefns_src = _datefns_source()
    goodbye = sentences["goodbye"]

    nodes = {}

    def add(n):
        nodes[n["name"]] = n

    # ---- terminal helper: say an (optional) fixed line + goodbye, close_call, hang up ----
    def terminal(key, outcome, reason, extra_sentence=""):
        hang = f"hang_up_{key}"
        close = f"close_{key}"
        say = f"say_{key}"
        add({
            "name": hang, "node_type": "end_call", "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True, "transitions": [],
            "terminal": {"type": "function", "function": {"name": f"ended_{key}", "handler": "__function__:end_call",
                         "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
        })
        close_tool = build_tool(tools_api, "close_call", "End the call. Always the last tool call.",
                                 baked={"outcome": outcome, "reason": reason})
        add(pre_action_node(close, close_tool, hang))
        text = (extra_sentence + " " + goodbye).strip() if extra_sentence else goodbye
        add(say_then_node(say, text, close))
        return say  # transitions should target this node

    close_not_reached_wrong_number = terminal("not_reached_wrong_number", "not_reached", "wrong_number")
    close_not_reached_deceased = terminal("not_reached_deceased", "not_reached", "deceased", sentences["deceased"])
    close_not_reached_unclear = terminal("not_reached_unclear", "not_reached", "unclear")
    close_not_reached_no_callback = terminal("not_reached_no_callback", "not_reached", "no_callback")
    close_not_reached_callback = terminal("not_reached_callback", "not_reached", "callback")
    close_not_verified_failed = terminal("not_verified_failed", "not_verified", "failed", sentences["not_verified_failed"])
    close_not_verified_declined = terminal("not_verified_declined", "not_verified", "declined")
    close_service_error_account = terminal("service_error_account", "no_promise", "service_error")

    # ================================= 1. opening =================================
    opening_max = limits["opening_max_asks"]
    add({
        "name": "opening", "node_type": "conversation",
        "task_message": (
            f"If {{{{opening_asks}}}} is 0 or unset, say: \"{sentences['opener']}\" then ask: "
            f"\"{sentences['confirm_identity']}\" (fill in the title and last name). "
            f"If {{{{opening_asks}}}} is 1, do not repeat the opener; just ask once more: "
            f"\"{sentences['confirm_identity']}\" (fill in the title and last name). "
            f"Only an explicit yes, \"that's me\", \"speaking\", or the customer saying their own name "
            f"counts as confirmation. \"Hello\", \"go ahead\" and \"who is this\" do not. "
            f"Never say the words {', '.join(forbidden)} or any product name before identity is "
            f"verified, even to a relative. If asked whether you are a robot or an AI, say: "
            f"\"{sentences['robot_question']}\" and continue. Do not call a tool in this turn."
        ),
        "extraction_schema": [
            {"name": "who_answered", "type": "string", "min_confidence": 0.65,
             "enum_values": ["customer", "relative", "wrong_number", "deceased", "unclear"],
             "description": ("Who is on the line, from the latest reply. 'customer' only for an explicit yes, "
                              "'that's me', 'speaking', or the customer's own name. 'relative' if a different "
                              "person is speaking for the customer. 'wrong_number' if they say this number is not "
                              "the customer's. 'deceased' if they say the customer has died. 'unclear' for "
                              "anything else, including 'hello', 'go ahead' or 'who is this'.")},
        ],
        "transitions": [
            {"target_node": "identity", "description": "Confirmed as the customer",
             "condition": {"logic": "or", "conditions": [{"variable": "who_answered", "operator": "equals", "operand": "customer"}]}},
            {"target_node": "third_party", "description": "A relative or other third party answered",
             "condition": {"logic": "or", "conditions": [{"variable": "who_answered", "operator": "equals", "operand": "relative"}]}},
            {"target_node": close_not_reached_wrong_number, "description": "Wrong number",
             "condition": {"logic": "or", "conditions": [{"variable": "who_answered", "operator": "equals", "operand": "wrong_number"}]}},
            {"target_node": close_not_reached_deceased, "description": "Customer has died",
             "condition": {"logic": "or", "conditions": [{"variable": "who_answered", "operator": "equals", "operand": "deceased"}]}},
            {"target_node": "opening_bump", "description": "Still unclear who is on the line",
             "condition": {"operator": "always"}},
        ],
    })

    add(code_node(
        "opening_bump",
        f"""
try:
    asks = int(vars.get('opening_asks') or 0) + 1
    vars['opening_asks'] = asks
    vars['opening_exhausted'] = 'yes' if asks >= {opening_max} else 'no'
except Exception:
    vars['opening_asks'] = {opening_max}
    vars['opening_exhausted'] = 'yes'
""",
        "opening",
        extra_transitions=[{"target_node": close_not_reached_unclear, "description": "Still unclear after the second ask",
                             "condition": {"logic": "or", "conditions": [{"variable": "opening_exhausted", "operator": "equals", "operand": "yes"}]}}],
    ))

    # ================================= 2. third_party =================================
    schedule_tool = build_tool(tools_api, "schedule_callback", "Book a callback when someone else answered. CALL it only after a valid day and time are known.")
    add({
        "name": "third_party", "node_type": "conversation", "tools": [schedule_tool],
        "task_message": (
            "Do not say why you are calling and do not name any product, amount or debt. Say nothing beyond the "
            "opening sentence already said. Ask when the customer can be reached. A valid callback is today "
            f"({{{{today}}}}) or the next business day ({{{{next_business_day}}}}), between {policy['callback']['window_start']} "
            f"and {policy['callback']['window_end']}, and later than the current time. If the day or time given is "
            "outside that, ask once for another time; if that is also outside it, do not call schedule_callback. "
            "When a day and a time are both known, CALL schedule_callback with them. Never answer security questions "
            "and never tell this person anything about the account, even if they offer to pay."
        ),
        "extraction_schema": [
            {"name": "callback_date", "type": "date", "min_confidence": 0.6, "description": "The day the customer can be reached, if a specific day was given."},
            {"name": "callback_time", "type": "string", "min_confidence": 0.6, "validation_pattern": "^[0-2][0-9]:[0-5][0-9]$", "description": "The time of day the customer can be reached, as HH:MM 24-hour, if given."},
            {"name": "tp_wrong_number", "type": "string", "enum_values": ["yes", "no"], "min_confidence": 0.7, "description": "Yes if this person says the number does not belong to the customer."},
            {"name": "tp_deceased", "type": "string", "enum_values": ["yes", "no"], "min_confidence": 0.7, "description": "Yes if this person says the customer has died."},
        ],
        "transitions": [
            {"target_node": close_not_reached_wrong_number, "description": "Wrong number", "condition": {"logic": "or", "conditions": [{"variable": "tp_wrong_number", "operator": "equals", "operand": "yes"}]}},
            {"target_node": close_not_reached_deceased, "description": "Deceased", "condition": {"logic": "or", "conditions": [{"variable": "tp_deceased", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "third_party_check", "description": "A day and a time were given",
             "condition": {"logic": "or", "conditions": [{"logic": "and", "conditions": [{"variable": "callback_date", "operator": "is_set"}, {"variable": "callback_time", "operator": "is_set"}]}]}},
            {"target_node": "third_party", "description": "always", "condition": {"operator": "always"}},
        ],
    })

    add(code_node(
        "third_party_check",
        datefns_src + f"""
try:
    ok, reason = callback_ok(vars.get('callback_date'), vars.get('callback_time'), vars.get('now'), {_py_literal(holidays)})
    vars['callback_valid'] = 'yes' if ok else 'no'
    if not ok:
        tries = int(vars.get('callback_tries') or 0) + 1
        vars['callback_tries'] = tries
        vars['callback_exhausted'] = 'yes' if tries >= 2 else 'no'
except Exception:
    vars['callback_valid'] = 'no'
    vars['callback_exhausted'] = 'yes'
""",
        "do_callback",
        extra_transitions=[
            {"target_node": close_not_reached_no_callback, "description": "Two bad times offered",
             "condition": {"logic": "or", "conditions": [{"logic": "and", "conditions": [{"variable": "callback_valid", "operator": "equals", "operand": "no"}, {"variable": "callback_exhausted", "operator": "equals", "operand": "yes"}]}]}},
            {"target_node": "third_party", "description": "Invalid, ask once more",
             "condition": {"logic": "or", "conditions": [{"variable": "callback_valid", "operator": "equals", "operand": "no"}]}},
        ],
    ))
    do_callback_tool = build_tool(tools_api, "schedule_callback", "Book the callback.", baked={"date": "{{callback_date}}", "time": "{{callback_time}}"})
    add(pre_action_node("do_callback", do_callback_tool, close_not_reached_callback))

    # ================================= 3. identity =================================
    max_wrong = identity_cfg["max_wrong_total"]
    max_declines = limits["identity_max_declines"]
    verify_tool = build_tool(tools_api, "verify_identity", "Check one security answer. CALL it every time the customer answers a security question.")
    verify_tool["post_process_code"] = _guard_no_mustache(f"""
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {{}}
        field = str(vars.get('verify_identity_args.field') or '')
        hint = d.get('hint') or ''
        wrong_total = int(vars.get('identity_wrong_total') or 0)
        if d.get('ok') and not d.get('verified') and not hint:
            wrong_total += 1
        vars['identity_wrong_total'] = wrong_total
        if d.get('verified') and field:
            vars['identity_passed_' + field] = 'yes'
        dob_ok = vars.get('identity_passed_date_of_birth') == 'yes'
        phone_ok = vars.get('identity_passed_phone_last4') == 'yes'
        vars['identity_all_passed'] = 'yes' if (dob_ok and phone_ok) else 'no'
        vars['identity_failed'] = 'yes' if wrong_total >= {max_wrong} else 'no'
        if wrong_total >= {max_wrong}:
            d['what_to_do_next'] = 'Stop. Do not state the debt. The call will close as not verified.'
        elif hint == {identity_cfg['swap_hint']!r}:
            d['what_to_do_next'] = 'Ask the customer to say the month as a word. This retry does not count as a wrong answer.'
        elif d.get('verified') and dob_ok and phone_ok:
            d['what_to_do_next'] = 'Both questions passed. Say nothing about the account yet; the next step will fetch it.'
        elif d.get('verified'):
            d['what_to_do_next'] = 'That question passed. Ask the next security question.'
        else:
            d['what_to_do_next'] = 'That answer was wrong. Ask the same question again.'
except Exception:
    pass
""")
    add({
        "name": "identity", "node_type": "conversation", "tools": [verify_tool],
        "task_message": (
            f"Ask for the date of birth first, then the last four digits of the phone number on file, one at a "
            f"time. When the customer answers, CALL verify_identity with field and answer. Send a date of birth "
            f"as YYYY-MM-DD; {identity_cfg['formats']['date_of_birth']} After the tool result, follow its "
            f"what_to_do_next instruction exactly. If the customer does not want to answer, or asks what the call "
            f"is about instead of answering, explain once that the questions protect their account; a second "
            f"refusal or a second such question ends the check. Never state the debt, the product or the amount "
            f"in this node. Never say the words {', '.join(forbidden)}."
        ),
        "extraction_schema": [
            {"name": "identity_declined", "type": "string", "enum_values": ["yes", "no"], "min_confidence": 0.65,
             "description": "Yes if the customer refused to answer the security question just asked, or asked what the call is about instead of answering."},
        ],
        "transitions": [
            {"target_node": close_not_verified_failed, "description": "Three wrong answers",
             "condition": {"logic": "or", "conditions": [{"variable": "identity_failed", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "fetch_account", "description": "Both questions passed",
             "condition": {"logic": "or", "conditions": [{"variable": "identity_all_passed", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "identity_decline_bump", "description": "Declined to answer",
             "condition": {"logic": "or", "conditions": [{"variable": "identity_declined", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "identity", "description": "always", "condition": {"operator": "always"}},
        ],
    })
    add(code_node(
        "identity_decline_bump",
        f"""
try:
    c = int(vars.get('identity_decline_count') or 0) + 1
    vars['identity_decline_count'] = c
    vars['identity_declined_exhausted'] = 'yes' if c >= {max_declines} else 'no'
except Exception:
    vars['identity_declined_exhausted'] = 'yes'
""",
        "identity",
        extra_transitions=[{"target_node": close_not_verified_declined, "description": "Declined twice",
                             "condition": {"logic": "or", "conditions": [{"variable": "identity_declined_exhausted", "operator": "equals", "operand": "yes"}]}}],
    ))

    # ================================= 4. fetch_account =================================
    get_account_tool = build_tool(tools_api, "get_account", "Get the customer's late products. Fired automatically once identity passes.")
    add(pre_action_node("fetch_account", get_account_tool, "compute_products",
                         response_mapping=[{"path": "data.products", "variable": "products"}],
                         failure_target=close_service_error_account))

    add(code_node(
        "compute_products",
        datefns_src + f"""
import json as _json
try:
    raw = vars.get('products')
    if raw is None:
        raw = vars.get('get_account_response.data.products')
    if isinstance(raw, str):
        raw = _json.loads(raw)
    if not isinstance(raw, list):
        raw = []
    today = parse_datetime(vars.get('now')).date()
    bands = {_py_literal(bands)}
    holidays = {_py_literal(holidays)}
    floor_field = {_py_literal(floor_field)}
    facts = [product_facts(p, today, bands, holidays, floor_field) for p in raw]
    vars['products_json'] = _json.dumps(facts)
    vars['product_count'] = len(facts)
    vars['current_product_index'] = 0
    vars['attempts_used_json'] = _json.dumps({{}})
    vars['promises_json'] = _json.dumps([])
    vars['notes_json'] = _json.dumps([])
    vars['support_offered'] = 'no'
    vars['human_asks'] = 0
    if len(facts) == 1:
        vars['products_summary'] = ('I am calling from the collections team about a late payment on your ' + facts[0]['label'] +
            '. You are ' + str(facts[0]['days_late']) + ' days late with a past-due amount of ' + str(facts[0]['full']) + '.')
        ask_line = 'Can you make this payment today?'
    else:
        lines = []
        for f in facts:
            lines.append('Your ' + f['label'] + ' is ' + str(f['full']) + ' past due and ' + str(f['days_late']) + ' days late.')
        vars['products_summary'] = 'I am calling from the collections team about some late payments. ' + ' '.join(lines)
        ask_line = 'Can you make all of these payments today?'
    f0 = facts[0] if facts else {{'product_id': '', 'label': '', 'floor': 0, 'full': 0, 'deadline': '', 'min_date': '', 'attempt1_date': '', 'attempts_allowed': 1, 'band': 'early'}}
    vars['current_product_id'] = f0['product_id']
    vars['current_label'] = f0['label']
    vars['current_floor'] = f0['floor']
    vars['current_full'] = f0['full']
    vars['current_deadline'] = f0['deadline']
    vars['current_min_date'] = f0['min_date']
    vars['current_attempt1_date'] = f0['attempt1_date']
    vars['current_attempts_allowed'] = f0['attempts_allowed']
    vars['current_band'] = f0['band']
    vars['current_attempt_number'] = 1
    multi_note = ' Read all of them back together and ask for one confirmation before calling create_promise for each.' if len(facts) > 1 else ''
    vars['current_instruction'] = (vars['products_summary'] + ' ' + ask_line +
        ' Never ask how much the customer can pay; you propose every amount.' + multi_note)
except Exception:
    vars['products_json'] = '[]'
    vars['product_count'] = 0
    vars['current_instruction'] = 'Apologize that the account could not be loaded and that the call will end.'
""",
        "negotiate",
    ))

    # ================================= 5. negotiate =================================
    create_promise_tool = build_tool(
        tools_api, "create_promise", "Record a payment promise. CALL it only after the customer confirmed the read-back.",
        baked={"product_id": "{{current_product_id}}"},
    )
    create_promise_tool["pre_process_code"] = _guard_no_mustache(datefns_src + f"""
try:
    holidays = {_py_literal(holidays)}
    date_in = config['body'].get('date')
    amt_in = config['body'].get('amount')
    ok, date_out, amt_out, reason = validate_promise(
        date_in, amt_in, vars.get('current_min_date'), vars.get('current_deadline'),
        float(vars.get('current_floor') or 0), float(vars.get('current_full') or 0), holidays)
    config['body']['date'] = date_out
    config['body']['amount'] = amt_out
    vars['promise_correction_reason'] = reason
except Exception:
    pass
""")
    create_promise_tool["post_process_code"] = _guard_no_mustache("""
import json as _json
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {}
        if d.get('ok'):
            promises = vars.get('promises_json')
            promises = _json.loads(promises) if isinstance(promises, str) else (promises or [])
            promises.append({'product_id': vars.get('current_product_id'), 'amount': d.get('amount', 0) or 0, 'date': d.get('date', '')})
            body_amount = None
            try:
                body_amount = float(vars.get('create_promise_args.amount'))
            except Exception:
                pass
            corrected_amount = body_amount
            corrected_date = vars.get('create_promise_args.date')
            promises[-1]['amount'] = corrected_amount
            promises[-1]['date'] = corrected_date
            vars['promises_json'] = _json.dumps(promises)
            vars['advance_reason'] = 'promised'
            reason = vars.get('promise_correction_reason') or ''
            if reason:
                d['correction'] = ('Before recording, the date or amount was adjusted (' + reason +
                    '). Read the recorded value back to the customer: ' + str(corrected_amount) + ' on ' + str(corrected_date) + '.')
            d['what_to_do_next'] = ('Say the promise is recorded for the amount and date just confirmed in the result, then say the '
                'text-message sentence. If this was the last unresolved product, thank the customer and say goodbye, '
                'then call finish_call.')
        else:
            fails = int(vars.get('current_promise_fails') or 0) + 1
            vars['current_promise_fails'] = fails
            if fails >= 2:
                d['what_to_do_next'] = 'It failed twice. Say you could not record it. Call add_note with reason service_error for this product.'
            else:
                d['what_to_do_next'] = 'It failed. Do not say it is recorded. Try create_promise once more with the same values.'
except Exception:
    pass
""")

    add_note_tool = build_tool(
        tools_api, "add_note", "Record why a product ends without a promise. CALL it once per product that is refused, disputed, already paid, or a hardship case the customer did not accept a transfer for.",
        baked={"product_id": "{{current_product_id}}"},
    )
    add_note_tool["post_process_code"] = _guard_no_mustache("""
import json as _json
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {}
        if d.get('ok'):
            notes = vars.get('notes_json')
            notes = _json.loads(notes) if isinstance(notes, str) else (notes or [])
            notes.append({'product_id': vars.get('current_product_id'), 'reason': vars.get('add_note_args.reason')})
            vars['notes_json'] = _json.dumps(notes)
            vars['advance_reason'] = 'noted'
            d['what_to_do_next'] = 'Move on to the next product, or if this was the last one, thank the customer and say goodbye, then call finish_call.'
        else:
            d['what_to_do_next'] = 'It failed. Try add_note once more with the same reason.'
except Exception:
    pass
""")

    negotiate_transitions = [
        {"target_node": "negotiate_advance", "description": "A promise was recorded",
         "condition": {"logic": "or", "conditions": [{"variable": "advance_reason", "operator": "equals", "operand": "promised"}]}},
        {"target_node": "negotiate_advance", "description": "A note was recorded",
         "condition": {"logic": "or", "conditions": [{"variable": "advance_reason", "operator": "equals", "operand": "noted"}]}},
        {"target_node": "prepare_transfer", "description": "Restructuring request",
         "condition": {"logic": "or", "conditions": [{"variable": "turn_signal", "operator": "equals", "operand": "restructuring"}]}},
        {"target_node": "prepare_transfer", "description": "Insisted on a human",
         "condition": {"logic": "or", "conditions": [{"variable": "turn_signal", "operator": "equals", "operand": "human"}]}},
        {"target_node": "prepare_transfer", "description": "Accepted the support team",
         "condition": {"logic": "or", "conditions": [{"variable": "turn_signal", "operator": "equals", "operand": "support"}]}},
        {"target_node": "negotiate_bump_attempt", "description": "A flat refusal or non-answer",
         "condition": {"logic": "or", "conditions": [{"variable": "turn_signal", "operator": "equals", "operand": "flat_refusal"}]}},
        {"target_node": "negotiate", "description": "always", "condition": {"operator": "always"}},
    ]
    add({
        "name": "negotiate", "node_type": "conversation", "tools": [create_promise_tool, add_note_tool],
        "max_node_turns": 60,
        "task_message": (
            "Follow this instruction for what to do right now: {{current_instruction}}\n\n"
            "Facts for the product you are discussing now, already computed: label {{current_label}}, floor "
            "{{current_floor}}, full past-due amount {{current_full}}, earliest date {{current_min_date}}, latest "
            "date (deadline) {{current_deadline}}, band {{current_band}}, attempts allowed {{current_attempts_allowed}}. "
            "You never calculate a date or a deadline yourself; these facts are already correct. Propose every "
            "amount yourself; never ask how much the customer can pay. Never offer a discount, waiver, instalment "
            "plan or any term not in these facts. If the customer says they already paid, or that this debt or "
            "product is not theirs, do not argue; call add_note with reason paid or dispute for this product and "
            "move on. If the customer mentions illness, a death in the family or job loss, say one short sentence "
            "of sympathy, make no persuasion attempt, and offer the support team once (only once per call). If a "
            "security-sounding question like 'is this a scam' comes up, say they can verify the call through "
            "Freya Bank's customer service number, and continue. When a rule here says to call a tool, call the "
            "tool; saying you did something is not enough. When every product is resolved, thank the customer "
            "and say goodbye in the same turn, then call the finish_call transition."
        ),
        "extraction_schema": [
            {"name": "turn_signal", "type": "string", "min_confidence": 0.6,
             "enum_values": ["flat_refusal", "restructuring", "human", "support", "other"],
             "description": ("'flat_refusal' for a plain no with no date and no amount for the current product. "
                              "'restructuring' if the customer asks for a new payment plan or lower instalments. "
                              "'human' only if the customer insists on a person after you already offered to help "
                              "yourself once. 'support' only if the customer just accepted your offer to transfer "
                              "them to the support team. 'other' for anything else, including giving a date, an "
                              "amount, a dispute, an already-paid claim, or a hardship mention.")},
        ],
        "transitions": [
            {"target_node": "finish_call", "description": "The call is over", "is_tool_call": True,
             "condition": {"operator": "matches_intent", "description": "Every product is resolved and the agent just said the final goodbye"}},
        ] + negotiate_transitions,
    })

    add(code_node(
        "negotiate_advance",
        """
import json as _json
try:
    idx = int(vars.get('current_product_index') or 0)
    products = _json.loads(vars.get('products_json') or '[]')
    idx += 1
    vars['current_product_index'] = idx
    vars['current_promise_fails'] = 0
    if idx >= len(products):
        vars['current_instruction'] = 'All products are resolved. Thank the customer and say goodbye now, then call the finish_call transition.'
    else:
        f = products[idx]
        vars['current_product_id'] = f['product_id']
        vars['current_label'] = f['label']
        vars['current_floor'] = f['floor']
        vars['current_full'] = f['full']
        vars['current_deadline'] = f['deadline']
        vars['current_min_date'] = f['min_date']
        vars['current_attempt1_date'] = f['attempt1_date']
        vars['current_attempts_allowed'] = f['attempts_allowed']
        vars['current_band'] = f['band']
        vars['current_attempt_number'] = 1
        vars['current_instruction'] = ('Now move on to the ' + f['label'] + '. Ask: can you make this payment today? '
            'If not, propose the full amount on ' + f['attempt1_date'] + ' first.')
except Exception:
    pass
""",
        "negotiate",
    ))

    add(code_node(
        "negotiate_bump_attempt",
        """
import json as _json
try:
    pid = vars.get('current_product_id')
    used = _json.loads(vars.get('attempts_used_json') or '{}')
    n = int(used.get(pid) or 0) + 1
    used[pid] = n
    vars['attempts_used_json'] = _json.dumps(used)
    allowed = int(vars.get('current_attempts_allowed') or 1)
    band = vars.get('current_band')
    if n >= allowed:
        vars['current_instruction'] = ('The customer refused the ' + str(vars.get('current_label')) +
            ' after every allowed attempt. Do not repeat an offer already refused. Call add_note with reason refused for this product, then move on.')
    elif n == 1 and band == 'late':
        vars['current_instruction'] = ('Make the second and final attempt on the ' + str(vars.get('current_label')) +
            ': propose the floor amount, ' + str(vars.get('current_floor')) + ', on the deadline, ' + str(vars.get('current_deadline')) + '.')
    else:
        vars['current_instruction'] = ('Propose the full amount, ' + str(vars.get('current_full')) +
            ', on ' + str(vars.get('current_attempt1_date')) + ' for the ' + str(vars.get('current_label')) + '.')
except Exception:
    pass
""",
        "negotiate",
    ))

    add(code_node(
        "finish_call",
        "pass",
        "closing",
    ))

    # ================================= 6. transfer =================================
    add(code_node(
        "prepare_transfer",
        f"""
try:
    trigger = str(vars.get('turn_signal') or '')
    mapping = {_py_literal(transfer_map)}
    m = mapping.get(trigger) or mapping.get('restructuring')
    vars['transfer_queue'] = m['queue']
    vars['transfer_reason'] = m['reason']
except Exception:
    pass
""",
        "transfer_call",
    ))
    transfer_tool = build_tool(tools_api, "transfer_to_agent", "Hand the call to a person.", baked={"queue": "{{transfer_queue}}"})
    add(pre_action_node("transfer_call", transfer_tool, "close_transferred"))
    close_transferred_tool = build_tool(tools_api, "close_call", "End the call.", baked={"outcome": "transferred", "reason": "{{transfer_reason}}"})
    add(pre_action_node("close_transferred", close_transferred_tool, "hang_up_transferred"))
    add({
        "name": "hang_up_transferred", "node_type": "end_call", "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {"name": "ended_transferred", "handler": "__function__:end_call",
                     "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    })

    # ================================= 7. closing =================================
    add(code_node(
        "closing",
        datefns_src + f"""
import json as _json
try:
    promises = _json.loads(vars.get('promises_json') or '[]')
    notes = _json.loads(vars.get('notes_json') or '[]')
    outcome, reason = outcome_for_call(promises, notes, {paid_reason!r})
    vars['final_outcome'] = outcome
    vars['final_reason'] = reason if outcome not in ('promise', 'paid') else ''
except Exception:
    vars['final_outcome'] = 'no_promise'
    vars['final_reason'] = 'service_error'
""",
        "close_final",
    ))
    close_final_tool = build_tool(tools_api, "close_call", "End the call.", baked={"outcome": "{{final_outcome}}", "reason": "{{final_reason}}"})
    add(pre_action_node("close_final", close_final_tool, "hang_up_final"))
    add({
        "name": "hang_up_final", "node_type": "end_call", "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {"name": "ended_final", "handler": "__function__:end_call",
                     "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    })

    system_prompt = (
        "You are Freya Bank's digital assistant on an outbound phone call. Speak English. Keep every turn to one "
        "or two short sentences. Call one tool per message and speak after every tool result; never end a turn "
        "with nothing and no tool call. If asked whether you are a robot or an AI, say: "
        f"\"{sentences['robot_question']}\" and continue. If asked whether this is a scam, say: "
        f"\"{sentences['scam_question']}\" and continue. If you cannot answer something from the tools, say you "
        "do not have that information; never invent a figure, a date or a reason."
    )

    return {
        "initial_node": "opening",
        "system_prompt": system_prompt,
        "nodes": nodes,
        "global_nodes": {},
        "test_variables": {"customer_id": "c_test", "first_name": "Dana", "last_name": "Reyes", "title": "Ms", "now": "2026-10-14T11:00"},
    }
