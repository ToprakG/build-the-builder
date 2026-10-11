"""F7: routing is code. The model extracts one enum per node; a router with a precedence table decides."""
import copy
import json
import re
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"
FEATS = "f1,f2,f3,f4,f9,f11,f12,f13,f8,f7"
ROUTED = ("third_party", "identity", "identity_explain", "negotiate", "negotiate_resume", "hardship", "human_offer", "promise_confirm")


def build(features=FEATS, name="brief-a", edit=None):
    pol = json.loads((GOLD / f"{name}.policy.json").read_text())
    if edit:
        pol = copy.deepcopy(pol)
        edit(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, book = compiler.compile(pol, api, features=features)
    return wf, pol, api, book


def test_no_model_decided_routing_is_left():
    wf, pol, api, _ = build()
    for nid, n in wf["nodes"].items():
        for t in n.get("transitions", []):
            assert t.get("condition", {}).get("operator") != "matches_intent", (nid, t["target_node"])
            assert not t.get("is_tool_call"), (nid, t["target_node"])
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_every_routed_node_has_the_router_last_and_the_extraction_field():
    wf, _, _, _ = build()
    for nid in ROUTED:
        n = wf["nodes"][nid]
        assert n["transitions"][-1]["target_node"] == f"route_{nid}", nid
        field = next(e for e in n["extraction_schema"] if e["name"] == "turn_intent")
        assert field["enum_values"][0] == "none" and "asks_robot" in field["enum_values"]
    for g in wf["global_nodes"].values():
        assert set(ROUTED) <= set(g["excluded_nodes"])


def test_the_refusal_is_a_side_value_not_a_tool():
    wf, _, _, _ = build()
    side = next(e for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side")
    assert "refused_1" in side["enum_values"] and "refused_1 .." in side["description"]
    t = next(t for t in wf["nodes"]["negotiate"]["transitions"] if t["target_node"] == "refused_1")
    assert t["condition"]["conditions"][0] == {"variable": "side", "operator": "equals", "operand": "refused_1"}
    assert "CALL" not in wf["nodes"]["negotiate"]["task_message"]
    assert "transition_to" not in json.dumps(wf)


def _target(wf, nid, route_to):
    for t in wf["nodes"][f"route_{nid}"]["transitions"]:
        if any(c["variable"] == "route_to" and c["operand"] == route_to for c in t["condition"].get("conditions") or []):
            return t["target_node"]
    for t in wf["nodes"][f"route_{nid}"]["transitions"]:
        if t["condition"].get("operator") == "always":
            return t["target_node"]


@pytest.mark.parametrize("it,expect", [("asks_robot", "ai_negotiate"), ("asks_scam", "scam_negotiate"), ("wants_human", "human_offer"),
                                       ("goodbye", "wrap"), ("finished", "wrap"), ("none", "negotiate_resume")])
def test_router_targets(it, expect):
    wf, pol, _, _ = build()
    v = after_setup_and_facts(wf, pol, NOW, 1)
    out = run_code(wf, "route_negotiate", dict(v, turn_intent=it, side="none"))
    assert out["turn_intent"] == "none"
    assert _target(wf, "negotiate", out["route_to"]) == expect
    assert wf["nodes"]["ai_negotiate"]["on_enter"]["text"] == pol["lines"]["ai"]["text"]


def test_precedence_decides_between_two_signals_and_comes_from_the_policy():
    wf, pol, _, _ = build()
    v = after_setup_and_facts(wf, pol, NOW, 1)
    both = dict(v, turn_intent="asks_robot", side="human")  # the customer asks if this is a robot and for a person
    assert run_code(wf, "route_negotiate", both)["route_to"] == "wants_human"
    wf2, pol2, _, _ = build(edit=lambda p: p.update(precedence=["asks_robot", "wants_human", "asks_scam", "finished", "goodbye"]))
    v2 = after_setup_and_facts(wf2, pol2, NOW, 1)
    assert run_code(wf2, "route_negotiate", dict(v2, turn_intent="asks_robot", side="human"))["route_to"] == "asks_robot"


def test_human_offer_does_not_route_to_itself_and_no_human_queue_is_ignored():
    wf, _, _, _ = build()
    assert not any(c["operand"] == "wants_human" for t in wf["nodes"]["route_human_offer"]["transitions"]
                   for c in t["condition"].get("conditions") or [])
    wf, pol, _, _ = build(edit=lambda p: p["queue_map"].update(human=None))
    v = after_setup_and_facts(wf, pol, NOW, 1)
    assert run_code(wf, "route_negotiate", dict(v, turn_intent="wants_human"))["route_to"] == "none"


def test_resets_are_inside_their_enum_and_zone_targets_still_point_at_their_text():
    wf, _, _, book = build()
    code = wf["nodes"]["route_negotiate"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    assert re.search(r"vars\['turn_intent'\] = 'none'", code)
    for t in book.targets:
        cur = wf
        for part in t["pointer"].strip("/").split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        if t["kind"] == "text":
            assert cur == book.zones[t["parts"][0]["zone"]]["text"], t["pointer"]


def test_off_changes_nothing():
    wf, _, _, _ = build("f1,f2,f3,f4,f9,f11,f12,f13,f8")
    assert "route_negotiate" not in wf["nodes"] and any(t.get("is_tool_call") for t in wf["nodes"]["identity"]["transitions"])
    assert "turn_intent" not in json.dumps(wf)


def test_the_nodes_own_decisions_come_before_the_router():
    wf, _, _, _ = build()
    order = [t["target_node"] for t in wf["nodes"]["human_offer"]["transitions"]]
    assert order.index("transfer_human") < order.index("route_human_offer")  # "insists" + "thanks" must transfer
    order = [t["target_node"] for t in wf["nodes"]["promise_confirm"]["transitions"]]
    assert order.index("promise_write") < order.index("route_promise_confirm")  # "yes, thanks, bye" must record
