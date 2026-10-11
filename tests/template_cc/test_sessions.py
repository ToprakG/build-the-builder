"""Phase 4: guard, MCP tools, audit and patcher sessions (mocked SDK query), analyzer (mocked LLM)."""

import json
import os
import time
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from builder.template_cc import analyze, audit, compiler, guard, patcher, session
from builder.template_cc.io import Brief, Models
from builder.template_cc.trace import NullTrace
from tests.template_cc.conftest import FIX, REPO, load


# --- guard -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    (w / "input").mkdir(parents=True)
    (w / "out").mkdir()
    (w / "input" / "brief-1.md").write_text("x")
    return w


@pytest.mark.parametrize("tool, inp, ok", [
    ("Read", {"file_path": "input/brief-1.md"}, True),
    ("Write", {"file_path": "out/patches.json"}, True),
    ("Write", {"file_path": "input/brief-1.md"}, False),
    ("Write", {"file_path": "current/zones.json"}, False),
    ("Read", {"file_path": "../testbed/configs/brief-a.config.json"}, False),
    ("Read", {"file_path": "/etc/passwd"}, False),
    ("Read", {"file_path": str(REPO / "README.md")}, False),
    ("Read", {"file_path": "input/cases.json"}, False),
    ("Read", {"file_path": "input/config.json"}, False),
    ("Read", {"file_path": "out/oracle.txt"}, False),
    ("Glob", {"pattern": "../**/*.json"}, False),
    ("Glob", {"pattern": "/tmp/*"}, False),
    ("Glob", {"pattern": "input/*.md"}, True),
    ("Grep", {"pattern": "deadline", "path": "input"}, True),
    ("Grep", {"pattern": "x", "path": "../.."}, False),
    ("Bash", {"command": "ls"}, False),
    ("WebFetch", {"url": "https://example.com"}, False),
    ("mcp__tcc__preview_patches", {}, True),
    ("mcp__other__x", {}, False),
])
def test_guard(ws, tool, inp, ok):
    assert guard.check(tool, inp, ws)[0] is ok


def test_guard_denies_symlink_escape(ws, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    os.symlink(secret, ws / "input" / "link.txt")
    assert guard.check("Read", {"file_path": "input/link.txt"}, ws)[0] is False
    os.symlink(tmp_path, ws / "out" / "up")
    assert guard.check("Write", {"file_path": "out/up/x.json"}, ws)[0] is False


def test_guard_hook_shape(ws):
    import asyncio
    hook = guard.make_hook(ws)
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}, "id", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(hook({"tool_name": "Read", "tool_input": {"file_path": "input/brief-1.md"}}, "id", None)) == {}


def test_mcp_tools_take_no_path_parameters():
    for name, (desc, schema) in session.TOOL_SPECS.items():
        assert all(t is int for t in schema.values()) and not any("path" in k or "file" in k for k in schema), name
    assert session.tool_names() == ["mcp__tcc__zones", "mcp__tcc__preview_patches", "mcp__tcc__time_left", "mcp__tcc__run_sims"]


# --- workspace + mocked sessions ------------------------------------------------------------------------------------

@pytest.fixture
def build(fx_api, fx_policy, fx_brief):
    wf, book = compiler.compile(fx_policy, fx_api)
    briefs = [Brief("brief-1.md", "x/brief.md", fx_brief)]
    return {"wf": wf, "book": book, "policy": fx_policy, "api": fx_api, "brief": fx_brief, "briefs": briefs,
            "api_path": FIX / "invented.openapi.json"}


FAILURE = {"case_id": "T-004", "rep": 2, "passed": False, "failed_reasons": ["promise date after the latest date"],
           "transcript": [{"role": "agent", "text": "Hello"}, {"role": "user", "text": "Yes"},
                          {"role": "agent", "text": "I recorded it for the 20th."}],
           "tool_calls": [{"tool": "create_promise", "args": {"pay_on": "2031-03-20"}, "result": {"ok": True}}],
           "expected": {"outcome": "kept"}, "oracle": {"outcome_key": "promise"}}


def test_workspace_holds_only_the_listed_files_and_no_expected_data(build):
    w = session.make_workspace(build["briefs"], build["api_path"], build["policy"], build["wf"], build["book"],
                               report={"bugs": []}, failures=[FAILURE])
    try:
        files = sorted(str(p.relative_to(w)) for p in w.rglob("*") if p.is_file())
        assert files == ["current/policy.json", "current/workflow.json", "current/zones.json", "input/brief-1.md",
                         "input/tools.openapi.json", "report/bugs.json", "report/failures/T-004-r2.json"]
        assert not str(w).startswith(str(REPO))
        text = "".join(p.read_text() for p in w.rglob("*") if p.is_file())
        assert "oracle" not in text and '"expected"' not in text and "testbed" not in text
        assert not os.access(w / "current" / "workflow.json", os.W_OK)
    finally:
        session.remove_workspace(w)
    assert not w.exists()


def scripted_query(write: dict, turns: int = 2, sleep: float = 0.0, seen: list | None = None):
    async def q(prompt, options):
        if seen is not None:
            seen.append(options)
        cwd = Path(options.cwd)
        for i in range(turns):
            yield AssistantMessage(content=[TextBlock(text=f"thinking {i}"), ToolUseBlock(id=f"t{i}", name="Read", input={"file_path": "input/brief-1.md"})], model="claude-test")
        if sleep:
            import anyio
            await anyio.sleep(sleep)
        for rel, obj in write.items():
            (cwd / rel).write_text(json.dumps(obj))
        yield AssistantMessage(content=[ToolUseBlock(id="w", name="Write", input={"file_path": "out/x"})], model="claude-test")
        yield ResultMessage(subtype="success", duration_ms=10, duration_api_ms=5, is_error=False, num_turns=turns + 1,
                            session_id="s", total_cost_usd=0.12, usage={"input_tokens": 5})
    return q


def test_patcher_session_picks_up_patches_and_traces_every_turn(build, tmp_path):
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)
    tr = NullTrace()
    patch = {"id": "p1", "bug_ids": ["b1"], "zone": "node:negotiate/rules", "op": "append", "before": "",
             "after": "- Check the latest date before the read-back.", "brief_quote": "Holidays", "rationale": "r"}
    seen = []
    rec = patcher.run_patcher(build["briefs"], build["brief"], build["api_path"], build["api"], build["policy"],
                              build["wf"], build["book"], {"bugs": [{"bug_id": "b1"}]}, [FAILURE], Models(), tr,
                              time.time() + 600, out, 1, query_fn=scripted_query({"out/patches.json": [patch]}, seen=seen),
                              server=object())
    assert rec["patches"] == [patch] and rec["error"] is None and rec["session"]["turns"] == 3
    turn_lines = [l for l in tr.lines if l.startswith("model call claude-test: patcher iter 1 turn")]
    assert len(turn_lines) == 3 and "Read(" in turn_lines[0]
    assert abs(tr.cost - 0.12) < 1e-9
    o = seen[0]
    assert o.tools == ["Read", "Glob", "Grep", "Write"] and o.permission_mode == "dontAsk" and "Bash" in o.disallowed_tools
    assert o.setting_sources == ["project"] and isinstance(o.system_prompt, str) and o.max_turns == 40
    assert set(o.allowed_tools) >= set(session.tool_names()) and o.max_budget_usd == Models().patch_budget_usd
    assert not Path(o.cwd).exists()  # workspace removed
    assert json.loads((out / "patches" / "iter1.proposed.json").read_text()) == [patch]


def test_session_skipped_without_time(build, tmp_path):
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)
    tr = NullTrace()
    rec = patcher.run_patcher(build["briefs"], build["brief"], build["api_path"], build["api"], build["policy"],
                              build["wf"], build["book"], {"bugs": []}, [], Models(), tr, time.time() + 600, out, 2,
                              query_fn=scripted_query({"out/patches.json": []}, sleep=5), server=object(), seconds=15)
    # a 15 s budget is below the 20 s minimum: the session is skipped, nothing hangs
    assert rec["session"]["error"] == "not enough time left" and rec["patches"] == []


def test_session_timeout_kills(tmp_path, monkeypatch):
    reaped = []
    monkeypatch.setattr(session, "reap_children", lambda grace_s=3.0: reaped.append(1) or 0)
    tr = NullTrace()
    t0 = time.time()
    state = session.run("t", "s", tmp_path, None, "m", tr, "audit", 20.2, query_fn=scripted_query({}, sleep=60), server=object())
    assert state["timed_out"] and reaped and time.time() - t0 < 30
    assert any("timed out" in l for l in tr.lines)


def test_audit_applies_only_judged_patches(build, tmp_path):
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)
    good = {"id": "a1", "path": "/callback/start", "old_value": "09:30", "new_value": "09:30", "brief_quote": "09:30 to 16:30", "rationale": "r"}
    bad = {"id": "a2", "path": "/bands/0/deadline_business_days", "old_value": 5, "new_value": 9, "brief_quote": "nine days", "rationale": "r"}

    class Judge:
        def judge_policy(self, p, ctx):
            from builder.template_cc.judge import Verdict
            return Verdict(accepted=p["id"] == "a1", checks=[], reasons=[] if p["id"] == "a1" else ["no"])

    tr = NullTrace()
    pol, rec = audit.run_audit(build["briefs"], build["brief"], build["api_path"], build["api"], build["policy"], Models(),
                               tr, Judge(), time.time() + 900, out, query_fn=scripted_query({"out/policy_patches.json": [good, bad]}),
                               server=object())
    assert [p["id"] for p in rec["applied"]] == ["a1"] and pol["bands"][0]["deadline_business_days"] == 5
    assert any("audit turn" in l for l in tr.lines)
    pol2, rec2 = audit.run_audit(build["briefs"], build["brief"], build["api_path"], build["api"], build["policy"], Models(),
                                 tr, None, time.time() + 900, out, query_fn=scripted_query({"out/policy_patches.json": [good]}),
                                 server=object())
    assert rec2["applied"] == [] and rec2["verdicts"][0]["reasons"] == ["no judge: logged only"]


def test_audit_failure_keeps_the_policy(build, tmp_path):
    out = tmp_path / "out"
    (out / "patches").mkdir(parents=True)

    async def boom(prompt, options):
        raise RuntimeError("cli missing")
        yield  # pragma: no cover

    pol, rec = audit.run_audit(build["briefs"], build["brief"], build["api_path"], build["api"], build["policy"], Models(),
                               NullTrace(), None, time.time() + 900, out, query_fn=boom, server=object())
    assert pol == build["policy"] and rec["proposed"] == []


def test_bound_tools_preview(build, tmp_path):
    w = session.make_workspace(build["briefs"], build["api_path"], build["policy"], build["wf"], build["book"])
    try:
        b = session.BoundTools(w, "patch", time.time() + 100, build["policy"], build["api"], build["brief"], build["wf"], build["book"])
        assert "does not exist" in b.call("preview_patches")
        (w / "out" / "patches.json").write_text(json.dumps([
            {"id": "p1", "bug_ids": [], "zone": "node:negotiate/rules", "op": "append", "before": "", "after": "- Be brief.",
             "brief_quote": "Holidays", "rationale": "r"},
            {"id": "p2", "bug_ids": [], "zone": "system/core", "op": "append", "before": "", "after": "x", "brief_quote": "q", "rationale": "r"}]))
        text = b.call("preview_patches")
        assert "p1: applied" in text and "p2: NOT applied" in text and "lint: 0 error(s)" in text and "+- Be brief." in text
        assert "seconds left" in b.call("time_left") and "node:negotiate/rules" in b.call("zones")
        a = session.BoundTools(w, "audit", time.time() + 100, build["policy"], build["api"], build["brief"])
        (w / "out" / "policy_patches.json").write_text(json.dumps([{"id": "a1", "path": "/queue_map/human", "new_value": "zzz",
                                                                    "brief_quote": "Holidays", "rationale": "r"}]))
        assert "policy_check after all patches: 1 violation" in a.call("preview_patches")
    finally:
        session.remove_workspace(w)


# --- analyzer --------------------------------------------------------------------------------------------------------

def test_analyzer_keeps_only_bugs_with_evidence(build):
    def fake(system, user, model, json_schema=None, **kw):
        assert "T-004" in user and "oracle" not in user and '"expected"' not in user
        return {"bugs": [
            {"bug_id": "b1", "behaviour": "records past the latest date", "expected_per_brief": "offer the latest date",
             "brief_quote": "Holidays", "evidence": [{"case_id": "T-004", "rep": 2, "turn": 2}], "suspected_zone": "node:negotiate/rules", "severity": "high"},
            {"bug_id": "b2", "behaviour": "x", "expected_per_brief": "y", "brief_quote": "z", "evidence": [], "suspected_zone": "", "severity": "low"},
            {"bug_id": "b3", "behaviour": "x", "expected_per_brief": "y", "brief_quote": "z",
             "evidence": [{"case_id": "T-004", "rep": 2, "turn": 99}], "suspected_zone": "no/such", "severity": "low"}]}

    rep = analyze.analyze([FAILURE], build["brief"], build["book"], fake, "m", NullTrace())
    assert [b["bug_id"] for b in rep["bugs"]] == ["b1"] and len(rep["dropped"]) == 2
    ev = analyze.evidence_text(rep["bugs"], [FAILURE])
    assert "T-004 rep 2 turn 2 AGENT: I recorded it" in ev["b1"]
    assert "expected" not in analyze.trim(FAILURE) and "oracle" not in analyze.trim(FAILURE)
