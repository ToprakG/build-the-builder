"""Unit tests for seed/datefns.py against the public practice cases that exposed the minimal
builder's date/attempt bugs (PUB-04, PUB-07, PUB-12, PUB-13), plus the generalisations the
testbed's fake briefs require (3-band attempt1 capping in f1, priority close_rule in f3). Pure
Python, no network.

    python3 seed/test_datefns.py
"""
import datetime
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from seed import datefns as d
from seed.policy_ab import POLICY_A

HOLIDAYS = POLICY_A["holidays"]
BANDS = POLICY_A["bands"]
TYPE_FIELD = POLICY_A["account_fields"]["type"]
PAST_DUE_FIELD = POLICY_A["account_fields"]["past_due"]
DAYS_LATE_FIELD = POLICY_A["account_fields"]["days_late"]
FLOOR_BY_ENUM = {pt["enum"]: pt["floor_field"] for pt in POLICY_A["product_types"].values()}

TODAY = datetime.date(2026, 10, 14)  # now = 2026-10-14T11:00 in every public case

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def facts(type_enum, days_late, past_due, **extra):
    product = {TYPE_FIELD: type_enum, DAYS_LATE_FIELD: days_late, PAST_DUE_FIELD: past_due, **extra}
    return d.product_facts(product, TODAY, BANDS, HOLIDAYS, TYPE_FIELD, FLOOR_BY_ENUM, PAST_DUE_FIELD, DAYS_LATE_FIELD)


# PUB-04: car_loan, days_late=14 -> late band, deadline = 7th business day after 2026-10-14.
# Oct 15(1) 16(2) 19(3) 20(4) 21(5) 22(6) 23(7) -> 2026-10-23. The minimal builder recorded
# 2026-11-06 (whatever the customer said) because nothing clamped it.
pub04 = facts("car_loan", 14, 1250.0, instalment_amount=400.0)
check("PUB-04 band", pub04["band"], "late")
check("PUB-04 deadline", pub04["deadline"], "2026-10-23")
ok, date, amt, reason = d.validate_promise("2026-11-06", 1250.0, pub04["min_date"], pub04["deadline"], pub04["floor"], pub04["full"], HOLIDAYS)
check("PUB-04 clamp ok flag", ok, False)
check("PUB-04 clamped date", date, "2026-10-23")
check("PUB-04 clamped amount", amt, 1250.0)

# PUB-07: personal_loan, days_late=5 -> early band, deadline = 12th business day after 2026-10-14.
# 15(1) 16(2) 19(3) 20(4) 21(5) 22(6) 23(7) [26 holiday, skipped] 27(8) 28(9) 29(10) 30(11) Nov-2(12)
# -> 2026-11-02. The customer named Monday 26 Oct, a holiday that is NOT after that deadline, so the
# agent must offer the next business day (27 Oct).
pub07 = facts("personal_loan", 5, 540.0, instalment_amount=180.0)
check("PUB-07 band", pub07["band"], "early")
check("PUB-07 deadline", pub07["deadline"], "2026-11-02")
ok, date, amt, reason = d.validate_promise("2026-10-26", 540.0, pub07["min_date"], pub07["deadline"], pub07["floor"], pub07["full"], HOLIDAYS)
check("PUB-07 clamped date (holiday -> next business day)", date, "2026-10-27")

# PUB-12: credit_card days_late=16 -> late (2 attempts), personal_loan days_late=16 -> late
# (2 attempts), car_loan days_late=7 -> early (1 attempt).
cc = facts("credit_card", 16, 510.0, minimum_payment=153.0)
pl = facts("personal_loan", 16, 1320.0, instalment_amount=440.0)
cl = facts("car_loan", 7, 140.0, instalment_amount=46.0)
check("PUB-12 credit card band", cc["band"], "late")
check("PUB-12 credit card attempts", cc["attempts_allowed"], 2)
check("PUB-12 personal loan attempts", pl["attempts_allowed"], 2)
check("PUB-12 car loan band", cl["band"], "early")
check("PUB-12 car loan attempts", cl["attempts_allowed"], 1)

# PUB-13: credit_card days_late=4 -> early band (deadline far out, 12 business days = 2026-11-02),
# personal_loan days_late=20 -> late band (deadline 2026-10-23, the nearer one). Customer names 30
# Oct for both; only the loan needs to be capped, to 23 Oct. The card's deadline (2 Nov) is after 30
# Oct, so the card keeps 30 Oct unchanged.
cc13 = facts("credit_card", 4, 300.0, minimum_payment=90.0)
pl13 = facts("personal_loan", 20, 900.0, instalment_amount=300.0)
check("PUB-13 credit card deadline (early band, after 30 Oct)", cc13["deadline"], "2026-11-02")
check("PUB-13 personal loan deadline", pl13["deadline"], "2026-10-23")
ok_cc, date_cc, _, _ = d.validate_promise("2026-10-30", 300.0, cc13["min_date"], cc13["deadline"], cc13["floor"], cc13["full"], HOLIDAYS)
ok_pl, date_pl, _, _ = d.validate_promise("2026-10-30", 900.0, pl13["min_date"], pl13["deadline"], pl13["floor"], pl13["full"], HOLIDAYS)
check("PUB-13 credit card date unchanged", (ok_cc, date_cc), (True, "2026-10-30"))
check("PUB-13 personal loan date capped", date_pl, "2026-10-23")

# PUB-06: credit_card, floor offered once when full amount is too much; accepted floor on a Friday.
pub06 = facts("credit_card", 12, 2125.10, minimum_payment=637.5)
check("PUB-06 floor", pub06["floor"], 637.5)
check("PUB-06 attempt1_date (Fri 16 Oct)", pub06["attempt1_date"], "2026-10-16")
ok, date, amt, reason = d.validate_promise("2026-10-16", 637.5, pub06["min_date"], pub06["deadline"], pub06["floor"], pub06["full"], HOLIDAYS)
check("PUB-06 floor amount accepted unchanged", (ok, date, amt), (True, "2026-10-16", 637.5))

# PUB-02: callback window, today 2026-10-14T11:00, next business day 2026-10-15, same_day=True.
START, END, SAME_DAY = POLICY_A["callback"]["start"], POLICY_A["callback"]["end"], POLICY_A["callback"]["same_day"]
ok, reason = d.callback_ok("2026-10-15", "11:00", "2026-10-14T11:00", HOLIDAYS, START, END, SAME_DAY)
check("PUB-02 callback within window", (ok, reason), (True, ""))
ok, reason = d.callback_ok("2026-10-14", "09:00", "2026-10-14T11:00", HOLIDAYS, START, END, SAME_DAY)
check("callback before now on the same day is rejected", ok, False)
ok, reason = d.callback_ok("2026-10-16", "11:00", "2026-10-14T11:00", HOLIDAYS, START, END, SAME_DAY)
check("callback two business days out is rejected", ok, False)

# f6-corvid-memo's rule: no same-day callbacks (same_day=False) -- today must be rejected even
# though it would otherwise be in-window, and the next business day must still be accepted.
ok, reason = d.callback_ok("2026-10-14", "15:00", "2026-10-14T11:00", HOLIDAYS, START, END, False)
check("same_day=False rejects today even in-window", ok, False)
ok, reason = d.callback_ok("2026-10-15", "15:00", "2026-10-14T11:00", HOLIDAYS, START, END, False)
check("same_day=False still accepts the next business day", ok, True)

# f1-nordhavn's rule: attempt 1 is capped at the deadline. A "final" band with only 3 business
# days to the deadline, starting on a day where "last business day of this week" would overshoot.
F1_HOLIDAYS = ["2026-10-30", "2026-11-02", "2026-11-20", "2026-12-24", "2026-12-25", "2026-12-31"]
F1_BANDS = [{"name": "final", "min_days": 31, "max_days": 60, "deadline_business_days": 3, "attempts": 1}]
f1_today = datetime.date(2026, 10, 21)  # Wednesday
f1 = d.product_facts({"type": "x", "days_late": 40, "past_due": 100.0, "f": 10.0}, f1_today, F1_BANDS, F1_HOLIDAYS, "type", {"x": "f"}, "past_due", "days_late")
check("f1 attempt1 is capped at the deadline, not after it", f1["attempt1_date"] <= f1["deadline"], True)

# outcome_for_call: Brief B's first_non_paid rule, and f3-kestrel's priority rule.
FIRST_NON_PAID = {"kind": "first_non_paid"}
PRIORITY = {"kind": "priority", "order": ["dispute", "hardship", "refused", "service_error"]}
check("all promised -> promise", d.outcome_for_call([{"product_id": "p1"}], [], FIRST_NON_PAID), ("promise", ""))
check("one promise, two refused notes -> promise wins (PUB-12)",
      d.outcome_for_call([{"product_id": "p1"}], [{"product_id": "p2", "reason": "refused"}, {"product_id": "p3", "reason": "refused"}], FIRST_NON_PAID),
      ("promise", ""))
check("no promise, all paid -> paid", d.outcome_for_call([], [{"product_id": "p1", "reason": "paid"}], FIRST_NON_PAID), ("paid", ""))
check("first_non_paid: first non-paid note's reason wins, in note order",
      d.outcome_for_call([], [{"product_id": "p1", "reason": "paid"}, {"product_id": "p2", "reason": "dispute"}], FIRST_NON_PAID),
      ("no_promise", "dispute"))
check("priority: refused comes before service_error in note order, but priority order picks dispute first",
      d.outcome_for_call([], [{"product_id": "p1", "reason": "refused"}, {"product_id": "p2", "reason": "dispute"}], PRIORITY),
      ("no_promise", "dispute"))
check("single product refused (Brief A shape, PUB-09-like) -> no_promise/refused",
      d.outcome_for_call([], [{"product_id": "p1", "reason": "refused"}], FIRST_NON_PAID), ("no_promise", "refused"))

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("All datefns checks passed.")
