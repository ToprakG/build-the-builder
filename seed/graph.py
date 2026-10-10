"""build_workflow(policy, tools_api) -> workflow JSON.

`policy` is the canonical config schema (see seed/policy_ab.py's POLICY_A/POLICY_B, or any
testbed/fake_briefs/<id>/config.json): holidays, bands, identity questions, callback window,
outcome/queue/note maps, close rule, product types, account field names, and -- critically --
`policy["tools"][tool_name]`, which names which literal field in that tool plays which role
(verify_identity's security-answer field might be called "field" or "check"; create_promise's
amount might be "amount" (currency) or "amount_cents" (integer cents); schedule_callback might
take separate date/time fields or one combined "callback_at"; close_call might take outcome+
reason or a single disposition "code"). Every one of those decisions is read from `policy`, never
hardcoded here, and `tools_api` (the brief's own openapi.json) supplies each field's JSON-schema
type, enum and description, so the generated tool definitions always match the real API regardless
of which fields exist or what they're called.

Internally, outcomes are tracked as a single canonical key from `policy["outcome_map"]`
("promise", "paid", "np_refused", "nr_wrong_number", "tr_restructuring", ...) and note reasons as
one of the five canonical keys (refused/hardship/dispute/paid/service_error); both are translated
to the brief's actual values only at the add_note/close_call boundary. This mirrors the
testbed's own `oracle.outcome_key` field, which uses the identical vocabulary.

Every piece of arithmetic, counting and policy validation happens in a `code` node or a tool's
pre/post hook, built from seed/datefns.py's functions via inspect.getsource so the sandboxed copy
is byte-identical to the one seed/test_datefns.py tests. The dialogue model is never asked to do
date math, count attempts, decide whether get_account may be called yet, or remember which fields
a renamed brief uses.
"""
import inspect
import json

from seed import datefns as D

OPENING_MAX_ASKS = 2
IDENTITY_MAX_DECLINES = 2
HUMAN_INSIST_THRESHOLD = 2

# ---------------------------------------------------------------------------
# Code-node source assembly
# ---------------------------------------------------------------------------

_DATEFN_NAMES = [
    "parse_date", "parse_datetime", "is_business_day", "next_business_day",
    "on_or_after_business_day", "add_business_days", "band_for", "deadline_for",
    "floor_for", "last_business_day_of_week", "attempt1_date_uncapped", "product_facts",
    "resolve_named_date", "validate_promise", "callback_ok", "outcome_for_call",
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
    """Render a plain-data value (str/int/float/bool/None/list/dict of those) as Python source,
    guaranteed not to contain "}}" by construction."""
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
# Tool definitions, generated from tools_api (the brief's own openapi.json)
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
    """One tool function, shaped entirely from tools_api's schema for `/<name>`: whatever
    properties that schema has, under whatever names, become this tool's body fields. `baked`
    supplies a literal or `{{var}}` default for the fields the engine should fill (hidden from the
    model); every other field defaults to `{{<its own name>}}`, so the model sees and fills it."""
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
# Canonical outcome-map helpers (schema structure, not brief content: every config, including
# every testbed fake brief, uses this exact key vocabulary -- only the mapped values vary)
# ---------------------------------------------------------------------------

def _close_bake(policy, outcome_key):
    """The baked `default` fields for a close_call whose canonical outcome is `outcome_key`."""
    cc = policy["tools"]["close_call"]
    value, reason = policy["outcome_map"][outcome_key]
    if cc["mode"] == "code":
        return {cc["code"]: value}
    return {cc["outcome"]: value, cc["reason"]: (reason or "")}


def _tr_key(trigger):
    return "tr_" + trigger


def _np_key(reason_key):
    return {"refused": "np_refused", "hardship": "np_hardship", "dispute": "np_dispute", "service_error": "np_service_error"}[reason_key]


# ---------------------------------------------------------------------------
# build_workflow
# ---------------------------------------------------------------------------

def build_workflow(policy, tools_api):
    holidays = policy["holidays"]
    bands = policy["bands"]
    sentences = policy["sentences"]
    identity_cfg = policy["identity"]
    cb = policy["callback"]
    outcome_map = policy["outcome_map"]
    queue_map = policy["queue_map"]
    note_map = policy["note_map"]
    close_rule = policy["close_rule"]
    notes_on_restructuring = policy.get("notes_on_restructuring")
    account_fields = policy["account_fields"]
    product_types = policy["product_types"]
    forbidden = policy["forbidden_before_verify"]
    t = policy["tools"]

    type_by_enum = {pt["enum"]: pt for pt in product_types.values()}
    floor_field_by_enum = {pt["enum"]: pt["floor_field"] for pt in product_types.values()}
    type_field = account_fields["type"]
    product_id_field = account_fields["product_id"]
    past_due_field = account_fields["past_due"]
    days_late_field = account_fields["days_late"]

    vi = t["verify_identity"]
    cp = t["create_promise"]
    sc = t["schedule_callback"]
    cc = t["close_call"]
    tf = t["transfer_to_agent"]
    an = t["add_note"]

    datefns_src = _datefns_source()
    address_tmpl = policy["address"].replace("{", "{{").replace("}", "}}")
    opener_line = f"I am {sentences['opening']}."
    robot_line = f"I am {sentences['ai']}."
    scam_line = f"You can {sentences['scam']}."
    failed_id_line = f"Please {sentences['failed_id']}."
    promise_confirmation_line = sentences["promise_confirmation"]

    nodes = {}

    def add(n):
        nodes[n["name"]] = n

    # ---- terminal helper: say an (optional) fixed line + goodbye, close_call, hang up ----
    def terminal(key, outcome_key, extra_sentence=""):
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
                                 baked=_close_bake(policy, outcome_key))
        add(pre_action_node(close, close_tool, hang))
        text = (extra_sentence + " Thank you, goodbye.").strip() if extra_sentence else "Thank you, goodbye."
        add(say_then_node(say, text, close))
        return say

    close_not_reached_wrong_number = terminal("not_reached_wrong_number", "nr_wrong_number")
    close_not_reached_deceased = terminal("not_reached_deceased", "nr_deceased", "I am sorry for your loss.")
    close_not_reached_unclear = terminal("not_reached_unclear", "nr_unclear")
    close_not_reached_no_callback = terminal("not_reached_no_callback", "nr_no_callback")
    close_not_reached_callback = terminal("not_reached_callback", "nr_callback")
    close_not_verified_failed = terminal("not_verified_failed", "nv_failed", failed_id_line)
    close_not_verified_declined = terminal("not_verified_declined", "nv_declined")
    close_service_error_account = terminal("service_error_account", _np_key("service_error"))

    # ================================= 0. init =================================
    callback_window_text = ("today ({{today}}) or the next business day ({{next_business_day}})" if cb["same_day"]
                             else "the next business day ({{next_business_day}}) only")
    add(code_node(
        "init",
        "import datetime\n" + f"""
try:
    now_dt = datetime.datetime.fromisoformat(str(vars.get('now')))
    today = now_dt.date()
    holidays = {_py_literal(holidays)}
    nb = today + datetime.timedelta(days=1)
    while nb.weekday() >= 5 or nb.isoformat() in holidays:
        nb = nb + datetime.timedelta(days=1)
    vars['today'] = today.isoformat()
    vars['next_business_day'] = nb.isoformat()
except Exception:
    vars['today'] = ''
    vars['next_business_day'] = ''
vars['opening_asks'] = 0
vars['opening_instruction'] = 'This is the first ask: say the opener sentence, then ask the identification question.'
""",
        "opening",
    ))

    # ================================= 1. opening =================================
    add({
        "name": "opening", "node_type": "conversation",
        "task_message": (
            "{{opening_instruction}}\n\n"
            f"The opener sentence, word for word, said only on the first ask: \"{opener_line}\"\n"
            f"The identification question, asked every time: \"Am I speaking with {address_tmpl}?\"\n\n"
            f"Only an explicit yes, \"that's me\", \"speaking\", or the customer saying their own name "
            f"counts as confirmation. \"Hello\", \"go ahead\" and \"who is this\" do not. "
            f"Never say the words {', '.join(forbidden)} or any product name before identity is "
            f"verified, even to a relative. If asked whether you are a robot or an AI, say: "
            f"\"{robot_line}\" and continue. Do not call a tool in this turn."
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
    vars['opening_exhausted'] = 'yes' if asks >= {OPENING_MAX_ASKS} else 'no'
    vars['opening_instruction'] = 'Do not repeat the opener. Just ask the identification question again.'
except Exception:
    vars['opening_asks'] = {OPENING_MAX_ASKS}
    vars['opening_exhausted'] = 'yes'
""",
        "opening",
        extra_transitions=[{"target_node": close_not_reached_unclear, "description": "Still unclear after the second ask",
                             "condition": {"logic": "or", "conditions": [{"variable": "opening_exhausted", "operator": "equals", "operand": "yes"}]}}],
    ))

    # ================================= 2. third_party =================================
    schedule_tool = build_tool(tools_api, "schedule_callback", "Book a callback when someone else answered. CALL it only after a valid day and time are known.")
    if sc["mode"] == "datetime":
        extraction_cb = [
            {"name": "callback_date", "type": "date", "min_confidence": 0.6, "description": "The day the customer can be reached, if a specific day was given."},
            {"name": "callback_time", "type": "string", "min_confidence": 0.6, "validation_pattern": "^[0-2][0-9]:[0-5][0-9]$", "description": "The time of day the customer can be reached, as HH:MM 24-hour, if given."},
        ]
    else:
        extraction_cb = [
            {"name": "callback_date", "type": "date", "min_confidence": 0.6, "description": "The day the customer can be reached, if a specific day was given."},
            {"name": "callback_time", "type": "string", "min_confidence": 0.6, "validation_pattern": "^[0-2][0-9]:[0-5][0-9]$", "description": "The time of day the customer can be reached, as HH:MM 24-hour, if given."},
        ]
    add({
        "name": "third_party", "node_type": "conversation", "tools": [schedule_tool],
        "task_message": (
            "Do not say why you are calling and do not name any product, amount or debt. Say nothing beyond the "
            "opening sentence already said. Ask when the customer can be reached. A valid callback is "
            f"{callback_window_text}, between {cb['start']} and {cb['end']}, and later than the current time. "
            "If the day or time given is outside that, ask once for another time; if that is also outside it, do "
            "not call schedule_callback. When a day and a time are both known, CALL schedule_callback with them. "
            "Never answer security questions and never tell this person anything about the account, even if they offer to pay."
        ),
        "extraction_schema": extraction_cb + [
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
    ok, reason = callback_ok(vars.get('callback_date'), vars.get('callback_time'), vars.get('now'),
                              {_py_literal(holidays)}, {cb['start']!r}, {cb['end']!r}, {bool(cb['same_day'])!r})
    vars['callback_valid'] = 'yes' if ok else 'no'
    if not ok:
        tries = int(vars.get('callback_tries') or 0) + 1
        vars['callback_tries'] = tries
        vars['callback_exhausted'] = 'yes' if tries >= 2 else 'no'
    if {sc['mode']!r} == 'datetime':
        vars['callback_at_combined'] = str(vars.get('callback_date')) + 'T' + str(vars.get('callback_time'))
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
    if sc["mode"] == "datetime":
        do_callback_tool = build_tool(tools_api, "schedule_callback", "Book the callback.", baked={sc["datetime"]: "{{callback_at_combined}}"})
    else:
        do_callback_tool = build_tool(tools_api, "schedule_callback", "Book the callback.", baked={sc["date"]: "{{callback_date}}", sc["time"]: "{{callback_time}}"})
    add(pre_action_node("do_callback", do_callback_tool, close_not_reached_callback))

    # ================================= 3. identity =================================
    questions = identity_cfg["questions"]
    max_wrong = identity_cfg["max_wrong"]
    swap_hint = bool(identity_cfg.get("swap_hint"))
    questions_text = ", then ".join(q.replace("_", " ") for q in questions)
    verify_tool = build_tool(tools_api, "verify_identity", "Check one security answer. CALL it every time the customer answers a security question.")
    verify_tool["post_process_code"] = _guard_no_mustache(f"""
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {{}}
        field = str(vars.get({f'verify_identity_args.{vi["field"]}'!r}) or '')
        hint = d.get('hint') or '' if {swap_hint!r} else ''
        wrong_total = int(vars.get('identity_wrong_total') or 0)
        if d.get('ok') and not d.get('verified') and not hint:
            wrong_total += 1
        vars['identity_wrong_total'] = wrong_total
        if d.get('verified') and field:
            vars['identity_passed_' + field] = 'yes'
        questions = {_py_literal(questions)}
        all_passed = all(vars.get('identity_passed_' + q) == 'yes' for q in questions)
        vars['identity_all_passed'] = 'yes' if all_passed else 'no'
        vars['identity_failed'] = 'yes' if wrong_total >= {max_wrong} else 'no'
        if wrong_total >= {max_wrong}:
            d['what_to_do_next'] = 'Stop. Do not state the debt. The call will close as not verified.'
        elif hint == 'day_month_swapped':
            d['what_to_do_next'] = 'Ask the customer to say the month as a word. This retry does not count as a wrong answer.'
        elif d.get('verified') and all_passed:
            d['what_to_do_next'] = 'Every question passed. Say nothing about the account yet; the next step will fetch it.'
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
            f"Ask these security questions, one at a time, in this order: {questions_text}. When the customer "
            f"answers, CALL verify_identity with {vi['field']} set to the question key and {vi['answer']} set to "
            "their answer; the tool's own description tells you the exact format each question needs. After the "
            "tool result, follow its what_to_do_next instruction exactly. If the customer does not want to answer, "
            "or asks what the call is about instead of answering, explain once that the questions protect their "
            "account; a second refusal or a second such question ends the check. Never state the debt, the "
            f"product or the amount in this node. Never say the words {', '.join(forbidden)}."
        ),
        "extraction_schema": [
            {"name": "identity_declined", "type": "string", "enum_values": ["yes", "no"], "min_confidence": 0.65,
             "description": "Yes if the customer refused to answer the security question just asked, or asked what the call is about instead of answering."},
        ],
        "transitions": [
            {"target_node": close_not_verified_failed, "description": "Every allowed wrong answer used",
             "condition": {"logic": "or", "conditions": [{"variable": "identity_failed", "operator": "equals", "operand": "yes"}]}},
            {"target_node": "fetch_account", "description": "Every question passed",
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
    vars['identity_declined_exhausted'] = 'yes' if c >= {IDENTITY_MAX_DECLINES} else 'no'
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
    floor_by_enum = {_py_literal(floor_field_by_enum)}
    type_labels = {_py_literal({pt["enum"]: pt["label"] for pt in product_types.values()})}
    last4_fields = {_py_literal({pt["enum"]: pt["last4_field"] for pt in product_types.values()})}
    facts = []
    for p in raw:
        f = product_facts(p, today, bands, holidays, {type_field!r}, floor_by_enum, {past_due_field!r}, {days_late_field!r})
        f['product_id'] = p.get({product_id_field!r})
        type_value = p.get({type_field!r})
        label = type_labels.get(type_value, str(type_value))
        last4_field = last4_fields.get(type_value)
        if last4_field:
            label = label.replace('{{last4}}', str(p.get(last4_field) or ''))
        f['label'] = label
        facts.append(f)
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
    cents = cp.get("amount_unit") == "cents"
    create_promise_tool = build_tool(
        tools_api, "create_promise", "Record a payment promise. CALL it only after the customer confirmed the read-back.",
        baked={cp["product_id"]: "{{current_product_id}}"},
    )
    create_promise_tool["pre_process_code"] = _guard_no_mustache(datefns_src + f"""
try:
    holidays = {_py_literal(holidays)}
    date_in = config['body'].get({cp['date']!r})
    amt_in = config['body'].get({cp['amount']!r})
    amt_dollars = (float(amt_in) / 100.0) if {cents!r} and amt_in is not None else amt_in
    ok, date_out, amt_out, reason = validate_promise(
        date_in, amt_dollars, vars.get('current_min_date'), vars.get('current_deadline'),
        float(vars.get('current_floor') or 0), float(vars.get('current_full') or 0), holidays)
    config['body'][{cp['date']!r}] = date_out
    config['body'][{cp['amount']!r}] = int(round(amt_out * 100)) if {cents!r} else amt_out
    vars['promise_correction_reason'] = reason
    vars['promise_corrected_amount_dollars'] = amt_out
except Exception:
    pass
""")
    create_promise_tool["post_process_code"] = _guard_no_mustache(f"""
import json as _json
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {{}}
        if d.get('ok'):
            promises = vars.get('promises_json')
            promises = _json.loads(promises) if isinstance(promises, str) else (promises or [])
            corrected_amount = vars.get('promise_corrected_amount_dollars')
            try:
                corrected_amount = float(vars.get({f'create_promise_args.{cp["amount"]}'!r}))
                if {cents!r}:
                    corrected_amount = corrected_amount / 100.0
            except Exception:
                pass
            corrected_date = vars.get({f'create_promise_args.{cp["date"]}'!r})
            promises.append({{'product_id': vars.get('current_product_id'), 'amount': corrected_amount, 'date': corrected_date}})
            vars['promises_json'] = _json.dumps(promises)
            vars['advance_reason'] = 'promised'
            reason = vars.get('promise_correction_reason') or ''
            lead = ('Before recording, the date or amount was adjusted (' + reason + '). ') if reason else ''
            next_step = (lead + 'Say the promise is recorded for ' + str(corrected_amount) + ' on ' + str(corrected_date) +
                ', then say word for word: "{promise_confirmation_line}"')
            d['what_to_do_next'] = next_step + ' If this was the last unresolved product, also thank the customer and say goodbye, then call finish_call.'
        else:
            fails = int(vars.get('current_promise_fails') or 0) + 1
            vars['current_promise_fails'] = fails
            if fails >= 2:
                d['what_to_do_next'] = 'It failed twice. Say you could not record it. Call add_note with reason {note_map["service_error"]} for this product.'
            else:
                d['what_to_do_next'] = 'It failed. Do not say it is recorded. Try create_promise once more with the same values.'
except Exception:
    pass
""")

    add_note_tool = build_tool(
        tools_api, "add_note", "Record why a product ends without a promise. CALL it once per product that is refused, disputed, already paid, or a hardship case the customer did not accept a transfer for.",
        baked={an["product_id"]: "{{current_product_id}}"},
    )
    reverse_note_map = {v: k for k, v in note_map.items()}
    add_note_tool["post_process_code"] = _guard_no_mustache(f"""
import json as _json
try:
    if isinstance(result, dict):
        result.pop('header', None)
        d = result.get('data') or {{}}
        if d.get('ok'):
            reverse_note_map = {_py_literal(reverse_note_map)}
            actual_reason = vars.get({f'add_note_args.{an["reason"]}'!r})
            notes = vars.get('notes_json')
            notes = _json.loads(notes) if isinstance(notes, str) else (notes or [])
            notes.append({{'product_id': vars.get('current_product_id'), 'reason': reverse_note_map.get(actual_reason, actual_reason)}})
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
            f"plan or any term not in these facts. If the customer says they already paid, say word for word: "
            f"\"Payments can take one business day to appear.\" then call add_note with reason {note_map['paid']} "
            f"for this product and move on. If the customer says this debt or product is not theirs, do not "
            f"argue; call add_note with reason {note_map['dispute']} for this product and move on. If the "
            "customer mentions illness, a death in the family or job loss, say one short sentence of sympathy, "
            "make no persuasion attempt, and offer the support team once (only once per call). If a "
            f"security-sounding question like 'is this a scam' comes up, say word for word: \"{scam_line}\" and "
            "continue. When a rule here says to call a tool, call the tool; saying you did something is not "
            "enough. When every product is resolved, thank the customer and say goodbye in the same turn, then "
            "call the finish_call transition."
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
        f"""
import json as _json
try:
    pid = vars.get('current_product_id')
    used = _json.loads(vars.get('attempts_used_json') or '{{}}')
    n = int(used.get(pid) or 0) + 1
    used[pid] = n
    vars['attempts_used_json'] = _json.dumps(used)
    allowed = int(vars.get('current_attempts_allowed') or 1)
    band = vars.get('current_band')
    if n >= allowed:
        vars['current_instruction'] = ('The customer refused the ' + str(vars.get('current_label')) +
            ' after every allowed attempt. Do not repeat an offer already refused. Call add_note with reason {note_map["refused"]} for this product, then move on.')
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

    add(code_node("finish_call", "pass", "closing"))

    # ================================= 6. transfer =================================
    def _close_tool_for_var(var_name):
        """A close_call whose outcome is only known at runtime: both fields (or the single code
        field) are baked from vars the way _close_bake would compute them, but since the key
        itself varies at runtime we bake the already-resolved value/reason strings instead."""
        if cc["mode"] == "code":
            return build_tool(tools_api, "close_call", "End the call.", baked={cc["code"]: "{{" + var_name + "_value}}"})
        return build_tool(tools_api, "close_call", "End the call.", baked={cc["outcome"]: "{{" + var_name + "_value}}", cc["reason"]: "{{" + var_name + "_reason}}"})

    add(code_node(
        "prepare_transfer",
        f"""
import json as _json
try:
    trigger = str(vars.get('turn_signal') or '')
    queue_map = {_py_literal(queue_map)}
    vars['transfer_queue'] = queue_map.get(trigger, queue_map.get('restructuring'))
    vars['transfer_outcome_key'] = 'tr_' + (trigger or 'restructuring')
except Exception:
    pass
""",
        "note_before_transfer_check" if notes_on_restructuring else "transfer_call",
    ))

    if notes_on_restructuring:
        # f3-kestrel style ("Notes before a restructuring transfer"): every product still without
        # a promise is noted with a fixed reason before the transfer fires. Only transfer_to_agent
        # itself is a forced code call; add_note is still a real tool the model calls, in a tiny
        # dedicated loop that is not the main per-product negotiation (the reason is fixed, not
        # negotiated), one product at a time until none are left.
        add(code_node(
            "note_before_transfer_check",
            f"""
import json as _json
try:
    if vars.get('transfer_outcome_key') != 'tr_restructuring':
        vars['note_before_transfer_done'] = 'yes'
    else:
        products = _json.loads(vars.get('products_json') or '[]')
        promised = set(p['product_id'] for p in _json.loads(vars.get('promises_json') or '[]'))
        noted = set(n['product_id'] for n in _json.loads(vars.get('notes_json') or '[]'))
        remaining = [p for p in products if p['product_id'] not in promised and p['product_id'] not in noted]
        if not remaining:
            vars['note_before_transfer_done'] = 'yes'
        else:
            vars['note_before_transfer_done'] = 'no'
            vars['current_product_id'] = remaining[0]['product_id']
            vars['current_label'] = remaining[0]['label']
            vars['current_instruction'] = 'Before the transfer, call add_note with reason {notes_on_restructuring} for the ' + remaining[0]['label'] + '.'
except Exception:
    vars['note_before_transfer_done'] = 'yes'
""",
            "note_before_transfer",
            extra_transitions=[{"target_node": "transfer_call", "description": "Nothing left to note",
                                 "condition": {"logic": "or", "conditions": [{"variable": "note_before_transfer_done", "operator": "equals", "operand": "yes"}]}}],
        ))
        note_before_transfer_tool = build_tool(
            tools_api, "add_note", "Record why a product ends without a promise before the transfer.",
            baked={an["product_id"]: "{{current_product_id}}", an["reason"]: note_map[notes_on_restructuring]},
        )
        add({
            "name": "note_before_transfer", "node_type": "conversation", "tools": [note_before_transfer_tool],
            "task_message": "{{current_instruction}} Call add_note now; do not say anything to the customer in this turn.",
            "transitions": [
                {"target_node": "note_before_transfer_check", "description": "always", "condition": {"operator": "always"}},
            ],
        })

    transfer_tool = build_tool(tools_api, "transfer_to_agent", "Hand the call to a person.", baked={tf["queue"]: "{{transfer_queue}}"})
    add(pre_action_node("transfer_call", transfer_tool, "close_transferred"))
    add(code_node(
        "close_transferred",
        f"""
try:
    outcome_map = {_py_literal(outcome_map)}
    key = vars.get('transfer_outcome_key') or 'tr_restructuring'
    value, reason = outcome_map.get(key, outcome_map['tr_restructuring'])
    vars['closevar_value'] = value
    vars['closevar_reason'] = reason or ''
except Exception:
    pass
""",
        "close_transferred_call",
    ))
    add(pre_action_node("close_transferred_call", _close_tool_for_var("closevar"), "hang_up_transferred"))
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
    close_rule = {_py_literal(close_rule)}
    outcome, reason = outcome_for_call(promises, notes, close_rule)
    if outcome == 'promise':
        key = 'promise'
    elif outcome == 'paid':
        key = 'paid'
    else:
        key = {{'refused': 'np_refused', 'hardship': 'np_hardship', 'dispute': 'np_dispute', 'service_error': 'np_service_error'}}.get(reason, 'np_refused')
    outcome_map = {_py_literal(outcome_map)}
    value, out_reason = outcome_map[key]
    vars['closevar_value'] = value
    vars['closevar_reason'] = out_reason or ''
except Exception:
    outcome_map = {_py_literal(outcome_map)}
    value, out_reason = outcome_map[{_np_key('service_error')!r}]
    vars['closevar_value'] = value
    vars['closevar_reason'] = out_reason or ''
""",
        "close_final",
    ))
    add(pre_action_node("close_final", _close_tool_for_var("closevar"), "hang_up_final"))
    add({
        "name": "hang_up_final", "node_type": "end_call", "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {"name": "ended_final", "handler": "__function__:end_call",
                     "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    })

    system_prompt = (
        "You are the bank's digital assistant on an outbound phone call. Speak English. Keep every turn to one "
        "or two short sentences. Call one tool per message and speak after every tool result; never end a turn "
        "with nothing and no tool call. If asked whether you are a robot or an AI, say: "
        f"\"{robot_line}\" and continue. If asked whether this is a scam, say: \"{scam_line}\" and continue. If "
        "you cannot answer something from the tools, say you do not have that information; never invent a "
        "figure, a date or a reason."
    )

    return {
        "initial_node": "init",
        "system_prompt": system_prompt,
        "nodes": nodes,
        "global_nodes": {},
        "test_variables": {"customer_id": "c_test", "first_name": "Dana", "last_name": "Reyes", "title": "Ms", "now": "2026-10-14T11:00"},
    }
