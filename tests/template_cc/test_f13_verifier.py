"""F13: every path into the close passes through the verifier (the node named wrap)."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"


def build(features):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def test_shape_every_wrap_target_goes_through_the_verifier():
    wf, pol, api = build("f8,f13")
    n = wf["nodes"]
    assert n["wrap"]["node_type"] == "code" and {"wrap_close", "say_missing"} <= set(n)
    assert [t["target_node"] for t in n["wrap"]["transitions"]] == ["say_missing", "wrap_close"]
    assert n["say_missing"]["on_enter"]["text"] == "{{miss_line}}" and n["say_missing"]["passthrough_default_transition"] == "wrap"
    assert any(t["target_node"] == "wrap" for t in n["negotiate"]["transitions"])  # the tool name transition_to_wrap stays
    assert compiler.lint(wf, pol, api)["errors"] == []
    off, _, _ = build("f8")
    assert "wrap_close" not in off["nodes"]


def test_a_missing_required_line_is_said_once_then_the_call_closes():
    wf, pol, _ = build("f8,f13")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    v = dict(v, side="hardship")
    # the hardship step ran (its line was spoken): nothing missing
    ok = run_code(wf, "hardship_mark", v)
    assert ok["said_hardship_sympathy"] == "1"
    assert run_code(wf, "wrap", ok)["vc_route"] == "go"
    # a hardship close without the flag: say it once
    lost = dict(ok, said_hardship_sympathy="0")
    out = run_code(wf, "wrap", lost)
    assert out["vc_route"] == "say" and out["miss_line"] == pol["lines"]["hardship_sympathy"]["text"]
    assert run_code(wf, "wrap", out)["vc_route"] == "go"  # never twice


def test_nothing_missing_goes_straight_through_and_every_product_gets_a_status():
    wf, pol, _ = build("f8,f13")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    out = run_code(wf, "wrap", dict(v, phase="products"))
    assert out["vc_route"] == "go"
    assert json.loads(out["pstate"])["p1"] == "refused"  # an open product is closed as refused


def test_the_requirement_needs_the_fixed_line_flag():
    wf, pol, _ = build("f13")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    out = run_code(wf, "wrap", dict(v, phase="products", side="hardship", close_key="np_hardship"))
    assert out["vc_route"] == "go"
