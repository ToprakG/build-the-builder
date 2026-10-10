"""Checks a config against the tool API. A failure is sent back to the model once."""
import datetime as dt
import re


CANON_NOTES = ["refused", "hardship", "dispute", "paid", "service_error"]
REQUIRED = [
    "language", "address", "identity", "callback", "outcome_map", "queue_map", "note_map",
    "close_rule", "notes_on_restructuring", "product_types", "account_fields", "product_id_format",
    "tools", "forbidden_before_verify", "multi_product", "max_products", "attempt1",
    "id", "bank", "kind", "holidays", "bands", "sentences",
]
SENTENCE_KEYS = ["opening", "promise_confirmation", "ai", "scam", "failed_id"]
OUTCOME_KEYS = [
    "promise", "paid", "tr_restructuring", "tr_human", "tr_support",
    "np_refused", "np_hardship", "np_dispute", "np_service_error",
    "nv_failed", "nv_declined",
    "nr_callback", "nr_no_callback", "nr_wrong_number", "nr_deceased", "nr_unclear",
]


def _hhmm(value):
    m = re.fullmatch(r"(\d{2}):(\d{2})", str(value or ""))
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def stated_range(text):
    m = re.search(r"who are\s+(\d+)\s+to\s+(\d+)\s+days late", text, re.I)
    if not m:
        m = re.search(r"behind,\s+(\d+)\s+to\s+(\d+)\s+days", text, re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def check(config, facts, brief_text=""):
    """Return a list of human-readable errors. Empty means the config passed."""
    errors = []
    for key in REQUIRED:
        if key not in config:
            errors.append(f"schema: missing {key}")
    if errors:
        return errors
    ident = config["identity"]
    if not isinstance(ident, dict) or not isinstance(ident.get("questions"), list):
        errors.append("schema: identity.questions must be a list")
    elif not isinstance(ident.get("max_wrong"), int) or ident["max_wrong"] < 1:
        errors.append("schema: identity.max_wrong must be a positive integer")
    elif not isinstance(ident.get("swap_hint"), bool):
        errors.append("schema: identity.swap_hint must be true or false")
    questions = set(facts.get("questions") or [])
    for q in ident.get("questions") or []:
        if questions and q not in questions:
            errors.append(f"identity field {q} is not in verify_identity's enum {sorted(questions)}")
    cb = config["callback"]
    start, end = _hhmm(cb.get("start")), _hhmm(cb.get("end"))
    if start is None or end is None:
        errors.append(f"callback times do not parse: {cb.get('start')} to {cb.get('end')}")
    elif start >= end:
        errors.append(f"callback start {cb.get('start')} is not before end {cb.get('end')}")
    if not isinstance(cb.get("same_day"), bool):
        errors.append("schema: callback.same_day must be true or false")
    for holiday in config["holidays"]:
        try:
            dt.date.fromisoformat(holiday)
        except ValueError:
            errors.append(f"holiday is not a real date: {holiday}")
    bands = config["bands"]
    if not isinstance(bands, list) or not bands:
        errors.append("schema: bands must be a non-empty list")
    else:
        ordered = sorted(bands, key=lambda b: b.get("min_days", 0))
        for band in ordered:
            for field in ("name", "min_days", "max_days", "deadline_business_days", "attempts"):
                if field not in band:
                    errors.append(f"schema: band missing {field}")
            if band.get("min_days", 0) > band.get("max_days", -1):
                errors.append(f"band {band.get('name')} has min_days after max_days")
        for prev, nxt in zip(ordered, ordered[1:]):
            if nxt["min_days"] <= prev["max_days"]:
                errors.append(f"bands overlap: {prev['name']} ends {prev['max_days']}, {nxt['name']} starts {nxt['min_days']}")
            elif nxt["min_days"] != prev["max_days"] + 1:
                errors.append(f"bands leave a gap: {prev['name']} ends {prev['max_days']}, {nxt['name']} starts {nxt['min_days']}")
        span = stated_range(brief_text)
        if span and ordered and not any("gap" in e or "overlap" in e for e in errors):
            if ordered[0]["min_days"] != span[0] or ordered[-1]["max_days"] != span[1]:
                errors.append(f"bands cover {ordered[0]['min_days']}-{ordered[-1]['max_days']}, the brief's days_late range is {span[0]}-{span[1]}")
    outcomes = set(facts.get("outcome_enum") or [])
    reasons = set(facts.get("reason_enum") or [])
    for key, pair in (config.get("outcome_map") or {}).items():
        if not isinstance(pair, list) or len(pair) != 2:
            errors.append(f"schema: outcome_map.{key} must be [outcome, reason]")
            continue
        if outcomes and pair[0] not in outcomes:
            errors.append(f"outcome {pair[0]} is not in close_call's enum")
        if pair[1] is not None and reasons and pair[1] not in reasons:
            errors.append(f"reason {pair[1]} is not in close_call's enum")
    note_enum = set(facts.get("note_enum") or [])
    for key, value in (config.get("note_map") or {}).items():
        if note_enum and value not in note_enum:
            errors.append(f"note reason {value} is not in add_note's enum")
    queue_enum = set(facts.get("queue_enum") or [])
    for key, value in (config.get("queue_map") or {}).items():
        if queue_enum and value not in queue_enum:
            errors.append(f"queue {value} is not in transfer_to_agent's enum")
    product_fields = (facts.get("specs") or {}).get("get_account", {}).get("products") or {}
    type_enum = set()
    type_name = (config.get("account_fields") or {}).get("type")
    if type_name and product_fields:
        type_enum = set((product_fields.get(type_name) or {}).get("enum") or [])
    for key, prod in (config.get("product_types") or {}).items():
        if not isinstance(prod, dict):
            errors.append(f"schema: product_types.{key} is missing")
            continue
        if type_enum and prod.get("enum") not in type_enum:
            errors.append(f"product {key} enum {prod.get('enum')} is not in get_account's type enum")
        floor = prod.get("floor_field")
        if floor and product_fields and floor not in product_fields:
            errors.append(f"floor field {floor} is not in get_account's response")
        last4 = prod.get("last4_field")
        if last4 and product_fields and last4 not in product_fields:
            errors.append(f"last4 field {last4} is not in get_account's response")
    if config["close_rule"].get("kind") not in ("first_non_paid", "priority"):
        errors.append("schema: close_rule.kind must be first_non_paid or priority")
    if config["close_rule"].get("kind") == "priority":
        order = config["close_rule"].get("order")
        if not isinstance(order, list) or not order:
            errors.append("schema: priority close_rule needs an order")
    for key in SENTENCE_KEYS:
        if key not in (config.get("sentences") or {}):
            errors.append(f"schema: sentences.{key} is missing")
    return errors
