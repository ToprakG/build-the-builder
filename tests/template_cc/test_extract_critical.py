"""The verifier's disputed-field list: which pointers count as critical."""
from builder.template_cc import extract


def test_cap_at_deadline_and_id_decline_are_critical():
    assert extract.is_critical("/attempt_plan/0/cap_at_deadline") and extract.is_critical("/attempt_plan/12/cap_at_deadline")
    assert extract.is_critical("/ask_limits/id_decline")


def test_other_plan_and_limit_fields_stay_non_critical_and_the_old_ones_still_critical():
    assert not extract.is_critical("/attempt_plan/0/amount") and not extract.is_critical("/ask_limits/right_party")
    assert not extract.is_critical("/attempt1")
    for ptr in ("/multi_product", "/bands/0/attempts", "/callback/start", "/tools/create_promise/amount_unit"):
        assert extract.is_critical(ptr)
