"""F28 (opt-in): a create_promise call that gives no answer (platform 5xx: no hook, no response variables) is counted in code, retried per the
brief's retry rule, then closed as the brief's service error. Without the verified retry rule or the outcome nothing changes."""
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

from .helpers import after_setup_and_facts, j, run_code

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f7,f8,f9,f11,f12,f13"
NOW = "2031-03-05T10:00"


def build(features, mutate=None):
    pol = json.loads((GOLD / "brief-a.policy.json").read_text())
    if mutate:
        mutate(pol)
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, _ = compiler.compile(pol, api, features=features)
    return wf, pol, api


def targets(wf, nid):
    return [t["target_node"] for t in wf["nodes"][nid]["transitions"]]


def test_shape_and_lint():
    wf, pol, api = build(BASE + ",f28")
    assert {"promise_try", "promise_error"} <= set(wf["nodes"])
    assert targets(wf, "promise_confirm")[-3:].count("promise_try") == 1 or "promise_try" in targets(wf, "promise_confirm")
    assert "promise_write" not in targets(wf, "promise_confirm")
    assert targets(wf, "promise_write")[0] == "promise_error"
    assert compiler.lint(wf, pol, api)["errors"] == []


def _ready(wf, pol):
    v = after_setup_and_facts(wf, pol, NOW, 1)
    v.update(rb_pid=j(v, "pids")[0], rb_amount="100", rb_date="2031-03-07", rb_text="100 on a Friday")
    return run_code(wf, "promise_try", v)


def test_no_answer_is_retried_once_then_closed_as_service_error():
    wf, pol, _ = build(BASE + ",f28")
    v = _ready(wf, pol)
    assert v["cp_state"] == "none"                       # the marker the call must overwrite
    first = run_code(wf, "promise_error", v)
    assert first["cp_route"] == "retry" and first["cp_state"] == "none"
    second = run_code(wf, "promise_error", first)         # a second failure: the retry rule is used up
    assert second["cp_route"] == "after" and second["cp_state"] == "failed"
    assert j(second, "pstate")[j(v, "pids")[0]] == "service_error" and second["say_line"]
    assert "recorded" not in second["say_line"].lower().replace("not record", "")


def test_a_retry_limit_of_zero_closes_at_the_first_failure():
    wf, pol, _ = build(BASE + ",f28", lambda p: p["retries"].update(create_promise=0))
    v = _ready(wf, pol)
    assert run_code(wf, "promise_error", v)["cp_state"] == "failed"


def test_missing_or_unverified_rule_leaves_the_workflow_unchanged():
    def drop(pol):
        pol["_source"] = {k: v for k, v in pol["_source"].items() if "/retries/create_promise" not in k}

    def unverified(pol):
        pol["_source"] = {k: ("UNVERIFIED: " + v if "/retries/create_promise" in k else v) for k, v in pol["_source"].items()}

    def no_outcome(pol):
        pol["outcome_map"]["np_service_error"] = None

    for mut in (drop, unverified, no_outcome):
        ref, _, _ = build(BASE, mut)
        wf, _, _ = build(BASE + ",f28", mut)
        assert json.dumps(wf, sort_keys=True) == json.dumps(ref, sort_keys=True), mut.__name__


def test_off_by_default():
    ref, _, _ = build(BASE)
    wf, _, _ = build("all")
    assert "promise_error" not in wf["nodes"] and "promise_error" not in ref["nodes"]


def test_a_failed_code_step_takes_the_normal_path_never_wrap():
    wf, pol, api = build(BASE + ",f28")
    for nid, target in (("promise_try", "promise_write"), ("promise_error", "after_promise")):
        tr = wf["nodes"][nid]["transitions"][0]
        assert tr["description"] == "the code step failed" and tr["target_node"] == target, nid
        assert tr["condition"]["conditions"][0]["variable"] == f"{nid}_failure_count"
    assert compiler.lint(wf, pol, api)["errors"] == []
