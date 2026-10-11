"""Phase 6: run.py orchestration with every model, SDK, judge and Freya call mocked."""

import copy
import json

import pytest

from builder.template_cc import judge as J
from builder.template_cc import run as R
from builder.template_cc.io import REPO
from tests.template_cc.conftest import FIX, load
from tests.template_cc.test_judge import ScriptedBackend
from tests.template_cc.test_sessions import scripted_query

ARGS = [str(FIX / "invented.brief.md"), str(FIX / "invented.openapi.json")]


class FakeFreya:
    def __init__(self):
        self.calls = []

    def save_workflow(self, slot, wf, state_file=".freya-agents.json"):
        self.calls.append(("save_workflow", slot, state_file, wf))
        return {"workflow_id": "wf_1", "agent_id": "ag_1"}

    def export_workflow(self, wid, path):
        self.calls.append(("export_workflow", wid, path))
        open(path, "w").write("{}")
        return path


def fake_llm(calls):
    pol = load(FIX / "invented.policy.json")

    def f(system, user, model, json_schema=None, **kw):
        calls.append(kw.get("purpose"))
        kw["trace"].model_call(model, kw.get("purpose"), 10, 5)
        return copy.deepcopy(pol)
    return f


@pytest.fixture(autouse=True)
def no_real_judge(monkeypatch):
    monkeypatch.delenv("FREYA_API_KEY", raising=False)
    answers = {"value_supported": "yes", "override_respected": "yes", "contradicts_brief": "no"}
    monkeypatch.setattr(R.judge, "make_judge", lambda models, trace, call_llm, deadline=None: (
        trace.line("judge: scripted test judge") or J.PatchJudge(ScriptedBackend(answers), trace=trace)))


def run(tmp_path, extra, **kw):
    out = tmp_path / "out"
    calls = []
    rc = R.main(ARGS + ["--out", str(out)] + extra, call_llm=kw.pop("call_llm", fake_llm(calls)), trace_root=tmp_path / "traces",
                sdk_server=object(), **kw)
    traces = list((tmp_path / "traces").glob("tcc-*"))
    txt = (traces[0] / "trace.txt").read_text() if traces else ""
    return rc, out, calls, txt


def test_save_none_with_audit_off(tmp_path):
    freya = FakeFreya()
    rc, out, calls, txt = run(tmp_path, ["--save", "none", "--audit", "off"], freya_mod=freya)
    assert rc == 0 and freya.calls == []
    for f in ("policy.raw.json", "policy.json", "workflow.baseline.json", "workflow.json", "build_report.json", "zones.json"):
        assert (out / f).exists(), f
    assert [c.split(":")[0] for c in calls] == ["extract reading A", "extract reading B", "extract pass 2"]
    assert "model call claude_cli:sonnet: extract reading A" in txt and "extract pass 2" in txt
    assert "no self-test (none)" in txt and "audit: off" in txt and "nothing saved" in txt and "judge: scripted" in txt
    rep = json.loads((out / "build_report.json").read_text())
    assert rep["status"] == "ok" and rep["lint"]["errors"] == 0 and rep["model_calls"] == 3
    assert "agent_config" not in json.loads((out / "workflow.json").read_text())


def test_audit_runs_after_the_baseline_is_written_and_applies_judged_patches(tmp_path):
    seen = {}
    patch = {"id": "a1", "path": "/callback/end", "old_value": "16:30", "new_value": "16:30", "brief_quote": "09:30 to 16:30", "rationale": "r"}
    inner = scripted_query({"out/policy_patches.json": [patch]})

    async def q(prompt, options):
        seen["baseline_before_audit"] = (tmp_path / "out" / "workflow.baseline.json").exists()
        async for m in inner(prompt, options):
            yield m

    rc, out, calls, txt = run(tmp_path, ["--save", "none"], query_fn=q)
    assert rc == 0 and seen["baseline_before_audit"]
    assert "audit turn 1" in txt and "audit: 1 of 1 policy patch(es) accepted" in txt
    log = [json.loads(l) for l in (out / "patch_log.jsonl").read_text().splitlines()]
    assert log[0]["kind"] == "policy" and log[0]["accepted"]


def test_save_freya_uses_the_absolute_state_file_and_exports(tmp_path):
    freya = FakeFreya()
    rc, out, calls, txt = run(tmp_path, ["--save", "freya", "--audit", "off", "--slot", "tcc-test"], freya_mod=freya)
    assert rc == 0
    save, export = freya.calls
    assert save[0] == "save_workflow" and save[1] == "tcc-test" and save[2] == str(REPO / ".freya-agents.json")
    assert export == ("export_workflow", "wf_1", str(out / "tcc-test.workflow.json"))
    assert "freya save_workflow" in txt and "freya export_workflow" in txt


def test_validate_dry_run_only_with_a_key(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setenv("FREYA_API_KEY", "k")
    rc, out, _, txt = run(tmp_path, ["--save", "none", "--audit", "off"], validate_fn=lambda wf, name: seen.append(name) or {"ok": True})
    assert rc == 0 and seen and "freya validate (dry run): ok" in txt


def test_exit_codes(tmp_path):
    def broken(*a, **k):
        raise RuntimeError("model down")

    rc, out, _, txt = run(tmp_path / "a", ["--save", "none", "--audit", "off"], call_llm=broken)
    assert rc == 1 and json.loads((out / "build_report.json").read_text())["status"] == "failed" and "model down" in txt
    import builder.template_cc.vendor_runner as VR
    orig = VR.availability
    VR.availability = lambda vendor=None: (False, "cloudflared is not installed")
    try:
        rc, out, _, txt = run(tmp_path / "b", ["--save", "none", "--self-test", "local"])
    finally:
        VR.availability = orig
    assert rc == 2 and "needs the local tester" in txt and not (out / "policy.raw.json").exists()
    assert R.main([str(FIX / "invented.brief.md"), "--out", str(tmp_path / "c")], call_llm=broken, trace_root=tmp_path) == 2


def test_audit_crash_keeps_the_extracted_policy(tmp_path):
    async def boom(prompt, options):
        raise RuntimeError("cli missing")
        yield  # pragma: no cover

    rc, out, _, txt = run(tmp_path, ["--save", "none"], query_fn=boom)
    assert rc == 0 and json.loads((out / "policy.json").read_text())["bank"] == "Quillmoor Credit Union"
    assert "cli missing" in txt


def test_self_test_with_a_fake_runner(tmp_path):
    from builder.template_cc.runner import FakeRunner
    r = FakeRunner(lambda wf, c, rep: True)
    rc, out, _, txt = run(tmp_path, ["--save", "none", "--audit", "off", "--self-test", "local"], runner=r)
    assert rc == 0 and r.calls and "self-test: baseline" in txt
    assert json.loads((out / "build_report.json").read_text())["self_test"]["stop_reason"] == "no failures"


def test_given_policy_skips_extraction(tmp_path):
    def no_llm(*a, **k):
        raise AssertionError("no model call expected")

    rc, out, calls, txt = run(tmp_path, ["--save", "none", "--audit", "off", "--policy", str(FIX / "invented.policy.json")], call_llm=no_llm)
    assert rc == 0 and "extract: skipped" in txt and "model call" not in txt
    assert json.loads((out / "build_report.json").read_text())["extract"]["skipped"].endswith("invented.policy.json")
