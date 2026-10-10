"""Hand-written correct configs: what a perfect extractor should read from each brief.
The oracle computes every expected outcome from these. Canonical keys on the left, the brief's own values on the right."""

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
    "card": {"enum": "credit_card", "label": "credit card ending {last4}", "floor_field": "minimum_payment", "last4_field": "card_last4", "floor_pct": 0.30},
    "loan": {"enum": "personal_loan", "label": "personal loan", "floor_field": "instalment_amount", "last4_field": None},
    "car": {"enum": "car_loan", "label": "car loan", "floor_field": "instalment_amount", "last4_field": None},
}
A_ACCOUNT = {"product_id": "product_id", "type": "type", "past_due": "past_due", "days_late": "days_late"}

BASE = {
    "language": "en",
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
    "attempt1": "last business day of this week (next week's if today is that day), never after the deadline",
}


def cfg(**kw):
    out = {k: (v.copy() if isinstance(v, dict) else v) for k, v in BASE.items()}
    out.update(kw)
    return out


BRIEF_A = cfg(
    id="brief-a", name="Brief A", bank="Freya Bank", kind="clean",
    holidays=["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
    bands=[{"name": "early", "min_days": 1, "max_days": 7, "deadline_business_days": 12, "attempts": 1},
           {"name": "late", "min_days": 8, "max_days": 45, "deadline_business_days": 7, "attempts": 2}],
    sentences={"opening": "a digital assistant calling from Freya Bank; the call is recorded",
               "promise_confirmation": "You will receive a text message confirming this promise.",
               "ai": "Freya Bank's digital assistant",
               "scam": "verify the call by phoning Freya Bank's customer service number",
               "failed_id": "use the mobile app or a branch"},
    now_dates=["2026-10-14T11:00", "2026-10-16T15:30", "2026-10-19T09:40", "2026-10-23T17:20", "2026-11-10T10:15"],
)

BRIEF_B = cfg(**{k: v for k, v in BRIEF_A.items() if k not in ("id", "name", "multi_product", "max_products")},
              id="brief-b", name="Brief B", multi_product=True, max_products=3)

# 1. Clean, single product, other holidays, 3 bands, different floors.
F1 = cfg(
    id="f1-nordhavn", name="Nordhavn Bank (clean, 3 bands)", bank="Nordhavn Bank", kind="clean",
    holidays=["2026-10-30", "2026-11-02", "2026-11-20", "2026-12-24", "2026-12-25", "2026-12-31"],
    bands=[{"name": "soft", "min_days": 1, "max_days": 10, "deadline_business_days": 10, "attempts": 1},
           {"name": "firm", "min_days": 11, "max_days": 30, "deadline_business_days": 6, "attempts": 2},
           {"name": "final", "min_days": 31, "max_days": 60, "deadline_business_days": 3, "attempts": 1}],
    callback={"start": "09:00", "end": "18:00", "same_day": True},
    product_types={
        "card": {"enum": "credit_card", "label": "credit card ending {last4}", "floor_field": "min_due", "last4_field": "card_ending", "floor_pct": 0.25},
        "overdraft": {"enum": "overdraft", "label": "overdraft", "floor_field": "min_due", "last4_field": None, "floor_pct": 0.20},
        "loan": {"enum": "personal_loan", "label": "personal loan", "floor_field": "monthly_instalment", "last4_field": None},
    },
    account_fields={"product_id": "product_id", "type": "type", "past_due": "overdue_amount", "days_late": "days_past_due"},
    tools={**BASE["tools"], "create_promise": {"product_id": "product_id", "amount": "amount", "date": "promise_date", "amount_unit": "currency"}},
    sentences={"opening": "a virtual assistant from Nordhavn Bank; the call is recorded",
               "promise_confirmation": "A confirmation of this promise will be sent to you by text message.",
               "ai": "Nordhavn Bank's virtual assistant",
               "scam": "verify the call by phoning the number on the back of their card or on nordhavn.example",
               "failed_id": "visit a branch or use online banking"},
    now_dates=["2026-10-28T10:30", "2026-10-21T09:15", "2026-11-06T16:45", "2026-11-18T14:00", "2026-12-22T11:00"],
)

# 2. Clean, different identity checks: three questions, two wrong answers end it.
F2 = cfg(
    id="f2-ostral", name="Ostral Bank (clean, 3 identity questions)", bank="Ostral Bank", kind="clean",
    holidays=["2026-11-09", "2026-11-23", "2026-12-08", "2026-12-25", "2026-12-28"],
    bands=[{"name": "early", "min_days": 1, "max_days": 14, "deadline_business_days": 10, "attempts": 1},
           {"name": "late", "min_days": 15, "max_days": 60, "deadline_business_days": 5, "attempts": 2}],
    identity={"questions": ["date_of_birth", "postcode", "phone_last4"], "max_wrong": 2, "swap_hint": True},
    callback={"start": "10:00", "end": "20:00", "same_day": True},
    queue_map={"restructuring": "restructuring", "human": "agent", "support": "welfare"},
    tools={**BASE["tools"]},
    sentences={"opening": "Ostral Bank's automated assistant; the call is recorded for quality and security",
               "promise_confirmation": "You will get a text message with the details of this promise.",
               "ai": "Ostral Bank's automated assistant",
               "scam": "hang up and call Ostral Bank on the number printed on their statement",
               "failed_id": "call the number on their statement or visit a branch"},
    forbidden_before_verify=["arrears", "debt", "payment", "overdue"],
    now_dates=["2026-11-05T11:20", "2026-11-20T15:10", "2026-11-24T09:30", "2026-12-04T18:15", "2026-12-07T10:00"],
)

# 3. Clean, multi-product like Brief B but other note reasons, priority closing, notes on restructuring, cents.
F3 = cfg(
    id="f3-kestrel", name="Kestrel Bank (clean, multi-product, other note and outcome rules)", bank="Kestrel Bank", kind="clean",
    holidays=["2026-10-12", "2026-11-11", "2026-11-26", "2026-11-27", "2026-12-25"],
    bands=[{"name": "early", "min_days": 1, "max_days": 9, "deadline_business_days": 11, "attempts": 1},
           {"name": "late", "min_days": 10, "max_days": 50, "deadline_business_days": 6, "attempts": 2}],
    multi_product=True, max_products=3,
    note_map={"refused": "declined", "hardship": "financial_difficulty", "dispute": "contested", "paid": "already_paid", "service_error": "system_error"},
    close_rule={"kind": "priority", "order": ["dispute", "hardship", "refused", "service_error"]},
    notes_on_restructuring="refused",
    product_id_format="acc-{n}",
    tools={**BASE["tools"], "create_promise": {"product_id": "account_ref", "amount": "amount_cents", "date": "date", "amount_unit": "cents"},
           "add_note": {"product_id": "account_ref", "reason": "reason"}},
    account_fields={"product_id": "account_ref", "type": "type", "past_due": "past_due", "days_late": "days_late"},
    sentences={"opening": "Kestrel Bank's digital assistant; the call is recorded",
               "promise_confirmation": "You will receive a text message confirming each promise.",
               "ai": "Kestrel Bank's digital assistant",
               "scam": "verify the call by phoning Kestrel Bank's customer service number",
               "failed_id": "use the Kestrel app or a branch"},
    now_dates=["2026-11-04T11:00", "2026-11-13T16:20", "2026-11-16T09:50", "2026-11-25T14:30", "2026-12-02T18:00"],
)

# 4. Clean, business customer: company number and founded year; up to three products.
F4 = cfg(
    id="f4-brightwell", name="Brightwell Commercial (clean, business customers)", bank="Brightwell Commercial", kind="clean",
    holidays=["2026-10-19", "2026-11-11", "2026-12-24", "2026-12-25", "2026-12-31"],
    bands=[{"name": "early", "min_days": 1, "max_days": 15, "deadline_business_days": 10, "attempts": 1},
           {"name": "late", "min_days": 16, "max_days": 60, "deadline_business_days": 5, "attempts": 2}],
    address="{title} {last_name} at {company_name}",
    identity={"questions": ["company_number", "founded_year"], "max_wrong": 3, "swap_hint": False},
    callback={"start": "08:30", "end": "17:30", "same_day": True},
    multi_product=True, max_products=3,
    queue_map={"restructuring": "restructuring", "human": "relationship_manager", "support": "support"},
    product_types={
        "bcard": {"enum": "business_card", "label": "business card ending {last4}", "floor_field": "minimum_payment", "last4_field": "card_last4", "floor_pct": 0.20},
        "bloan": {"enum": "business_loan", "label": "business loan", "floor_field": "instalment_amount", "last4_field": None},
        "overdraft": {"enum": "overdraft", "label": "business overdraft", "floor_field": "minimum_repayment", "last4_field": None, "floor_pct": 0.15},
    },
    tools={**BASE["tools"], "verify_identity": {"field": "check", "answer": "value"}},
    sentences={"opening": "Brightwell Commercial's digital assistant; the call is recorded",
               "promise_confirmation": "You will receive an email confirming this promise.",
               "ai": "Brightwell Commercial's digital assistant",
               "scam": "verify the call by phoning their relationship manager or the number on brightwell.example",
               "failed_id": "contact their relationship manager"},
    forbidden_before_verify=["collections", "debt", "payment", "arrears"],
    now_dates=["2026-10-14T10:10", "2026-10-21T15:40", "2026-10-30T09:05", "2026-11-12T16:50", "2026-11-17T11:30"],
)

# 5. Raw: email thread with the rules scattered. Postcode + birth year. Callback as one datetime field.
F5 = cfg(
    id="f5-halden-email", name="Halden Savings (raw email thread)", bank="Halden Savings", kind="raw",
    holidays=["2026-11-03", "2026-11-17", "2026-12-25", "2026-12-26", "2027-01-01"],
    bands=[{"name": "early", "min_days": 1, "max_days": 10, "deadline_business_days": 8, "attempts": 1},
           {"name": "late", "min_days": 11, "max_days": 40, "deadline_business_days": 4, "attempts": 2}],
    identity={"questions": ["postcode", "birth_year"], "max_wrong": 3, "swap_hint": False},
    callback={"start": "10:00", "end": "18:00", "same_day": True},
    tools={**BASE["tools"], "schedule_callback": {"mode": "datetime", "datetime": "callback_at"}},
    sentences={"opening": "an automated assistant from Halden Savings; the call is recorded",
               "promise_confirmation": "You will receive an email confirming this promise.",
               "ai": "Halden Savings' automated assistant",
               "scam": "verify the call by phoning Halden Savings on the number on their passbook or the website",
               "failed_id": "visit a branch"},
    now_dates=["2026-10-27T11:00", "2026-11-02T14:20", "2026-11-13T09:45", "2026-11-16T17:10", "2026-12-01T10:30"],
)

# 6. Raw: policy extract + a memo that changes two rules (late deadline 7 -> 5, no same-day callbacks).
F6 = cfg(
    id="f6-corvid-memo", name="Corvid Bank (raw policy extract + memo)", bank="Corvid Bank", kind="raw",
    holidays=["2026-11-06", "2026-11-27", "2026-12-25", "2027-01-01"],
    bands=[{"name": "early", "min_days": 1, "max_days": 7, "deadline_business_days": 12, "attempts": 1},
           {"name": "late", "min_days": 8, "max_days": 45, "deadline_business_days": 5, "attempts": 2}],
    callback={"start": "10:00", "end": "19:00", "same_day": False},
    queue_map={"restructuring": "restructuring", "human": "general", "support": "hardship"},
    sentences={"opening": "a digital assistant calling from Corvid Bank; the call is recorded",
               "promise_confirmation": "You will receive a text message confirming this promise.",
               "ai": "Corvid Bank's digital assistant",
               "scam": "verify the call by phoning Corvid Bank's customer service number",
               "failed_id": "use the mobile app or a branch"},
    memo_changes=["late-band deadline is the 5th business day (was the 7th)", "callbacks only on the next business day (no same-day callbacks)"],
    now_dates=["2026-11-02T10:45", "2026-11-05T15:15", "2026-11-12T09:30", "2026-11-20T17:40", "2026-11-26T11:10"],
)

# 7. Raw: code list / tables in an odd format. close_call takes a single outcome code.
F7_CODES = {
    "promise": "10-PTP", "paid": "20-PAID", "tr_restructuring": "31-TRF-RS", "tr_human": "32-TRF-HU", "tr_support": "33-TRF-SU",
    "np_refused": "41-NP-REF", "np_hardship": "42-NP-HS", "np_dispute": "43-NP-DSP", "np_service_error": "44-NP-ERR",
    "nv_failed": "51-NV-FAIL", "nv_declined": "52-NV-DEC",
    "nr_callback": "61-NR-CB", "nr_no_callback": "62-NR-NOCB", "nr_wrong_number": "63-NR-WN", "nr_deceased": "64-NR-RIP", "nr_unclear": "65-NR-UNC",
}
F7 = cfg(
    id="f7-lumen-codes", name="Lumen Bank (raw code list)", bank="Lumen Bank", kind="raw",
    holidays=["2026-10-23", "2026-11-11", "2026-11-26", "2026-12-25", "2026-12-28"],
    bands=[{"name": "B1", "min_days": 1, "max_days": 5, "deadline_business_days": 12, "attempts": 1},
           {"name": "B2", "min_days": 6, "max_days": 20, "deadline_business_days": 8, "attempts": 2},
           {"name": "B3", "min_days": 21, "max_days": 45, "deadline_business_days": 4, "attempts": 2}],
    outcome_map={k: [v, None] for k, v in F7_CODES.items()},
    product_types={
        "card": {"enum": "CC", "label": "credit card ending {last4}", "floor_field": "minimum_payment", "last4_field": "card_last4", "floor_pct": 0.30},
        "loan": {"enum": "PL", "label": "personal loan", "floor_field": "instalment_amount", "last4_field": None},
        "car": {"enum": "AL", "label": "car loan", "floor_field": "instalment_amount", "last4_field": None},
    },
    tools={**BASE["tools"], "close_call": {"mode": "code", "code": "code"}},
    sentences={"opening": "a digital assistant calling from Lumen Bank; the call is recorded",
               "promise_confirmation": "You will receive a text message confirming this promise.",
               "ai": "Lumen Bank's digital assistant",
               "scam": "verify the call by phoning Lumen Bank's customer service number",
               "failed_id": "use the mobile app or a branch"},
    now_dates=["2026-10-20T11:00", "2026-10-22T16:00", "2026-11-04T09:20", "2026-11-13T17:50", "2026-11-24T10:40"],
)

ALL = [BRIEF_A, BRIEF_B, F1, F2, F3, F4, F5, F6, F7]
