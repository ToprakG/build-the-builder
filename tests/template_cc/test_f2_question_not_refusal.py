"""F2: a question back is not a refusal; only a repeat (or an explicit refusal) counts."""
import copy
import json
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"


def build(limit, features):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    pol = copy.deepcopy(pol)
    pol.setdefault("ask_limits", {})["id_decline"] = limit
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


@pytest.mark.parametrize("features", ["f2", "f1,f2"])
def test_question_then_repeat_then_refusal_limit(features):
    wf, pol, api = build(1, features)
    assert compiler.lint(wf, pol, api)["errors"] == []
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v["id_intent"] = "asks_question"
    out = run_code(wf, "id_refusal_count", v)  # the first question: answered, not counted
    assert out["id_route"] == "ask" and out["id_q_pending"] == "1" and out["id_declines"] == "0" and out["id_intent"] == "other"
    out["id_intent"] = "asks_question"
    out = run_code(wf, "id_refusal_count", out)  # asks again instead of answering: counted, explain once
    assert out["id_route"] == "explain" and out["id_declines"] == "1" and out["id_q_pending"] == "0"
    out["id_intent"] = "refuses"
    out = run_code(wf, "id_refusal_count", out)  # limit 1: the second counted decline closes
    assert out["id_route"] == "decline" and out["id_declines"] == "2"


def test_refusal_counts_immediately_and_limit_zero_has_no_explain_step():
    wf, pol, _ = build(1, "f2")
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v["id_intent"] = "refuses"
    out = run_code(wf, "id_refusal_count", v)
    assert out["id_route"] == "explain" and out["id_declines"] == "1"
    wf0, pol0, _ = build(0, "f2")
    assert "identity_explain" not in wf0["nodes"]
    v = run_code(wf0, "setup", call_vars(pol0, "2031-03-05T10:00"))
    v["id_intent"] = "refuses"
    assert run_code(wf0, "id_refusal_count", v)["id_route"] == "decline"


def test_answer_after_a_question_resets_pending():
    wf, pol, _ = build(1, "f1,f2")
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v.update(id_q_pending="1", id_intent="answer", id_answer_text="the tenth of April, nineteen eighty")
    assert run_code(wf, "id_check", v)["id_q_pending"] == "0"


def test_graph_shape():
    wf, _, _ = build(1, "f2")
    n = wf["nodes"]["identity"]
    ext = {e["name"]: e["enum_values"] for e in n["extraction_schema"]}
    assert ext["id_intent"] == ["answer", "asks_question", "refuses", "other"]
    assert any(t["target_node"] == "id_refusal_count" for t in n["transitions"])
    assert n["tools"] and wf["nodes"]["id_refusal_count"]["node_type"] == "code"


def test_a_question_after_an_explanation_counts_and_explaining_happens_once():
    wf, pol, _ = build(2, "f1,f2")  # a limit of 2 still explains only once
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v["id_intent"] = "asks_question"
    out = run_code(wf, "id_refusal_count", v)
    assert out["id_route"] == "ask"
    out["id_intent"] = "asks_question"
    out = run_code(wf, "id_refusal_count", out)
    assert out["id_route"] == "explain" and out["id_declines"] == "1"
    out["id_intent"] = "asks_question"  # asks again after the explanation: counted at once
    out = run_code(wf, "id_refusal_count", out)
    assert out["id_route"] == "decline" and out["id_declines"] == "2"
