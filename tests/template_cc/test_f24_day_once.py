"""F24 (rule E): the exact day is asked once. After the ask, a turn with no day is 'still none' and the date is offered."""
import datetime
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import runtime as RT

from .helpers import after_setup_and_facts, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13"
NOW = "2031-03-05T10:00"  # a Wednesday


def build(features, mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def test_shape_and_lint():
    wf, pol, api = build(BASE + ",f24_day_once")
    tr = [t for t in wf["nodes"]["negotiate_resume"]["transitions"] if t["description"].startswith("the exact day was asked")]
    assert len(tr) == 1 and tr[0]["target_node"] == "promise_check"
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_the_day_is_asked_once_then_the_deadline_is_offered():
    wf, pol, _ = build(BASE + ",f24_day_once")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    first = run_code(wf, "promise_check", dict(v, offer_intent="vague"))
    assert first["day_asked"] == "1" and "which day exactly" in first["say_line"].lower()
    second = run_code(wf, "promise_check", dict(first, offer_intent="none"))  # a non-answer: still no day
    assert second["day_asked"] == "0" and "latest date" in second["say_line"].lower()


def test_missing_or_unverified_limit_leaves_the_p6_workflow_unchanged():
    def drop(pol):
        pol["_source"] = {k: v for k, v in pol["_source"].items() if "vague_date" not in k}

    def unverified(pol):
        pol["_source"] = {k: ("UNVERIFIED: " + v if "vague_date" in k else v) for k, v in pol["_source"].items()}

    for mut in (drop, unverified):
        ref, _, _ = build(BASE, mut)
        wf, _, _ = build(BASE + ",f24_day_once", mut)
        assert json.dumps(wf, sort_keys=True) == json.dumps(ref, sort_keys=True), mut.__name__


def test_deadline_arithmetic_business_days_holidays_month_end():
    hol = {datetime.date(2031, 3, 10)}  # a Monday holiday
    d = datetime.date
    assert RT.add_bd(d(2031, 3, 5), 3, set()) == d(2031, 3, 10)  # Wed + 3 business days = Mon
    assert RT.add_bd(d(2031, 3, 5), 3, hol) == d(2031, 3, 11)  # the holiday is skipped
    assert RT.add_bd(d(2031, 1, 30), 2, set()) == d(2031, 2, 3)  # across a month end and a weekend
    assert RT.add_bd(d(2031, 2, 27), 1, set()) == d(2031, 2, 28)
    assert RT.add_bd(d(2031, 2, 28), 1, set()) == d(2031, 3, 3)  # Feb has no 29th in 2031
    assert RT.add_bd(d(2031, 12, 31), 1, {d(2032, 1, 1)}) == d(2032, 1, 2)


def test_f25_dispute_wins_over_a_refusal_and_degrades_without_the_quote():
    def side(wf):
        return next(e for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side")["description"]

    wf, _, _ = build(BASE + ",f25_dispute_wins")
    assert "even if the customer also refuses" in side(wf)

    def drop(pol):
        pol["_source"] = {k: v for k, v in pol["_source"].items() if "np_dispute" not in k}

    ref, _, _ = build(BASE, drop)
    off, _, _ = build(BASE + ",f25_dispute_wins", drop)
    assert json.dumps(off, sort_keys=True) == json.dumps(ref, sort_keys=True)


def _offer_desc(wf):
    return next(e for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "offer_intent")["description"]


def test_vague_signal_marks_a_not_today_with_a_time_hint_as_vague():
    wf, pol, api = build(BASE + ",f24_day_once")  # opt-in: not on by default
    assert "is vague, not none" not in _offer_desc(wf)
    wf, pol, api = build(BASE + ",f24_day_once,f27")
    assert "is vague, not none" in _offer_desc(wf)
    assert compiler.lint(wf, pol, api)["errors"] == []
    nof24, _, _ = build(BASE)
    assert "is vague, not none" not in _offer_desc(nof24)


def test_vague_signal_needs_the_vague_day_rule_of_the_brief():
    def no_rule(pol):  # the brief has no vague-day rule: no verified limit
        pol["_source"] = {k: v for k, v in pol["_source"].items() if "vague_date" not in k}

    def unverified(pol):
        pol["_source"] = {k: ("UNVERIFIED: " + v if "vague_date" in k else v) for k, v in pol["_source"].items()}

    for mut in (no_rule, unverified):
        ref, _, _ = build(BASE, mut)
        wf, _, _ = build(BASE + ",f24_day_once,f27", mut)
        assert "is vague, not none" not in _offer_desc(wf), mut.__name__
        assert json.dumps(wf, sort_keys=True) == json.dumps(ref, sort_keys=True), mut.__name__


def test_attempt_plan_path_is_untouched_by_the_vague_signal():
    wf, pol, _ = build(BASE + ",f24_day_once,f27")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    # a plain refusal (offer_intent none, no day asked yet) is not routed to the day step: the attempt plan keeps its own trigger
    tr = [t for t in wf["nodes"]["negotiate"]["transitions"] if t["description"].startswith("the exact day was asked")]
    conds = json.dumps(tr[0]["condition"])
    assert '"day_asked"' in conds and '"operand": "1"' in conds
    # a named day after the deadline gets the deadline line, not the day question, and does not mark the day as asked
    late = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text="the full amount", offer_date_text="the 28th of March"))
    assert "latest date" in late["say_line"].lower() and late.get("day_asked") != "1"
    # a vague answer: the day is asked once, then the deadline
    first = run_code(wf, "promise_check", dict(v, offer_intent="vague"))
    assert first["day_asked"] == "1" and "which day exactly" in first["say_line"].lower()
    second = run_code(wf, "promise_check", dict(first, offer_intent="vague"))
    assert "latest date" in second["say_line"].lower()
