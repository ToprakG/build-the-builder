"""Offline checks behind the f3 GATE regression hypotheses (no model, no Freya).

1. f25_dispute_wins: routing of "already paid" vs a dispute claim is decided by the extracted `side` value; the code after it
   (side_mark) is the same with and without f25, and f25 only appends one sentence to the side description.
2. f18_side_resume: a third-party first reply takes the same path to third_party / the callback step as before f18; f18 only adds an
   `always` fallback on the opening node for a first reply that sets no `who1`.
"""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import prompts

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
PREV = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13,f15"
M1 = ",".join(x for x in (REPO / "M1_FLAGS.txt").read_text().replace("\n", "").split(",") if x)


def build(features, name="brief-a"):
    pol = json.loads((GOLD / f"{name}.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(copy.deepcopy(pol), api, features=features)
    return wf.get("workflow", wf)


def code_of(wf, nid):
    c = wf["nodes"][nid]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    return [l for l in c.splitlines() if l.strip() and "CFG = json.loads(" not in l]


def test_f25_only_appends_one_sentence_and_keeps_the_already_paid_value():
    base = prompts.side_desc(dispute_first=False)
    f25 = prompts.side_desc(dispute_first=True)
    assert f25.startswith(base) and len(f25) > len(base)
    assert "paid_already = they already paid this past-due amount" in base and "paid_already = they already paid this past-due amount" in f25
    added = f25[len(base):]
    assert "closed or paid off in the past" in added and "dispute" in added
    assert "paid_already" not in added                      # f25 does not name the already-paid value at all


def test_dispute_and_already_paid_are_split_by_the_same_code_with_and_without_f25():
    before, after = build(PREV), build(M1)
    a, b = code_of(before, "side_mark"), code_of(after, "side_mark")
    assert a == b                                           # no routing code was changed by f25
    assert any("status = 'dispute' if str(vars.get('side')) == 'dispute' else 'paid'" in l.strip() for l in b)


def test_third_party_first_reply_keeps_its_path():
    before, after = build(PREV), build(M1)
    for nid in ("third_party", "mark_nr_callback", "mark_nr_no_callback"):
        assert json.dumps(before["nodes"][nid], sort_keys=True) == json.dumps(after["nodes"][nid], sort_keys=True), nid

    def targets(wf, nid, who):
        out = []
        for t in wf["nodes"][nid]["transitions"]:
            conds = json.dumps(t["condition"])
            if f'"{who}"' in conds:
                out.append(t["target_node"])
        return out
    assert targets(before, "setup", "someone_else") == targets(after, "setup", "someone_else") == ["third_party"]
    assert targets(before, "opening_ask2", "someone_else") == targets(after, "opening_ask2", "someone_else") == ["third_party"]


def test_f18_only_adds_the_fallback_when_who1_is_not_set():
    before, after = build(PREV), build(M1 + ",f18_opening_fallback")
    tb = [(t["target_node"], json.dumps(t["condition"])) for t in before["nodes"]["opening"]["transitions"]]
    ta = [(t["target_node"], json.dumps(t["condition"])) for t in after["nodes"]["opening"]["transitions"]]
    assert ta[:len(tb)] == [(("human_gate" if x[0] == "human_offer" else x[0]), x[1]) for x in tb] or ta[1:len(tb)] == tb[1:]
    assert ta[-1][0] == "opening_resume" and "always" in ta[-1][1]     # last: only reached when who1 is not set
    assert any(c.get("variable") == "who1" for c in [json.loads(ta[1][1])["conditions"][0]])   # the who1 branch comes first


def _norm_targets(wf, nid):
    swap = {"human_gate": "human_offer"}          # the human step is renamed by another flag; not part of this switch
    return [(swap.get(t["target_node"], t["target_node"]), json.dumps(t["condition"], sort_keys=True)) for t in wf["nodes"][nid]["transitions"]]


def test_opening_fallback_off_keeps_an_empty_who1_in_opening_as_without_f18():
    p6 = build(PREV)
    off = build(M1 + ",-f18_opening_fallback")
    on = build(M1 + ",f18_opening_fallback")
    # the empty-who1 case: with the switch OFF the opening node has no always-fallback, exactly the transitions of the pre-f18 build
    assert _norm_targets(off, "opening") == _norm_targets(p6, "opening")
    assert "opening_resume" not in off["nodes"]
    assert off["nodes"]["opening_ask2"].get("on_enter") == p6["nodes"]["opening_ask2"].get("on_enter")
    # ON (m1): the fallback exists and is the LAST transition, reached only when who1 is not set
    assert "opening_resume" in on["nodes"]
    assert _norm_targets(on, "opening")[:-1] == _norm_targets(p6, "opening")
    assert _norm_targets(on, "opening")[-1][0] == "opening_resume"
    # the rest of f18 stays on with the switch off: only the opening differs
    assert sorted(n for n in on["nodes"] if n not in off["nodes"]) == ["opening_resume"]


def test_the_fallback_is_opt_in_and_default_off():
    from builder.template_cc import features as F

    assert "f18_opening_fallback" not in F.parse(M1)               # the final's flags: off
    assert "f18_opening_fallback" not in F.parse("all")
    assert "f18_opening_fallback" in F.parse(M1 + ",f18_opening_fallback")
    assert "f18_opening_fallback" not in F.parse(M1 + ",f18_opening_fallback,-f18_opening_fallback")
    assert "f18_side_resume" in F.parse(M1)                        # f18 itself stays on


def test_default_m1_build_has_no_opening_resume_node():
    wf = build(M1)
    assert "opening_resume" not in wf["nodes"] and "opening_ask2" in wf["nodes"]
