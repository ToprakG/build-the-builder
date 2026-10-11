"""F8: lines the policy gives are spoken as fixed text by the node that says them."""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"


def build(features, edit=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if edit:
        pol = copy.deepcopy(pol)
        edit(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def test_hardship_speaks_the_policy_sympathy_line_once():
    wf, pol, api = build("f8")
    h = wf["nodes"]["hardship"]
    assert h["on_enter"] == {"type": "tts", "text": pol["lines"]["hardship_sympathy"]["text"]}
    assert h["on_enter_first_entry_only"] is True
    assert "Do not say it again" in h["task_message"]
    assert compiler.lint(wf, pol, api)["errors"] == []
    off, _, _ = build("none")
    assert off["nodes"]["hardship"]["on_enter"] == {"type": "llm"}


def test_global_answers_and_the_close_use_policy_text():
    wf, pol, _ = build("f8")
    assert wf["global_nodes"]["g_scam"]["on_enter"]["text"] == pol["lines"]["scam"]["text"]
    assert wf["global_nodes"]["g_ai"]["on_enter"]["type"] == "tts"


def test_paid_notice_is_a_fixed_say_line_and_dispute_has_none():
    wf, pol, _ = build("f8")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    out = run_code(wf, "side_mark", dict(v, side="paid_already", side_product=""))
    assert out["say_line"] == pol["lines"]["paid_reply"]["text"]
    out = run_code(wf, "side_mark", dict(v, side="dispute", side_product=""))
    assert out["say_line"] == ""
    assert "word for word" in wf["nodes"]["negotiate_resume"]["on_enter"]["prompt"]


def test_no_discount_line_from_the_policy_else_generic():
    """The key is not in policy_check.LINE_KEYS yet (adding it would change every completed policy), so the extractor cannot
    write it; a policy that has it is honoured."""
    def add(p):
        p["lines"]["no_discount"] = {"text": "We cannot lower the amount.", "verbatim": True}
    wf, pol, _ = build("f4,f8", add)
    v = after_setup_and_facts(wf, pol, NOW, 1)
    assert run_code(wf, "discount_reply", dict(v, side="discount"))["say_line"] == "We cannot lower the amount."
    wf, pol, _ = build("f4,f8")
    assert run_code(wf, "discount_reply", dict(v, side="discount"))["say_line"] == "I am not able to reduce the amount."
    wf, pol, _ = build("f4", add)  # flag off: the generic line
    assert run_code(wf, "discount_reply", dict(v, side="discount"))["say_line"] == "I am not able to reduce the amount."


def test_a_sympathy_line_without_a_question_gets_the_support_offer_when_there_is_a_support_queue():
    def plain(p):
        p["lines"]["hardship_sympathy"] = {"text": "I am very sorry to hear that.", "verbatim": False}
    wf, pol, _ = build("f8", plain)
    assert pol["queue_map"].get("support")
    text = wf["nodes"]["hardship"]["on_enter"]["text"]
    assert text.startswith("I am very sorry to hear that.") and "support team?" in text
    assert "Offer to connect" not in wf["nodes"]["hardship"]["task_message"]

    def no_support(p):
        plain(p)
        p["queue_map"]["support"] = None
    wf2, _, _ = build("f8", no_support)
    assert wf2["nodes"]["hardship"]["on_enter"]["text"] == "I am very sorry to hear that."
    assert "Do not offer a transfer" in wf2["nodes"]["hardship"]["task_message"]
