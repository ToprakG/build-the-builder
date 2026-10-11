"""Offline tests of the self-test tooling (no Freya, no model): scenario skeletons, scoring modes, the R2 allow-list, the R4 guard."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO))
FX = Path(__file__).parent / "fixtures"


def policy():
    return json.loads((FX / "invented.policy.json").read_text())


def test_skeletons_are_policy_driven_and_valid():
    import selftest_cases as S
    p = policy()
    sk = S.skeletons(p, S.Ctx(p, 7))
    assert sk, "at least one scenario"
    om = p["outcome_map"]
    for c in sk:
        assert c["expected"]["outcome"] is not None
        assert c["products"] and set(c["identity"]) == set(p["identity"]["questions"])
        if c["rule"] == "dispute":
            assert [c["expected"]["outcome"], c["expected"]["reason"]] == om["np_dispute"]
    assert [c["rule"] for c in sk] == sorted([c["rule"] for c in sk], key=lambda r: S.PRIORITY.index(r))


def test_r2_allow_list_is_parametric_only():
    import selftest_repair as R
    for ok in ("f17_anything", "f19_refusal_rule", "f22_forbidden_disclosure", "f24_day_once", "f25_dispute_wins"):
        assert R.r2_ok(ok)
    for core in ("f1_id_complete", "f7_router", "f15_hardship_terms", "f18_side_resume", "f20_once_flags", "f21_spoken_answers", "f13_verifier"):
        assert not R.r2_ok(core)


def test_r4_only_for_marked_unknown_rules():
    import selftest_repair as R
    assert not R.is_unknown({"rule": "dispute"})
    assert not R.is_unknown({"rule": "unknown_rule", "unknown": True})  # no brief quote
    assert R.is_unknown({"rule": "unknown_rule", "unknown": True, "quotes": ["x"]})


def test_check_patch_needs_the_brief_sentence_verbatim():
    import selftest_repair as R
    brief = "Always say the branch code. Never promise a waiver."
    ok = {"zone": "node:x/rules", "op": "append", "before": "", "after": "Never promise a waiver. IF asked for a waiver THEN decline.",
          "brief_quote": "Never promise a waiver."}
    assert isinstance(R.check_patch(ok, brief, [{"id": "c", "variables": {}, "identity": {}, "products": []}]), dict)
    assert isinstance(R.check_patch({**ok, "after": "Decline waivers."}, brief, []), str)
    assert isinstance(R.check_patch({**ok, "brief_quote": "Not in the brief."}, brief, []), str)


def test_judge_ledger_mode_keeps_only_structural_checks():
    import types
    stub = types.ModuleType("freya")
    stub.api = lambda *a, **k: None
    sys.modules.setdefault("freya", stub)
    try:
        import selftest_run as SR
    except Exception:  # vendor tester not importable here
        import pytest
        pytest.skip("vendor tester not available")
    case = {"ledger": "judge", "expected": {}}
    led = {"outcome": "x", "reason": None, "promises": [], "notes": [], "callbacks": [], "transfers": [],
           "calls": [{"tool": "close_call", "result": {}}]}
    assert SR.score_case(case, led)["pass"]


# --- LLM calls go through llm.py: TCC_LLM_PROVIDER=anthropic needs no Claude Code login -------------------------------------------------
def _mock_anthropic(monkeypatch, replies):
    import subprocess
    from builder.template_cc import llm
    calls = []

    def fake(url, headers, payload, timeout):
        calls.append((url, payload))
        return {"content": [{"type": "text", "text": json.dumps(replies.pop(0))}], "usage": {"input_tokens": 1, "output_tokens": 1}}

    def no_cli(*a, **k):
        raise AssertionError("the claude CLI / a subprocess must not be used with TCC_LLM_PROVIDER=anthropic")
    monkeypatch.setattr(llm, "TRANSPORT", fake)
    monkeypatch.setattr(llm, "load_env", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", no_cli)
    monkeypatch.setattr(subprocess, "Popen", no_cli)
    monkeypatch.setenv("TCC_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    return calls


def test_patcher_diagnosis_uses_llm_py_only(monkeypatch):
    import selftest_repair as R
    calls = _mock_anthropic(monkeypatch, [{"diagnosis": "the agent skipped a rule the brief states", "action": "none"}])
    row = {"case": "ST-01", "failed": ["conversation (Freya judge): x"], "ledger": {}, "tools": []}
    case = {"rule": "dispute", "script": "s", "expected": {}, "criteria": []}
    act = R.diagnose(row, case, policy(), "brief text", ["f7_router"], "/nonexistent", set())
    assert act["action"] == "none" and len(calls) == 1
    assert calls[0][0].startswith("https://api.anthropic.com") and calls[0][1]["model"] == "claude-sonnet-5-5"


def test_scenario_writer_uses_llm_py_only(monkeypatch):
    import selftest_cases as S
    calls = _mock_anthropic(monkeypatch, [{"scenarios": [{"id": "ST-01", "script": "x", "criteria": []}]}])
    out = S.write_personas("brief", [{"id": "ST-01", "rule": "dispute", "facts": "f", "expected": {"outcome": "o", "reason": None}}], None)
    assert out["scenarios"][0]["id"] == "ST-01" and calls[0][0].startswith("https://api.anthropic.com")


def test_final_trace_appends_scenarios_and_actions(tmp_path):
    import subprocess
    out, base = tmp_path / "out", tmp_path / "out" / "r0"
    (base / "selftest").mkdir(parents=True)
    tdir = tmp_path / "trace"
    tdir.mkdir()
    (tdir / "trace.txt").write_text("start\n")
    (tdir / "events.jsonl").write_text("")
    (base / "build_report.json").write_text(json.dumps({"slot": "final-x", "trace": str(tdir), "saved": {"workflow_id": "w1", "agent_id": "a1"}}))
    (base / "workflow.json").write_text("{}")
    (out / "selftest").mkdir()
    (out / "selftest" / "cases.json").write_text(json.dumps([{"id": "ST-01", "title": "t", "rule": "dispute"}]))
    (base / "selftest" / "results.json").write_text(json.dumps({"passed": 0, "cases": 1, "seconds": {}, "rows": [
        {"case": "ST-01", "rule": "dispute", "pass": False, "freya_judge": False, "failed": ["outcome (a, b), expected (c, d)", "conversation (Freya judge): bad"]}]}))
    (out / "patch_report.json").write_text(json.dumps({"start": str(base), "rounds": [{"round": 1, "actions": [
        {"case": "ST-01", "action": "policy_field", "pointer": "/x", "how": "ledger diff -> R1"}], "used": ["ST-01"], "kept": False, "result": "reverted", "fixed": [], "broken": ["ST-02"]}]}))
    env = {**__import__("os").environ, "FINAL_SAVE": "none"}
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "final_trace.py"), "--out", str(out), "--base", str(base), "--green", str(base)], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    ev = [json.loads(x) for x in (tdir / "events.jsonl").read_text().splitlines()]
    assert [e["type"] for e in ev if e["type"] in ("selftest_scenario", "patcher_action")] == ["selftest_scenario", "patcher_action"]
    sc = next(e for e in ev if e["type"] == "selftest_scenario")
    assert sc["passed"] is False and "bad" in sc["judge_verdict"] and sc["ledger_diff"].startswith("outcome")
    pa = next(e for e in ev if e["type"] == "patcher_action")
    assert pa["reverted"] is True and pa["field"] == "/x"
    assert "REVERTED" in (tdir / "trace.txt").read_text()
    assert json.loads((out / "build_report.json").read_text())["saved"]["workflow_id"] == "w1"


def test_final_save_retries_only_on_5xx(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("final_trace", REPO / "scripts" / "final_trace.py")
    ft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ft)
    import selftest_common
    monkeypatch.setattr(selftest_common.time, "sleep", lambda s: None)
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] < 3:
            raise RuntimeError("POST /api/v2/workflow -> 502 Bad Gateway")
        return "ok"
    assert ft.retry_5xx(flaky) == "ok" and n["c"] == 3
    n["c"] = 0

    def always():
        n["c"] += 1
        raise RuntimeError("503 unavailable")
    try:
        ft.retry_5xx(always)
        raise AssertionError("must fail after 3 tries")
    except RuntimeError:
        assert n["c"] == 3
    n["c"] = 0

    def bad():
        n["c"] += 1
        raise RuntimeError("400 invalid workflow")
    try:
        ft.retry_5xx(bad)
    except RuntimeError:
        assert n["c"] == 1


# --- outage guard and the Freya 5xx retry -----------------------------------------------------------------------------------------------
def test_outage_signature():
    import selftest_common as C
    quiet = [{"case": "a", "tools": []}, {"case": "b", "tools": []}]
    assert C.outage_signature(quiet) and C.is_outage({"rows": quiet})
    assert not C.outage_signature([{"case": "a", "tools": ["verify_identity"]}, {"case": "b", "tools": []}])
    assert C.outage_signature([], missing=["a", "b"]) and not C.outage_signature([{"case": "a", "tools": []}])  # one scenario is not enough
    assert C.is_outage({"outage": True, "rows": []})


def test_freya_calls_retry_on_5xx_only(monkeypatch):
    import types
    import selftest_common as C
    monkeypatch.setattr(C.time, "sleep", lambda s: None)
    calls = []
    fake = types.ModuleType("fakefreya")

    def api(method, path, body=None, **params):
        calls.append((method, path))
        if len(calls) < 3:
            raise RuntimeError(f"{method} {path} -> 502: bad gateway")
        return {"ok": True}
    fake.api = api
    fake.get_call = lambda cid: fake.api("GET", f"/api/v2/call/{cid}")   # resolved through the module global, like freya.py
    C.install(fake)
    assert fake.get_call("c1") == {"ok": True} and len(calls) == 3
    C.install(fake)  # idempotent
    calls.clear()
    fake.api = C.install(types.SimpleNamespace(api=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("POST x -> 400: bad request")))).api
    try:
        fake.api("POST", "/x")
        raise AssertionError("a 400 must not be retried silently")
    except RuntimeError as e:
        assert "400" in str(e)


def test_repair_does_not_act_on_an_outage_run(tmp_path, monkeypatch, capsys):
    import selftest_repair as R
    b = tmp_path / "build"
    (b / "selftest").mkdir(parents=True)
    (b / "policy.json").write_text((FX / "invented.policy.json").read_text())
    (b / "build_report.json").write_text(json.dumps({"features": []}))
    (tmp_path / "brief.md").write_text((FX / "invented.brief.md").read_text())
    (tmp_path / "api.json").write_text((FX / "invented.openapi.json").read_text())
    (tmp_path / "cases.json").write_text(json.dumps([{"id": "ST-01", "rule": "dispute"}, {"id": "ST-02", "rule": "robot_turn1"}]))
    rows = [{"case": "ST-01", "rule": "dispute", "pass": False, "failed": ["x"], "tools": [], "ledger": {}},
            {"case": "ST-02", "rule": "robot_turn1", "pass": False, "failed": ["y"], "tools": [], "ledger": {}}]
    (b / "selftest" / "results.json").write_text(json.dumps({"rows": rows, "passed": 0, "cases": 2, "missing": []}))
    monkeypatch.setattr(R.llm, "call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no model call on an outage run")))
    monkeypatch.setattr(sys, "argv", ["x", "--dir", str(b), "--work", str(tmp_path / "w"), "--briefs", str(tmp_path / "brief.md"),
                                     "--api", str(tmp_path / "api.json"), "--cases", str(tmp_path / "cases.json")])
    R.main()
    assert capsys.readouterr().out.strip().startswith("FINAL " + str(b))
    rep = json.loads((tmp_path / "w" / "patch_report.json").read_text())
    assert rep["status"] == "outage" and "outage signature" in rep["outage"]


def test_static_precheck_never_touches_policy_fixups_fields_and_needs_two_agreeing_answers(monkeypatch):
    import selftest_repair as R
    p = policy()
    assert R.verify_field(p, "/ask_limits/id_decline", "x", "brief") == (None, "this field is set by policy_fixups from explicit brief phrases")
    ptr = next(k for k in ("/identity/max_wrong", "/callback/start") if R.get_ptr(p, k) is not R.extract.extract_vote.ABSENT)
    p["_source"] = {ptr: "q"}
    answers = iter([({**p}, "quote"), ({**p}, "quote")])
    seen = []

    def fake_verify(pol, pt, diag, brief):
        seen.append(pt)
        a = next(answers)
        new = json.loads(json.dumps(pol))
        R.extract._set(new, pt, 99 if len(seen) == 1 else 98)   # two different answers
        return new, "quote"
    monkeypatch.setattr(R, "precheck", lambda pol, brief, d=None: [ptr])
    monkeypatch.setattr(R, "verify_field", fake_verify)
    new, log = R.precheck_fix(p, "brief")
    assert len(seen) == 2 and "new" not in log[0] and R.get_ptr(new, ptr) == R.get_ptr(p, ptr)


def test_llm_preflight_ok_and_fails_clearly(monkeypatch):
    import llm_preflight as P
    from builder.template_cc import llm
    calls = []
    monkeypatch.delenv("TCC_LLM_PROVIDER", raising=False)

    def cli(system, user, name, max_tokens, timeout, trace):
        calls.append((name, timeout))
        return "OK", {}
    monkeypatch.setattr(llm, "_claude_cli", cli)
    ok, msg = P.preflight()
    assert ok and "provider claude_cli" in msg and len(calls) == 1 and calls[0][1] <= 25   # default: claude_cli:sonnet, one call, short timeout

    def down(*a, **k):
        raise llm.LLMError("claude usage limit reached", status=429)
    monkeypatch.setattr(llm, "_claude_cli", down)
    ok, msg = P.preflight()
    assert not ok and "FAILED" in msg and "usage limit" in msg and "no other provider" in msg and "do not loop" in msg

    monkeypatch.setenv("TCC_LLM_PROVIDER", "anthropic")   # variant B: needs a key
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    ok, msg = P.preflight()
    assert not ok and "anthropic" in msg and "ANTHROPIC_API_KEY" in msg
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    seen = []
    monkeypatch.setattr(llm, "TRANSPORT", lambda url, h, body, t: seen.append((url, body["model"])) or {"content": [{"type": "text", "text": "OK"}], "usage": {}})
    ok, msg = P.preflight()
    assert ok and seen == [("https://api.anthropic.com/v1/messages", "claude-sonnet-5-5")]

    monkeypatch.setenv("TCC_LLM_PROVIDER", "bogus")
    ok, msg = P.preflight()
    assert not ok and "bogus" in msg


def test_final_flags_file_precedence(tmp_path):
    import subprocess
    sh = str(REPO / "scripts" / "final_flags.sh")
    root, main = tmp_path / "r", tmp_path / "m"
    root.mkdir(); main.mkdir()
    run = lambda: subprocess.run(["bash", sh, str(root), str(main)], capture_output=True, text=True)
    assert run().returncode == 1                                   # nothing there: no output, exit 1
    (main / "M1_FLAGS.txt").write_text("f1, f2\n")
    assert run().stdout.strip() == "f1,f2"                         # default: M1_FLAGS.txt, spaces and newline stripped
    (root / "M1_FLAGS.txt").write_text("f1,f2,f3\n")
    assert run().stdout.strip() == "f1,f2,f3"
    (main / "FINAL_FLAGS.txt").write_text("m2a,m2b\n")
    assert run().stdout.strip() == "m2a,m2b"                       # FINAL_FLAGS.txt (m2) beats every M1 file
    (root / "FINAL_FLAGS.txt").write_text("\n")                    # an empty override is ignored
    assert run().stdout.strip() == "m2a,m2b"


def test_infra_error_transcript_is_missing_and_half_infra_is_an_outage():
    import selftest_common as C
    dead = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "function_call_error close_call: Error code: 530 - cloudflare"}]
    fine = [{"role": "assistant", "content": "Thanks, I have noted that."}]
    ok_call = [{"role": "assistant", "tool_calls": [{"function": {"name": "get_account", "arguments": "{\"url\": \"https://x-y.trycloudflare.com/v1/get_account\"}"}}]}]
    assert C.infra_error(dead) and not C.infra_error(fine) and not C.infra_error(ok_call)   # a tunnel URL in a good tool call is not an error
    src = (REPO / "scripts" / "selftest_run.py").read_text()
    assert "infra_error(transcript)" in src and "2 * len(infra) >= len(chosen)" in src   # wired: infra rows are MISSING, half of them = outage


# --- the export of an unchanged first build (builder/build.py copies <out>/<slot>.workflow.json to workflows/) ------------------------------
def _run_final_trace(tmp_path, monkeypatch, env, export):
    import importlib.util
    import types
    out = tmp_path / "out"
    base = out / "r0"
    (tmp_path / "trace").mkdir()
    base.mkdir(parents=True)
    (base / "workflow.json").write_text("{}")
    (base / "build_report.json").write_text(json.dumps({"slot": "s", "trace": str(tmp_path / "trace"), "saved": {"workflow_id": "w1", "agent_id": "a1"}}))
    fake = types.ModuleType("freya")
    fake.export_workflow = export
    monkeypatch.setitem(sys.modules, "freya", fake)
    for k in ("FINAL_SAVE", "FREYA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("final_trace", REPO / "scripts" / "final_trace.py")
    ft = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ft)
    monkeypatch.setattr(sys, "argv", ["final_trace.py", "--out", str(out), "--base", str(base), "--green", str(base), "--slot", "tcc-x"])
    monkeypatch.setattr(ft.time, "sleep", lambda s: None) if hasattr(ft, "time") else None
    ft.main()
    return out, tmp_path / "trace"


def test_unchanged_first_build_is_exported_for_build_py(tmp_path, monkeypatch):
    got = []

    def export(wid, path):
        got.append(wid)
        Path(path).write_text('{"exported": true}')
    out, tdir = _run_final_trace(tmp_path, monkeypatch, {"FINAL_SAVE": "freya", "FREYA_API_KEY": "k"}, export)
    assert got == ["w1"] and (out / "tcc-x.workflow.json").exists()
    assert "exported workflow w1" in (tdir / "trace.txt").read_text()


def test_no_export_when_not_saving_or_when_the_export_keeps_failing(tmp_path, monkeypatch):
    def export(wid, path):
        raise AssertionError("must not be called")
    out, _ = _run_final_trace(tmp_path, monkeypatch, {"FINAL_SAVE": "none", "FREYA_API_KEY": "k"}, export)
    assert not list(out.glob("*.workflow.json"))
    n = {"c": 0}

    def down(wid, path):
        n["c"] += 1
        raise RuntimeError("502 Bad Gateway")
    import selftest_common
    monkeypatch.setattr(selftest_common.time, "sleep", lambda s: None)
    tmp2 = tmp_path / "second"
    tmp2.mkdir()
    out2, t2 = _run_final_trace(tmp2, monkeypatch, {"FINAL_SAVE": "freya", "FREYA_API_KEY": "k"}, down)
    assert n["c"] == 3 and not list(out2.glob("*.workflow.json"))                    # 3 tries, then a freya_error event, the run goes on
    assert "freya_error" in (t2 / "events.jsonl").read_text() and (out2 / "build_report.json").exists()
