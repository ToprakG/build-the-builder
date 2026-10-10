"""Pure date, business-day and policy math, keyed exactly like the canonical config schema
(testbed/generator/configs.py / fake_briefs/*/config.json): bands use min_days/max_days/
deadline_business_days/attempts, close_rule is {"kind": "first_non_paid"} or {"kind": "priority",
"order": [...]}, note reasons are the five canonical keys (refused/hardship/dispute/paid/
service_error) everywhere except at the final add_note/close_call boundary. Stdlib only
(datetime), and no dict/list literal that ends in two adjacent closing braces, so this module's
functions can be embedded verbatim (via inspect.getsource) into Freya's sandboxed code nodes,
which forbid "{{" and "}}" in the source. seed/graph.py embeds these exact functions; this file's
own unit tests import them directly, so the sandboxed copy and the tested copy can never drift.

These functions mirror testbed/generator/engine.py's Cal/Policy methods rule for rule (same check
order, same capping), since that engine is the oracle the testbed's expected records are graded
against.
"""
import datetime


def parse_date(value):
    return datetime.date.fromisoformat(str(value)[:10])


def parse_datetime(value):
    return datetime.datetime.fromisoformat(str(value))


def is_business_day(d, holidays):
    return d.weekday() < 5 and d.isoformat() not in holidays


def next_business_day(d, holidays):
    d = d + datetime.timedelta(days=1)
    while not is_business_day(d, holidays):
        d = d + datetime.timedelta(days=1)
    return d


def on_or_after_business_day(d, holidays):
    return d if is_business_day(d, holidays) else next_business_day(d, holidays)


def add_business_days(start, n, holidays):
    d = start
    moved = 0
    while moved < n:
        d = next_business_day(d, holidays)
        moved += 1
    return d


def band_for(days_late, bands):
    for b in bands:
        if b["min_days"] <= days_late <= b["max_days"]:
            return b
    return bands[-1]


def deadline_for(today, days_late, bands, holidays):
    band = band_for(days_late, bands)
    return add_business_days(today, band["deadline_business_days"], holidays)


def floor_for(product, type_field, floor_field_by_enum):
    enum_value = product.get(type_field)
    field = floor_field_by_enum.get(enum_value)
    return float(product.get(field) or 0)


def last_business_day_of_week(day, holidays):
    friday = day + datetime.timedelta(days=4 - day.weekday())
    d = friday
    while not is_business_day(d, holidays):
        d = d - datetime.timedelta(days=1)
    return d


def attempt1_date_uncapped(today, holidays):
    this_week = last_business_day_of_week(today, holidays)
    if this_week <= today:
        return last_business_day_of_week(today + datetime.timedelta(days=7), holidays)
    return this_week


def product_facts(product, today, bands, holidays, type_field, floor_field_by_enum, past_due_field, days_late_field):
    days_late = int(product.get(days_late_field) or 0)
    band = band_for(days_late, bands)
    deadline = deadline_for(today, days_late, bands, holidays)
    min_date = on_or_after_business_day(today, holidays)
    attempt1 = min(attempt1_date_uncapped(today, holidays), deadline)
    return {
        "band": band["name"],
        "attempts_allowed": band["attempts"],
        "deadline": deadline.isoformat(),
        "min_date": min_date.isoformat(),
        "attempt1_date": attempt1.isoformat(),
        "floor": floor_for(product, type_field, floor_field_by_enum),
        "full": float(product.get(past_due_field) or 0),
        "days_late": days_late,
    }


def resolve_named_date(named_str, today, deadline_str, holidays):
    """Mirrors engine.py's Policy.resolve_named_date rule order exactly: a date after the deadline
    is capped to the deadline BEFORE a non-business-day check ever runs; only a date that is
    already within the deadline gets rolled forward off a weekend or holiday. Returns
    (rule, date_iso) where rule is "ok", "after_deadline" or "non_business_day"."""
    try:
        named = parse_date(named_str)
    except Exception:
        return "unreadable", today.isoformat()
    deadline = parse_date(deadline_str)
    if named < today:
        return "before_today", on_or_after_business_day(today, holidays).isoformat()
    if named > deadline:
        return "after_deadline", deadline.isoformat()
    if not is_business_day(named, holidays):
        return "non_business_day", next_business_day(named, holidays).isoformat()
    return "ok", named.isoformat()


def validate_promise(date_str, amount, min_date_str, deadline_str, floor, full, holidays):
    """Clamp a proposed (date, amount) into the product's allowed window and [floor, full].
    Returns (ok, corrected_date_iso, corrected_amount, reason). ok is True only when nothing
    needed correcting, so the caller can decide whether the customer must be told again."""
    reasons = []
    today = parse_date(min_date_str)  # min_date is already on_or_after_business_day(today)
    rule, date_out = resolve_named_date(date_str, today, deadline_str, holidays)
    if rule == "unreadable":
        reasons.append("the date could not be read")
    elif rule == "before_today":
        reasons.append("the date was before today")
    elif rule == "after_deadline":
        reasons.append("the date was after the deadline")
    elif rule == "non_business_day":
        reasons.append("the date was a weekend or a holiday")
    try:
        amt = float(amount)
    except Exception:
        amt = 0.0
        reasons.append("the amount could not be read")
    if amt < floor:
        reasons.append("the amount was below the floor")
        amt = floor
    elif amt > full:
        reasons.append("the amount was above the full past-due amount")
        amt = full
    ok = not reasons
    return ok, date_out, round(amt, 2), "; ".join(reasons)


def callback_ok(date_str, time_str, now_value, holidays, start, end, same_day):
    """A callback is valid on an allowed day (today only if same_day, always the next business
    day), inside [start, end] inclusive, and later than `now`. Returns (ok, reason)."""
    try:
        d = parse_date(date_str)
        hh, mm = str(time_str).split(":")
        hh = int(hh)
        mm = int(mm)
    except Exception:
        return False, "unreadable date or time"
    now_dt = parse_datetime(now_value)
    today = now_dt.date()
    allowed = {next_business_day(today, holidays).isoformat()}
    if same_day:
        allowed.add(today.isoformat())
    if d.isoformat() not in allowed:
        return False, "not an allowed day"
    hhmm = f"{hh:02d}:{mm:02d}"
    if hhmm < start or hhmm > end:
        return False, "outside the callback window"
    candidate = datetime.datetime(d.year, d.month, d.day, hh, mm)
    if candidate <= now_dt:
        return False, "not later than now"
    return True, ""


def outcome_for_call(promises, notes, close_rule):
    """promises/notes use CANONICAL note-reason keys (refused/hardship/dispute/paid/
    service_error); translate to the brief's actual values only at the add_note/close_call
    boundary. Mirrors Brief B's own closing rule, generalised with close_rule["kind"]:
    "first_non_paid" (the first note, in get_account/notes order, that isn't paid) or
    "priority" (the first reason in close_rule["order"] that appears in notes at all).
    Returns a canonical outcome key: "promise", "paid" or "no_promise", plus a reason key or ""."""
    if promises:
        return "promise", ""
    if not notes:
        return "no_promise", "refused"
    if all(n.get("reason") == "paid" for n in notes):
        return "paid", ""
    if close_rule.get("kind") == "priority":
        for reason in close_rule.get("order", []):
            if any(n.get("reason") == reason for n in notes):
                return "no_promise", reason
    for n in notes:
        if n.get("reason") != "paid":
            return "no_promise", n.get("reason")
    return "no_promise", "refused"
