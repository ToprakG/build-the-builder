"""Writes the invented-values fixtures for the T-CC tests: a brief, its tool API and its correct policy.json.

Every value is invented (bank, dates in 2031, field names, enums, sentences). None comes from a kit or testbed brief.
Run: python3 tests/template_cc/fixtures/make_fixtures.py
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "https://quillmoor.invalid/api"


def obj(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def op(summary, req_props, req_required, resp_props):
    return {"post": {
        "operationId": None, "summary": summary,
        "requestBody": {"required": True, "content": {"application/json": {"schema": obj(req_props, req_required)}}},
        "responses": {
            "200": {"description": "`ok` is false when the action was not carried out.",
                    "content": {"application/json": {"schema": obj({"ok": {"type": "boolean"}, "error": {"type": "string"}, **resp_props})}}},
            "500": {"description": "The service failed. Nothing was recorded. The call may be repeated."}}}}


S = {"type": "string"}
CID = {"customer_id": S}
API = {
    "openapi": "3.0.3",
    "info": {"title": "Quillmoor tools", "version": "1"},
    "servers": [{"url": BASE}],
    "paths": {
        "/verify_identity": op("Check one answer.", {**CID, "question": {"type": "string", "enum": ["birth_date", "pin_last3"]},
                                                     "reply": {"type": "string", "description": "birth_date as YYYY-MM-DD; pin_last3 three digits"}},
                               ["customer_id", "question", "reply"],
                               {"verified": {"type": "boolean"}, "tries_left": {"type": "integer"}, "all_verified": {"type": "boolean"},
                                "hint": {"type": "string", "enum": ["order_mixed"]}}),
        "/get_account": op("The late items.", CID, ["customer_id"], {"items": {"type": "array", "items": obj({
            "item_ref": S, "kind": {"type": "string", "enum": ["kind_a", "kind_b"]}, "ending": S,
            "overdue": {"type": "number"}, "days_over": {"type": "integer"}, "floor_amount": {"type": "number"}})}}),
        "/create_promise": op("Record a promise.", {**CID, "item_ref": S, "amount_minor": {"type": "integer", "description": "amount in cents"},
                                                    "pay_on": {"type": "string", "format": "date"}},
                              ["customer_id", "item_ref", "amount_minor", "pay_on"], {"promise_id": S}),
        "/add_note": op("Note an item.", {**CID, "item_ref": S, "code": {"type": "string", "enum": ["nx_refused", "nx_hard", "nx_dispute", "nx_paid", "nx_error"]}},
                        ["customer_id", "item_ref", "code"], {}),
        "/schedule_callback": op("Book a callback.", {**CID, "slot": {"type": "string", "description": "YYYY-MM-DDTHH:MM"}},
                                 ["customer_id", "slot"], {}),
        "/transfer_to_agent": op("Transfer.", {**CID, "desk": {"type": "string", "enum": ["desk_rework", "desk_person", "desk_care"]}},
                                 ["customer_id", "desk"], {}),
        "/close_call": op("Close.", {**CID, "result": {"type": "string", "enum": ["kept", "settled", "handed_over", "no_deal", "unverified", "unreached"]},
                                     "detail": {"type": "string", "enum": ["rework", "person", "care", "said_no", "hard_times", "contested", "fault",
                                                                           "wrong_answers", "would_not_answer", "later", "no_later", "not_theirs",
                                                                           "passed_away", "unsure"]}},
                         ["customer_id", "result"], {}),
    },
}
for path, item in API["paths"].items():
    item["post"]["operationId"] = path.strip("/")

BRIEF = """# Quillmoor Credit Union: overdue items (invented fixture)

Call variables: `customer_id`, `given_name`, `family_name`, `honorific`, `now`.

1. Say: "Good day, this is the automated helper of Quillmoor Credit Union, and this line is recorded."
2. Ask: "Is this {honorific} {family_name}?". Do not mention money, overdue or items before the checks pass.
3. Checks: birth date first, then the last three digits of the PIN. Two wrong replies end the checks.
4. Callbacks: only the next business day, 09:30 to 16:30.
5. Holidays: 2031-03-14 and 2031-04-02.
6. Bands: 1 to 20 days late: deadline is the 5th business day, 1 attempt. 21 to 90 days: 3rd business day, 2 attempts.
7. Ask first: "Could you settle this today?". Confirm with: "Shall I record that?".
8. After a recorded promise say: "A letter will confirm your promise."
9. Up to two items per member; read every promise back together.
10. Notes: refused nx_refused, hard times nx_hard, contested nx_dispute, paid nx_paid, fault nx_error.
11. Robot question: say you are "the automated helper of Quillmoor Credit Union". Scam question: "check the number on your member card". Failed checks: "visit a Quillmoor office".
"""

O = lambda a, b=None: [a, b]
POLICY = {
    "bank": "Quillmoor Credit Union", "language": "en", "address": "{honorific} {family_name}",
    "call_variables": ["customer_id", "given_name", "family_name", "honorific", "now"],
    "identity": {"questions": ["birth_date", "pin_last3"], "max_wrong": 2, "swap_hint": True,
                 "prompts": {"birth_date": "What is your date of birth?", "pin_last3": "What are the last three digits of your PIN?"},
                 "kinds": {"birth_date": "date", "pin_last3": "digits"}},
    "callback": {"start": "09:30", "end": "16:30", "same_day": False},
    "outcome_map": {"promise": O("kept"), "paid": O("settled"), "tr_restructuring": O("handed_over", "rework"),
                    "tr_human": O("handed_over", "person"), "tr_support": O("handed_over", "care"),
                    "np_refused": O("no_deal", "said_no"), "np_hardship": O("no_deal", "hard_times"),
                    "np_dispute": O("no_deal", "contested"), "np_service_error": O("no_deal", "fault"),
                    "nv_failed": O("unverified", "wrong_answers"), "nv_declined": O("unverified", "would_not_answer"),
                    "nr_callback": O("unreached", "later"), "nr_no_callback": O("unreached", "no_later"),
                    "nr_wrong_number": O("unreached", "not_theirs"), "nr_deceased": O("unreached", "passed_away"),
                    "nr_unclear": O("unreached", "unsure")},
    "queue_map": {"restructuring": "desk_rework", "human": "desk_person", "support": "desk_care"},
    "note_map": {"refused": "nx_refused", "hardship": "nx_hard", "dispute": "nx_dispute", "paid": "nx_paid", "service_error": "nx_error"},
    "close_rule": {"kind": "priority", "order": ["dispute", "hardship", "refused", "service_error"]},
    "notes_on_restructuring": "refused",
    "product_types": {"a": {"enum": "kind_a", "label": "item ending {last4}", "floor_field": "floor_amount", "last4_field": "ending"},
                      "b": {"enum": "kind_b", "label": "second item", "floor_field": "floor_amount", "last4_field": None}},
    "account_fields": {"product_id": "item_ref", "type": "kind", "past_due": "overdue", "days_late": "days_over"},
    "product_id_format": "q{n}",
    "tools": {"verify_identity": {"field": "question", "answer": "reply"},
              "create_promise": {"product_id": "item_ref", "amount": "amount_minor", "date": "pay_on", "amount_unit": "cents"},
              "schedule_callback": {"mode": "datetime", "datetime": "slot"},
              "close_call": {"mode": "outcome_reason", "outcome": "result", "reason": "detail"},
              "transfer_to_agent": {"queue": "desk"}, "add_note": {"product_id": "item_ref", "reason": "code"}},
    "sentences": {"opening": "the automated helper of Quillmoor Credit Union; this line is recorded",
                  "promise_confirmation": "A letter will confirm your promise.",
                  "ai": "the automated helper of Quillmoor Credit Union", "scam": "check the number on your member card",
                  "failed_id": "visit a Quillmoor office"},
    "lines": {"opening": {"text": "Good day, this is the automated helper of Quillmoor Credit Union, and this line is recorded.", "verbatim": True},
              "right_party_question": {"text": "Is this {honorific} {family_name}?", "verbatim": True},
              "first_ask": {"text": "Could you settle this today?", "verbatim": True},
              "confirm_question": {"text": "Shall I record that?", "verbatim": True},
              "promise_confirmation": {"text": "A letter will confirm your promise.", "verbatim": True}},
    "forbidden_before_verify": ["money", "overdue", "items"],
    "multi_product": True, "max_products": 2,
    "attempt1": "last business day of this week, or next week's when today is that day; never after the deadline",
    "attempt_plan": [{"date": "end_of_week_roll", "amount": "full_or_floor", "cap_at_deadline": True},
                     {"date": "deadline", "amount": "floor", "cap_at_deadline": True}],
    "holidays": ["2031-03-14", "2031-04-02"],
    "bands": [{"name": "first", "min_days": 1, "max_days": 20, "deadline_business_days": 5, "attempts": 1},
              {"name": "second", "min_days": 21, "max_days": 90, "deadline_business_days": 3, "attempts": 2}],
    "unused_tools": [],
    "extra_rules": [{"statement": "Read every promise back together.", "brief_quote": "read every promise back together", "stage": "negotiate"}],
    "_source": {"/callback/start": "09:30 to 16:30", "/identity/max_wrong": "Two wrong replies end the checks"},
}

if __name__ == "__main__":
    (HERE / "invented.openapi.json").write_text(json.dumps(API, indent=1) + "\n")
    (HERE / "invented.brief.md").write_text(BRIEF)
    (HERE / "invented.policy.json").write_text(json.dumps(POLICY, indent=1) + "\n")
    print("wrote", HERE)
