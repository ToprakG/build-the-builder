"""Unit-test gate: suite generation from the policy, the Freya unit-test API calls, and the gate in the loop. Offline."""

import json

import pytest

from builder.template_cc import compiler, policy_check
from builder.template_cc import unittests as U
from tests.template_cc.conftest import tb_policies
from tests.template_cc.test_loop import MARK, fake_analyze, fake_patcher, marked, run


def all_cases():
    from builder.template_cc.apimodel import ApiModel
    from tests.template_cc.conftest import FIX, load
    api = ApiModel.load(FIX / "invented.openapi.json")
    return [("fixture", policy_check.complete(load(FIX / "invented.policy.json"), api), api)] + tb_policies()


@pytest.mark.parametrize("pid,policy,api", all_cases(), ids=[c[0] for c in all_cases()])
def test_suite_targets_real_nodes_and_api_fields(pid, policy, api):
    wf, _ = compiler.compile(policy, api)
    tests = U.build_suite(policy, wf)
    assert 8 <= len(tests) <= 12 and len({t["name"] for t in tests}) == len(tests)
    nodes = set(wf["nodes"]) | set(wf["global_nodes"])
    for t in tests:
        assert t["messages"][-1]["role"] == "user" and "tcc" in t["tags"]
        snaps = [m for m in t["messages"] if m.get("workflowSnapshot")]
        assert snaps and all(m["nodeId"] in nodes for m in snaps)
        for a in t["assertions"]:
            if a["type"] == "routing":
                assert a["target"] in nodes, (t["name"], a)
            if a["type"] == "tool_call" and "args" in a:
                assert set(a["args"]) <= set(api.request(a["tool"])), (t["name"], a)
    neg = next(t for t in tests if "confirmed read-back" in t["name"])
    snap = neg["messages"][-2]["workflowSnapshot"]["variableTable"]
    assert snap["facts_state"]["value"] == "ok" and "Date table" in snap["facts_text"]["value"]


class FakeFM:
    def __init__(self):
        self.calls = []

    def workspace_id(self):
        return "ws"

    def save_workflow(self, slot, wf, state_file=None):
        self.calls.append(("save", slot))
        return {"workflow_id": "wf-unit"}

    def api(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        if method == "GET" and path == "/api/v2/unit-tests":
            return {"tests": [{"id": "old", "name": "tcc stale", "tags": ["tcc"]}]}
        if method == "POST" and path == "/api/v2/unit-tests":
            return {"id": "id-" + body["name"][-6:]}
        if path.endswith("/run-all"):
            return {"enqueued": [{"unitTestId": i, "runIds": ["r-" + i]} for i in body["unitTestIds"]]}
        if path.startswith("/api/v2/unit-tests/runs/"):
            return {"outcome": "fail" if "cent" in path else "pass", "verdictReason": "x"}
        return {}


def test_sync_creates_archives_and_run_collects_verdicts(fx_api, fx_policy):
    wf, _ = compiler.compile(fx_policy, fx_api)
    fm = FakeFM()
    from builder.template_cc.trace import NullTrace
    rep = U.UnitSuite("tcc-x", fx_policy, fx_api, fm, NullTrace()).check(wf)
    assert ("save", "tcc-x-unit") in fm.calls and ("PATCH", "/api/v2/unit-tests/old") in fm.calls
    assert rep["total"] >= 8 and rep["passed"] <= rep["total"]
    best = {"results": [{"name": "a", "outcome": "pass"}, {"name": "b", "outcome": "fail"}]}
    cand = {"results": [{"name": "a", "outcome": "fail"}, {"name": "b", "outcome": "pass"}]}
    assert U.UnitSuite.regressions(best, cand) == ["a"]


class FakeUnit:
    def __init__(self, breaks: bool):
        self.breaks, self.checked = breaks, []

    def check(self, wf):
        self.checked.append(marked(wf))
        ok = not (self.breaks and marked(wf))
        return {"passed": int(ok), "total": 1, "results": [{"name": "u1", "outcome": "pass" if ok else "fail"}]}

    regressions = staticmethod(U.UnitSuite.regressions)


def test_the_unit_gate_discards_a_regressing_candidate_before_the_chats(fx_api, fx_policy, fx_brief, tmp_path, monkeypatch):
    import builder.template_cc.loop as L
    unit = FakeUnit(breaks=True)
    orig = L.LoopDeps

    def deps(*a, **k):
        d = orig(*a, **k)
        d.unit = unit
        return d

    monkeypatch.setattr(L, "LoopDeps", deps)
    import tests.template_cc.test_loop as TL
    monkeypatch.setattr(TL.loop, "LoopDeps", deps)
    res, d = run(fx_api, fx_policy, fx_brief, tmp_path, lambda wf, c, r: marked(wf) or int(c["id"][2:]) % 4 == 0, max_iters=1, reps=2)
    assert unit.checked == [False, True] and res.committed == 0
    assert res.iterations[0]["unit"]["regressions"] == ["u1"] and len(d.runner.calls) == 2  # no candidate chats
