"""Drive a few /chat/message turns against a published workflow, using a REAL practice session
(same /v1/practice/sessions call run_cases.py makes) so verify_identity and get_account see a
customer_id that actually exists in the mock bank, instead of a made-up one that always fails.

    python3 seed/smoke_chat.py <workflow_id> <case_id> <scenario>

scenarios: happy, wrong_three, confirm_only (just the opener + identity confirmation step)
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from tools.score import http


def turn(workflow_id, call_id, message, extra_vars=None):
    body = {"workflowId": workflow_id, "message": message}
    if call_id:
        body["callId"] = call_id
    else:
        body["customerVariables"] = extra_vars
    r = freya.api("POST", "/api/v2/chat/message", body)
    return r.get("callId") or call_id, r.get("response")


SCENARIOS = {
    "happy": lambda c, ident: [
        f"Yes, this is {c['variables']['first_name']} {c['variables']['last_name']}.",
        ident["date_of_birth"],
        ident["phone_last4"] + ".",
        "Yes, I can pay today.",
        "Yes, I confirm.",
    ],
    "wrong_three": lambda c, ident: [
        f"Yes, speaking.",
        "12 July 1991.",
        "21 August 1992.",
        "21 July 1990.",
    ],
}


def main():
    workflow_id = sys.argv[1]
    case_id = sys.argv[2] if len(sys.argv) > 2 else "PUB-01"
    scenario = sys.argv[3] if len(sys.argv) > 3 else "happy"

    cases = {c["id"]: c for c in json.load(open(ROOT / "public-test-set.json"))}
    case = cases[case_id]
    session = http("/v1/practice/sessions", {"case": case_id})["variables"]
    print(f"-- case {case_id}, real customer_id {session['customer_id']}")

    call_id = None
    # Burn one neutral first message: /chat/message's first turn never reaches extraction/routing
    # (seen in a trial: route_evaluated logs "Suppressed: Missing prior assistant message" for
    # turn 1), so its `response` is on_enter's own line, not a reply to anything we sent.
    call_id, response = turn(workflow_id, None, "Hello?", session)
    print("AGENT (on_enter):", response)
    print()
    for msg in SCENARIOS[scenario](case, case["identity"]):
        call_id, response = turn(workflow_id, call_id, msg)
        print(f"USER: {msg}")
        print(f"AGENT: {response}")
        print()
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except Exception as e:
            print("end call warning:", e)
    print(f"-- ledger for {session['customer_id']}:")
    print(json.dumps(http("/v1/practice/ledger/" + session["customer_id"]), indent=1))


if __name__ == "__main__":
    main()
