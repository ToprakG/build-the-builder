"""Unit tests for seed/datefns.py against the public practice cases that exposed the minimal
builder's date/attempt bugs (PUB-04, PUB-07, PUB-12, PUB-13). Pure Python, no network.

    python3 seed/test_datefns.py
"""
import datetime
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from seed import datefns as d
from seed.policy_ab import POLICY

HOLIDAYS = POLICY["holidays"]
BANDS = POLICY["bands"]
FLOOR_FIELD = POLICY["floor_field_by_type"]

TODAY = datetime.date(2026, 10, 14)  # now = 2026-10-14T11:00 in every public case

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# PUB-04: car_loan, days_late=14 -> late band, deadline = 7th business day after 2026-10-14.
# Oct 15(1) 16(2) 19(3) 20(4) 21(5) 22(6) 23(7) -> 2026-10-23. The minimal builder recorded
# 2026-11-06 (whatever the customer said) because nothing clamped it.
pub04 = d.product_facts({"type": "car_loan", "days_late": 14, "past_due": 1250.0, "instalment_amount": 400.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
check("PUB-04 band", pub04["band"], "late")
check("PUB-04 deadline", pub04["deadline"], "2026-10-23")
ok, date, amt, reason = d.validate_promise("2026-11-06", 1250.0, pub04["min_date"], pub04["deadline"], pub04["floor"], pub04["full"], HOLIDAYS)
check("PUB-04 clamp ok flag", ok, False)
check("PUB-04 clamped date", date, "2026-10-23")
check("PUB-04 clamped amount", amt, 1250.0)

# PUB-07: personal_loan, days_late=5 -> early band, deadline = 12th business day after 2026-10-14.
# 15(1) 16(2) 19(3) 20(4) 21(5) 22(6) 23(7) [26 holiday, skipped] 27(8) 28(9) 29(10) 30(11) Nov-2(12)
# -> 2026-11-02. The customer named Monday 26 Oct, a holiday that is NOT after that deadline, so the
# agent must offer the next business day (27 Oct), not treat 540 as capped at 22 Oct as the minimal
# builder did (it applied the late-band 7-business-day formula to an early-band customer).
pub07 = d.product_facts({"type": "personal_loan", "days_late": 5, "past_due": 540.0, "instalment_amount": 180.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
check("PUB-07 band", pub07["band"], "early")
check("PUB-07 deadline", pub07["deadline"], "2026-11-02")
ok, date, amt, reason = d.validate_promise("2026-10-26", 540.0, pub07["min_date"], pub07["deadline"], pub07["floor"], pub07["full"], HOLIDAYS)
check("PUB-07 clamped date (holiday -> next business day)", date, "2026-10-27")

# PUB-12: credit_card days_late=16 -> late (2 attempts), personal_loan days_late=16 -> late
# (2 attempts), car_loan days_late=7 -> early (1 attempt). The minimal builder gave the car loan
# two attempts in some runs and the personal loan three in others, because nothing counted.
cc = d.product_facts({"type": "credit_card", "days_late": 16, "past_due": 510.0, "minimum_payment": 153.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
pl = d.product_facts({"type": "personal_loan", "days_late": 16, "past_due": 1320.0, "instalment_amount": 440.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
cl = d.product_facts({"type": "car_loan", "days_late": 7, "past_due": 140.0, "instalment_amount": 46.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
check("PUB-12 credit card band", cc["band"], "late")
check("PUB-12 credit card attempts", cc["attempts_allowed"], 2)
check("PUB-12 personal loan attempts", pl["attempts_allowed"], 2)
check("PUB-12 car loan band", cl["band"], "early")
check("PUB-12 car loan attempts", cl["attempts_allowed"], 1)

# PUB-13: credit_card days_late=4 -> early band (deadline far out, 12 business days = 2026-11-02),
# personal_loan days_late=20 -> late band (deadline 2026-10-23, the nearer one). Customer names 30
# Oct for both; only the loan needs to be capped, to 23 Oct. The card's deadline (2 Nov) is after 30
# Oct, so the card keeps 30 Oct unchanged. The minimal builder sometimes capped the wrong product
# and invented a third number (26 Oct) for it.
cc13 = d.product_facts({"type": "credit_card", "days_late": 4, "past_due": 300.0, "minimum_payment": 90.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
pl13 = d.product_facts({"type": "personal_loan", "days_late": 20, "past_due": 900.0, "instalment_amount": 300.0}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
check("PUB-13 credit card deadline (early band, after 30 Oct)", cc13["deadline"], "2026-11-02")
check("PUB-13 personal loan deadline", pl13["deadline"], "2026-10-23")
ok_cc, date_cc, _, _ = d.validate_promise("2026-10-30", 300.0, cc13["min_date"], cc13["deadline"], cc13["floor"], cc13["full"], HOLIDAYS)
ok_pl, date_pl, _, _ = d.validate_promise("2026-10-30", 900.0, pl13["min_date"], pl13["deadline"], pl13["floor"], pl13["full"], HOLIDAYS)
check("PUB-13 credit card date unchanged", (ok_cc, date_cc), (True, "2026-10-30"))
check("PUB-13 personal loan date capped", date_pl, "2026-10-23")

# PUB-06: credit_card, floor offered once when full amount is too much; accepted floor on a Friday.
pub06 = d.product_facts({"type": "credit_card", "days_late": 12, "past_due": 2125.10, "minimum_payment": 637.5}, TODAY, BANDS, HOLIDAYS, FLOOR_FIELD)
check("PUB-06 floor", pub06["floor"], 637.5)
check("PUB-06 attempt1_date (Fri 16 Oct)", pub06["attempt1_date"], "2026-10-16")
ok, date, amt, reason = d.validate_promise("2026-10-16", 637.5, pub06["min_date"], pub06["deadline"], pub06["floor"], pub06["full"], HOLIDAYS)
check("PUB-06 floor amount accepted unchanged", (ok, date, amt), (True, "2026-10-16", 637.5))

# PUB-02: callback window, today 2026-10-14T11:00, next business day 2026-10-15.
ok, reason = d.callback_ok("2026-10-15", "11:00", "2026-10-14T11:00", HOLIDAYS)
check("PUB-02 callback within window", (ok, reason), (True, ""))
ok, reason = d.callback_ok("2026-10-14", "09:00", "2026-10-14T11:00", HOLIDAYS)
check("callback before now on the same day is rejected", ok, False)
ok, reason = d.callback_ok("2026-10-16", "11:00", "2026-10-14T11:00", HOLIDAYS)
check("callback two business days out is rejected", ok, False)

# outcome_for_call: Brief B's aggregation rule, checked against PUB-11/12/13's expected shapes.
check("all promised -> promise", d.outcome_for_call([{"product_id": "p1"}], []), ("promise", ""))
check("one promise, two refused notes -> promise wins (PUB-12)",
      d.outcome_for_call([{"product_id": "p1"}], [{"product_id": "p2", "reason": "refused"}, {"product_id": "p3", "reason": "refused"}]),
      ("promise", ""))
check("no promise, all paid -> paid", d.outcome_for_call([], [{"product_id": "p1", "reason": "paid"}]), ("paid", ""))
check("no promise, first non-paid note's reason wins",
      d.outcome_for_call([], [{"product_id": "p1", "reason": "paid"}, {"product_id": "p2", "reason": "dispute"}]),
      ("no_promise", "dispute"))
check("single product refused (Brief A shape, PUB-09-like) -> no_promise/refused",
      d.outcome_for_call([], [{"product_id": "p1", "reason": "refused"}]), ("no_promise", "refused"))

if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("All datefns checks passed.")
