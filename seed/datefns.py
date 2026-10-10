"""Pure date, business-day and policy math. Stdlib only (datetime), no other imports and no
dict/list literal that ends in two adjacent closing braces, so this module's functions can be
embedded verbatim (via inspect.getsource) into Freya's sandboxed code nodes, which forbid "{{"
and "}}" in the source. seed/graph.py embeds these exact functions; this file's own unit tests
import them directly, so the sandboxed copy and the tested copy can never drift apart.
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
        if b["min"] <= days_late <= b["max"]:
            return b
    return bands[-1]


def deadline_for(today, days_late, bands, holidays):
    band = band_for(days_late, bands)
    return add_business_days(today, band["deadline_business_days"], holidays)


def floor_for(product, floor_field_by_type):
    field = floor_field_by_type.get(product.get("type"))
    return float(product.get(field) or 0)


def last_business_day_of_week(day, holidays):
    sunday = day + datetime.timedelta(days=6 - day.weekday())
    d = sunday
    while not is_business_day(d, holidays):
        d = d - datetime.timedelta(days=1)
    return d


def attempt1_proposal_date(today, holidays):
    this_week = last_business_day_of_week(today, holidays)
    if this_week == today:
        return last_business_day_of_week(today + datetime.timedelta(days=7), holidays)
    return this_week


def label_for(product):
    t = product.get("type")
    if t == "credit_card":
        return "credit card ending " + str(product.get("card_last4") or "")
    if t == "personal_loan":
        return "personal loan"
    if t == "car_loan":
        return "car loan"
    return str(t or "product")


def product_facts(product, today, bands, holidays, floor_field_by_type):
    days_late = int(product.get("days_late") or 0)
    band = band_for(days_late, bands)
    deadline = deadline_for(today, days_late, bands, holidays)
    min_date = on_or_after_business_day(today, holidays)
    facts = {
        "product_id": product.get("product_id"),
        "label": label_for(product),
        "band": band["name"],
        "attempts_allowed": band["attempts"],
        "deadline": deadline.isoformat(),
        "min_date": min_date.isoformat(),
        "attempt1_date": attempt1_proposal_date(today, holidays).isoformat(),
        "floor": floor_for(product, floor_field_by_type),
        "full": float(product.get("past_due") or 0),
        "days_late": days_late,
    }
    return facts


def validate_promise(date_str, amount, min_date_str, deadline_str, floor, full, holidays):
    """Clamp a proposed (date, amount) into the product's allowed window and [floor, full].
    Returns (ok, corrected_date_iso, corrected_amount, reason). ok is True only when nothing
    needed correcting, so the caller can decide whether the customer must be told again."""
    reasons = []
    try:
        d = parse_date(date_str)
    except Exception:
        d = None
        reasons.append("the date could not be read")
    min_d = parse_date(min_date_str)
    deadline_d = parse_date(deadline_str)
    if d is None or d < min_d:
        reasons.append("the date was before today")
        d = min_d
    if not is_business_day(d, holidays):
        reasons.append("the date was a weekend or a holiday")
        d = on_or_after_business_day(d, holidays)
    if d > deadline_d:
        reasons.append("the date was after the deadline")
        d = deadline_d
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
    return ok, d.isoformat(), round(amt, 2), "; ".join(reasons)


def callback_ok(date_str, time_str, now_value, holidays):
    """A callback is valid for today or the next business day, 10:00-19:00 inclusive, and later
    than `now`. Returns (ok, reason)."""
    try:
        d = parse_date(date_str)
        hh, mm = str(time_str).split(":")
        hh = int(hh)
        mm = int(mm)
    except Exception:
        return False, "unreadable date or time"
    now_dt = parse_datetime(now_value)
    today = now_dt.date()
    allowed = (today.isoformat(), next_business_day(today, holidays).isoformat())
    if d.isoformat() not in allowed:
        return False, "not today or the next business day"
    minutes = hh * 60 + mm
    if minutes < 600 or minutes > 19 * 60:
        return False, "outside 10:00-19:00"
    candidate = datetime.datetime(d.year, d.month, d.day, hh, mm)
    if candidate <= now_dt:
        return False, "not later than now"
    return True, ""


def outcome_for_call(promises, notes, policy_paid_reason="paid"):
    """Brief B's closing rule, which is also exactly right for a one-product Brief A call:
    promise wins if any promise was recorded; otherwise paid if every note is 'paid'; otherwise
    no_promise with the reason of the first (in get_account order) note that is not 'paid'."""
    if promises:
        return "promise", ""
    if not notes:
        return "no_promise", "refused"
    if all(n.get("reason") == policy_paid_reason for n in notes):
        return "paid", ""
    for n in notes:
        if n.get("reason") != policy_paid_reason:
            return "no_promise", n.get("reason")
    return "no_promise", "refused"
