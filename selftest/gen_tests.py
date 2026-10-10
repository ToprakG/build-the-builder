"""Unit tests from a config. Expected values come from the date library. Each test names the text slots it exercises.

    python3 -m selftest.gen_tests
"""
import datetime
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seed import datefns as D
from seed import unit_tests as U

# First match wins. A short run keeps the front of this list.
_ORDER = (
    "wrong answer reaches",
    "swap hint",
    "callback inside",
    "callback before",
    "deadline cap",
    "holiday",
    "weekend",
    "floor offer",
    "below the floor",
    "payment plan",
    "already paid",
    "dispute",
    "hardship",
    "scam",
    "second promise failure",
    "failed promise",
    "callback after",
    "promise uses the current",
    "wrong number",
    "deceased",
    "declined twice",
    "insists on a person",
    "accepts the support",
    "wrong answer before",
)

_BY_TAG = {
    "callback-inside": [("third_party", "task_message", None), ("third_party", "tool", "schedule_callback")],
    "callback-edge": [("third_party", "task_message", None)],
    "identity-limit": [("identity", "task_message", None), ("identity", "transition", "Every allowed wrong answer used")],
    "swap": [("identity", "task_message", None), ("identity", "tool", "verify_identity")],
    "deadline": [("negotiate", "task_message", None)],
    "holiday": [("negotiate", "task_message", None)],
    "weekend": [("negotiate", "task_message", None)],
    "floor": [("negotiate", "task_message", None)],
    "below-floor": [("negotiate", "task_message", None)],
    "transfer": [("negotiate", "task_message", None), ("negotiate", "extract", "turn_signal")],
    "promise-fail": [("negotiate", "task_message", None), ("negotiate", "tool", "create_promise")],
    "promise-retry": [("negotiate", "task_message", None), ("negotiate", "tool", "add_note")],
    "paid": [("negotiate", "task_message", None), ("negotiate", "tool", "add_note")],
    "dispute": [("negotiate", "task_message", None), ("negotiate", "tool", "add_note")],
    "hardship": [("negotiate", "task_message", None)],
    "scam": [("negotiate", "task_message", None)],
    "multi": [("negotiate", "task_message", None)],
    "outcome": [("opening", "task_message", None), ("opening", "extract", "who_answered")],
    "decline": [("identity", "task_message", None), ("identity", "extract", "identity_declined")],
}


def _slot(node, spec, key):
    row = {"node": node, "spec": spec}
    if key is not None:
        row["key"] = key
    row["id"] = node + "|" + spec + ("" if key is None else "|" + key)
    return row


def _attach(test):
    targets = []
    seen = set()
    for tag in test.get("tags") or []:
        for node, spec, key in _BY_TAG.get(tag, ()):
            slot = _slot(node, spec, key)
            if slot["id"] not in seen:
                seen.add(slot["id"])
                targets.append(slot)
    test["targets"] = targets
    return test


def _rank(test):
    for index, prefix in enumerate(_ORDER):
        if test["name"].startswith(prefix):
            return index
    return len(_ORDER)


def _widest(policy):
    widest = U.TODAY
    for band in policy["bands"]:
        deadline = D.deadline_for(U.TODAY, band["min_days"], policy["bands"], policy["holidays"])
        if deadline > widest:
            widest = deadline
    return widest


def _note_body(policy, product_id, reason_key):
    fields = policy["tools"]["add_note"]
    return {fields["product_id"]: product_id, fields["reason"]: policy["note_map"][reason_key]}


def _extra(policy):
    tests = []
    customer = {"title": "Ms", "first_name": "Maria", "last_name": "Costa", "company_name": "Northwind"}
    opener = U._opener(policy, customer)
    questions = policy["identity"]["questions"]
    limit = int(policy["identity"]["max_wrong"])
    wrong = U.WRONG.get(questions[0], "0000")
    asked = questions[0].replace("_", " ")

    if policy["identity"].get("swap_hint"):
        U._add(tests, policy, "swap hint does not spend an attempt",
               "The checker says the day and the month may be swapped. Ask the customer to say the month as a word. Do not end the check.",
               [U.route("say_not_verified_failed", negate=True), U.tool("verify_identity"), U.tool("get_account", negate=True)],
               [U.speak("assistant", f"What is your {asked}?", "identity",
                        {"identity_wrong_total": limit - 1, "identity_failed": "no", "identity_all_passed": "no"}),
                U.speak("user", wrong)],
               ["swap"], {"verify_identity": U.envelope({"ok": True, "verified": False, "hint": "day_month_swapped"})})

    widest = _widest(policy)
    weekend = None
    day = U.TODAY + datetime.timedelta(days=1)
    while day <= widest:
        if day.weekday() >= 5 and day.isoformat() not in set(policy["holidays"]):
            shifted = D.on_or_after_business_day(day, policy["holidays"])
            if shifted != day and shifted <= widest:
                weekend = (day, shifted)
                break
        day += datetime.timedelta(days=1)
    if weekend:
        named, shifted = weekend
        product, _kind = U._product(policy, policy["bands"][0]["min_days"], 1)
        facts = U._facts(policy, product)
        amount = U.whole(facts["full"])
        date_field = policy["tools"]["create_promise"]["date"]
        U._add(tests, policy, "weekend shifts to the next business day",
               f"{U.say_date(named.isoformat())} is not a business day. Offer {U.say_date(shifted.isoformat())} and record that date.",
               [U.tool("create_promise", U._promise_body(policy, facts["product_id"], amount, shifted.isoformat())),
                U.tool("create_promise", {date_field: named.isoformat()}, negate=True)],
               [U.speak("assistant", f"Can you pay {amount} today?", "negotiate", U._negotiate_vars(facts)),
                U.speak("user", f"I can pay on {U.say_date(named.isoformat())}."),
                U.speak("assistant", f"That day is not a business day. The next business day is {U.say_date(shifted.isoformat())}. Can you pay {amount} then?", "negotiate", U._negotiate_vars(facts)),
                U.speak("user", "Yes, I confirm.")],
               ["weekend"])

    late = next((band for band in policy["bands"] if int(band["attempts"]) >= 2), None)
    if late:
        product, _kind = U._product(policy, late["min_days"], 1, full=2100.0, floor=700.0)
        facts = U._facts(policy, product)
        floor = U.whole(facts["floor"])
        full = U.whole(facts["full"])
        U._add(tests, policy, "floor offer is recorded",
               f"The customer confirmed the floor. Record {floor} on {U.say_date(facts['deadline'])}.",
               [U.tool("create_promise", U._promise_body(policy, facts["product_id"], floor, facts["deadline"]))],
               [U.speak("assistant", f"The least I can record is {floor} on {U.say_date(facts['deadline'])}.", "negotiate", U._negotiate_vars(facts)),
                U.speak("user", f"Yes, I will pay {floor} on that date.")],
               ["floor"])
        low = max(1, floor - 50)
        amount_field = policy["tools"]["create_promise"]["amount"]
        U._add(tests, policy, "below the floor is not recorded",
               f"{low} is below the floor {floor}. Do not record {low}. Offer {floor} on {U.say_date(facts['deadline'])}.",
               [U.tool("create_promise", {amount_field: low if policy["tools"]["create_promise"].get("amount_unit") != "cents" else low * 100}, negate=True)],
               [U.speak("assistant", f"The full amount is {full}. The least I can record is {floor}.", "negotiate", U._negotiate_vars(facts)),
                U.speak("user", f"I can only pay {low}.")],
               ["below-floor"])

    product, _kind = U._product(policy, policy["bands"][0]["min_days"], 1)
    facts = U._facts(policy, product)
    base = U._negotiate_vars(facts)
    U._add(tests, policy, "already paid gets a note",
           "The customer says the bill is already paid. Call add_note with the paid reason. Do not record a promise.",
           [U.tool("add_note", _note_body(policy, facts["product_id"], "paid")), U.tool("create_promise", negate=True)],
           [U.speak("assistant", f"Can you pay {U.whole(facts['full'])} today?", "negotiate", base),
            U.speak("user", "I already paid this yesterday.")],
           ["paid"])
    U._add(tests, policy, "dispute gets a note",
           "The customer says the debt is not theirs. Call add_note with the dispute reason. Do not argue and do not record a promise.",
           [U.tool("add_note", _note_body(policy, facts["product_id"], "dispute")), U.tool("create_promise", negate=True)],
           [U.speak("assistant", f"Can you pay {U.whole(facts['full'])} today?", "negotiate", base),
            U.speak("user", "That debt is not mine.")],
           ["dispute"])
    U._add(tests, policy, "hardship offers support once",
           "The customer mentions job loss. Say one short sentence of sympathy and offer the support team once. Do not transfer in this turn and do not close the call.",
           [U.tool("transfer_to_agent", negate=True), U.tool("close_call", negate=True)],
           [U.speak("assistant", f"Can you pay {U.whole(facts['full'])} today?", "negotiate", base),
            U.speak("user", "I lost my job last week and I cannot deal with this.")],
           ["hardship"])
    scam = policy["sentences"]["scam"]
    U._add(tests, policy, "scam question keeps the call here",
           f"The customer asks if this is a scam. Say that they can {scam}. Do not transfer and do not close the call.",
           [U.tool("transfer_to_agent", negate=True), U.tool("close_call", negate=True)],
           [U.speak("assistant", f"Can you pay {U.whole(facts['full'])} today?", "negotiate", base),
            U.speak("user", "Is this a scam?")],
           ["scam"])

    failed = dict(base)
    failed["current_promise_fails"] = 1
    when = "today" if facts["min_date"] == U.TODAY.isoformat() else U.say_date(facts["min_date"])
    U._add(tests, policy, "second promise failure notes a service error",
           "This is the second time the promise service failed. Do not say the promise is recorded. Call add_note with the service-error reason.",
           [U.tool("create_promise"), U.tool("add_note", _note_body(policy, facts["product_id"], "service_error"))],
           [U.speak("assistant", f"Can you pay {U.whole(facts['full'])} {when}?", "negotiate", failed),
            U.speak("user", f"Yes, I confirm I will pay the full amount {when}.")],
           ["promise-retry"], {"create_promise": U.envelope({"ok": False, "error": "unavailable"})})

    U._add(tests, policy, "declined twice ends the check",
           "The customer refused a security question for the second time. End the check. Do not open the account.",
           [U.route("say_not_verified_declined"), U.tool("get_account", negate=True)],
           [U.speak("assistant", f"What is your {asked}?", "identity",
                    {"identity_decline_count": 1, "identity_failed": "no", "identity_all_passed": "no"}),
            U.speak("user", "I will not answer that.")],
           ["decline"])
    return tests


def generate(policy, limit=None):
    """Config tests, highest priority first. `limit` keeps the front of the list."""
    tests = [_attach(test) for test in U.suite(policy) + _extra(policy)]
    tests.sort(key=_rank)
    if limit is not None:
        tests = tests[:limit]
    return tests


def main():
    from seed.policy_ab import POLICY_A
    from seed.workflow_check import iter_configs
    problems = []
    for name, policy, _tools in iter_configs():
        tests = generate(policy)
        names = [test["name"] for test in tests]
        if len(names) != len(set(names)):
            problems.append(f"{name}: duplicate name")
        for test in tests:
            if not test.get("targets"):
                problems.append(f"{name}: {test['name']} has no target slot")
            U._walk_none(test, test["name"], problems)
    if problems:
        print(f"FAIL {len(problems)}")
        for problem in problems[:20]:
            print(" ", problem)
        sys.exit(1)
    tests = generate(POLICY_A)
    print(f"{len(tests)} Brief A tests")
    for test in tests:
        print(f"  {test['name']} -> {test['targets'][0]['id']}")
    short = generate(POLICY_A, limit=15)
    print(f"15-test cut: {', '.join(test['name'] for test in short)}")


if __name__ == "__main__":
    main()
