"""Phase 2: compiler, zones, prompts, snippets."""

import datetime as dt
import importlib.util
import json
import re
import sys

import pytest

from builder.agentic.tools import lint as alint
from builder.agentic.tools.snippets import run_snippet
from builder.template_cc import compiler, zones
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import snippets as S
from builder.template_cc.zones import ZoneError
from tests.template_cc.conftest import FIX, REPO, load, tb_policies
from tests.template_cc.helpers import (after_setup_and_facts, call_vars, fake_products, j, run_code, run_post,
                                       run_pre)

NOWS = ["2031-03-05T10:00", "2031-03-07T16:20", "2031-03-13T18:55"]  # Wed, Fri afternoon, day before a holiday
ALL = None


def all_policies():
    global ALL
    if ALL is None:
        api = ApiModel.load(FIX / "invented.openapi.json")
        ALL = [("fixture", load(FIX / "invented.policy.json"), api)] + tb_policies()
    return ALL


def compiled(pid, form="lifecycle"):
    for i, p, a in all_policies():
        if i == pid:
            wf, book = compiler.compile(p, a, form)
            return wf, book, compiler.policy_check.complete(p, a), a
    raise KeyError(pid)


IDS = [x[0] for x in all_policies()]


def test_compile_is_deterministic():
    _, p, a = all_policies()[0]
    w1, b1 = compiler.compile(p, a)
    w2, b2 = compiler.compile(p, a)
    assert compiler.dump(w1) == compiler.dump(w2)
    assert compiler.dump(b1.to_json()) == compiler.dump(b2.to_json())


@pytest.mark.parametrize("pid", IDS)
@pytest.mark.parametrize("form", ["lifecycle", "node"])
def test_lints_with_zero_errors(pid, form):
    wf, _, p, a = compiled(pid, form)
    report = compiler.lint(wf, p, a)
    assert report["errors"] == [], report["errors"][:5]


@pytest.mark.parametrize("pid", IDS)
def test_no_double_braces_in_code_and_every_snippet_compiles(pid):
    wf, *_ = compiled(pid)
    for path, owner, tool, placement in alint._tools_of(wf):
        for code in (alint._code_of(tool), tool.get("pre_process_code"), tool.get("post_process_code")):
            if isinstance(code, str) and code.strip():
                assert "{{" not in code and "}}" not in code, path
                compile(code, path, "exec")


@pytest.mark.parametrize("pid", ["fixture", "brief-a", "brief-b", "f3-kestrel", "f5-halden-email", "f7-lumen-codes"])
@pytest.mark.parametrize("now", NOWS)
def test_every_code_node_runs_via_agentic_snippets(pid, now):
    """The A-agent runner (subprocess, python -I) on setup + facts for 1-3 products, and on every other code node."""
    wf, _, p, _ = compiled(pid)
    base = call_vars(p, now)
    res = run_snippet(wf, "setup", base)
    assert res.get("exception") is None, res
    v = {**base, **res["vars_written"]}
    for n in range(1, min(3, int(p.get("max_products") or 1) if p.get("multi_product") else 1) + 1):
        v2 = {**v, "products_raw": fake_products(p, n)}
        res = run_snippet(wf, "facts", v2)
        assert res.get("exception") is None and "tcc_error" not in res["vars_written"], res
        assert res["vars_written"]["facts_state"] == "ok"
        v3 = {**v2, **res["vars_written"]}
        for nid, node in wf["nodes"].items():
            if node["node_type"] == "code" and nid not in ("setup", "facts"):
                r = run_snippet(wf, nid, v3)
                assert r.get("exception") is None and "tcc_error" not in r["vars_written"], (nid, r)


@pytest.mark.parametrize("pid", ["fixture", "brief-b", "f3-kestrel", "f5-halden-email", "f7-lumen-codes"])
def test_every_hook_runs_via_agentic_snippets(pid):
    wf, _, p, a = compiled(pid)
    v = after_setup_and_facts(wf, p, NOWS[0], 2 if p.get("multi_product") else 1)
    t = p["tools"]
    args = {t["create_promise"]["product_id"]: "2", t["create_promise"]["amount"]: "120.50",
            t["create_promise"]["date"]: "7 March", t["verify_identity"]["field"]: p["identity"]["questions"][0],
            t["verify_identity"]["answer"]: "12/04/1980"}
    sc = t["schedule_callback"]
    args.update({sc["datetime"]: "2031-03-06 3pm"} if sc["mode"] == "datetime" else {sc["date"]: "6 March", sc["time"]: "3pm"})
    for path, owner, tool, placement in alint._tools_of(wf):
        name = tool.get("function", {}).get("name")
        for kind, key in (("pre_hook", "pre_process_code"), ("post_hook", "post_process_code")):
            if tool.get(key):
                r = run_snippet(wf, owner, {**v, **args}, kind=kind, tool=name, result={"ok": True, "verified": True})
                assert r.get("exception") is None and "tcc_error" not in r.get("vars_written", {}), (owner, name, kind, r)


def _engine():
    spec = importlib.util.spec_from_file_location("_tcc_test_engine", REPO / "testbed" / "generator" / "engine.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("pid", [x for x in IDS if x != "fixture"])
def test_facts_deadlines_and_attempt1_match_the_generator_engine(pid):
    """Test-only use of testbed/generator/engine.py as an independent oracle on known dates."""
    eng = _engine()
    wf, _, p, _ = compiled(pid)
    pol = eng.Policy({**p, "id": pid})
    for now in ("2026-10-14T11:00", "2026-10-23T17:20", "2026-11-10T10:15", "2026-11-25T14:30", "2026-12-22T11:00"):
        bands = p["bands"]
        days = [b["min_days"] for b in bands][:3] + [bands[-1]["max_days"]]
        for d in days:
            v = run_code(wf, "setup", call_vars(p, now))
            prods = fake_products(p, 1, days=(d,))
            v["products_raw"] = prods
            v = run_code(wf, "facts", v)
            row = j(v, "products")[0]
            today = dt.date.fromisoformat(now[:10])
            assert row["deadline"] == pol.deadline(today, d).isoformat(), (pid, now, d)
            first = pol.attempt1(today, d).isoformat()
            got = re.search(r"Attempt 1: propose .*? on [A-Za-z]+ \d+ [A-Za-z]+ \((\d{4}-\d{2}-\d{2})\)\.", v["facts_text"]).group(1)
            assert got == first, (pid, now, d, got, first)


@pytest.mark.parametrize("pid", IDS)
def test_close_call_is_the_last_bank_tool(pid):
    wf, *_ = compiled(pid, "lifecycle")
    life = [t for t in wf["tools"] if t["function"]["name"] == "close_call"]
    assert len(life) == 1 and life[0]["config"]["alwaysRunsAt"] == ["end_of_call"]
    for group in ("nodes", "global_nodes"):
        for nid, n in wf[group].items():
            assert all(t["function"]["name"] != "close_call" for t in (n.get("tools") or []) + (n.get("pre_actions") or [])), nid
    wf, *_ = compiled(pid, "node")
    graph = alint.edges(wf)
    assert set(graph["close_node"]) == {"end"} and wf["nodes"]["end"]["node_type"] == "end_call"
    assert "tools" not in wf


# --- runtime behaviour (in process) ----------------------------------------------------------------------------------

def test_identity_post_hook_flow_and_failure_limit():
    wf, _, p, _ = compiled("fixture")
    v = run_code(wf, "setup", call_vars(p, NOWS[0]))
    v["id_last_field"] = "birth_date"
    v2, r = run_post(wf, "identity", "verify_identity", v, {"ok": True, "verified": True, "tries_left": 2, "all_verified": False})
    assert v2["id_state"] == "next" and "PIN" in r["data"]["what_to_do_next"] and "header" not in r
    v2["id_last_field"] = "pin_last3"
    v3, _ = run_post(wf, "identity", "verify_identity", v2, {"ok": True, "verified": False, "tries_left": 1})
    assert v3["id_state"] == "retry"
    v4, r = run_post(wf, "identity", "verify_identity", v3, {"ok": True, "verified": False, "tries_left": 0})
    assert v4["id_state"] == "failed" and v4["close_outcome"] == "unverified" and v4["close_reason"] == "wrong_answers"
    v5, r = run_post(wf, "identity", "verify_identity", v3, {"ok": True, "verified": False, "hint": "order_mixed", "tries_left": 1})
    assert v5["id_state"] == "retry" and "not counted" in r["data"]["what_to_do_next"]
    v6, _ = run_post(wf, "identity", "verify_identity", v2, {"ok": True, "verified": True, "all_verified": True})
    assert v6["id_state"] == "passed"


def test_identity_pre_hook_normalises_dates_and_digits():
    wf, _, p, _ = compiled("fixture")
    v = run_code(wf, "setup", call_vars(p, NOWS[0]))
    _, cfg = run_pre(wf, "identity", "verify_identity", v, {"question": "birth_date", "reply": "4/7/1980"})
    assert cfg["body"]["reply"] == "1980-07-04"
    _, cfg = run_pre(wf, "identity", "verify_identity", v, {"question": "birth_date", "reply": "the 4th of July 1980"})
    assert cfg["body"]["reply"] == "1980-07-04"
    _, cfg = run_pre(wf, "identity", "verify_identity", v, {"question": "pin_last3", "reply": "seven 1 2 3"})
    assert cfg["body"]["reply"] == "123"


def test_promise_pre_hook_converts_cents_and_dates_and_ids():
    wf, _, p, _ = compiled("fixture")
    v = after_setup_and_facts(wf, p, NOWS[0], 2)
    v2, cfg = run_pre(wf, "negotiate", "create_promise", v, {"item_ref": "2", "amount_minor": "300", "pay_on": "Friday 7 March"})
    assert cfg["body"] == {"customer_id": "c_test", "item_ref": "q2", "amount_minor": 30000, "pay_on": "2031-03-07"}
    assert v2["cp_pid"] == "q2"
    _, cfg = run_pre(wf, "negotiate", "create_promise", v, {"item_ref": "q1", "amount_minor": "41250", "pay_on": "2031-03-07"})
    assert cfg["body"]["amount_minor"] == 41250  # already cents
    _, cfg = run_pre(wf, "negotiate", "create_promise", v, {"item_ref": "q1", "amount_minor": "137.50", "pay_on": "07.03.2031"})
    assert cfg["body"]["amount_minor"] == 13750 and cfg["body"]["pay_on"] == "2031-03-07"


def test_promise_retry_then_service_error_then_close_rule():
    wf, _, p, _ = compiled("fixture")
    v = after_setup_and_facts(wf, p, NOWS[0], 2)
    v["cp_pid"] = "q1"
    v, r = run_post(wf, "negotiate", "create_promise", v, {"ok": False})
    assert "once more" in r["data"]["what_to_do_next"] and j(v, "pstate")["q1"] == "open"
    v, r = run_post(wf, "negotiate", "create_promise", v, {"ok": False})
    assert j(v, "pstate")["q1"] == "service_error" and "could not save it" in r["data"]["what_to_do_next"]
    v["cp_pid"] = "q2"
    v, _ = run_post(wf, "negotiate", "create_promise", v, {"ok": True, "promise_id": "x"})
    assert v["neg_state"] == "done" and v["close_outcome"] == "kept" and v["close_reason"] == ""
    v = run_code(wf, "wrap", v)
    assert j(v, "note_queue") == [["q1", "nx_error"]] and v["notes_state"] == "pending"
    assert v["closing_line"].startswith("A letter will confirm your promise.")
    v = run_code(wf, "note_next", v)
    assert (v["note_pid"], v["note_reason"], v["notes_state"]) == ("q1", "nx_error", "go")
    v = run_code(wf, "note_next", v)
    assert v["notes_state"] == "done"


def test_multi_product_priority_close_and_restructuring_notes():
    wf, _, p, _ = compiled("fixture")
    v = after_setup_and_facts(wf, p, NOWS[0], 2)
    v["side"], v["side_product"] = "already_paid", "1"
    v = run_code(wf, "side_mark", v)
    assert j(v, "pstate") == {"q1": "paid", "q2": "open"} and v["side"] == "none"
    v = run_code(wf, "refused_2", v)
    assert v["neg_state"] == "done"
    v = run_code(wf, "wrap", v)
    assert (v["close_outcome"], v["close_reason"]) == ("no_deal", "said_no")
    assert j(v, "note_queue") == [["q1", "nx_paid"], ["q2", "nx_refused"]]
    # restructuring: notes first (notes_on_restructuring), then the transfer, then no notes at wrap
    v = after_setup_and_facts(wf, p, NOWS[0], 2)
    v = run_code(wf, "pre_transfer", v)
    assert j(v, "note_queue") == [["q1", "nx_refused"], ["q2", "nx_refused"]] and v["after_notes"] == "transfer"
    v, _ = run_post(wf, "transfer_restructuring", "transfer_to_agent", v, {"ok": True})
    v = run_code(wf, "wrap", v)
    assert (v["close_outcome"], v["close_reason"]) == ("handed_over", "rework") and v["notes_state"] == "done"


def test_single_product_paid_and_code_mode_close():
    wf, _, p, _ = compiled("f7-lumen-codes")
    v = after_setup_and_facts(wf, p, "2026-10-20T11:00", 1)
    v["side"] = "already_paid"
    v = run_code(wf, "side_mark", v)
    v = run_code(wf, "wrap", v)
    assert v["close_code"] == p["outcome_map"]["paid"][0] and v["notes_state"] == "done"
    _, cfg = run_pre(wf, "workflow", "close_call", v, {})
    assert cfg["body"] == {"customer_id": "c_test", p["tools"]["close_call"]["code"]: p["outcome_map"]["paid"][0]}


def test_close_pre_hook_drops_an_empty_reason():
    wf, _, p, _ = compiled("brief-a")
    v = after_setup_and_facts(wf, p, "2026-10-14T11:00", 1)
    v["cp_pid"] = "p1"
    v, _ = run_post(wf, "negotiate", "create_promise", v, {"ok": True})
    _, cfg = run_pre(wf, "workflow", "close_call", v, {})
    assert cfg["body"] == {"customer_id": "c_test", "outcome": "promise"}


def test_callback_window_and_datetime_hook():
    wf, _, p, _ = compiled("f5-halden-email")
    v = run_code(wf, "setup", call_vars(p, "2026-11-05T15:15"))
    assert "Thursday 5 November" in v["cb_window_text"] and "15:16" in v["cb_window_text"]
    _, cfg = run_pre(wf, "third_party", "schedule_callback", v, {"callback_at": "Friday 6 November at 3:30 pm"})
    assert cfg["body"]["callback_at"] == "2026-11-06T15:30"
    v2, r = run_post(wf, "third_party", "schedule_callback", v, {"ok": True})
    assert v2["cb_state"] == "booked" and v2["close_reason"] == "callback"


def test_flattened_account_keys_are_read_when_the_mapping_is_empty():
    wf, _, p, _ = compiled("brief-b")
    v = run_code(wf, "setup", call_vars(p, "2026-10-14T11:00"))
    for i, row in enumerate(fake_products(p, 2)):
        for k, val in row.items():
            v[f"fetch_account_response.data.products.{i}.{k}"] = val
    v = run_code(wf, "facts", v)
    assert v["facts_state"] == "ok" and j(v, "pids") == ["p1", "p2"]


def test_no_products_goes_to_service_error():
    wf, _, p, _ = compiled("brief-a")
    v = run_code(wf, "setup", call_vars(p, "2026-10-14T11:00"))
    v = run_code(wf, "facts", v)
    assert v["facts_state"] == "error"
    v = run_code(wf, "wrap", v)
    assert (v["close_outcome"], v["close_reason"]) == ("no_promise", "service_error")


# --- zones -----------------------------------------------------------------------------------------------------------

def test_every_unlocked_zone_round_trips_and_locked_zones_are_refused():
    wf, book, *_ = compiled("fixture")
    from builder.template_cc.policy_check import get_pointer
    unlocked = [k for k, z in book.zones.items() if not z["locked"]]
    assert any(k.startswith("node:negotiate/rules") for k in unlocked)
    assert any(k.startswith("hook:create_promise/msg/") for k in unlocked)
    assert any(k.startswith("tool:") for k in unlocked) and any(k.startswith("extract:") for k in unlocked)
    for zid in unlocked:
        text = "Say the amount slowly." if not zid.startswith("hook:") else "Say the amount slowly."
        w2, b2 = zones.apply(wf, book, zid, text)
        targets = [t for t in b2.targets if any(p["zone"] == zid for p in t["parts"])]
        assert targets, zid
        for t in targets:
            val = get_pointer(w2, t["pointer"])
            assert text in val, (zid, t["pointer"])
            if t["kind"] == "hook_msgs":
                compile(val, zid, "exec")
                assert "Say the amount slowly." in S.read_msgs(val).values()
    for zid in [k for k, z in book.zones.items() if z["locked"]][:20]:
        with pytest.raises(ZoneError):
            zones.apply(wf, book, zid, "x")


def test_zone_budgets_and_braces():
    wf, book, *_ = compiled("fixture")
    with pytest.raises(ZoneError):
        zones.apply(wf, book, "node:negotiate/rules", "x" * 5000)
    with pytest.raises(ZoneError):
        zones.apply(wf, book, "hook:create_promise/msg/recorded", "Recorded {ok}")
    with pytest.raises(ZoneError):
        zones.apply(wf, book, "no/such/zone", "x")


def test_patched_workflow_still_lints():
    wf, book, p, a = compiled("fixture")
    w2, _ = zones.apply(wf, book, "node:negotiate/rules", "- If the customer asks for the total, give the sum of the past-due amounts from the facts.")
    assert compiler.lint(w2, p, a)["errors"] == []
    assert "give the sum" in w2["nodes"]["negotiate"]["task_message"] and "give the sum" in w2["nodes"]["negotiate_resume"]["task_message"]


def test_verbatim_lines_are_spoken_by_tts():
    wf, _, p, _ = compiled("fixture")
    text = wf["nodes"]["opening"]["on_enter"]["text"]
    assert text.startswith("Good day, this is the automated helper") and "{{honorific}} {{family_name}}" in text
    assert wf["nodes"]["negotiate"]["on_enter"]["text"] == "{{debt_statement}} {{first_ask_line}}"
    for w in p["forbidden_before_verify"]:
        assert w in wf["system_prompt"]


def test_lines_with_account_placeholders_are_cut_not_garbled():
    from builder.template_cc.compiler import prompts as P
    pol = {"call_variables": ["title", "last_name"]}
    assert P.spoken("I am calling from the team about a late payment on your {product}: {past_due} is due.", pol) == \
        "I am calling from the team about a late payment."
    assert P.spoken("Am I speaking with {title} {last_name}?", pol) == "Am I speaking with {{title}} {{last_name}}?"
    assert P.spoken("{amount} is due.", pol) == ""


@pytest.mark.parametrize("said, iso", [
    ("Twenty-third of February nineteen eighty-nine", "1989-02-23"),
    ("the fourth of July nineteen seventy", "1970-07-04"),
    ("fourteenth March two thousand one", "2001-03-14"),
    ("first of May two thousand and three", "2003-05-01"),
    ("23/02/1989", "1989-02-23"),
])
def test_dates_said_in_words_are_normalised(said, iso):
    from builder.template_cc.compiler import runtime
    assert runtime.norm_answer("date", said, dt.date(2026, 10, 14)) == iso


def test_callback_window_names_today_and_tomorrow():
    wf, _, p, _ = compiled("brief-a")
    v = run_code(wf, "setup", call_vars(p, "2026-10-14T11:00"))
    t = v["cb_window_text"]
    assert t.startswith("Today is Wednesday 14 October") and "today, Wednesday 14 October" in t and "tomorrow, Thursday 15 October" in t


def test_a_failing_code_node_never_loops():
    wf, *_ = compiled("fixture")
    for nid, n in wf["nodes"].items():
        if n["node_type"] == "code" and nid not in ("setup", "wrap"):
            first = n["transitions"][0]
            assert first["target_node"] == "wrap" and first["condition"]["conditions"][0]["variable"] == f"{nid}_failure_count"


def test_too_much_is_not_restructuring():
    from builder.template_cc.compiler import prompts as P
    assert "too much" in P.EXTRACT["side"] and "NOT restructuring" in P.EXTRACT["side"]


@pytest.mark.parametrize("pid", IDS)
def test_every_conversation_after_the_opening_can_end_the_call(pid):
    """A finished call must route to wrap instead of looping on goodbye (seen on the platform)."""
    wf, book, *_ = compiled(pid)
    convs = [nid for nid, n in wf["nodes"].items() if n["node_type"] == "conversation" and nid not in ("start", "opening", "opening_ask2")]
    assert convs
    for nid in convs:
        fin = [t for t in wf["nodes"][nid]["transitions"] if t.get("is_tool_call") and t["target_node"] == "wrap"]
        assert len(fin) == 1, nid
    g = wf["global_nodes"]["g_goodbye"]
    assert g["transitions"] == [{"target_node": "wrap", "description": "always", "condition": {"operator": "always"}}]
    assert {"opening", "opening_ask2", "setup", "wrap", "end"} <= set(g["excluded_nodes"])
    assert not book.zones["transition:finish/intent"]["locked"] and not book.zones["global:g_goodbye/intent"]["locked"]


def test_prompts_forbid_offers_outside_the_facts():
    wf, *_ = compiled("brief-a")
    assert "Never offer or advertise any product" in wf["system_prompt"]
    assert "transition_to_wrap" in wf["system_prompt"]
    assert "Never offer anything that is not in your facts" in wf["nodes"]["negotiate_resume"]["on_enter"]["prompt"]


def test_promise_failure_messages_avoid_the_word_recorded_and_retry_without_asking():
    wf, _, p, _ = compiled("brief-a")
    msgs = S.read_msgs(wf["nodes"]["negotiate"]["tools"][0]["post_process_code"])
    assert "recorded" not in msgs["retry"].replace("Do not use the word recorded", "") and "Do not ask the customer again" in msgs["retry"]
    assert "recorded" not in msgs["failed"]
    task = wf["nodes"]["negotiate"]["task_message"]
    assert "not a persuasion attempt and does not change the amount" in task
    from builder.template_cc.compiler import prompts as P
    assert "robot or an AI is NOT human" in P.EXTRACT["side"]


@pytest.mark.parametrize("pid", IDS)
def test_the_opening_is_spoken_before_any_code_runs(pid):
    wf, *_ = compiled(pid)
    assert wf["initial_node"] == "start" and wf["nodes"]["start"]["transitions"] == [
        {"target_node": "opening", "description": "always", "condition": {"operator": "always"}}]
    assert "pre_actions" not in wf["nodes"]["start"] and wf["nodes"]["opening"]["on_enter"]["type"] == "tts"
    op = wf["nodes"]["opening"]
    assert [t["target_node"] for t in op["transitions"]] == ["setup"] and op["max_turn_transition"] == "setup"
    targets = [t["target_node"] for t in wf["nodes"]["setup"]["transitions"]]
    assert targets[0] == "identity" and targets[-1] == "mark_nr_unclear" and "third_party" in targets


def test_setup_routes_on_who_answered():
    wf, _, p, _ = compiled("brief-a")
    v = run_code(wf, "setup", {**call_vars(p, "2026-10-14T11:00"), "who1": "someone_else"})
    assert v["cb_window_text"].startswith("Today is")  # setup ran after the first reply and still computed the window


def test_a_duplicate_promise_call_never_downgrades_a_recorded_promise():
    wf, _, p, _ = compiled("brief-a")
    v = after_setup_and_facts(wf, p, "2026-10-14T11:00", 1)
    v["cp_pid"] = "p1"
    v, r = run_post(wf, "negotiate", "create_promise", v, {"ok": True, "promise_id": "pr1"})
    assert j(v, "pstate")["p1"] == "promised"
    for _ in range(2):
        v, r = run_post(wf, "negotiate", "create_promise", v, {"ok": False, "error": "this product already has a promise"})
        assert j(v, "pstate")["p1"] == "promised" and "Do not call create_promise for it again" in r["data"]["what_to_do_next"]
    assert v["close_outcome"] == "promise"
