"""F18 side answers then the step resumes, F19 declined identity questions: explain once, close at the limit."""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import call_vars, run_code, run_post

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
NOW = "2031-03-05T10:00"
BASE = "f1,f2,f3,f7,f8"


def build(features, edit=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if edit:
        pol = copy.deepcopy(pol)
        edit(pol)
    wf, _ = compiler.compile(pol, ApiModel.load(REPO / "tools" / "openapi.json"), features=features)
    return wf, pol


def start(wf, pol, **kw):
    v = run_code(wf, "setup", call_vars(pol, NOW))
    v.update(kw)
    return v


def test_a_refusal_sentence_is_not_a_partial_answer_with_f19():
    wf, pol = build(BASE + ",f19")
    v = start(wf, pol, id_intent="refuses", id_answer_text="I am not comfortable giving that over the phone")
    assert run_code(wf, "id_check", v)["id_route"] == "refuse"
    wf0, pol0 = build(BASE)
    assert run_code(wf0, "id_check", start(wf0, pol0, id_intent="refuses", id_answer_text="I am not comfortable giving that"))["id_route"] == "ask"


def test_a_complete_answer_wins_over_a_refusal_intent():
    wf, pol = build(BASE + ",f19")
    v = start(wf, pol, id_intent="asks_question", id_answer_text="the tenth of April nineteen eighty", id_q="date_of_birth")
    assert run_code(wf, "id_check", v)["id_route"] == "send"


def test_every_refusal_counts_explain_once_then_close():
    def one(p):
        p["ask_limits"]["id_decline"] = 1
    wf, pol = build(BASE + ",f19", one)
    v = start(wf, pol, id_intent="asks_question")
    v = run_code(wf, "id_refusal_count", v)
    assert v["id_route"] == "explain" and v["id_say"].startswith(pol["lines"]["id_reason"]["text"])
    v["id_intent"] = "refuses"
    assert run_code(wf, "id_refusal_count", v)["id_route"] == "decline"
    wf2, pol2 = build(BASE)  # f2 only: the first question back is answered without counting
    v2 = run_code(wf2, "id_refusal_count", start(wf2, pol2, id_intent="asks_question"))
    assert v2["id_route"] == "ask" and v2["id_declines"] == "0"


def test_no_id_decline_parameter_means_no_explanation_node():
    def zero(p):
        p["ask_limits"]["id_decline"] = 0
    wf, pol = build(BASE + ",f19", zero)
    assert "identity_explain" not in wf["nodes"]
    assert run_code(wf, "id_refusal_count", start(wf, pol, id_intent="refuses"))["id_route"] == "decline"


def test_robot_question_with_an_answer_is_answered_and_the_answer_processed():
    wf, pol = build(BASE + ",f18")
    ai = pol["lines"]["ai"]["text"]
    assert wf["nodes"]["identity"]["transitions"][-1]["target_node"] == "id_resume"
    firsts = [t["target_node"] for t in wf["nodes"]["identity"]["transitions"]]
    assert firsts.index("id_aside") < firsts.index("id_check")
    v = start(wf, pol, turn_intent="asks_robot", id_intent="answer", id_answer_text="May twenty third", id_q="date_of_birth")
    v = run_code(wf, "id_aside", v)
    assert v["id_route"] == "answer" and v["aside_line"] == ai and v["turn_intent"] == "none"
    v = run_code(wf, "id_check", v)  # incomplete answer: the question for the rest, with the disclosure in front
    assert v["id_route"] == "ask" and v["id_say"].startswith(ai) and v["aside_line"] == ""


def test_a_pure_robot_question_resumes_the_pending_question():
    wf, pol = build(BASE + ",f18")
    v = start(wf, pol, turn_intent="asks_robot", id_intent="other", id_q="phone_last4")
    v = run_code(wf, "id_aside", v)
    assert v["id_route"] == "resume"
    v = run_code(wf, "id_resume", v)
    assert v["id_say"] == pol["lines"]["ai"]["text"] + " " + pol["identity"]["prompts"]["phone_last4"]
    v = run_code(wf, "id_resume", dict(v, aside_line=""))  # filler: the same question, no free model turn
    assert v["id_say"].endswith(pol["identity"]["prompts"]["phone_last4"])


def test_a_robot_question_with_the_last_answer_is_carried_to_the_first_debt_line():
    from .helpers import fake_products
    wf, pol = build(BASE + ",f18")
    v = start(wf, pol, aside_line="I am Freya Bank's digital assistant.")
    v["products_raw"] = fake_products(pol, 1)
    out = run_code(wf, "facts", v)
    assert out["debt_statement"].startswith("I am Freya Bank's digital assistant.") and out["aside_line"] == ""


def test_without_a_policy_line_or_the_flag_nothing_changes():
    from builder.template_cc.compiler.graph import Graph
    from builder.template_cc import features as FT
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    pol = compiler.policy_check.complete(copy.deepcopy(json.loads((GOLD / "brief-a.policy.json").read_text())), api)
    pol["lines"]["ai"] = {"text": "", "verbatim": False}  # complete() refills a missing line, so the graph is built directly
    pol["lines"]["scam"] = {"text": "", "verbatim": False}
    wf, _ = Graph(pol, api, features=FT.parse(BASE + ",f18,f18_opening_fallback")).build()
    assert "id_aside" not in wf["nodes"] and "id_resume" not in wf["nodes"]
    wf, _ = build(BASE)
    assert "id_aside" not in wf["nodes"]
    wf, _ = build("f1,f2,f18")  # no router: no turn_intent to read
    assert "id_aside" not in wf["nodes"]
    for feats in (BASE + ",f18,f19", BASE + ",f18"):
        wf, pol = build(feats)
        assert compiler.lint(wf, pol, api)["errors"] == []


def test_a_first_reply_without_a_decision_gets_the_fixed_second_ask():
    wf, pol = build(BASE + ",f18,f18_opening_fallback")
    n = wf["nodes"]
    assert n["opening"]["transitions"][-1]["target_node"] == "opening_resume"
    assert n["opening_ask2"]["on_enter"]["type"] == "tts" and pol["lines"]["ask_again"]["text"].split("{")[0] in n["opening_ask2"]["on_enter"]["text"]
    v = run_code(wf, "opening_resume", {"ai_asked": "yes"})
    assert v["aside_open"] == pol["lines"]["ai"]["text"]
    assert run_code(wf, "opening_resume", {"ai_asked": "no"})["aside_open"] == ""
    off, _ = build(BASE)
    assert "opening_resume" not in off["nodes"] and off["nodes"]["opening_ask2"]["on_enter"] == {"type": "llm"}
    f18_only, _ = build(BASE + ",f18")      # f18 without the opt-in fallback: the opening stays as without f18
    assert "opening_resume" not in f18_only["nodes"]


def test_id_decline_is_the_number_of_re_asks_after_the_first_refusal():
    """id_decline=1: refusal 1 -> explanation once + re-ask; refusal 2 -> close as declined; never a third ask."""
    def lim(n):
        def f(p):
            p["ask_limits"]["id_decline"] = n
        return f
    for n, expect in ((0, ["decline"]), (1, ["explain", "decline"]), (2, ["explain", "ask", "decline"])):
        wf, pol = build(BASE + ",f19", lim(n))
        v = start(wf, pol, id_intent="refuses", id_q="date_of_birth")
        got = []
        for _ in range(len(expect)):
            v = run_code(wf, "id_refusal_count", dict(v, id_intent="refuses"))
            got.append(v["id_route"])
            if got[-1] == "explain":
                assert v["id_say"].startswith(pol["lines"]["id_reason"]["text"])  # the explanation, said once
            if got[-1] == "ask":
                assert v["id_say"] == pol["identity"]["prompts"]["date_of_birth"]  # a re-ask has no second explanation
        assert got == expect
        assert ("identity_explain" in wf["nodes"]) == (n >= 1)


def test_after_the_explanation_the_second_non_answer_in_a_row_is_a_refusal():
    wf, pol = build(BASE + ",f18,f19")
    assert wf["nodes"]["identity_explain"]["transitions"][-1]["target_node"] == "id_resume"
    v = start(wf, pol, resume_step="identity_explain", id_q="date_of_birth")
    v = run_code(wf, "id_resume", v)
    assert v["id_route"] == "resume" and v["id_fill"] == "1"  # the first one is only re-asked
    assert run_code(wf, "id_resume", v)["id_route"] == "refuse"
    assert run_code(wf, "id_resume", dict(v, resume_step="identity", id_fill="0"))["id_route"] == "resume"  # not after the explanation
    wf, pol = build(BASE + ",f18")
    assert run_code(wf, "id_resume", start(wf, pol, resume_step="identity_explain", id_fill="5"))["id_route"] == "resume"


def test_a_scam_doubt_without_an_answer_is_a_question_back_only_with_f19():
    wf, pol = build(BASE + ",f18,f19")
    v = run_code(wf, "id_aside", start(wf, pol, turn_intent="asks_scam", id_intent="other", id_q="date_of_birth"))
    assert v["id_route"] == "refuse" and v["aside_line"] == pol["lines"]["scam"]["text"]
    out = run_code(wf, "id_refusal_count", v)
    assert out["id_route"] == "explain" and out["id_say"].startswith(pol["lines"]["scam"]["text"] + " " + pol["lines"]["id_reason"]["text"])
    wf, pol = build(BASE + ",f18")
    assert run_code(wf, "id_aside", start(wf, pol, turn_intent="asks_scam", id_intent="other"))["id_route"] == "resume"


def test_refusals_are_consecutive_and_the_explanation_is_said_once_per_call():
    def one(p):
        p["ask_limits"]["id_decline"] = 1
    wf, pol = build(BASE + ",f19", one)
    reason = pol["lines"]["id_reason"]["text"]
    v = run_code(wf, "id_refusal_count", start(wf, pol, id_intent="refuses", id_q="date_of_birth"))
    assert v["id_route"] == "explain" and v["id_say"].startswith(reason)
    v = run_code(wf, "id_check", dict(v, id_intent="answer", id_answer_text="the tenth of April nineteen eighty"))
    assert v["id_route"] == "send" and v["id_declines"] == "0"  # a complete answer ends the streak
    v = run_code(wf, "id_refusal_count", dict(v, id_intent="asks_question"))
    assert v["id_route"] == "ask" and reason not in v["id_say"]  # a later question: no second explanation, no close
    assert run_code(wf, "id_refusal_count", dict(v, id_intent="refuses"))["id_route"] == "decline"


def _no_side_lines():
    from builder.template_cc.compiler.graph import Graph
    from builder.template_cc import features as FT
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    pol = compiler.policy_check.complete(copy.deepcopy(json.loads((GOLD / "brief-a.policy.json").read_text())), api)
    pol["lines"]["ai"] = {"text": "", "verbatim": False}
    pol["lines"]["scam"] = {"text": "", "verbatim": False}
    return pol, lambda feats: Graph(pol, api, features=FT.parse(feats)).build()[0]


def test_chatter_is_a_non_answer_even_when_the_policy_has_no_side_lines():
    pol, mk = _no_side_lines()
    wf = mk(BASE + ",f18,f19")
    assert "id_aside" not in wf["nodes"] and wf["nodes"]["identity"]["transitions"][-1]["target_node"] == "id_resume"
    v = start(wf, pol, id_q="date_of_birth", id_declines="0")
    v = run_code(wf, "id_resume", v)
    assert v["id_route"] == "resume" and v["resume_step"] == "identity" and v["id_say"].startswith("Sorry")
    assert run_code(wf, "id_resume", v)["id_route"] == "refuse"  # the second non-answer in a row is a refusal
    after = run_code(wf, "id_resume", dict(v, id_fill="0", id_declines="1"))
    assert after["resume_step"] == "identity_explain" and after["id_route"] == "resume"
    # without f19 the chatter is left to the free turn, as before
    assert "id_resume" not in mk(BASE).get("nodes", {})


def test_a_complete_answer_resets_the_streak_so_an_earlier_refusal_is_not_carried_to_a_later_question():
    def one(p):
        p["ask_limits"]["id_decline"] = 1
    wf, pol = build(BASE + ",f19", one)
    # a question before any answer counts: it is refusal number 1
    first = run_code(wf, "id_refusal_count", start(wf, pol, id_intent="asks_question", id_q="date_of_birth"))
    assert first["id_declines"] == "1" and first["id_route"] == "explain"
    # a refusal, then a complete answer: the streak is back to zero
    v = run_code(wf, "id_check", dict(first, id_intent="answer", id_answer_text="the tenth of April nineteen eighty"))
    assert v["id_declines"] == "0" and v["id_fill"] == "0"
    # the question that follows is a fresh first one (not refusal number 2, so the call does not close)
    nxt = run_code(wf, "id_refusal_count", dict(v, id_intent="asks_question"))
    assert nxt["id_declines"] == "1" and nxt["id_route"] != "decline"
    # without the answer in between the same question would have closed the call
    assert run_code(wf, "id_refusal_count", dict(first, id_intent="asks_question"))["id_route"] == "decline"
