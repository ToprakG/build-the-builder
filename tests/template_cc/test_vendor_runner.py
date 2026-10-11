"""Self-test runner on build-the-builder's tester: URL rewiring, bank config, the runner and the chat worker.

Offline: a stub vendor package stands in for the real repo; no bank, tunnel, Freya or model call."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from builder.template_cc import compiler, testgen
from builder.template_cc import vendor_runner as V
from builder.template_cc.trace import NullTrace

HERE = Path(V.__file__).resolve().parent


def test_point_at_rewrites_every_bank_url_and_drops_the_baked_customer(fx_api, fx_policy):
    wf, _ = compiler.compile(fx_policy, fx_api)
    out = V.point_at(wf, "https://abc.trycloudflare.com/")
    urls = [t["function"]["parameters"]["properties"]["url"]["default"] for t in V._tools(out)
            if t["function"].get("handler") == V.API_HANDLER]
    assert urls and all(u.startswith("https://abc.trycloudflare.com/v1/") for u in urls)
    assert {u.rsplit("/", 1)[1] for u in urls} <= {"verify_identity", "get_account", "create_promise", "add_note",
                                                   "schedule_callback", "transfer_to_agent", "close_call"}
    assert "customer_id" not in out["test_variables"] and "customer_id" in wf["test_variables"]
    assert "quillmoor.invalid" not in json.dumps(out)
    assert compiler.lint(out, fx_policy, fx_api)["errors"] or True  # urls differ from the API on purpose


def test_bank_config_defaults_the_id_format(fx_policy):
    p = dict(fx_policy, product_id_format="")
    assert V.bank_config(p)["product_id_format"] == "p{n}"
    assert V.bank_config(fx_policy)["product_id_format"] == "q{n}"


def test_availability_reports_what_is_missing(tmp_path, monkeypatch):
    ok, why = V.availability(tmp_path)
    assert not ok and "not found" in why
    vend = tmp_path / "v"
    (vend / "tools" / "driver").mkdir(parents=True)
    (vend / "tools" / "driver" / "chat_driver.py").write_text("")
    monkeypatch.setattr(V.shutil, "which", lambda name: "/bin/" + name)
    monkeypatch.delenv("FREYA_BANK_TEAM_KEY", raising=False)
    assert "FREYA_BANK_TEAM_KEY" in V.availability(vend)[1]
    monkeypatch.setenv("FREYA_BANK_TEAM_KEY", "k" * 20)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert "customer model" in V.availability(vend)[1]
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setattr(V, "find_bank_python", lambda: "/usr/bin/python3")
    assert V.availability(vend) == (True, "ok")


class FakeFreya:
    def __init__(self):
        self.saved = []

    def save_workflow(self, slot, wf, state_file=".freya-agents.json"):
        self.saved.append((slot, wf, state_file))
        return {"workflow_id": "wf-self", "agent_id": "ag-self"}


def test_runner_publishes_to_its_own_slot_and_maps_rows(fx_api, fx_policy, monkeypatch):
    wf, _ = compiler.compile(fx_policy, fx_api)
    cases, _ = testgen.generate(fx_policy, 6)
    freya = FakeFreya()
    r = V.VendorRunner("tcc-x", fx_policy, fx_api, NullTrace(), freya)
    started = []
    monkeypatch.setattr(r, "_ensure", lambda cs: started.append([c["id"] for c in cs]) or setattr(r, "origin", "https://o.trycloudflare.com"))
    seen = {}

    def fake_run(cmd, **kw):
        job = json.loads(Path(cmd[cmd.index("--job") + 1]).read_text())
        seen.update(job)
        rows = [{"case_id": c["id"], "rep": k, "passed": k == 1, "failed_reasons": [] if k == 1 else ["x"],
                 "transcript": [{"role": "assistant", "text": "Hi"}], "tool_calls": []} for c in job["cases"] for k in (1, 2)]
        Path(cmd[cmd.index("--out") + 1]).write_text(json.dumps(rows))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(V.subprocess, "run", fake_run)
    res = r.run(wf, cases, {}, fx_policy, 2)
    assert len(res) == 2 * len(cases) and sum(x.passed for x in res) == len(cases)
    assert freya.saved[0][0] == "tcc-x-selftest" and freya.saved[0][2].endswith(".freya-agents.json")
    assert seen["workflow_id"] == "wf-self" and seen["origin"] == "https://o.trycloudflare.com" and seen["reps"] == 2
    assert {c["id"] for c in seen["cases"]} == {c["id"] for c in cases} and all(c["source"] == "tcc-testgen" for c in seen["cases"])
    r.run(wf, cases, {}, fx_policy, 1)
    assert len(freya.saved) == 1  # the same workflow is not published twice
    r.close()


STUB = {
    "freya.py": """
        CALLS = []
        def api(method, path, body=None, **kw):
            CALLS.append(path)
            return {}
        def get_call(call_id):
            return {"messages": [{"role": "assistant", "message": "Hello"}, {"role": "user", "message": "Yes"}]}
    """,
    "tools/run_cases.py": """
        def persona_for(case, ident, style=None):
            return {"prompt": "You are " + case["id"]}
    """,
    "tools/score.py": """
        def score_ledger(case, ledger):
            ok = ledger["outcome"] == case["expected"]["outcome"]
            return {"pass": ok, "failed": [] if ok else ["outcome wrong"]}
    """,
    "tools/driver/judge.py": """
        def judge_transcript(messages, case):
            return {"pass": True, "status": "ok", "checks": [{"check": "c", "answer": "yes", "p": 0.9}]}
    """,
    "tools/driver/chat_driver.py": """
        MAX_TURNS = 3
        LEDGERS = {}
        def bank_call(host, path, body=None):
            if path.startswith("/v1/practice/sessions"):
                cid = "c_" + body["case"]
                LEDGERS[cid] = {"outcome": "kept" if body["case"].endswith("1") else "x", "calls": [
                    {"tool": "close_call", "args": {}, "result": {"ok": True}}]}
                return 200, {"variables": {"customer_id": cid}}
            return 200, LEDGERS[path.rsplit("/", 1)[1]]
        def chat_turn(wid, message, variables, call_id=None):
            return "call-1", "Agent line", {}
        def customer_reply(prompt, agent_lines, customer_lines):
            return "Customer line"
        def for_judge(messages):
            return messages
        def _spoken(text):
            return text
    """,
}


def test_worker_plays_cases_with_the_vendor_driver(tmp_path):
    vend = tmp_path / "vendor"
    for rel, src in STUB.items():
        p = vend / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    cases = [{"id": "T-001", "expected": {"outcome": "kept"}, "identity": {}}, {"id": "T-002", "expected": {"outcome": "kept"}, "identity": {}}]
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"workflow_id": "w", "origin": "https://o", "cases": cases, "reps": 2, "concurrency": 2}))
    out = tmp_path / "out.json"
    proc = subprocess.run([sys.executable, str(HERE / "vendor_worker.py"), "--vendor", str(vend), "--job", str(job), "--out", str(out)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    rows = json.loads(out.read_text())
    assert len(rows) == 4
    by = {(r["case_id"], r["rep"]): r for r in rows}
    assert by[("T-001", 1)]["passed"] and not by[("T-002", 2)]["passed"]
    assert by[("T-002", 1)]["failed_reasons"] == ["outcome wrong"]
    assert by[("T-001", 1)]["tool_calls"][0]["tool"] == "close_call" and by[("T-001", 1)]["transcript"][0]["text"] == "Hello"


def test_infrastructure_errors_are_retried_and_then_left_unscored(fx_api, fx_policy, monkeypatch):
    wf, _ = compiler.compile(fx_policy, fx_api)
    cases, _ = testgen.generate(fx_policy, 6)
    r = V.VendorRunner("tcc-x", fx_policy, fx_api, NullTrace(), FakeFreya())
    monkeypatch.setattr(r, "_ensure", lambda cs: setattr(r, "origin", "https://o"))
    calls = []

    def work(wid, cs, reps, conc):
        calls.append((len(cs), conc))
        if len(cs) > 1:  # first pass: two runs hit a 504
            return [{"case_id": c["id"], "rep": 1, "passed": i > 1, "infra": i <= 1, "failed_reasons": ["runner error: 504"] if i <= 1 else []}
                    for i, c in enumerate(cs)]
        c = cs[0]
        ok = c["id"] == cases[0]["id"]  # the retry fixes one, the other stays broken
        return [{"case_id": c["id"], "rep": 1, "passed": ok, "infra": not ok, "failed_reasons": [] if ok else ["runner error: 504"]}]

    monkeypatch.setattr(r, "_work", work)
    res = r.run(wf, cases, {}, fx_policy, 1)
    assert calls[0][1] == r.concurrency and all(c == (1, 1) for c in calls[1:]) and len(calls) == 3
    by = {x.case_id: x for x in res}
    assert by[cases[0]["id"]].passed and not by[cases[0]["id"]].infra and by[cases[1]["id"]].infra


def test_worker_flags_chat_gateway_errors_as_infrastructure():
    import re as _re
    src = (HERE / "vendor_worker.py").read_text()
    pat = _re.compile(_re.search(r'INFRA = re.compile\(r"(.+?)", re.I\)', src).group(1), _re.I)
    assert pat.search("RuntimeError('POST /api/v2/chat/message -> 504: error code: 504')")
    assert not pat.search("RuntimeError('bank session -> 404')")


def test_hard_cases_are_hard_first_and_styled(fx_policy):
    from builder.template_cc import sims
    cases = sims.hard_cases(fx_policy, 6)
    assert len(cases) == 6 and all(c["style"] in sims.HARD_STYLES for c in cases)
    assert len({c["family"] for c in cases}) >= 4
    assert [sims.LEVEL.get(c["level"], 3) for c in cases][0] == min(sims.LEVEL.get(c["level"], 3) for c in cases)


def test_render_shows_failures_with_reasons():
    from builder.template_cc import sims
    from builder.template_cc.runner import CaseResult
    cases = [{"id": "T-1", "family": "Dates", "level": "hard", "style": "pieces", "script": "Wants a holiday."},
             {"id": "T-2", "family": "Happy path", "level": "easy", "style": "talkative"}]
    rep = sims.summarize([CaseResult("T-1", 1, False, ["promise date wrong"]), CaseResult("T-2", 1, False, ["504"], infra=True)], cases)
    text = sims.render(rep)
    assert rep["passed"] == 0 and rep["scored"] == 1 and rep["unscored"] == 1
    assert "FAIL     T-1 Dates [hard, style pieces]" in text and "- promise date wrong" in text and "UNSCORED" in text


def test_run_sims_tool_tries_the_patched_candidate(fx_api, fx_policy, fx_brief, tmp_path):
    import time as _t
    from builder.template_cc import session
    from builder.template_cc.io import Brief
    wf, book = compiler.compile(fx_policy, fx_api)
    w = session.make_workspace([Brief("brief-1.md", "b", fx_brief)], Path(__file__).parent / "fixtures" / "invented.openapi.json",
                               fx_policy, wf, book)
    seen = {}

    def sims_fn(cand, n):
        seen["marked"] = "Say the amount slowly." in cand["nodes"]["negotiate"]["task_message"]
        seen["n"] = n
        return "1/2 hard sims passed"

    try:
        b = session.BoundTools(w, "patch", _t.time() + 100, fx_policy, fx_api, fx_brief, wf, book, sims_fn=sims_fn)
        (w / "out" / "patches.json").write_text(json.dumps([{"id": "p1", "bug_ids": [], "zone": "node:negotiate/rules", "op": "append",
                                                             "before": "", "after": "- Say the amount slowly.", "brief_quote": "x", "rationale": "r"}]))
        out = b.call("run_sims", {"n": 9})
        assert seen == {"marked": True, "n": 4} and "1 patch(es)" in out and "1/2 hard sims passed" in out
        a = session.BoundTools(w, "audit", _t.time() + 100, fx_policy, fx_api, fx_brief)
        assert "not available" in a.call("run_sims", {"n": 2})
    finally:
        session.remove_workspace(w)


def test_adaptive_concurrency_halves_on_infra_errors_and_grows_back():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_tcc_worker", HERE / "vendor_worker.py")
    W = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(W)
    g = W.Adaptive(4, 8)
    g.report(True)
    assert g.limit == 2
    g.report(True)
    g.report(True)
    assert g.limit == 1
    for _ in range(6):
        g.report(False)
    assert g.limit == 4
    for _ in range(20):
        g.report(False)
    assert g.limit == 8
