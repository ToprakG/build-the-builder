"""final_vote: deterministic rules, majority, verifier on a tie. Invented text only; no model call."""

from builder.template_cc.final_vote import majority, rule_cap_at_deadline, rule_id_decline, vote

from builder.template_cc.final_vote import rule_cap_at_deadline as rule_cap  # noqa: E402,F401

BRIEF = """1. If the customer does not want to answer the security questions, explain once that they protect the account.
A second refusal closes the call.
2. A promise date must be today or later and not after the deadline.
   - Attempt 1: propose the last business day of this week. If that day is after the deadline, propose the deadline.
   - Attempt 2: propose the floor amount on the deadline.
"""


def pol(cap=(True, True), decline=1):
    return {"attempt_plan": [{"date": "end_of_week_roll", "cap_at_deadline": cap[0]}, {"date": "deadline", "cap_at_deadline": cap[1]}],
            "ask_limits": {"id_decline": decline}}


def test_id_decline_rule():
    assert rule_id_decline(BRIEF) == 1                                    # the second refusal closes -> one re-ask
    assert rule_id_decline("The first refusal of a security question ends the call.") == 0
    assert rule_id_decline("The caller may decline the identity question twice; the third refusal ends the call.") == 2
    assert rule_id_decline("Greet the customer. Ask the identity question.") is None


def test_cap_rule_attempt_sentence_and_general_sentence():
    assert rule_cap_at_deadline(BRIEF, 1) is True            # "if that day is after the deadline, propose the deadline"
    assert rule_cap_at_deadline("Attempt 1: propose Friday. No later than the deadline.", 1) is True
    assert rule_cap_at_deadline("Attempt 1: propose Friday, even if it is after the deadline.", 1) is False
    only_general = "A promise date must be a business day and not after the deadline.\nAttempt 1: propose Friday."
    assert rule_cap_at_deadline(only_general, 1) is True
    assert rule_cap_at_deadline("Attempt 1: propose Friday.", 1) is None


def test_majority():
    assert majority([True, True, False]) == (True, True)
    assert majority([2, 2, 2]) == (2, True)
    assert majority([True, False]) == (None, False)
    assert majority([None, None]) == (None, False)


def test_rule_beats_majority_and_deadline_attempt_is_fixed():
    out, log = vote([pol((False, False), 2), pol((False, False), 2), pol((True, False), 2)], BRIEF)
    assert out["ask_limits"]["id_decline"] == 1                                    # the second refusal closes -> 1
    assert [s["cap_at_deadline"] for s in out["attempt_plan"]] == [True, True]     # rule for 1; attempt 2 is on the deadline
    assert any("rule" in l for l in log)


def test_majority_when_the_brief_is_silent():
    silent = "Greet the customer."
    out, log = vote([pol((True, True), 2), pol((False, True), 2), pol((True, True), 1)], silent)
    assert out["attempt_plan"][0]["cap_at_deadline"] is True and out["ask_limits"]["id_decline"] == 2
    assert any("majority" in l for l in log)


def test_tie_goes_to_the_verifier_and_needs_a_quote_in_the_brief():
    silent = "Explain the reason. The customer may decline up to a point."
    calls = []

    def good(ptr, cands):
        calls.append((ptr, cands))
        return 2, "The customer may decline up to a point"

    out, log = vote([pol((True, True), 1), pol((True, True), 2)], silent, good)
    assert out["ask_limits"]["id_decline"] == 2 and calls and any("verifier" in l for l in log)

    out, log = vote([pol((True, True), 1), pol((True, True), 2)], silent, lambda p, c: (2, "words the brief never says"))
    assert out["ask_limits"]["id_decline"] == 1 and any("first extraction" in l for l in log)
    out, _ = vote([pol((True, True), 1), pol((True, True), 2)], silent, None)
    assert out["ask_limits"]["id_decline"] == 1
