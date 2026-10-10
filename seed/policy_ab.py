"""Brief A and Brief B as plain data, in the canonical config schema seed/graph.py reads for any
brief: holidays, bands, identity, callback window, outcome/queue/note maps, close rule, product
types, account field names, tool field-name roles, sentences. graph.py never sees a Brief A/B
value directly -- it reads this same shape for the testbed's seven fake briefs too, each with its
own holidays, bands, renamed fields and renamed enum values.

POLICY_B is POLICY_A plus multi_product=True, max_products=3: Brief B's own closing rule ("promise
wins if any was recorded, otherwise no_promise with the reason of the first non-paid note, in
get_account order") is exactly Brief A's table when there is one product, so one shape serves both.
"""

A_OUTCOMES = {
    "promise": ["promise", None], "paid": ["paid", None],
    "tr_restructuring": ["transferred", "restructuring"], "tr_human": ["transferred", "human"], "tr_support": ["transferred", "support"],
    "np_refused": ["no_promise", "refused"], "np_hardship": ["no_promise", "hardship"], "np_dispute": ["no_promise", "dispute"],
    "np_service_error": ["no_promise", "service_error"],
    "nv_failed": ["not_verified", "failed"], "nv_declined": ["not_verified", "declined"],
    "nr_callback": ["not_reached", "callback"], "nr_no_callback": ["not_reached", "no_callback"],
    "nr_wrong_number": ["not_reached", "wrong_number"], "nr_deceased": ["not_reached", "deceased"], "nr_unclear": ["not_reached", "unclear"],
}
A_QUEUES = {"restructuring": "restructuring", "human": "general", "support": "support"}
A_NOTES = {"refused": "refused", "hardship": "hardship", "dispute": "dispute", "paid": "paid", "service_error": "service_error"}
A_TYPES = {
    "card": {"enum": "credit_card", "label": "credit card ending {last4}", "floor_field": "minimum_payment", "last4_field": "card_last4"},
    "loan": {"enum": "personal_loan", "label": "personal loan", "floor_field": "instalment_amount", "last4_field": None},
    "car": {"enum": "car_loan", "label": "car loan", "floor_field": "instalment_amount", "last4_field": None},
}
A_ACCOUNT = {"product_id": "product_id", "type": "type", "past_due": "past_due", "days_late": "days_late"}

POLICY_A = {
    "id": "brief-a", "name": "Brief A", "bank": "Freya Bank", "kind": "clean", "language": "en",
    "address": "{title} {last_name}",
    "identity": {"questions": ["date_of_birth", "phone_last4"], "max_wrong": 3, "swap_hint": True},
    "callback": {"start": "10:00", "end": "19:00", "same_day": True},
    "outcome_map": A_OUTCOMES, "queue_map": A_QUEUES, "note_map": A_NOTES,
    "close_rule": {"kind": "first_non_paid"},
    "notes_on_restructuring": None,
    "product_types": A_TYPES, "account_fields": A_ACCOUNT, "product_id_format": "p{n}",
    "tools": {
        "verify_identity": {"field": "field", "answer": "answer"},
        "create_promise": {"product_id": "product_id", "amount": "amount", "date": "date", "amount_unit": "currency"},
        "schedule_callback": {"mode": "date_time", "date": "date", "time": "time"},
        "close_call": {"mode": "outcome_reason", "outcome": "outcome", "reason": "reason"},
        "transfer_to_agent": {"queue": "queue"},
        "add_note": {"product_id": "product_id", "reason": "reason"},
    },
    "forbidden_before_verify": ["collections", "debt", "payment"],
    "multi_product": False, "max_products": 1,
    "holidays": ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
    "bands": [
        {"name": "early", "min_days": 1, "max_days": 7, "deadline_business_days": 12, "attempts": 1},
        {"name": "late", "min_days": 8, "max_days": 45, "deadline_business_days": 7, "attempts": 2},
    ],
    "sentences": {
        "opening": "a digital assistant calling from Freya Bank; the call is recorded",
        "promise_confirmation": "You will receive a text message confirming this promise.",
        "ai": "Freya Bank's digital assistant",
        "scam": "verify the call by phoning Freya Bank's customer service number",
        "failed_id": "use the mobile app or a branch",
    },
}

POLICY_B = {**POLICY_A, "id": "brief-b", "name": "Brief B", "multi_product": True, "max_products": 3}
