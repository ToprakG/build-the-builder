"""F20: once-rules are code flags (said_once)."""
from builder.template_cc.compiler import runtime as RT


def test_said_once_is_true_once_per_line_id():
    v = {}
    assert RT.said_once(v, "id_reason") is True
    assert v["said_id_reason"] == "1"
    assert RT.said_once(v, "id_reason") is False
    assert RT.said_once(v, "hardship_sympathy") is True  # another line is independent


def test_said_once_honours_the_key_the_verifier_reads():
    assert RT.said_once({"said_hardship_sympathy": "1"}, "hardship_sympathy") is False


import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"


def build(features, limit=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    pol = copy.deepcopy(pol)
    if limit is not None:
        pol["ask_limits"]["human_offer"] = limit
    wf, _ = compiler.compile(pol, ApiModel.load(REPO / "tools" / "openapi.json"), features=features)
    return wf, pol


def targets(wf):
    return {(n, t["target_node"]) for n, node in list(wf["nodes"].items()) + list(wf["global_nodes"].items())
            for t in node.get("transitions", [])}


def test_every_way_into_the_help_offer_goes_through_the_gate():
    wf, _ = build("f3,f7,f8,f20")
    assert not {n for n, t in targets(wf) if t == "human_offer" and n != "human_gate"}
    assert ("negotiate", "human_gate") in targets(wf) and ("human_gate", "human_offer") in targets(wf)
    off, _ = build("f3,f7,f8")
    assert "human_gate" not in off["nodes"] and ("negotiate", "human_offer") in targets(off)


def test_the_offer_is_made_once_then_a_request_transfers():
    wf, pol = build("f3,f7,f8,f20")
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v = run_code(wf, "human_gate", dict(v, turn_intent="wants_human"))
    assert v["gate"] == "offer" and v["turn_intent"] == "none"  # a stale intent does not come back
    assert run_code(wf, "human_gate", v)["gate"] == "transfer"


def test_a_zero_limit_transfers_at_once_and_the_sympathy_flag_is_said_once():
    wf, pol = build("f3,f7,f8,f20", limit=0)
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    assert run_code(wf, "human_gate", v)["gate"] == "transfer"
    wf, pol = build("f8,f13,f20")
    v = run_code(wf, "hardship_mark", run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00")))
    assert v["said_hardship_sympathy"] == "1"
