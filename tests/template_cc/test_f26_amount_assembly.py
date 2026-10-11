"""F26: spoken amounts are assembled in code: fragments over turns are joined, a cut-off self-correction is not taken."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13"
NOW = "2031-03-05T10:00"  # a Wednesday


def build(features, mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def setup(v):
    rows = json.loads(v["products"]) if isinstance(v["products"], str) else v["products"]
    return rows[0]


def test_fragments_are_joined_across_turns_and_lint_is_clean():
    wf, pol, api = build(BASE + ",f26_amount_assembly")
    assert compiler.lint(wf, pol, api)["errors"] == []
    v = after_setup_and_facts(wf, pol, NOW, 1)
    r = setup(v)
    top = float(r["past_due"])
    whole = str(int(top))
    hi, lo = whole[:-2], whole[-2:]
    assert int(hi) < r["floor"] and int(lo) < r["floor"]  # both pieces alone are below the floor
    one = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text=hi, offer_date_text="today"))
    assert one["amt_frag"] == hi and one["cp_route"] != "confirm"
    two = run_code(wf, "promise_check", dict(one, offer_intent="proposes", offer_amount_text=lo, offer_date_text=""))
    assert two["cp_route"] == "confirm" and two["rb_amount"] == str(float(whole)) or two["rb_amount"] == str(round(float(whole), 2))


def test_a_cut_off_correction_is_not_taken_as_the_amount():
    wf, pol, _ = build(BASE + ",f26_amount_assembly")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    top = int(float(setup(v)["past_due"]))
    out = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text=f"{top}, sorry, I", offer_date_text="today"))
    assert out["cp_route"] != "confirm" and "amount" in out["say_line"].lower()
    done = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text=f"{top}, sorry, I mean {top - 1}",
                                              offer_date_text="today"))
    assert done["cp_route"] == "confirm" and str(top - 1) in done["rb_amount"]  # a finished correction: the last amount wins


def test_missing_or_unverified_parameter_leaves_the_p6_workflow_unchanged():
    def drop(pol):
        pol["_source"] = {k: v for k, v in pol["_source"].items() if not k.startswith("/product_types")}

    def unverified(pol):
        pol["_source"] = {k: ("UNVERIFIED: " + v if k.startswith("/product_types") else v) for k, v in pol["_source"].items()}

    for mut in (drop, unverified):
        ref, _, _ = build(BASE, mut)
        wf, _, _ = build(BASE + ",f26_amount_assembly", mut)
        assert json.dumps(wf, sort_keys=True) == json.dumps(ref, sort_keys=True), mut.__name__


def test_flag_off_leaves_no_placeholder_in_the_code():
    wf, _, _ = build(BASE)
    assert "AMTCUT" not in json.dumps(wf) and "AMTJOIN" not in json.dumps(wf)


def test_no_join_when_a_correction_marker_sits_between_the_fragments():
    wf, pol, _ = build(BASE + ",f26_amount_assembly")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    r = setup(v)
    whole = str(int(float(r["past_due"])))
    hi, lo = whole[:-2], whole[-2:]
    one = run_code(wf, "promise_check", dict(v, offer_intent="proposes", offer_amount_text=hi, offer_date_text="today"))
    two = run_code(wf, "promise_check", dict(one, offer_intent="proposes", offer_amount_text=f"no, {lo}", offer_date_text=""))
    assert two["cp_route"] != "confirm"  # the last stated amount stays: below the floor, so it is not recorded
    three = run_code(wf, "promise_check", dict(one, offer_intent="proposes", offer_amount_text=lo, offer_date_text=""))
    assert three["cp_route"] == "confirm"
