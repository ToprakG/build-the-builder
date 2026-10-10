"""Freya unit tests: one scripted turn, checked on the real engine.

A test is the conversation so far, the call variables, and checks on the next
turn: a tool call (arguments matched against the request body), the node the
call entered, a variable's value, and a sentence for the judge.

There is no run-one endpoint. This syncs the suite onto a workflow, calls
run-all, and reads each verdict. `seed-call` turns one message of a real call
into a test. `from-run` does that for failed practice cases.

    python3 seed/unit_tests.py --print
    python3 seed/unit_tests.py --slot brief-a
    python3 seed/unit_tests.py --workflow-id WORKFLOW --config testbed/fake_briefs/f1-nordhavn/config.json
    python3 seed/unit_tests.py seed-call --workflow-id WORKFLOW --call-id CALL --message-id MSG --name "PUB-02 callback" --expect "callback booked for the next business day at 11:00"
    python3 seed/unit_tests.py from-run results/seed-a-r1.json --workflow-id WORKFLOW --cases PUB-02,PUB-03,PUB-07,PUB-09

`--print` checks the suite offline for all nine configs and does not call Freya.
A run writes results/unit-tests.json. `run_cases.py --unit-gate` refuses to
start a simulation unless that file is all pass for the same agent.
"""
import datetime
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seed import datefns as D

NOW = "2026-10-14T11:00"
TODAY = datetime.date.fromisoformat(NOW[:10])
REPORT = ROOT / "results" / "unit-tests.json"
WRONG = {
    "date_of_birth": "1 January 1900", "phone_last4": "0000", "phone": "0000",
    "postcode": "ZZ99 9ZZ", "birth_year": "1900", "company_number": "00000000", "founded_year": "1800",
}


def _ordinal(day):
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def say_date(iso):
    day = datetime.date.fromisoformat(iso)
    return f"{day.strftime('%A')} the {_ordinal(day.day)} of {day.strftime('%B')}"


def shift_clock(hhmm, minutes):
    hour, minute = (int(part) for part in hhmm.split(":"))
    total = hour * 60 + minute + minutes
    if total < 0 or total >= 24 * 60:
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


def whole(value):
    number = float(value)
    return int(number) if number == int(number) else number


def address(policy, customer):
    text = policy.get("address") or "{title} {last_name}"
    for key, value in customer.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def customer_variables(policy):
    values = {"customer_id": "c_unit", "first_name": "Maria", "last_name": "Costa", "title": "Ms", "now": NOW, "company_name": "Northwind"}
    kept = {"customer_id", "first_name", "last_name", "title", "now"}
    if "company_name" in (policy.get("address") or ""):
        kept.add("company_name")
    return {key: values[key] for key in kept}


def speak(role, text, node=None, variables=None):
    row = {"role": role, "message": text}
    if node:
        row["nodeId"] = node
    if node and variables is not None:
        row["messageId"] = "m-" + hashlib.sha1(f"{role}|{node}|{text}".encode()).hexdigest()[:12]
        row["workflowSnapshot"] = {
            "currentNode": node,
            "nodeTurnCount": 1,
            "totalTurnCount": 2,
            "transitionHistory": [],
            "enteredNodes": [node],
            "variableTable": {
                key: {"value": value, "confidence": 1, "source": "tool_result", "source_node": node}
                for key, value in variables.items()
            },
        }
    return row


def tool(name, body=None, negate=False):
    row = {"type": "tool_call", "tool": name}
    if negate:
        row["negate"] = True
    if body is not None:
        row["args"] = {"body": body}
        row["argsMatch"] = "subset"
    return row


def route(target, negate=False):
    row = {"type": "routing", "signal": "node_entered", "target": target}
    if negate:
        row["negate"] = True
    return row


def extract(name, value):
    return {"type": "extraction", "name": name, "match": "equals", "value": value}


def envelope(data):
    return {"status": "success", "data": data, "message": "ok", "status_code": 200}


def _product(policy, days_late, number, full=420.0, floor=100.0):
    fields = policy["account_fields"]
    kinds = list(policy["product_types"].values())
    kind = kinds[(number - 1) % len(kinds)]
    product = {
        fields["product_id"]: policy.get("product_id_format", "p{n}").format(n=number),
        fields["type"]: kind["enum"],
        fields["past_due"]: full,
        fields["days_late"]: days_late,
        kind["floor_field"]: floor,
    }
    if kind.get("last4_field"):
        product[kind["last4_field"]] = "4821"
    return product, kind


def _facts(policy, product):
    fields = policy["account_fields"]
    floors = {item["enum"]: item["floor_field"] for item in policy["product_types"].values()}
    facts = dict(D.product_facts(
        product, TODAY, policy["bands"], policy["holidays"],
        fields["type"], floors, fields["past_due"], fields["days_late"],
    ))
    facts["product_id"] = product[fields["product_id"]]
    kind = next(item for item in policy["product_types"].values() if item["enum"] == product[fields["type"]])
    label = kind["label"]
    if kind.get("last4_field"):
        label = label.replace("{last4}", str(product.get(kind["last4_field"]) or ""))
    facts["label"] = label
    return facts


def _negotiate_vars(facts, extra=None):
    values = {
        "products_json": json.dumps([facts] if isinstance(facts, dict) else facts),
        "current_product_index": 0,
        "current_product_id": facts["product_id"] if isinstance(facts, dict) else facts[0]["product_id"],
        "current_label": facts["label"] if isinstance(facts, dict) else facts[0]["label"],
        "current_floor": whole(facts["floor"] if isinstance(facts, dict) else facts[0]["floor"]),
        "current_full": whole(facts["full"] if isinstance(facts, dict) else facts[0]["full"]),
        "current_deadline": facts["deadline"] if isinstance(facts, dict) else facts[0]["deadline"],
        "current_min_date": facts["min_date"] if isinstance(facts, dict) else facts[0]["min_date"],
        "current_attempt1_date": facts["attempt1_date"] if isinstance(facts, dict) else facts[0]["attempt1_date"],
        "current_attempts_allowed": facts["attempts_allowed"] if isinstance(facts, dict) else facts[0]["attempts_allowed"],
        "current_band": facts["band"] if isinstance(facts, dict) else facts[0]["band"],
        "current_attempt_number": 1,
        "promises_json": "[]",
        "notes_json": "[]",
        "attempts_used_json": "{}",
        "support_offered": "no",
        "human_asks": 0,
        "identity_all_passed": "yes",
    }
    if extra:
        values.update(extra)
    if not isinstance(facts, dict):
        values["products_json"] = json.dumps(facts)
    return values


def _promise_body(policy, product_id, amount, day):
    fields = policy["tools"]["create_promise"]
    sent = int(round(float(amount) * 100)) if fields.get("amount_unit") == "cents" else whole(amount)
    return {fields["product_id"]: product_id, fields["amount"]: sent, fields["date"]: day}


def _callback_body(policy, day, clock):
    fields = policy["tools"]["schedule_callback"]
    if fields["mode"] == "datetime":
        return {fields["datetime"]: f"{day}T{clock}"}
    return {fields["date"]: day, fields["time"]: clock}


def _close_body(policy, outcome_key):
    fields = policy["tools"]["close_call"]
    value, reason = policy["outcome_map"][outcome_key]
    if fields["mode"] == "code":
        return {fields["code"]: value}
    body = {fields["outcome"]: value}
    if reason:
        body[fields["reason"]] = reason
    return body


def _transfer_body(policy, queue):
    return {policy["tools"]["transfer_to_agent"]["queue"]: queue}


def inside_callback(policy):
    """Next business day, preferring 11:00. Today at 11:00 is not later than now."""
    window = policy["callback"]
    day = D.next_business_day(TODAY, policy["holidays"]).isoformat()
    for clock in ("11:00", "15:00", "12:00", window["end"]):
        ok, _reason = D.callback_ok(day, clock, NOW, policy["holidays"], window["start"], window["end"], window["same_day"])
        if ok:
            return day, clock
    raise RuntimeError(f"no callback slot inside {window['start']}-{window['end']}")


def _opener(policy, customer):
    return f"I am {policy['sentences']['opening']}. Am I speaking with {address(policy, customer)}?"


def _add(tests, policy, name, expectation, assertions, messages, tags, mock=None):
    row = {
        "name": name,
        "expectation": expectation,
        "assertions": assertions,
        "messages": messages,
        "customer_variables": customer_variables(policy),
        "tags": ["generated", *tags],
    }
    if mock:
        row["mock"] = mock
    tests.append(row)


def suite(policy):
    """The build-time suite for one config. Dates and field names come from the config."""
    tests = []
    customer = {"title": "Ms", "first_name": "Maria", "last_name": "Costa", "company_name": "Northwind"}
    opener = _opener(policy, customer)
    day, clock = inside_callback(policy)
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    if day == tomorrow and clock == "11:00":
        callback_line = "She can be reached tomorrow morning around eleven."
    else:
        callback_line = f"She can be reached on {say_date(day)} at {clock}."
    _add(tests, policy, "callback inside the window",
         f"Book the callback for {say_date(day)} at {clock}. Do not verify identity and do not open the account.",
         [tool("schedule_callback", _callback_body(policy, day, clock)), extract("callback_date", day), extract("callback_time", clock),
          tool("verify_identity", negate=True), tool("get_account", negate=True)],
         [speak("assistant", opener, "third_party", {"opening_asks": 1}), speak("user", callback_line)],
         ["callback-inside"])

    window = policy["callback"]
    for label, clock_out in (("before the window", shift_clock(window["start"], -60)), ("after the window", shift_clock(window["end"], 60))):
        if not clock_out:
            continue
        ok, _reason = D.callback_ok(day, clock_out, NOW, policy["holidays"], window["start"], window["end"], window["same_day"])
        if ok:
            continue
        _add(tests, policy, f"callback {label}",
             f"The time {clock_out} is outside {window['start']} to {window['end']}. Do not tell the caller the callback is booked. Ask for another time.",
             [tool("verify_identity", negate=True), tool("get_account", negate=True)],
             [speak("assistant", opener, "third_party", {"opening_asks": 1}),
              speak("user", f"She can be reached on {say_date(day)} at {clock_out}.")],
             ["callback-edge"])

    questions = policy["identity"]["questions"]
    limit = int(policy["identity"]["max_wrong"])
    wrong = WRONG.get(questions[0], "0000")
    asked = questions[0].replace("_", " ")
    failed_line = policy["sentences"]["failed_id"]
    verify_mock = {"verify_identity": envelope({"ok": True, "verified": False})}
    _add(tests, policy, "wrong answer reaches the limit",
         f"This wrong answer uses the last attempt. End the check. Say to {failed_line}. Do not state the debt or open the account.",
         [route("say_not_verified_failed"), tool("verify_identity"), tool("get_account", negate=True)],
         [speak("assistant", f"What is your {asked}?", "identity", {"identity_wrong_total": limit - 1, "identity_failed": "no", "identity_all_passed": "no"}),
          speak("user", wrong)],
         ["identity-limit"], verify_mock)
    if limit >= 2:
        _add(tests, policy, "wrong answer before the limit",
             "The answer is wrong, but attempts remain. Ask the question again. Do not end the check and do not open the account.",
             [route("say_not_verified_failed", negate=True), tool("verify_identity"), tool("get_account", negate=True)],
             [speak("assistant", f"What is your {asked}?", "identity", {"identity_wrong_total": limit - 2, "identity_failed": "no", "identity_all_passed": "no"}),
              speak("user", wrong)],
             ["identity-limit"], verify_mock)

    for band in policy["bands"]:
        product, _kind = _product(policy, band["min_days"], 1)
        facts = _facts(policy, product)
        late = (datetime.date.fromisoformat(facts["deadline"]) + datetime.timedelta(days=14)).isoformat()
        amount = whole(facts["full"])
        _add(tests, policy, f"deadline cap {band['name']}",
             f"The customer named {say_date(late)}, which is after the deadline {say_date(facts['deadline'])}. Record the promise on the deadline for the full amount, not on the later date.",
             [tool("create_promise", _promise_body(policy, facts["product_id"], amount, facts["deadline"])),
              tool("create_promise", {policy["tools"]["create_promise"]["date"]: late}, negate=True)],
             [speak("assistant", f"Can you pay {amount} today? The latest date I can record is {say_date(facts['deadline'])}.", "negotiate", _negotiate_vars(facts)),
              speak("user", f"I can only pay on {say_date(late)}."),
              speak("assistant", f"The latest date I can record is {say_date(facts['deadline'])}. Can you pay {amount} then?", "negotiate", _negotiate_vars(facts)),
              speak("user", "Yes, I confirm.")],
             ["deadline"])

    _days, widest = policy["bands"][0]["min_days"], TODAY
    for band in policy["bands"]:
        deadline = D.deadline_for(TODAY, band["min_days"], policy["bands"], policy["holidays"])
        if deadline > widest:
            widest = deadline
            _days = band["min_days"]
    holiday = None
    for raw in policy["holidays"]:
        candidate = datetime.date.fromisoformat(raw)
        shifted = D.next_business_day(candidate, policy["holidays"])
        if TODAY < candidate <= widest and shifted <= widest:
            holiday = candidate
            break
    if holiday is not None:
        shifted = D.next_business_day(holiday, policy["holidays"])
        product, _kind = _product(policy, _days, 1)
        facts = _facts(policy, product)
        amount = whole(facts["full"])
        date_field = policy["tools"]["create_promise"]["date"]
        _add(tests, policy, "holiday shifts to the next business day",
             f"{say_date(holiday.isoformat())} is not a business day. Offer {say_date(shifted.isoformat())} and record that date. Do not record the holiday.",
             [tool("create_promise", _promise_body(policy, facts["product_id"], amount, shifted.isoformat())),
              tool("create_promise", {date_field: holiday.isoformat()}, negate=True)],
             [speak("assistant", f"Can you pay {amount} today?", "negotiate", _negotiate_vars(facts)),
              speak("user", f"I can pay on {say_date(holiday.isoformat())}."),
              speak("assistant", f"That day is not a business day. The next business day is {say_date(shifted.isoformat())}. Can you pay {amount} then?", "negotiate", _negotiate_vars(facts)),
              speak("user", "Yes, I confirm.")],
             ["holiday"])

    product, _kind = _product(policy, policy["bands"][-1]["min_days"], 1, full=2100.0, floor=700.0)
    facts = _facts(policy, product)
    base = _negotiate_vars(facts)
    queues = policy["queue_map"]
    _add(tests, policy, "payment plan transfers to restructuring",
         "The customer asked for a payment plan. Transfer the call. Do not describe a plan and do not close the call in this turn.",
         [tool("transfer_to_agent", _transfer_body(policy, queues["restructuring"])), tool("close_call", negate=True)],
         [speak("assistant", f"Can you pay {whole(facts['full'])} today?", "negotiate", base),
          speak("user", "I can't pay this. Can I get a payment plan with lower instalments?")],
         ["transfer"])
    human = dict(base)
    human["human_asks"] = 1
    _add(tests, policy, "insists on a person",
         "The customer has already been offered help and still wants a person. Transfer the call. Do not close it in this turn.",
         [tool("transfer_to_agent", _transfer_body(policy, queues["human"])), tool("close_call", negate=True)],
         [speak("assistant", "I can help you with this payment.", "negotiate", human),
          speak("user", "No. I still need to speak to a person.")],
         ["transfer"])
    support = dict(base)
    support["support_offered"] = "yes"
    _add(tests, policy, "accepts the support team",
         "The customer accepted the support team. Transfer the call. Do not close it in this turn.",
         [tool("transfer_to_agent", _transfer_body(policy, queues["support"])), tool("close_call", negate=True)],
         [speak("assistant", "I am sorry. I can transfer you to the support team.", "negotiate", support),
          speak("user", "Yes, please transfer me to the support team.")],
         ["transfer"])

    early, _kind = _product(policy, policy["bands"][0]["min_days"], 1)
    early_facts = _facts(policy, early)
    when = "today" if early_facts["min_date"] == TODAY.isoformat() else say_date(early_facts["min_date"])
    _add(tests, policy, "failed promise is not called recorded",
         "The promise service fails. Do not tell the customer the promise was recorded. Say it was not saved.",
         [tool("create_promise")],
         [speak("assistant", f"Can you pay {whole(early_facts['full'])} {when}?", "negotiate", _negotiate_vars(early_facts)),
          speak("user", f"Yes, I confirm I will pay the full amount {when}.")],
         ["promise-fail"], {"create_promise": envelope({"ok": False, "error": "unavailable"})})

    if policy.get("max_products", 1) > 1:
        first_product, _kind = _product(policy, policy["bands"][0]["min_days"], 1, full=420.0, floor=100.0)
        second_days = policy["bands"][-1]["max_days"]
        second_product, _kind = _product(policy, second_days, 2, full=200.0, floor=50.0)
        first, second = _facts(policy, first_product), _facts(policy, second_product)
        _add(tests, policy, "promise uses the current product",
             f"Record {first['product_id']} on {say_date(first['deadline'])}. Do not record {second['product_id']}.",
             [tool("create_promise", _promise_body(policy, first["product_id"], whole(first["full"]), first["deadline"])),
              tool("create_promise", {policy["tools"]["create_promise"]["product_id"]: second["product_id"]}, negate=True)],
             [speak("assistant", f"Can you pay the {first['label']}, {whole(first['full'])}, on {say_date(first['deadline'])}?", "negotiate",
                    _negotiate_vars(first, {"products_json": json.dumps([first, second])})),
              speak("user", "Yes, I confirm.")],
             ["multi"])

    _add(tests, policy, "wrong number closes not reached",
         "The person says this is the wrong number. Apologise briefly, do not open the account, and close the call as not reached, wrong number.",
         [route("say_not_reached_wrong_number"), tool("close_call", _close_body(policy, "nr_wrong_number")), tool("get_account", negate=True)],
         [speak("assistant", opener, "opening", {"opening_asks": 0}), speak("user", "You have the wrong number.")],
         ["outcome"])
    _add(tests, policy, "deceased closes not reached",
         "The person says the customer has died. Say you are sorry for their loss, ask nothing else, and close the call as not reached, deceased.",
         [route("say_not_reached_deceased"), tool("close_call", _close_body(policy, "nr_deceased")), tool("get_account", negate=True)],
         [speak("assistant", opener, "opening", {"opening_asks": 0}), speak("user", "I am afraid she passed away last month.")],
         ["outcome"])
    return tests


def _walk_none(value, path, problems):
    if value is None:
        problems.append(f"{path} is null")
    elif isinstance(value, dict):
        for key, item in value.items():
            _walk_none(item, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_none(item, f"{path}[{index}]", problems)


def _positive_body(test, tool_name):
    for assertion in test["assertions"]:
        if assertion.get("type") == "tool_call" and assertion.get("tool") == tool_name and not assertion.get("negate"):
            return (assertion.get("args") or {}).get("body") or {}
    return None


def audit(policy):
    """The suite's dates, queues, and shapes agree with the config. No network."""
    problems = []
    tests = suite(policy)
    names = [test["name"] for test in tests]
    if len(names) != len(set(names)):
        problems.append("duplicate test name")
    day, clock = inside_callback(policy)
    for test in tests:
        _walk_none(test, test["name"], problems)
        if not test["messages"] or test["messages"][-1]["role"] != "user":
            problems.append(f"{test['name']} does not end on a user turn")
        if len(test["name"]) > 180 or not test["expectation"]:
            problems.append(f"{test['name']} name or expectation is empty or too long")
        for assertion in test["assertions"]:
            if assertion["type"] == "routing" and not assertion.get("target"):
                problems.append(f"{test['name']} routing has no target")
            if assertion["type"] == "tool_call" and not assertion.get("tool"):
                problems.append(f"{test['name']} tool check has no tool")
            if assertion["type"] == "extraction" and "value" not in assertion:
                problems.append(f"{test['name']} extraction has no value")
    inside = next(test for test in tests if "callback-inside" in test["tags"])
    body = _positive_body(inside, "schedule_callback")
    fields = policy["tools"]["schedule_callback"]
    if fields["mode"] == "datetime":
        if body.get(fields["datetime"]) != f"{day}T{clock}":
            problems.append(f"callback body {body} is not {day}T{clock}")
    elif body.get(fields["date"]) != day or body.get(fields["time"]) != clock:
        problems.append(f"callback body {body} is not {day} {clock}")
    ok, reason = D.callback_ok(day, clock, NOW, policy["holidays"], policy["callback"]["start"], policy["callback"]["end"], policy["callback"]["same_day"])
    if not ok:
        problems.append(f"inside callback is illegal: {reason}")
    tomorrow = (TODAY + datetime.timedelta(days=1)).isoformat()
    if day == tomorrow and clock == "11:00":
        if not any("eleven" in message["message"].lower() for message in inside["messages"]):
            problems.append("11:00 tomorrow was not said as eleven")
    seen_bands = set()
    for test in tests:
        if "deadline" not in test["tags"]:
            continue
        for band in policy["bands"]:
            if test["name"].endswith(band["name"]):
                seen_bands.add(band["name"])
                deadline = D.deadline_for(TODAY, band["min_days"], policy["bands"], policy["holidays"]).isoformat()
                recorded = _positive_body(test, "create_promise")
                if recorded.get(policy["tools"]["create_promise"]["date"]) != deadline:
                    problems.append(f"{test['name']} records {recorded.get(policy['tools']['create_promise']['date'])}, deadline is {deadline}")
    if seen_bands != {band["name"] for band in policy["bands"]}:
        problems.append(f"deadline tests cover {seen_bands}")
    holiday = next((test for test in tests if "holiday" in test["tags"]), None)
    if holiday:
        positive = _positive_body(holiday, "create_promise")
        recorded = positive.get(policy["tools"]["create_promise"]["date"])
        if not recorded or not D.is_business_day(datetime.date.fromisoformat(recorded), policy["holidays"]):
            problems.append(f"holiday test records {recorded}")
    restructuring = next(test for test in tests if test["name"].startswith("payment plan"))
    if _positive_body(restructuring, "transfer_to_agent").get(policy["tools"]["transfer_to_agent"]["queue"]) != policy["queue_map"]["restructuring"]:
        problems.append("restructuring queue mismatch")
    if policy.get("max_products", 1) > 1 and not any("multi" in test["tags"] for test in tests):
        problems.append("multi-product config has no product-id test")
    if policy.get("max_products", 1) <= 1 and any("multi" in test["tags"] for test in tests):
        problems.append("single-product config has a multi-product test")
    wrong_number = next(test for test in tests if test["name"].startswith("wrong number"))
    close_body = _positive_body(wrong_number, "close_call")
    expected = _close_body(policy, "nr_wrong_number")
    if any(close_body.get(key) != value for key, value in expected.items()):
        problems.append(f"wrong-number close {close_body} != {expected}")
    return problems


def _norm_assertion(assertion):
    row = {"type": assertion["type"]}
    if assertion["type"] == "tool_call":
        row["tool"] = assertion["tool"]
        if assertion.get("negate"):
            row["negate"] = True
        if assertion.get("args"):
            row["args"] = assertion["args"]
            row["argsMatch"] = assertion.get("argsMatch") or "subset"
    elif assertion["type"] == "routing":
        row["target"] = assertion["target"]
        row["signal"] = assertion.get("signal") or "node_entered"
        if assertion.get("negate"):
            row["negate"] = True
    else:
        row["name"] = assertion["name"]
        row["match"] = assertion.get("match") or "equals"
        if "value" in assertion:
            row["value"] = assertion["value"]
    return row


def _norm_message(message):
    return {key: message[key] for key in ("role", "message", "messageId", "nodeId", "workflowSnapshot") if message.get(key) is not None}


def _fingerprint(test):
    return json.dumps({
        "expectation": test.get("expectation") or "",
        "assertions": [_norm_assertion(item) for item in test.get("assertions") or []],
        "messages": [_norm_message(item) for item in test["messages"]],
        "customer_variables": test.get("customer_variables") or {},
        "tags": test.get("tags") or [],
    }, sort_keys=True)


def _stored_fingerprint(row):
    data = row.get("input") or {}
    return _fingerprint({
        "expectation": row.get("expectation") or "",
        "assertions": row.get("assertions") or [],
        "messages": data.get("messages") or [],
        "customer_variables": data.get("customerVariables") or {},
        "tags": row.get("tags") or [],
    })


def sync_tests(workflow_id, tests):
    """Create or update generated tests. Archive generated tests this suite no longer has. Leave seeded tests."""
    import freya
    existing = freya.list_unit_tests("workflow", workflow_id)
    by_name = {row["name"]: row for row in existing}
    wanted = {test["name"] for test in tests}
    ids = {}
    for test in tests:
        current = by_name.get(test["name"])
        if current is None:
            created = freya.create_unit_test("workflow", workflow_id, test)
            ids[test["name"]] = created["id"]
            print(f"created {test['name']}")
        elif current.get("isArchived") or _stored_fingerprint(current) != _fingerprint(test):
            freya.update_unit_test(current["id"], test)
            ids[test["name"]] = current["id"]
            print(f"updated {test['name']}")
        else:
            ids[test["name"]] = current["id"]
    for row in existing:
        if row.get("isArchived"):
            continue
        if "generated" in (row.get("tags") or []) and row["name"] not in wanted:
            freya.archive_unit_test(row["id"])
            print(f"archived {row['name']}")
    return ids


def _wait(run_ids, timeout=240):
    import freya
    pending, done = set(run_ids), {}
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for run_id in list(pending):
            row = freya.get_unit_test_run(run_id)
            if row.get("outcome") in ("pass", "fail", "error"):
                done[run_id] = row
                pending.discard(run_id)
        if pending:
            time.sleep(3)
    for run_id in pending:
        done[run_id] = {"id": run_id, "outcome": "error", "verdictReason": "timed out waiting for the run"}
    return done


def run_tests(workflow_id, tests, ids):
    """Run the suite in mock groups. run-all applies one mock map to every test in the call."""
    import freya
    groups = {}
    for test in tests:
        groups.setdefault(json.dumps(test.get("mock") or {}, sort_keys=True), []).append(test)
    results = []
    for raw, group in groups.items():
        mock = json.loads(raw)
        enqueued = freya.run_all_unit_tests(
            "workflow", workflow_id,
            unit_test_ids=[ids[test["name"]] for test in group],
            mock_tool_results=mock or None,
        )
        by_test = {item["unitTestId"]: item.get("runIds") or [] for item in enqueued.get("enqueued") or []}
        run_ids = [run_id for item in by_test.values() for run_id in item]
        done = _wait(run_ids) if run_ids else {}
        skipped = set(enqueued.get("skipped") or [])
        for test in group:
            test_id = ids[test["name"]]
            runs = [done[run_id] for run_id in by_test.get(test_id, []) if run_id in done]
            if not runs:
                results.append({"name": test["name"], "outcome": "error", "reason": "not enqueued", "skipped": test_id in skipped or test["name"] in skipped})
                continue
            run = runs[-1]
            failed = [item for item in run.get("assertionResults") or [] if item.get("passed") is False]
            results.append({
                "name": test["name"],
                "outcome": run.get("outcome"),
                "reason": run.get("verdictReason") or "",
                "agent": (run.get("agentResponse") or "")[:400],
                "assertions": failed,
                "run_id": run.get("id"),
            })
    return results


def sync_and_run(workflow_id, policy, trace=None):
    from tools.envfile import load_env
    load_env()
    import freya
    tests = suite(policy)
    local = audit(policy)
    if local:
        raise RuntimeError("unit suite failed its offline check: " + "; ".join(local[:3]))
    ids = sync_tests(workflow_id, tests)
    if trace:
        trace.model(freya.AGENT_LLM["model"], f"run {len(tests)} unit tests on the published workflow")
    results = run_tests(workflow_id, tests, ids)
    workflow = freya.api("GET", f"/api/v2/workflow/{workflow_id}", workspaceId=freya.workspace_id())["workflow"]
    passed = sum(row["outcome"] == "pass" for row in results)
    report = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workflow_id": workflow_id,
        "agent_id": workflow.get("agentId"),
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=1))
    for row in results:
        mark = "PASS" if row["outcome"] == "pass" else "FAIL"
        print(mark, row["name"])
        if row["outcome"] != "pass":
            if row.get("reason"):
                print("   ", row["reason"][:300])
            for item in row.get("assertions") or []:
                print("   ", item.get("type"), item.get("detail") or item.get("observed"))
            if row.get("agent"):
                print("    agent:", row["agent"][:240])
    line = f"{passed}/{len(results)} unit tests passed"
    print(line)
    if trace:
        trace.line(f"- {line}. Report: {REPORT}")
        for row in results:
            if row["outcome"] != "pass":
                trace.line(f"- fail {row['name']}: {(row.get('reason') or row['outcome'])[:200]}")
    return report


def _slim_message(message):
    kept = {}
    for key in ("role", "message", "messageId", "nodeId", "secondsFromStart", "workflowSnapshot"):
        if message.get(key) is not None:
            kept[key] = message[key]
    return kept


def _messages_until(messages, message_id):
    kept = []
    for message in messages:
        if message.get("role") not in ("user", "assistant", "system"):
            continue
        kept.append(_slim_message(message))
        if message.get("messageId") == message_id:
            break
    while kept and kept[-1].get("role") != "user":
        kept.pop()
    return kept


def _pick_message(messages, hints):
    users = [message for message in messages if message.get("role") == "user" and message.get("messageId")]
    if hints:
        for message in reversed(users):
            text = (message.get("message") or "").lower()
            if any(hint in text for hint in hints):
                return message
    return users[-1] if users else None


def _call_customer(messages):
    for message in messages:
        table = ((message.get("workflowSnapshot") or {}).get("variableTable") or {})
        if "customer_id" not in table and "now" not in table:
            continue
        values = {}
        for key in ("customer_id", "first_name", "last_name", "title", "now", "company_name"):
            raw = table.get(key)
            if isinstance(raw, dict) and "value" in raw:
                values[key] = raw["value"]
            elif isinstance(raw, str):
                values[key] = raw
        if values:
            return values
    return {}


def seed_from_call(workflow_id, call_id, message_id, name, expectation, assertions, tags=None):
    """One API create. The script is the call up to and including that message."""
    from tools.envfile import load_env
    load_env()
    import freya
    call = freya.get_call(call_id)
    messages = call.get("messages") or []
    if not message_id:
        picked = _pick_message(messages, ())
        if not picked:
            raise RuntimeError(f"{call_id} has no user message")
        message_id = picked["messageId"]
    script = _messages_until(messages, message_id)
    if not script:
        raise RuntimeError(f"message {message_id} is not a user turn in {call_id}")
    test = {
        "name": name,
        "expectation": expectation,
        "assertions": assertions,
        "messages": script,
        "customer_variables": _call_customer(messages),
        "tags": ["seeded", *(tags or [])],
        "seed_call_id": call_id,
        "seed_message_id": message_id,
    }
    created = freya.create_unit_test("workflow", workflow_id, test)
    print(json.dumps({"id": created.get("id"), "name": name, "message_id": message_id, "turns": len(script)}))
    return created


def _case_assertions(case, policy):
    """Checks for a practice case, using that case's expected records."""
    expected = case.get("expected") or {}
    assertions = []
    family = (case.get("family") or "").lower()
    if expected.get("callbacks"):
        callback = expected["callbacks"][0]
        assertions.append(tool("schedule_callback", _callback_body(policy, callback["date"], callback["time"])))
        assertions.append(tool("verify_identity", negate=True))
        assertions.append(tool("get_account", negate=True))
    if "identity" in family:
        assertions.append(route("say_not_verified_failed"))
        assertions.append(tool("get_account", negate=True))
    if expected.get("promises"):
        promise = expected["promises"][0]
        assertions.append(tool("create_promise", _promise_body(policy, promise["product_id"], promise["amount"], promise["date"])))
    if expected.get("transfers"):
        assertions.append(tool("transfer_to_agent", _transfer_body(policy, expected["transfers"][0]["queue"])))
        assertions.append(tool("close_call", negate=True))
    outcome = expected.get("outcome")
    reason = expected.get("reason")
    # A transfer or a callback is the turn under test. close_call comes after it, so it is not required here.
    if outcome in ("not_reached", "not_verified") and not expected.get("callbacks") and not expected.get("transfers"):
        for key, pair in policy["outcome_map"].items():
            if pair[0] == outcome and (pair[1] or None) == (reason or None):
                assertions.append(tool("close_call", _close_body(policy, key)))
                break
    return assertions


HINTS = {
    "PUB-02": ("eleven", "tomorrow"),
    "PUB-03": ("1990", "july"),
    "PUB-07": ("26", "monday"),
    "PUB-09": ("instalment", "plan", "lower"),
}


def _rows(payload):
    found = []
    def walk(value):
        if isinstance(value, dict):
            if value.get("id") and value.get("call_id"):
                found.append(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(payload)
    return found


def from_run(path, workflow_id, case_ids, policy):
    payload = json.loads(pathlib.Path(path).read_text())
    cases = {case["id"]: case for case in json.loads((ROOT / "public-test-set.json").read_text())}
    chosen = {}
    for row in _rows(payload):
        if case_ids and row["id"] not in case_ids:
            continue
        previous = chosen.get(row["id"])
        if previous is None or (previous.get("pass") and not row.get("pass")):
            chosen[row["id"]] = row
    if not chosen:
        raise RuntimeError(f"no matching calls in {path}")
    created = []
    for case_id, row in chosen.items():
        case = cases.get(case_id, {"id": case_id, "checks": [], "family": ""})
        from tools.envfile import load_env
        load_env()
        import freya
        call = freya.get_call(row["call_id"])
        message = _pick_message(call.get("messages") or [], HINTS.get(case_id, ()))
        if not message:
            print(f"skip {case_id}: no user message")
            continue
        expectation = " ".join(case.get("checks") or []) or f"{case_id} stays correct at this turn"
        result = seed_from_call(
            workflow_id, row["call_id"], message["messageId"],
            f"{case_id} {case.get('title') or case.get('family') or 'regression'}",
            expectation, _case_assertions(case, policy), tags=[case_id],
        )
        created.append(result.get("id"))
    return created


def _policy_from_args(argv):
    from seed.policy_ab import POLICY_A, POLICY_B
    if "--config" in argv:
        return json.loads(pathlib.Path(argv[argv.index("--config") + 1]).read_text())
    if "--brief" in argv and argv[argv.index("--brief") + 1].upper() == "B":
        return POLICY_B
    return POLICY_A


def _workflow_from_args(argv):
    if "--workflow-id" in argv:
        return argv[argv.index("--workflow-id") + 1]
    if "--slot" in argv:
        slot = argv[argv.index("--slot") + 1]
        state = json.loads((ROOT / ".freya-agents.json").read_text())
        return state[slot]["workflow_id"]
    raise SystemExit("pass --workflow-id or --slot")


def _print_audit():
    from seed.policy_ab import POLICY_A
    from seed.workflow_check import iter_configs
    problems = []
    for name, policy, _tools in iter_configs():
        for problem in audit(policy):
            problems.append(f"{name}: {problem}")
    if problems:
        print(f"FAIL {len(problems)} suite problem(s)")
        for problem in problems[:20]:
            print("  ", problem)
        sys.exit(1)
    tests = suite(POLICY_A)
    for test in tests:
        checks = []
        for assertion in test["assertions"]:
            if assertion["type"] == "tool_call":
                checks.append(("not " if assertion.get("negate") else "") + assertion["tool"])
            elif assertion["type"] == "routing":
                checks.append(("not " if assertion.get("negate") else "") + assertion["target"])
        print(f"{test['name']}: {', '.join(checks)}")
    print(f"\n{len(tests)} Brief A tests. All 9 configs match their own dates, queues, and close values.")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] == "--print":
        _print_audit()
        return
    if argv[0] == "seed-call":
        flags = argv[1:]
        def flag(name):
            return flags[flags.index(name) + 1] if name in flags else ""
        assertions = []
        if "--tool" in flags:
            body = json.loads(flag("--args")) if "--args" in flags else None
            assertions.append(tool(flag("--tool"), body, negate="--negate" in flags))
        if "--route" in flags:
            assertions.append(route(flag("--route")))
        seed_from_call(_workflow_from_args(flags), flag("--call-id"), flag("--message-id"), flag("--name"), flag("--expect"), assertions)
        return
    if argv[0] == "from-run":
        flags = argv[1:]
        cases = set(flags[flags.index("--cases") + 1].split(",")) if "--cases" in flags else set()
        positional = [arg for index, arg in enumerate(flags) if not arg.startswith("--") and (index == 0 or not flags[index - 1].startswith("--"))]
        if not positional:
            raise SystemExit("from-run needs the results json path")
        from_run(positional[0], _workflow_from_args(flags), cases, _policy_from_args(flags))
        return
    report = sync_and_run(_workflow_from_args(argv), _policy_from_args(argv))
    sys.exit(0 if report["passed"] == report["total"] and report["total"] else 1)


if __name__ == "__main__":
    main()
