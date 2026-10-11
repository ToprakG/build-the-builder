"""Phase 5: testgen, runner, gate, loop (FakeRunner; mocked analyzer, patcher and decision model)."""

import builtins
import datetime as dt
import io as _io
import json
import pathlib
import sys
import time

import pytest

from builder.template_cc import compiler, gate, loop, runner, testgen
from builder.template_cc import judge as J
from builder.template_cc.io import Brief, Models
from builder.template_cc.trace import NullTrace
from tests.template_cc.conftest import FIX, REPO, load, tb_policies
from tests.template_cc.test_judge import PASS, ScriptedBackend


def pols(fx_policy):
    tb = {pid: p for pid, p, _ in tb_policies()}
    return [("fixture", fx_policy), ("brief-b", tb["brief-b"]), ("f4-brightwell", tb["f4-brightwell"]),
            ("f7-lumen-codes", tb["f7-lumen-codes"])]


# --- testgen ---------------------------------------------------------------------------------------------------------

def test_testgen_makes_cases_from_our_policy(fx_policy):
    for pid, p in pols(fx_policy):
        cases, gaps = testgen.generate(p, 30)
        assert len(cases) >= 25, (pid, gaps)
        outcomes = {v[0] for v in p["outcome_map"].values() if v}
        assert all(c["expected"]["outcome"] in outcomes for c in cases), pid
        assert all(c["id"].startswith("T-") and c["source"] == "tcc-testgen" for c in cases)
    cases, _ = testgen.generate(fx_policy, 30)
    assert {c["expected"]["outcome"] for c in cases} >= {"kept", "unreached"}
    assert any(set(c["identity"]) == {"birth_date", "pin_last3"} for c in cases)  # field names the generator never knew


def test_synthesized_now_dates_are_business_days(fx_policy):
    for pid, p in pols(fx_policy):
        hol = {dt.date.fromisoformat(h) for h in p["holidays"]}
        nows = testgen.synth_now_dates(p)
        assert len(nows) == 5, pid
        for n in nows:
            d = dt.date.fromisoformat(n[:10])
            assert d.weekday() < 5 and d not in hol, (pid, n)


def test_testgen_reads_no_testbed_data_and_imports_no_data_module(fx_policy, monkeypatch):
    opened = []
    real_open, real_read, real_pathopen = builtins.open, pathlib.Path.read_text, pathlib.Path.open

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_read(self, *a, **k):
        opened.append(str(self))
        return real_read(self, *a, **k)

    def spy_popen(self, *a, **k):
        opened.append(str(self))
        return real_pathopen(self, *a, **k)

    for m in ("engine", "cases", "assemble"):
        sys.modules.pop(m, None)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(_io, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "read_text", spy_read)
    monkeypatch.setattr(pathlib.Path, "open", spy_popen)
    cases, gaps = testgen.generate(fx_policy, 20)
    assert cases
    bad = [p for p in opened if "testbed" in p and not p.endswith(".py")]
    assert bad == []
    gen = str(REPO / "testbed" / "generator")
    for m in testgen.FORBIDDEN_MODULES:
        mod = sys.modules.get(m)
        assert mod is None or not str(getattr(mod, "__file__", "")).startswith(gen), m
    assert gen not in sys.path


def test_generator_state_is_restored_after_a_build(fx_policy):
    E, C, A = testgen.load_generator()
    plan_for, prefix = A.plan_for, dict(A.PREFIX)
    testgen.generate(fx_policy, 20)
    assert A.plan_for is plan_for and A.PREFIX == prefix and "birth_date" not in C.QNAME


def test_unexpressible_families_are_dropped_and_logged(fx_policy):
    p = json.loads(json.dumps(fx_policy))
    p["outcome_map"]["nr_deceased"] = None
    p["queue_map"]["human"] = None
    p["attempt_plan"] = [{"date": "deadline", "amount": "full", "cap_at_deadline": True}]
    cases, gaps = testgen.generate(p, 30)
    fams = {c["family"] for c in cases}
    assert "Deceased" not in fams and "Human request" not in fams and "Dates" not in fams and "Refusal" not in fams
    assert any("Deceased" in g for g in gaps) and any("attempt 1" in g for g in gaps)


def test_split_is_stratified_and_deterministic(fx_policy):
    cases, _ = testgen.generate(fx_policy, 30)
    tr1, he1 = testgen.split(cases)
    tr2, he2 = testgen.split(list(reversed(cases)))
    assert [c["id"] for c in tr1] == [c["id"] for c in tr2] and len(he1) == len(he2)
    assert abs(len(tr1) - 2 * len(he1)) <= len({c["family"] for c in cases}) * 2
    assert not ({c["id"] for c in tr1} & {c["id"] for c in he1})


def test_score_calls_with_our_policy_scores_a_perfect_replay(fx_policy):
    cases, _ = testgen.generate(fx_policy, 20)
    c = next(c for c in cases if c["expected"]["outcome"] == "kept")
    t = fx_policy["tools"]
    calls = [{"tool": "verify_identity", "args": {}, "result": {"ok": True}}]
    for p in c["expected"]["promises"]:
        calls.append({"tool": "create_promise", "args": {t["create_promise"]["product_id"]: p["product_id"],
                                                         t["create_promise"]["amount"]: int(round(p["amount"] * 100)),
                                                         t["create_promise"]["date"]: p["date"]}, "result": {"ok": True}})
    for n in c["expected"]["notes"]:
        calls.append({"tool": "add_note", "args": {"item_ref": n["product_id"], "code": n["reason"]}, "result": {"ok": True}})
    calls.append({"tool": "close_call", "args": {"result": "kept"}, "result": {"ok": True}})
    assert runner.score_calls(c, calls, fx_policy)["pass"]


def test_null_runner_fails_fast():
    with pytest.raises(runner.RunnerNotConnected):
        runner.NullRunner().run({}, [], {}, {}, 1)


# --- gate ------------------------------------------------------------------------------------------------------------

def test_gate_sign_test_numbers():
    assert gate.sign_p([1, 1, 1, 1, 1]) == (0.0625, 5)
    assert gate.sign_p([1, -1, 0]) == (1.0, 0)
    d = gate.decide({f"c{i}": 1 for i in range(6)}, {f"c{i}": 2 for i in range(6)}, reps=2)
    assert d.commit and d.wins == 6 and d.p_value < 0.1
    d = gate.decide({f"c{i}": 1 for i in range(6)}, {**{f"c{i}": 2 for i in range(5)}, "c5": 0}, reps=2)
    assert not d.commit  # 5 wins 1 loss: p = 0.22
    best = {**{f"c{i}": 0 for i in range(8)}, "x": 2}
    cand = {**{f"c{i}": 2 for i in range(8)}, "x": 0}
    d = gate.decide(best, cand, reps=2)
    assert not d.commit and d.broken == ["x"] and any("now fail every rep" in r for r in d.reasons)


# --- loop ------------------------------------------------------------------------------------------------------------

MARK = "Read every promise back together before recording"


def fixture_deps(fx_api, fx_policy, fx_brief, tmp_path, run_fn, judge=None, deadline=None):
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)
    return loop.LoopDeps(runner=runner.FakeRunner(run_fn), judge=judge or J.PatchJudge(ScriptedBackend(PASS)), call_llm=None,
                         models=Models(), trace=NullTrace(), briefs=[Brief("brief-1.md", "b", fx_brief)], brief_text=fx_brief,
                         api_path=FIX / "invented.openapi.json", api=fx_api, tool_api={}, policy=fx_policy, out_dir=out,
                         deadline=deadline or time.time() + 3600)


def fake_analyze(failures, brief_text, book, call_llm, model, trace, deadline=None):
    f = failures[0]
    return {"bugs": [{"bug_id": "b1", "behaviour": "records before reading back", "expected_per_brief": "read back",
                      "brief_quote": "read every promise back together",
                      "evidence": [{"case_id": f["case_id"], "rep": f["rep"], "turn": 1}], "suspected_zone": "node:negotiate/rules",
                      "severity": "high"}], "dropped": []}


def fake_patcher(*a, **k):
    return {"patches": [{"id": "p1", "bug_ids": ["b1"], "zone": "node:negotiate/rules", "op": "append", "before": "",
                         "after": f"- {MARK}.", "brief_quote": "read every promise back together", "rationale": "r"}]}


def marked(wf):
    return MARK in wf["nodes"]["negotiate"]["task_message"]


def half_fail(wf, case, rep):
    return marked(wf) or hash(case["id"]) % 2 == 0 and int(case["id"][2:]) % 2 == 0


def run(fx_api, fx_policy, fx_brief, tmp_path, run_fn, **kw):
    wf, book = compiler.compile(fx_policy, fx_api)
    judge = kw.pop("judge", None)
    deadline = kw.pop("deadline", None)
    d = fixture_deps(fx_api, fx_policy, fx_brief, tmp_path, run_fn, judge, deadline)
    res = loop.run_loop(wf, book, d, analyze_fn=kw.pop("analyze_fn", fake_analyze), patcher_fn=kw.pop("patcher_fn", fake_patcher), **kw)
    return res, d


def test_loop_commits_a_significant_heldout_improvement(fx_api, fx_policy, fx_brief, tmp_path):
    res, d = run(fx_api, fx_policy, fx_brief, tmp_path, lambda wf, c, r: marked(wf) or int(c["id"][2:]) % 4 == 0,
                 max_iters=3, reps=2, n_cases=36)
    assert res.committed == 1 and marked(res.workflow)
    assert res.iterations[0]["gate"]["commit"] and res.stop_reason == "no failures"
    assert (d.out_dir / "patches" / "iter1.applied.json").exists() and (d.out_dir / "patch_log.jsonl").exists()
    assert any("COMMIT" in l for l in d.trace.lines)


def test_loop_discards_when_heldout_does_not_improve(fx_api, fx_policy, fx_brief, tmp_path):
    res, d = run(fx_api, fx_policy, fx_brief, tmp_path, lambda wf, c, r: int(c["id"][2:]) % 4 == 0, max_iters=2, reps=2)
    assert res.committed == 0 and not marked(res.workflow)
    assert all(not it["gate"]["commit"] for it in res.iterations if "gate" in it)
    assert res.stop_reason == "max iterations"


def test_loop_discards_when_a_fully_passing_case_breaks(fx_api, fx_policy, fx_brief, tmp_path):
    cases, _ = testgen.generate(fx_policy, 36)
    _, held = testgen.split(cases)
    victim = held[0]["id"]

    def fn(wf, c, r):
        if c["id"] == victim:
            return not marked(wf)
        return marked(wf) or int(c["id"][2:]) % 4 == 0

    res, _ = run(fx_api, fx_policy, fx_brief, tmp_path, fn, max_iters=1, reps=2, n_cases=36)
    g = res.iterations[0]["gate"]
    assert not g["commit"] and g["broken"] == [victim] and res.committed == 0


def test_loop_stops_without_failures_and_without_accepted_patches(fx_api, fx_policy, fx_brief, tmp_path):
    res, _ = run(fx_api, fx_policy, fx_brief, tmp_path / "a", lambda wf, c, r: True)
    assert res.stop_reason == "no failures" and res.iterations == []
    reject = J.PatchJudge(ScriptedBackend(dict(PASS, necessary="no")))
    res, d = run(fx_api, fx_policy, fx_brief, tmp_path / "b", lambda wf, c, r: False, judge=reject)
    assert res.stop_reason == "no accepted patches" and not marked(res.workflow)
    assert len(d.runner.calls) == 2  # baseline only: no candidate run


def test_loop_stops_when_time_is_short(fx_api, fx_policy, fx_brief, tmp_path):
    res, _ = run(fx_api, fx_policy, fx_brief, tmp_path, lambda wf, c, r: False, deadline=time.time() - 1)
    assert res.stop_reason.startswith("time") and res.iterations == []


def test_loop_keeps_the_best_on_patcher_or_judge_errors(fx_api, fx_policy, fx_brief, tmp_path):
    def boom(*a, **k):
        raise RuntimeError("patcher crashed")

    res, d = run(fx_api, fx_policy, fx_brief, tmp_path / "a", lambda wf, c, r: False, patcher_fn=boom)
    assert res.stop_reason == "error" and not marked(res.workflow) and "patcher crashed" in res.iterations[0]["error"]

    class BadJudge:
        def judge(self, p, ctx):
            raise ValueError("judge down")

    res, d = run(fx_api, fx_policy, fx_brief, tmp_path / "b", lambda wf, c, r: False, judge=BadJudge())
    assert res.stop_reason == "error" and not marked(res.workflow)


def test_patcher_receives_no_testbed_or_expected_data(fx_api, fx_policy, fx_brief, tmp_path):
    seen = {}

    def spy(briefs, brief_text, api_path, api, policy, wf, book, report, failures, *a, **k):
        seen["failures"] = failures
        seen["api_path"] = str(api_path)
        return {"patches": []}

    run(fx_api, fx_policy, fx_brief, tmp_path, lambda wf, c, r: False, patcher_fn=spy)
    blob = json.dumps(seen["failures"])
    assert seen["failures"] and "oracle" not in blob and '"expected"' not in blob and "testbed" not in seen["api_path"]


def test_empty_product_id_format_gets_the_bank_default(fx_policy):
    p = json.loads(json.dumps(fx_policy))
    p["product_id_format"] = ""
    cases, gaps = testgen.generate(p, 20)
    ids = {pr["product_id"] for c in cases for pr in c["expected"]["promises"]}
    assert ids and all(i.startswith("p") and i[1:].isdigit() for i in ids)
    from builder.template_cc import vendor_runner
    assert vendor_runner.bank_config(p)["product_id_format"] == "p{n}"


def test_unscored_runs_reach_neither_the_analyzer_nor_the_gate(fx_api, fx_policy, fx_brief, tmp_path):
    from builder.template_cc.runner import CaseResult
    seen = {}

    def fn(wf, c, rep):
        if int(c["id"][2:]) % 2:
            return CaseResult(c["id"], rep, False, ["runner error: 504"], [], [], True)
        return marked(wf) or int(c["id"][2:]) % 4 == 0

    def analyze_spy(failures, *a, **k):
        seen["ids"] = {f["case_id"] for f in failures}
        return fake_analyze(failures, *a, **k)

    res, d = run(fx_api, fx_policy, fx_brief, tmp_path, fn, analyze_fn=analyze_spy, max_iters=1, reps=1)
    assert seen["ids"] and all(int(i[2:]) % 2 == 0 for i in seen["ids"])
    g = res.iterations[0]["gate"]
    assert all(int(i[2:]) % 2 == 0 for i in g["best"]) and any("ignores" in l for l in d.trace.lines)
