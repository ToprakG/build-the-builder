"""F15: terms named in the same turn as "no thanks" to the support offer are checked and read back; the resume prompt stays
inside the product list."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"
BASE = "f1,f2,f3,f4,f9,f11"


def build(features):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def test_terms_win_over_declining_in_the_hardship_node():
    wf, pol, api = build(BASE + ",f15")
    h = wf["nodes"]["hardship"]
    names = {e["name"] for e in h["extraction_schema"]}
    assert {"hs_choice", "offer_intent", "offer_amount_text", "offer_date_text"} <= names
    order = [t["target_node"] for t in h["transitions"]]
    assert order[0] == "promise_check" and order.index("promise_check") < order.index("hardship_mark")
    names_terms = next(t for t in h["transitions"] if t["condition"]["conditions"][0]["operand"] == "names_terms")
    assert names_terms["target_node"] == "promise_check"
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_the_terms_go_through_the_normal_check_and_read_back():
    wf, pol, _ = build(BASE + ",f15")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    out = run_code(wf, "promise_check", dict(v, hs_choice="declines", offer_intent="proposes",
                                             offer_amount_text="three hundred", offer_date_text="Friday the seventh of March"))
    assert out["cp_route"] == "confirm" and out["rb_date"] == "2031-03-07"
    assert out["offer_intent"] == "none"


def test_flag_off_keeps_the_old_hardship_node():
    wf, _, _ = build(BASE)
    h = wf["nodes"]["hardship"]
    assert "offer_intent" not in {e["name"] for e in h["extraction_schema"]}
    assert [t["target_node"] for t in h["transitions"]][0] != "promise_check"
    assert next(t for t in h["transitions"] if t["condition"]["conditions"][0]["operand"] == "names_terms")["target_node"] == "resume"


def test_resume_prompt_is_scoped_to_the_listed_products():
    wf, _, _ = build(BASE + ",f15")
    assert "exactly {{product_count}} product" in wf["nodes"]["negotiate_resume"]["on_enter"]["prompt"]
    off, _, _ = build(BASE)
    assert "product_count" not in off["nodes"]["negotiate_resume"]["on_enter"]["prompt"]
    v = after_setup_and_facts(wf, json.loads((GOLD / "brief-a.policy.json").read_text()), NOW, 1)
    assert v["product_count"] == "1"


def test_terms_named_while_accepting_help_from_the_human_offer_are_checked():
    wf, pol, api = build(BASE + ",f15")
    h = wf["nodes"]["human_offer"]
    assert {"human_choice", "offer_intent", "offer_amount_text", "offer_date_text"} <= {e["name"] for e in h["extraction_schema"]}
    order = [t["target_node"] for t in h["transitions"]]
    assert order.index("transfer_human") < order.index("promise_check") < order.index("human_back")  # insists still wins
    assert compiler.lint(wf, pol, api)["errors"] == []
    off, _, _ = build(BASE)
    assert "offer_intent" not in {e["name"] for e in off["nodes"]["human_offer"]["extraction_schema"]}
    assert "card details" in wf["nodes"]["negotiate"]["task_message"]
