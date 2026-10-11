"""F4: a request to knock something off the amount is not a restructuring request."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"


def build(features):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, book = compiler.compile(pol, api, features=features)
    return wf, pol, api, book


def test_side_enum_transition_and_reset():
    wf, pol, api, book = build("f4")
    for nid in ("negotiate", "negotiate_resume"):
        side = next(e for e in wf["nodes"][nid]["extraction_schema"] if e["name"] == "side")
        assert "discount" in side["enum_values"] and "NOT restructuring" in side["description"]
        t = next(t for t in wf["nodes"][nid]["transitions"] if t["target_node"] == "discount_reply")
        assert t["condition"]["conditions"][0] == {"variable": "side", "operator": "equals", "operand": "discount"}
    assert "{{say_line}}" in wf["nodes"]["negotiate_resume"]["on_enter"]["prompt"]
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    assert v["say_line"] == ""
    v["side"] = "discount"
    out = run_code(wf, "discount_reply", v)
    assert out["side"] == "none" and out["say_line"]
    assert compiler.lint(wf, pol, api)["errors"] == []


def test_other_exits_clear_the_line():
    wf, pol, _, _ = build("f4")
    v = run_code(wf, "setup", call_vars(pol, "2031-03-05T10:00"))
    v.update(say_line="stale", side="hardship")
    assert run_code(wf, "hardship_mark", v)["say_line"] == ""


def test_off_means_no_discount_value():
    wf, _, _, _ = build("none")
    side = next(e for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side")
    assert "discount" not in side["enum_values"] and "discount_reply" not in wf["nodes"]


def test_hardship_wins_over_a_discount_request_in_the_wording():
    wf, _, _, _ = build("f4")
    side = next(e for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side")
    assert "job loss, it is hardship" in side["description"]
    orig = next(e for e in build("none")[0]["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side")
    assert side["description"].replace(side["description"][side["description"].index("discount = "):side["description"].index("human = ")], "") == orig["description"]
