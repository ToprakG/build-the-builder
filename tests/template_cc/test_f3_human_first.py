"""F3: a request for a person wins at every stage, also before identity is verified."""
import copy
import json
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"


def build(features="f3", routes=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if routes is not None:
        pol = copy.deepcopy(pol)
        pol["routes"] = routes
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


@pytest.mark.parametrize("features", ["f3", "f1,f2,f3"])
def test_human_transition_is_first_in_every_early_stage(features):
    wf, pol, api = build(features)
    assert "human" in pol["queue_map"] and pol["queue_map"]["human"]
    for nid in ("opening", "opening_ask2", "identity", "identity_explain"):
        first = wf["nodes"][nid]["transitions"][0]
        assert first["target_node"] == "human_offer", nid
    assert compiler.lint(wf, pol, api)["errors"] == []
    names = {e["name"] for e in wf["nodes"]["opening"]["extraction_schema"]}
    assert {"who1", "who_wants_human"} <= names <= {"who1", "who_wants_human", "ai_asked"}
    assert "wants_human" in next(e for e in wf["nodes"]["identity"]["extraction_schema"] if e["name"] == "id_intent")["enum_values"]


def test_off_when_the_policy_says_so_or_flag_off():
    wf, _, _ = build("f3", routes={"human_before_verify": False})
    for nid in ("opening", "opening_ask2", "identity", "identity_explain"):
        assert all(t["target_node"] != "human_offer" for t in wf["nodes"][nid]["transitions"]), nid
    wf, _, _ = build("none")
    assert all(t["target_node"] != "human_offer" for t in wf["nodes"]["identity"]["transitions"])


def test_human_offer_goes_back_to_where_the_call_was():
    wf, pol, _ = build("f3")
    ho = wf["nodes"]["human_offer"]
    assert next(t for t in ho["transitions"] if t["condition"]["conditions"][0]["operand"] == "continues")["target_node"] == "human_back"
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v.update(who1="customer", phase="pre")
    assert run_code(wf, "human_back", v)["back_to"] == "identity"
    v.update(phase="products")
    assert run_code(wf, "human_back", v)["back_to"] == "products"
    assert run_code(wf, "human_back", {"who1": "unclear", "phase": "pre"})["back_to"] == "opening"


def test_wrap_with_no_products_and_the_human_key_gives_the_human_outcome():
    wf, pol, _ = build("f3")
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    assert v["phase"] == "pre"
    v["transfer_key"] = "human"
    out = run_code(wf, "wrap", v)
    assert out["close_key"] == "tr_human" and out["closing_line"]
    out2 = run_code(wf, "wrap", {"transfer_key": "human"})  # even when setup never ran (a request at the very first reply)
    assert out2["close_key"] == "tr_human"
