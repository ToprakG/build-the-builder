"""The policy of Freya Bank's early-stage collections call (Brief A) and its up-to-three-product
extension (Brief B), as plain data. No part of seed/graph.py knows a holiday date, a band
threshold, a floor field name or a sentence; it only reads this dict. A final brief with other
numbers, fields or outcome names is a new file like this one, not a change to graph.py.

Brief B's own closing rule ("promise wins if any promise was recorded, otherwise no_promise with
the reason of the first non-paid note") is also exactly Brief A's table for a one-product call, so
one policy and one graph serve both: pass brief-a.md alone and the up-to-three-product machinery
is simply never exercised, because get_account only ever returns one product.
"""

POLICY = {
    "holidays": ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],

    # days_late band -> deadline (business days after `now`) and persuasion attempts allowed.
    "bands": [
        {"name": "early", "min": 1, "max": 7, "deadline_business_days": 12, "attempts": 1},
        {"name": "late", "min": 8, "max": 45, "deadline_business_days": 7, "attempts": 2},
    ],

    "floor_field_by_type": {
        "credit_card": "minimum_payment",
        "personal_loan": "instalment_amount",
        "car_loan": "instalment_amount",
    },

    "identity": {
        "questions": ["date_of_birth", "phone_last4"],
        "max_wrong_total": 3,
        "formats": {
            "date_of_birth": "YYYY-MM-DD. The caller may say it any way; read day, then month, then year when only numbers are given.",
            "phone_last4": "four digits",
        },
        "swap_hint": "day_month_swapped",
    },

    "callback": {"window_start": "10:00", "window_end": "19:00", "days_ahead": ["today", "next_business_day"]},

    # outcome -> allowed reasons. A reason of "" means the outcome takes none.
    "outcomes": {
        "promise": [""],
        "paid": [""],
        "transferred": ["restructuring", "human", "support"],
        "no_promise": ["refused", "hardship", "dispute", "service_error"],
        "not_verified": ["failed", "declined"],
        "not_reached": ["callback", "no_callback", "wrong_number", "deceased", "unclear"],
    },
    "note_reasons": ["refused", "hardship", "dispute", "paid", "service_error"],
    "paid_note_reason": "paid",

    "queues": ["restructuring", "general", "support"],

    # transfer trigger -> {queue to call transfer_to_agent with, reason close_call takes}.
    # "human" is the one case where the two differ: queue general, close reason human.
    "transfer_map": {
        "restructuring": {"queue": "restructuring", "reason": "restructuring"},
        "human": {"queue": "general", "reason": "human"},
        "support": {"queue": "support", "reason": "support"},
    },

    "limits": {
        "opening_max_asks": 2,
        "identity_max_declines": 2,
        "human_insist_threshold": 2,
    },

    "forbidden_before_verification": ["collections", "debt", "payment"],

    # Every value here is the literal sentence the agent speaks (first person, Freya's "{{var}}"
    # double-brace substitution where a call variable belongs), never an instruction describing
    # one, since graph.py drops these straight into on_enter.text or a task_message quote.
    "sentences": {
        "opener": "I am a digital assistant calling from Freya Bank, and this call is recorded.",
        "confirm_identity": "Am I speaking with {{title}} {{last_name}}?",
        "robot_question": "I am Freya Bank's digital assistant.",
        "scam_question": "You can verify this call by phoning Freya Bank's customer service number.",
        "not_verified_failed": "Please use the mobile app or visit a branch.",
        "deceased": "I am sorry for your loss.",
        "text_message": "You will receive a text message confirming this promise.",
        "already_paid": "Payments can take one business day to appear.",
        "goodbye": "Thank you, goodbye.",
    },

    # Brief A rule 30: attempt 1 proposes the full amount (or the floor, if already refused as too
    # much) on the last business day of this week (next week's if today already is that day).
    # Attempt 2 (late band only) proposes the floor on the deadline. graph.py reads these keys to
    # build the facts it hands the model; it does not know the rule numbers.
    "attempts": {
        1: {"amount": "full_or_floor", "date": "attempt1_date"},
        2: {"amount": "floor", "date": "deadline", "bands_only": ["late"]},
    },

    "multi_product": {
        "max_products": 3,
        "ask_all_today_first": True,
    },
}
