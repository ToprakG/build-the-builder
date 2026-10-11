"""policy_fixups: id_decline from the refusal count (id_decline = closing refusal number - 1), cap_at_deadline from explicit phrases."""

import copy

from builder.template_cc.policy_fixups import apply, cap_at_deadline, id_decline


def test_id_decline_from_the_closing_refusal_number():
    # 0: the first refusal closes; 1: the second closes; 2: the third closes
    assert id_decline("If the customer refuses a security question, close the call as declined. The first refusal ends the call.")[0] == 0
    assert id_decline("If the caller does not want to answer, explain once why we ask. A second refusal closes the call.")[0] == 1
    assert id_decline("The caller may decline the identity question twice; the third refusal ends the call.")[0] == 2
    assert id_decline("After the second refusal of a verification question, end the call.")[0] == 1


def test_id_decline_ask_once_more_and_supporting_explain():
    assert id_decline("When they decline the security question, ask once more.")[0] == 1
    assert id_decline("When they decline the security question, ask twice more.")[0] == 2
    # "explain once" is only supporting evidence: with no refusal count it counts only next to "still declines ... close"
    assert id_decline("If they do not want to answer, explain once why we ask. If they still decline, close the call.")[0] == 1
    assert id_decline("If they do not want to answer, explain once why we ask.") is None


def test_id_decline_silent_or_conflicting_brief_is_left_alone():
    assert id_decline("Greet the customer and state the debt.") is None
    assert id_decline("A second refusal of the security question closes the call. A third refusal of the identity question ends it.") is None


def test_cap_at_deadline_phrases():
    b = "- Attempt 1: propose Friday. If that day is after the deadline, propose the deadline.\n- Attempt 2: propose the floor on the deadline."
    assert cap_at_deadline(b, 1)[0] is True and "after the deadline" in cap_at_deadline(b, 1)[1]
    assert cap_at_deadline(b, 2) is None
    assert cap_at_deadline("Attempt 1: propose Friday, even if it is after the deadline.", 1)[0] is False
    assert cap_at_deadline("A promise date must be a business day and no later than the deadline.\nAttempt 1: propose Friday.", 1)[0] is True
    assert cap_at_deadline("Attempt 1: propose Friday.", 1) is None


def test_apply_writes_value_and_quote_and_leaves_silent_fields_alone():
    brief = "If the caller does not want to answer, explain once. A second refusal closes the call.\nAttempt 1: propose Friday. No later than the deadline."
    pol = {"ask_limits": {"id_decline": 2, "right_party": 2},
           "attempt_plan": [{"date": "end_of_week", "cap_at_deadline": False}, {"date": "deadline", "cap_at_deadline": False}]}
    ch = apply(pol, brief)
    assert pol["ask_limits"] == {"id_decline": 1, "right_party": 2}
    assert pol["attempt_plan"][0]["cap_at_deadline"] is True and pol["attempt_plan"][1]["cap_at_deadline"] is False
    assert pol["_source"]["/ask_limits/id_decline"] == "A second refusal closes the call."
    assert [c["field"] for c in ch] == ["ask_limits.id_decline", "attempt_plan[0].cap_at_deadline"]
    silent = copy.deepcopy(pol)
    assert apply(silent, "Greet the customer.") == [] and silent == pol


def test_right_party_is_the_total_number_of_asks():
    from builder.template_cc.policy_fixups import right_party

    assert right_party("3. Only a yes counts as confirmation. Hello does not. Ask once more. If it is still unclear after the second ask, close.")[0] == 2
    assert right_party("Check the person: if the reply is not a confirmation, ask twice more, then close the call.")[0] == 3
    assert right_party("Ask whether we are speaking with the customer. Do not repeat the question.") is None
    assert right_party("Greet the customer. State the debt.") is None
    # a repeat of a different question (the security questions) is not the right-party ask
    assert right_party("If the customer refuses to answer a security question, ask once more.") is None


def test_apply_sets_right_party_with_its_quote():
    pol = {"ask_limits": {"right_party": 1, "id_decline": 1}}
    brief = "Only a yes counts as confirmation. Ask once more. If it is still unclear after the second ask, close."
    ch = apply(pol, brief)
    assert pol["ask_limits"]["right_party"] == 2 and pol["ask_limits"]["id_decline"] == 1
    assert any(c["field"] == "ask_limits.right_party" and c["before"] == 1 and c["after"] == 2 for c in ch)
    assert pol["_source"]["/ask_limits/right_party"] == "Ask once more."
