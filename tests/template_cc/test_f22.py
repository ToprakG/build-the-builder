"""F22 (fixed lines before verification and with a third party, banned words checked in code)."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f7,f8,f9,f11_promise_typed,f12_slot_summary,f13_verifier,f15_hardship_terms"


def build(features, mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, book = compiler.compile(pol, api, features=features)
    return wf, pol, api


def test_off_is_p6_and_all_does_not_enable_them():
    for spec in ("all", BASE, "none"):
        wf, _, _ = build(spec)
        assert "tp_check" not in wf["nodes"] and wf["nodes"]["third_party"]["on_enter"] == {"type": "llm"}
        assert "schedule_callback" in [t["function"]["name"] for t in wf["nodes"]["third_party"]["tools"]]


def test_no_policy_parameter_means_p6():
    wf, _, _ = build(BASE + ",f22", lambda p: (p.update(forbidden_before_verify=[]), p.update(callback={})))
    assert "tp_check" not in wf["nodes"]


def test_f22_cb_validates_in_code_and_lints():
    wf, pol, api = build(BASE + ",f22")
    assert compiler.lint(wf, pol, api)["errors"] == []
    tp = wf["nodes"]["third_party"]
    assert tp["on_enter"]["text"] == "{{tp_say}}" and "tools" not in tp
    assert "schedule_callback" in json.dumps(wf["nodes"]["tp_book"]) and "{{cb_date}}" in json.dumps(wf["nodes"]["tp_book"])
    assert not any(t.get("target_node") == "third_party" for n in ("opening", "opening_ask2") for t in wf["nodes"][n]["transitions"])
    v = run_code(wf, "setup", call_vars(pol, "2026-10-14T11:00"))
    v = run_code(wf, "tp_init", v)
    assert v["tp_say"] and v["tp_n"] == "0"
    # inside the window: booked through the api node
    ok = run_code(wf, "tp_check", {**v, "cb_day_in": "2026-10-14", "cb_time_in": "3pm", "turn_intent": "none"})
    assert ok["tp_route"] == "book" and ok["cb_date"] == "2026-10-14" and ok["cb_time"] == "15:00"
    # earlier than now, after the end, a day outside: re-ask once, then give up
    for day, time in (("2026-10-14", "10:30"), ("2026-10-14", "20:00"), ("2026-10-20", "12:00")):
        bad = run_code(wf, "tp_check", {**v, "cb_day_in": day, "cb_time_in": time, "turn_intent": "none"})
        assert bad["tp_route"] == "ask" and "We can call back" in bad["tp_say"] and "cb_date" not in bad
        again = run_code(wf, "tp_check", {**bad, "cb_day_in": day, "cb_time_in": time})
        assert again["tp_route"] == "giveup"
    # words for the day, bounds included, a router intent is left to the router
    assert run_code(wf, "tp_check", {**v, "cb_day_in": "tomorrow", "cb_time_in": "19:00", "turn_intent": "none"})["tp_route"] == "book"
    assert run_code(wf, "tp_check", {**v, "cb_day_in": "", "cb_time_in": "", "turn_intent": "asks_scam"})["tp_route"] == "router"
    assert run_code(wf, "tp_check", {**v, "cb_day_in": "", "cb_time_in": ""})["tp_route"] == "ask"


def test_f22_cb_same_day_off_rejects_today():
    wf, pol, _ = build(BASE + ",f22", lambda p: p["callback"].update(same_day=False))
    v = run_code(run_code(wf, "setup", call_vars(pol, "2026-10-14T11:00")) and wf, "tp_init", run_code(wf, "setup", call_vars(pol, "2026-10-14T11:00")))
    assert run_code(wf, "tp_check", {**v, "cb_day_in": "2026-10-14", "cb_time_in": "15:00"})["tp_route"] == "ask"
    assert run_code(wf, "tp_check", {**v, "cb_day_in": "2026-10-15", "cb_time_in": "15:00"})["tp_route"] == "book"


def test_f22_every_turn_goes_through_code_and_banned_words_fall_back():
    wf, pol, api = build(BASE + ",f22")
    assert compiler.lint(wf, pol, api)["errors"] == []
    tos = [t["target_node"] for t in wf["nodes"]["third_party"]["transitions"]]
    assert tos.count("tp_check") == 2          # gave_time and none: the model never speaks in this step
    v = run_code(wf, "setup", call_vars(pol, "2026-10-14T11:00"))
    assert run_code(wf, "tp_init", v)["tp_say"].startswith("Could you tell me when")
    assert "collections" not in run_code(wf, "tp_init", v)["tp_say"].lower()

    def poison(p):
        p["lines"]["third_party_ask"] = {"text": "Please pay the debt of {first_name}", "verbatim": True}
    wf2, pol2, _ = build(BASE + ",f22", poison)
    v2 = run_code(wf2, "tp_init", run_code(wf2, "setup", call_vars(pol2, "2026-10-14T11:00")))
    assert v2["tp_say"] == "Could you tell me when they can be reached?"
    assert "tp_check" in [t["target_node"] for t in wf["nodes"]["third_party"]["transitions"]]
