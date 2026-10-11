import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import policy_critical as pc  # noqa: E402


def status(rows, field):
    return next(r["status"] for r in rows if r["field"] == field)


BASE = {"multi_product": False, "max_products": 1, "close_rule": {"kind": "first_non_paid"}, "note_map": {}}


def test_note_map_is_not_a_miss_when_the_brief_says_notes_are_never_used():
    cfg = dict(BASE, note_map={"refused": "declined"})
    brief = "The agent never calls add_note in this process."
    assert status(pc.compare(BASE, cfg, brief, None), "note_map") == "N/A"
    assert status(pc.compare(BASE, cfg, "We don't use `add_note` in this pilot.", None), "note_map") == "N/A"
    assert status(pc.compare(BASE, cfg, "Use add_note for every refusal.", None), "note_map") == "MISS"


def test_note_map_is_not_a_miss_when_the_api_has_no_add_note():
    cfg = dict(BASE, note_map={"refused": "declined"})
    assert status(pc.compare(BASE, cfg, "", '{"paths": {"/get_account": {}}}'), "note_map") == "N/A"
    assert status(pc.compare(BASE, cfg, "", '{"paths": {"/add_note": {}}}'), "note_map") == "MISS"


def test_a_non_empty_wrong_note_map_stays_a_miss():
    got = dict(BASE, note_map={"refused": "x"})
    cfg = dict(BASE, note_map={"refused": "declined"})
    assert status(pc.compare(got, cfg, "never add_note", '{"paths": {}}'), "note_map") == "MISS"


def test_single_and_first_non_paid_are_equivalent_for_one_product():
    got, cfg = dict(BASE, close_rule={"kind": "single"}), dict(BASE)
    assert status(pc.compare(got, cfg), "close_rule.kind") == "EQUIV"
    multi = dict(got, multi_product=True)
    assert status(pc.compare(multi, cfg), "close_rule.kind") == "MISS"
    other = dict(BASE, close_rule={"kind": "priority", "order": ["a"]})
    assert status(pc.compare(other, cfg), "close_rule.kind") == "MISS"


def test_cap_at_deadline_and_id_decline_are_critical_between_two_policies():
    a = dict(BASE, attempt_plan=[{"date": "deadline", "cap_at_deadline": True}, {"date": "today", "cap_at_deadline": True}],
             ask_limits={"id_decline": 1})
    b = dict(BASE, attempt_plan=[{"date": "deadline", "cap_at_deadline": True}, {"date": "today", "cap_at_deadline": False}],
             ask_limits={"id_decline": 2})
    assert pc.diff(a, b) == ["ask_limits.id_decline", "attempt_plan.cap_at_deadline"]
    assert pc.diff(a, dict(a)) == []


def test_a_config_without_them_does_not_make_them_misses():
    got = dict(BASE, attempt_plan=[{"cap_at_deadline": True}], ask_limits={"id_decline": 1})
    rows = pc.compare(got, dict(BASE))
    assert "attempt_plan.cap_at_deadline" not in {r["field"] for r in rows} and "ask_limits.id_decline" not in {r["field"] for r in rows}
