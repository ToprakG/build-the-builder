"""The deadline offered after a vague day is band-by-days_late, N business days after the call date, holidays skipped (any policy)."""
import datetime
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import runtime as RT

from .helpers import after_setup_and_facts, fake_products, run_code, call_vars

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
FLAGS = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13,f24_day_once"


def build(mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    return compiler.compile(pol, api, features=FLAGS)[0], pol


def offered(wf, pol, now, days_late):
    af = pol["account_fields"]
    v = run_code(wf, "setup", call_vars(pol, now))
    rows = fake_products(pol, 1)
    rows[0][af["days_late"]] = days_late
    v["products_raw"] = rows
    v = run_code(wf, "facts", v)
    v1 = run_code(wf, "promise_check", dict(v, offer_intent="vague"))
    return run_code(wf, "promise_check", dict(v1, offer_intent="none"))["say_line"]


def expect(pol, now, days_late):
    band = next(b for b in pol["bands"] if b["min_days"] <= days_late <= b["max_days"])
    d = RT.add_bd(datetime.datetime.fromisoformat(now).date(), band["deadline_business_days"], {datetime.date.fromisoformat(h) for h in pol["holidays"]})
    return f"{d.strftime('%A')} {d.day} {d.strftime('%B')}"


def test_deadline_matches_band_business_days_and_holidays_on_a_grid():
    wf, pol = build()
    for now in ("2031-03-05T10:00", "2031-03-07T10:00", "2031-03-31T16:00"):  # Wed, Fri, month end
        for b in pol["bands"]:
            for d in (b["min_days"], b["max_days"]):
                assert expect(pol, now, d) in offered(wf, pol, now, d), (now, d)


def test_a_holiday_inside_the_window_is_skipped():
    def add_holiday(pol):
        pol["holidays"] = sorted(set(pol["holidays"]) | {"2031-03-07"})

    wf, pol = build(add_holiday)
    now, d = "2031-03-05T10:00", pol["bands"][0]["min_days"]
    plain = expect({**pol, "holidays": [h for h in pol["holidays"] if h != "2031-03-07"]}, now, d)
    assert expect(pol, now, d) != plain and expect(pol, now, d) in offered(wf, pol, now, d)
