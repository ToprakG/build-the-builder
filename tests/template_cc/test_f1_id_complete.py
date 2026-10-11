"""F1: no verify_identity call until the answer is complete."""
import json
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import runtime as R

from .helpers import call_vars, run_code, run_post

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
F1 = frozenset({"f1_id_complete"})


@pytest.mark.parametrize("kind,text,value,complete", [
    ("digits:4", "double zero seven seven", "0077", True),
    ("digits:4", "five oh", "", False),
    ("digits:4", "five oh two three", "5023", True),
    ("digits:4", "1636, sorry, I mean 5023", "5023", True),
    ("digits:4", "it is four three two one", "4321", True),
    ("date", "the tenth of April", "", False),
    ("date", "the tenth of April, two thousand", "2000-04-10", True),
    ("date", "30/01/1984", "1984-01-30", True),
    ("date", "thirtieth of January nineteen eighty four", "1984-01-30", True),
    ("year", "nineteen sixty eight", "1968", True),
    ("year", "nineteen", "", False),
    ("alnum_code", "L S six, two H Q", "LS6 2HQ", True),
])
def test_answer_from_speech(kind, text, value, complete):
    got, ok = R.answer_from_speech(kind, text)
    assert ok == complete
    if complete:
        assert got == value


def test_kind_from_description():
    assert R.kind_from_description("phone_last4 For phone_last4: four digits.") == "digits:4"
    assert R.kind_from_description("date_of_birth the date as YYYY-MM-DD") == "date"


@pytest.fixture(scope="module")
def built():
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=F1)
    return wf, pol, api


def test_compile_shape_and_lint(built):
    wf, pol, api = built
    n = wf["nodes"]
    assert not n["identity"].get("tools") and not n["identity_explain"].get("tools")
    assert {"id_check", "id_verify", "id_refusal_count"} <= set(n)
    assert n["identity"]["on_enter"] == {"type": "tts", "text": "{{id_say}}"}  # the questions are spoken by the system, not the model
    assert n["identity_explain"]["on_enter"] == {"type": "tts", "text": "{{id_say}}"}
    assert "{{id_say}}" in n["identity"]["task_message"]
    pre = n["id_verify"]["pre_actions"][0]
    assert pre["function"]["name"] == "verify_identity"
    body = pre["function"]["parameters"]["properties"]["body"]["properties"]
    assert {"{{id_send_field}}", "{{id_send_value}}"} <= {v.get("default") for v in body.values()}
    assert n["id_verify"]["node_type"] == "api_request" and n["id_verify"]["response_mapping"]
    assert compiler.lint(wf, pol, api)["errors"] == []


def _vars(wf, pol):
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    assert v["id_q"] and v["id_say"] and v["id_intent"] == "other"
    return v


def test_id_check_partial_then_complete(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    v["id_q"] = "phone_last4"
    v.update(id_intent="answer", id_answer_text="five oh")
    out = run_code(wf, "id_check", v)
    assert out["id_partial"] == "five oh" and out["id_route"] == "ask" and out["id_intent"] == "other"
    assert out["id_answer_text"] == "" and out["id_say"]
    out.update(id_intent="answer", id_answer_text="two three")
    out = run_code(wf, "id_check", out)
    assert out["id_route"] == "send" and out["id_send_value"] == "5023" and out["id_send_field"] == "phone_last4"
    assert out["id_partial"] == ""


def test_id_check_date_and_empty(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    v.update(id_intent="answer", id_answer_text="the tenth of April, nineteen eighty")
    out = run_code(wf, "id_check", v)
    assert out["id_route"] == "send" and out["id_send_value"] == "1980-04-10"
    v = _vars(wf, pol)
    v.update(id_intent="answer", id_answer_text="")
    out = run_code(wf, "id_check", v)
    assert out["id_route"] == "ask" and out["id_say"].endswith(pol["identity"]["prompts"][pol["identity"]["questions"][0]])


def test_id_verify_post_writes_say_and_next_question(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    qs = pol["identity"]["questions"]
    v["id_last_field"] = qs[0]
    data = {"ok": True, "verified": True, "all_verified": False, "wrong_answers_left": 3}
    out, _ = run_post(wf, "id_verify", "verify_identity", v, data)
    assert out["id_state"] == "next" and out["id_q"] == qs[1]
    assert pol["identity"]["prompts"][qs[1]] in out["id_say"] and "(field:" not in out["id_say"]
    data = {"ok": True, "verified": False, "all_verified": False, "wrong_answers_left": 2}
    out, _ = run_post(wf, "id_verify", "verify_identity", v, data)
    prompt = pol["identity"]["prompts"][qs[0]]
    assert out["id_state"] == "retry" and out["id_say"] == "That answer does not match. " + prompt and out["id_q"] == v["id_q"]
    data = {"ok": False}  # unreadable: not counted as wrong
    out, _ = run_post(wf, "id_verify", "verify_identity", v, data)
    assert out["id_say"] == "Sorry, I could not read that. " + prompt
    out, _ = run_post(wf, "id_verify", "verify_identity", v, {}, status="error", code=500)
    assert out["id_say"] == "The check did not go through. " + prompt
    data = {"ok": True, "verified": False, "all_verified": False, "wrong_answers_left": 2, "hint": "day_month_swapped"}
    out, _ = run_post(wf, "id_verify", "verify_identity", v, data)
    assert out["id_say"] == pol["lines"]["hint_retry"]["text"]


def _ask(wf, v, text):
    v = dict(v, id_intent="answer", id_answer_text=text)
    return run_code(wf, "id_check", v)


def test_a_fresh_full_answer_wins_over_an_old_partial(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    v["id_q"] = "phone_last4"
    out = _ask(wf, v, "six nine")
    assert out["id_route"] == "ask"
    out = _ask(wf, out, "the traffic was awful but it is two eight six nine")
    assert out["id_route"] == "send" and out["id_send_value"] == "2869"
    v = _vars(wf, pol)
    out = _ask(wf, v, "May twenty-third")
    out = _ask(wf, out, "May twenty-third nineteen eighty")  # repeats the whole answer instead of the rest
    assert out["id_send_value"] == "1980-05-23"


def test_a_never_loops_more_than_twice_then_sends_what_it_has(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    prompt = pol["identity"]["prompts"][pol["identity"]["questions"][0]]
    out = _ask(wf, v, "14 July")
    assert out["id_route"] == "ask" and out["id_inc"] == "1" and out["id_partial"] == "14 July"
    out = _ask(wf, out, "9 October")
    assert out["id_route"] == "ask" and out["id_inc"] == "2" and out["id_partial"] == "" and out["id_say"].endswith(prompt)
    out = _ask(wf, out, "1 September")
    assert out["id_route"] == "send" and out["id_inc"] == "0" and out["id_send_value"]


def test_b_day_and_month_only_after_a_swap_hint_reuses_the_year(built):
    wf, pol, _ = built
    v = _vars(wf, pol)
    out = _ask(wf, v, "zero four zero one nineteen seventy six")  # spoken digits: day, month, year
    assert out["id_send_value"] == "1976-01-04" and out["id_route"] == "send"
    out = _ask(wf, dict(out, id_state="retry"), "the first of April")  # the bank said the day and month are swapped
    assert out["id_route"] == "send" and out["id_send_value"] == "1976-04-01"


@pytest.mark.parametrize("text,value", [
    ("May twenty-third nineteen eighty", "1980-05-23"), ("tenth of April nineteen eighty", "1980-04-10"),
    ("the twenty first of May nineteen fifty eight", "1958-05-21"), ("May twenty first, two thousand and three", "2003-05-21"),
    ("23 May 1980", "1980-05-23")])
def test_dates_with_the_day_after_the_month(text, value):
    assert R.answer_from_speech("date", text) == (value, True)


def test_the_ai_question_line_is_in_the_identity_task_when_the_policy_has_one(built):
    wf, pol, _ = built
    ai = pol["lines"]["ai"]["text"]
    assert ai and ai.split("{")[0].strip()[:20] in wf["nodes"]["identity"]["task_message"]


def test_an_answer_text_alone_goes_to_id_check(built):
    wf, _, _ = built
    t = next(t for t in wf["nodes"]["identity"]["transitions"] if t["target_node"] == "id_check")
    conds = {(c["variable"], c["operator"], c.get("operand")) for c in t["condition"]["conditions"]}
    assert ("id_intent", "equals", "answer") in conds and ("id_answer_text", "not_equals", "") in conds
    assert any(x["target_node"] == "id_check" for x in wf["nodes"]["identity_explain"]["transitions"])


def test_a_robot_question_in_the_first_reply_is_answered_before_the_first_question(built):
    wf, pol, _ = built
    assert "ai_asked" in {e["name"] for e in wf["nodes"]["opening"]["extraction_schema"]}
    v = call_vars(pol, "2031-03-05T10:00")
    first = run_code(wf, "setup", v)["id_say"]
    out = run_code(wf, "setup", dict(v, ai_asked="yes"))
    assert out["id_say"] == pol["lines"]["ai"]["text"] + " " + first
    assert run_code(wf, "setup", dict(v, ai_asked="no"))["id_say"] == first
