"""F11: the model never fills create_promise; code validates what was said, the customer confirms a read-back."""
import json
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, run_code, run_post

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
FEATS = "f1,f2,f3,f4,f9,f11"
NOW = "2031-03-05T10:00"  # a Wednesday


def build(name="brief-a", api_path=None, features=FEATS):
    pol = json.loads((GOLD / f"{name}.policy.json").read_text())
    api = ApiModel.load(api_path or (REPO / "tools" / "openapi.json"))
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


@pytest.fixture(scope="module")
def a():
    wf, pol, api = build()
    return wf, pol, api, after_setup_and_facts(wf, pol, NOW, 1)


def check(wf, v, amount, date, intent="proposes"):
    return run_code(wf, "promise_check", dict(v, offer_intent=intent, offer_amount_text=amount, offer_date_text=date))


def test_shape_no_model_facing_promise_tool_and_lint(a):
    wf, pol, api, _ = a
    for nid in ("negotiate", "negotiate_resume"):
        assert not wf["nodes"][nid].get("tools")
        assert any(t["target_node"] == "promise_check" for t in wf["nodes"][nid]["transitions"])
    assert {"promise_check", "promise_confirm", "promise_write", "after_promise"} <= set(wf["nodes"])
    pw = wf["nodes"]["promise_write"]
    body = pw["pre_actions"][0]["function"]["parameters"]["properties"]["body"]["properties"]
    assert {"{{rb_pid}}", "{{rb_amount}}", "{{rb_date}}"} <= {x.get("default") for x in body.values()}
    assert pw["response_mapping"]
    assert compiler.lint(wf, pol, api)["errors"] == []
    ext = {e["name"]: e.get("enum_values") for e in wf["nodes"]["promise_confirm"]["extraction_schema"]}
    assert ext["rb_answer"] == ["yes", "no", "change", "other"]


def test_valid_words_go_to_a_read_back(a):
    wf, pol, _, v = a
    o = check(wf, v, "three hundred", "Friday the seventh of March")
    assert o["cp_route"] == "confirm" and o["rb_pid"] == "p1" and o["rb_date"] == "2031-03-07" and float(o["rb_amount"]) == 300.0
    assert "300" in o["say_line"] and "7 March" in o["say_line"] and "(2031" not in o["say_line"]
    assert o["offer_intent"] == "none" and o["offer_amount_text"] == "" and o["rb_answer"] == "other"
    o = check(wf, v, "the whole amount", "the sixth of March")
    assert o["cp_route"] == "confirm" and float(o["rb_amount"]) == 412.5


def test_cents_are_sent_as_integers():
    wf, pol, api = build("f3-kestrel", REPO / "testbed/fake_briefs/f3-kestrel/tools.openapi.json")
    v = after_setup_and_facts(wf, pol, NOW, 1)
    o = check(wf, v, "one hundred and fifty", "tomorrow")
    if o["cp_route"] == "confirm":
        assert o["rb_amount"] == "15000"


@pytest.mark.parametrize("amount,date,needle", [
    ("one hundred", "the sixth of March", "cannot record less than"),
    ("nine hundred", "the sixth of March", "cannot record more than"),
    ("three hundred", "the eighth of March", "not a business day"),  # a Saturday
    ("three hundred", "the first of April", "latest date I can record"),
])
def test_invalid_terms_come_back_with_a_reason(a, amount, date, needle):
    wf, _, _, v = a
    o = check(wf, v, amount, date)
    assert o["cp_route"] == "back" and needle in o["say_line"] and not o.get("rb_amount")
    assert o["offer_intent"] == "none"


def test_a_vague_day_is_asked_once_then_the_latest_date_is_offered(a):
    wf, pol, _, v = a
    o = check(wf, v, "three hundred", "")
    assert o["cp_route"] == "back" and "which day" in o["say_line"].lower()
    n = int(pol["ask_limits"].get("vague_date", 1))
    for _ in range(n):
        o = check(wf, o, "three hundred", "")
    assert "latest date I can record" in o["say_line"]


def test_confirm_routes_and_write_node(a):
    wf, _, _, v = a
    tr = {t["target_node"]: t["condition"]["conditions"][0]["operand"] for t in wf["nodes"]["promise_confirm"]["transitions"]
          if t["condition"].get("conditions") and t["condition"]["conditions"][0]["variable"] == "rb_answer"}
    assert tr == {"promise_write": "yes", "promise_check": "change", "negotiate_resume": "no"}
    assert wf["nodes"]["promise_confirm"]["on_enter"]["text"] == "{{say_line}}"


def test_post_hook_recorded_retry_then_failed(a):
    wf, _, _, v = a
    v = dict(v, rb_pid="p1")
    out, _ = run_post(wf, "promise_write", "create_promise", v, {"ok": True, "promise_id": "pr1"})
    assert out["cp_state"] == "recorded" and out["say_line"] and out["promises_n"] == "1"
    assert json.loads(out["pstate"])["p1"] == "promised"
    bad = {"ok": False}
    out, _ = run_post(wf, "promise_write", "create_promise", v, bad)
    assert out["cp_state"] == "retry"
    out, _ = run_post(wf, "promise_write", "create_promise", out, bad)
    assert out["cp_state"] == "failed" and "technical problem" in out["say_line"] and "recorded" not in out["say_line"].lower()
    assert json.loads(out["pstate"])["p1"] != "promised"
    out2 = run_code(wf, "after_promise", out)
    assert out2["neg_state"] in ("open", "done")


def test_every_reset_value_is_inside_its_enum(a):
    wf, _, _, _ = a
    import re
    enums = {}
    for n in wf["nodes"].values():
        for e in n.get("extraction_schema") or []:
            if e.get("enum_values"):
                enums.setdefault(e["name"], set()).update(e["enum_values"])
    for nid in ("setup", "promise_check", "human_back", "side_mark", "hardship_mark", "discount_reply", "id_check", "id_refusal_count"):
        node = wf["nodes"].get(nid)
        if not node:
            continue
        code = node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        for var, val in re.findall(r"vars\['(\w+)'\] = '([^']*)'", code):
            if var in enums and var != "id_answer_text":
                assert val in enums[var], (nid, var, val)


def test_multi_product_resets_side_product_inside_its_enum():
    import re
    pol = json.loads((GOLD / "brief-b.policy.json").read_text())
    assert pol.get("multi_product")
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=FEATS)
    enum = next(e["enum_values"] for e in wf["nodes"]["negotiate"]["extraction_schema"] if e["name"] == "side_product")
    assert "none" in enum
    for nid in ("setup", "side_mark", "hardship_mark", "stop_count", "discount_reply", "resume"):
        code = wf["nodes"][nid]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        for val in re.findall(r"vars\['side_product'\] = '([^']*)'", code):
            assert val in enum, (nid, val)


def test_an_unreadable_amount_is_asked_once_per_offer_never_guessed(a):
    wf, _, _, v = a
    o = check(wf, v, "", "the sixth of March", "accepts_offer")
    assert o["cp_route"] == "back" and "which amount" in o["say_line"].lower() and not o.get("rb_amount")
    o = check(wf, o, "um, whatever works", "the sixth of March", "accepts_offer")  # still unreadable: not asked again
    assert o["cp_route"] == "back" and o["say_line"] == "" and not o.get("rb_amount") and o["amt_ask_n"] == "2"
    o = check(wf, o, "three hundred", "the sixth of March")  # a readable amount resets the question for the next offer
    assert o["cp_route"] == "confirm" and o["amt_ask_n"] == "0"
    o = check(wf, o, "", "the sixth of March", "accepts_offer")
    assert "which amount" in o["say_line"].lower()


def _with_offer(mode):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mode:
        pol["vague_date_offer"] = mode
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=FEATS)
    return wf, pol


@pytest.mark.parametrize("mode,expect", [("attempt1", "date I can record is"), ("deadline", "latest date I can record"),
                                         (None, "latest date I can record")])
def test_vague_date_offer_follows_the_policy_or_defaults_to_the_latest_date(mode, expect):
    wf, pol = _with_offer(mode)
    v = after_setup_and_facts(wf, pol, NOW, 1)
    row = json.loads(v["products"])[0]
    o = v
    for _ in range(int(pol["ask_limits"].get("vague_date", 1)) + 1):
        o = check(wf, o, "three hundred", "when things settle")
    assert expect in o["say_line"] and o["cp_route"] == "back"
    if mode == "attempt1":
        assert "latest" not in o["say_line"] and row["att1"]


def test_the_missing_policy_field_is_traced():
    from builder.template_cc import run
    ev = []

    class T:
        def event(self, kind, **d): ev.append((kind, d))
        def line(self, text): ev.append(("line", text))
        def check(self, *a, **k): pass

    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    run.compile_checked(pol, api, "lifecycle", T(), "t", frozenset({"f11_promise_typed"}))
    assert any("vague_date_offer missing, defaulting to latest date" in str(e) for e in ev)
    ev.clear()
    run.compile_checked({**pol, "vague_date_offer": "deadline"}, api, "lifecycle", T(), "t", frozenset({"f11_promise_typed"}))
    assert not any("vague_date_offer" in str(e) for e in ev)


def test_a_date_said_first_is_kept_until_the_amount_comes(a):
    wf, _, _, v = a
    o = check(wf, v, "", "the sixth of March")  # date only: the amount is asked
    assert o["cp_route"] == "back" and "which amount" in o["say_line"].lower() and o["pend_date"] == "the sixth of March"
    o = check(wf, o, "three hundred", "")  # the next message has only the amount
    assert o["cp_route"] == "confirm" and o["rb_date"] == "2031-03-06" and float(o["rb_amount"]) == 300.0
    assert o["pend_amt"] == "" and o["pend_date"] == ""


def test_an_amount_said_first_is_kept_and_an_invalid_part_is_dropped(a):
    wf, _, _, v = a
    o = check(wf, v, "three hundred", "")
    assert o["pend_amt"] == "three hundred" and o["pend_date"] == ""
    o = check(wf, o, "", "the eighth of March")  # a Saturday: the date is dropped, the amount stays
    assert o["cp_route"] == "back" and "not a business day" in o["say_line"] and o["pend_amt"] == "three hundred" and o["pend_date"] == ""
    o = check(wf, o, "", "the tenth of March")
    assert o["cp_route"] == "confirm" and float(o["rb_amount"]) == 300.0
    below = check(wf, v, "one hundred", "the sixth of March")  # a too-small amount is dropped, the date stays
    assert below["pend_amt"] == "" and below["pend_date"] == "the sixth of March"


def test_promise_check_consumes_the_signals_that_sent_the_customer_here(a):
    wf, _, _, v = a
    o = check(wf, dict(v, side="human", hs_choice="declines", human_choice="continues"), "three hundred", "Friday the seventh of March", "accepts_offer")
    assert o["cp_route"] == "confirm" and (o["side"], o["hs_choice"], o["human_choice"]) == ("none", "none", "none")


def test_free_text_extractions_have_a_lower_confidence_bar_than_enums(a):
    wf, _, _, _ = a
    for nid, names in (("identity", ("id_answer_text",)), ("identity_explain", ("id_answer_text",)),
                       ("negotiate", ("offer_amount_text", "offer_date_text"))):
        fields = {e["name"]: e["min_confidence"] for e in wf["nodes"][nid]["extraction_schema"]}
        for n in names:
            assert fields[n] == 0.3, (nid, n)
        assert all(c == 0.6 for k, c in fields.items() if k not in names and "text" not in k), nid


def test_a_vague_day_is_an_extracted_signal_and_the_code_owns_the_reply(a):
    wf, pol, _, v = a
    for nid in ("negotiate", "negotiate_resume"):
        ext = next(e for e in wf["nodes"][nid]["extraction_schema"] if e["name"] == "offer_intent")
        assert "vague" in ext["enum_values"] and "no exact day" in ext["description"]
        t = next(t for t in wf["nodes"][nid]["transitions"] if t["target_node"] == "promise_check")
        assert {c["operand"] for c in t["condition"]["conditions"]} == {"proposes", "accepts_offer", "vague"}
    assert "the system asks for the exact day" in wf["nodes"]["negotiate"]["task_message"]
    o = check(wf, v, "", "", "vague")  # "I'll pay in a while": ask once, then offer the latest date
    assert o["cp_route"] == "back" and "exact" in o["say_line"]
    for _ in range(int(pol["ask_limits"].get("vague_date", 1))):
        o = check(wf, o, "", "", "vague")
    assert "latest date I can record" in o["say_line"]
