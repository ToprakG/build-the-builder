"""Score the seven fake briefs, then run the patch loop.

Phase 1 builds each brief with tcc90 (Sonnet, no patch loop) and scores the
first 10 cases on Freya, 3 reps. The builder is given a copy of the brief and
the tool API only.

Phase 2 starts after every normal score is written. It runs the T-CC patch
loop. The loop's cases are generated from the policy. It does not read the
testbed cases. A patched workflow is scored again only when the loop commits.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from envfile import load_env

load_env()

from tcc90.boot import install

install()

FAKES = (
    "f1-nordhavn",
    "f2-ostral",
    "f3-kestrel",
    "f4-brightwell",
    "f5-halden-email",
    "f6-corvid-memo",
    "f7-lumen-codes",
)
LIMIT = 10
REPS = 3
PY = str(ROOT / ".venv" / "bin" / "python")
MODELS = ROOT / "results" / "tcc90" / "sonnet-models.json"


def say(text):
    print(text, flush=True)


def copy_inputs(brief_id: str) -> Path:
    src = ROOT / "testbed" / "fake_briefs" / brief_id
    dest = ROOT / "results" / "tcc90" / "fakes" / brief_id / "input"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "brief.md", dest / "brief.md")
    shutil.copyfile(src / "tools.openapi.json", dest / "tools.openapi.json")
    return dest


def workflow_ok(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text())
    return bool((data.get("nodes") or {}))


def build(brief_id: str) -> bool:
    out = ROOT / "results" / "tcc90" / "fakes" / brief_id
    if workflow_ok(out / "workflow.json") and (out / "policy.json").exists():
        say(f"{brief_id}: workflow already built")
        return True
    inputs = copy_inputs(brief_id)
    log_path = out / "build.log"
    out.mkdir(parents=True, exist_ok=True)
    cmd = [
        PY, "-m", "tcc90",
        "--brief", str(inputs / "brief.md"),
        "--api", str(inputs / "tools.openapi.json"),
        "--models", str(MODELS),
        "--out", str(out),
        "--slot", brief_id,
        "--save", "none",
        "--self-test", "none",
        "--max-minutes", "20",
    ]
    for attempt in range(1, 4):
        say(f"{brief_id}: build attempt {attempt}")
        proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)
        log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
        if workflow_ok(out / "workflow.json"):
            say(f"{brief_id}: build wrote workflow.json (exit {proc.returncode})")
            return True
        blob = proc.stdout + proc.stderr
        if "in_flight" in blob and attempt < 3:
            say(f"{brief_id}: OpenRouter in-flight budget, waiting 150s")
            time.sleep(150)
            continue
        say(f"{brief_id}: build failed\n{blob[-800:]}")
        return False
    return False


def _stop(proc):
    if not proc or proc.poll() is not None:
        return
    import signal
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass


def score(brief_id: str, workflow_path: Path, out_name: str) -> dict:
    import freya
    from run_fake import bank_http, personas_for, start_bank, start_tunnel
    from score import score_ledger
    from tools.driver.mockbank import load_testbed
    from tcc90.freya_sim import FreyaSimRunner
    from tcc90.retarget import retarget

    cases, _config = load_testbed(brief_id)
    cases = cases[:LIMIT]
    workflow = json.loads(workflow_path.read_text())
    bank = tunnel = None
    try:
        say(f"{brief_id}: mock bank, {len(cases)} cases, {REPS} reps")
        bank = start_bank(brief_id)
        tunnel, origin = start_tunnel()
        say(f"{brief_id}: public {origin}")
        pointed = retarget(workflow, origin)
        runner = FreyaSimRunner(
            freya, slot=f"{brief_id}-score", origin=origin,
            session_fn=lambda case: {}, ledger_fn=lambda variables: {},
            score_fn=score_ledger,
        )
        saved = runner._publish(pointed)
        agent_id = saved["agent_id"]
        say(f"{brief_id}: agent {agent_id}")
        persona_ids = personas_for(cases)
        reps = []
        for rep in range(1, REPS + 1):
            say(f"{brief_id}: rep {rep}")
            variables = {}
            for case in cases:
                opened = bank_http(origin, "/v1/practice/sessions", {"case": case["id"]})
                if "variables" not in opened:
                    raise RuntimeError(f"session for {case['id']} failed: {opened}")
                variables[case["id"]] = opened["variables"]
            mapping = {persona_ids[case["id"]]: variables[case["id"]] for case in cases}
            sim_id, it_id, status = freya.run_simulation(
                agent_id, f"tcc90 {brief_id} r{rep}", mapping, max_seconds=420, concurrency=5)
            runs = {row.get("personaId"): row for row in freya.get_iteration(sim_id, it_id)["runs"]}
            rows = []
            for case in cases:
                judge = runs.get(persona_ids[case["id"]]) or {}
                ledger = bank_http(origin, "/v1/practice/ledger/" + variables[case["id"]]["customer_id"])
                settled = status in ("completed", "paused") and judge
                if not isinstance(ledger, dict) or "calls" not in ledger or not settled:
                    row = {"id": case["id"], "pass": False, "infra": True,
                           "failed": [f"infra status={status} judge={bool(judge)}"],
                           "family": case.get("family")}
                else:
                    scored = score_ledger(case, ledger)
                    if judge.get("outcome") != "pass":
                        reason = judge.get("reason") or "no verdict"
                        scored["failed"].append("conversation: " + " ".join(str(reason).split())[:400])
                        scored["pass"] = False
                    row = {"id": case["id"], "pass": scored["pass"], "failed": scored["failed"],
                           "family": case.get("family"), "infra": False,
                           "tools": [item.get("tool") for item in ledger.get("calls") or []]}
                rows.append(row)
                say(("  PASS " if row["pass"] else "  FAIL ") + case["id"])
            reps.append(rows)
        final = []
        for index, case in enumerate(cases):
            passes = sum(1 for run in reps if run[index]["pass"])
            final.append({
                "id": case["id"], "family": case.get("family"),
                "passes": passes, "runs": REPS, "pass": passes == REPS,
                "failed": reps[-1][index]["failed"],
            })
        passed = sum(item["pass"] for item in final)
        report = {
            "brief": brief_id, "reps": REPS, "limit": LIMIT,
            "agent_id": agent_id, "workflow_id": saved.get("workflow_id"),
            "passed": passed, "total": len(final), "final": final, "reps_rows": reps,
        }
        path = workflow_path.parent / out_name
        path.write_text(json.dumps(report, indent=1))
        say(f"{brief_id}: {passed}/{len(final)} strict")
        return report
    finally:
        _stop(tunnel)
        _stop(bank)


class _FreyaLoopRunner:
    """Bank and tunnel from the vendor runner. The score is a Freya simulation."""

    def __init__(self, vendor, freya_mod):
        from tcc90.freya_sim import FreyaSimRunner
        from tools.score import score_ledger
        from run_fake import bank_http
        self.vendor = vendor
        self.freya_mod = freya_mod
        self.score_ledger = score_ledger
        self.bank_http = bank_http
        self.inner = None
        self.origin = None

    def _runner(self, cases):
        from tcc90.freya_sim import FreyaSimRunner
        self.vendor._ensure(cases)
        origin = self.vendor.origin
        local = f"http://127.0.0.1:{self.vendor.port}"
        if self.inner is not None and origin == self.origin:
            return self.inner
        self.origin = origin

        def session_fn(case, origin=local):
            opened = self.bank_http(origin, "/v1/practice/sessions", {"case": case["id"]})
            if "variables" not in opened:
                raise RuntimeError(f"bank session {case.get('id')}: {opened}")
            return opened["variables"]

        def ledger_fn(variables, origin=local):
            customer = variables["customer_id"] if isinstance(variables, dict) else variables
            return self.bank_http(origin, "/v1/practice/ledger/" + str(customer))

        self.inner = FreyaSimRunner(
            self.freya_mod, slot=f"{self.vendor.slot}", origin=origin,
            session_fn=session_fn, ledger_fn=ledger_fn, score_fn=self.score_ledger,
        )
        return self.inner

    def run(self, workflow, cases, tool_api, policy, reps):
        from builder.template_cc.runner import CaseResult
        rows = self._runner(cases).run(workflow, cases, tool_api, policy, reps)
        return [CaseResult(row.case_id, row.rep, row.passed, row.failed_reasons, row.transcript, row.tool_calls, row.infra)
                for row in rows]

    def run_units(self, workflow, tests):
        case_ids = {test["case_id"] for test in tests}
        loaded = [self.vendor.loaded[item] for item in case_ids if item in self.vendor.loaded]
        if loaded:
            self._runner(loaded)
        return self.inner.run_units(workflow, tests) if self.inner else []

    def close(self):
        self.vendor.close()


def improve(brief_id: str) -> dict:
    import freya
    from builder.template_cc import compiler, gate, judge, llm, loop
    from builder.template_cc.apimodel import ApiModel
    from builder.template_cc.io import Models, read_briefs, brief_text
    from builder.template_cc.trace import Trace
    from builder.template_cc.vendor_runner import VendorRunner, availability
    from tcc90.assemble import assemble

    out = ROOT / "results" / "tcc90" / "fakes" / brief_id
    ok, why = availability()
    if not ok:
        say(f"{brief_id}: patch loop skipped ({why})")
        return {"started": False, "reason": why}
    inputs = out / "input"
    briefs = read_briefs([inputs / "brief.md"])
    text = brief_text(briefs)
    api = ApiModel.load(inputs / "tools.openapi.json")
    policy = json.loads((out / "policy.json").read_text())
    workflow, book, _planned = assemble(policy, api, text, compiler.compile)
    models = Models.load(str(MODELS))
    trace = Trace(out / "loop-trace", {"variant": "tcc90-loop", "brief": brief_id})
    deadline = time.time() + 40 * 60
    the_judge = judge.make_judge(models, trace, llm.call_llm, deadline)
    if the_judge is None:
        say(f"{brief_id}: patch loop skipped (no judge)")
        return {"started": False, "reason": "no judge"}
    vendor = VendorRunner(f"tcc90-{brief_id}-loop", policy, api, trace, freya, port=8791)
    runner = _FreyaLoopRunner(vendor, freya)
    (out / "patches").mkdir(parents=True, exist_ok=True)
    try:
        deps = loop.LoopDeps(
            runner=runner, judge=the_judge, call_llm=llm.call_llm, models=models, trace=trace,
            briefs=briefs, brief_text=text, api_path=inputs / "tools.openapi.json", api=api,
            tool_api=json.loads((inputs / "tools.openapi.json").read_text()), policy=policy,
            out_dir=out, deadline=deadline,
        )
        say(f"{brief_id}: patch loop")
        result = loop.run_loop(workflow, book, deps, max_iters=2, reps=2, n_cases=18, p_threshold=gate.WIN_P)
        record = {"started": True, "committed": result.committed, "stop_reason": result.stop_reason, "cases": result.cases}
        if result.committed:
            loop_path = out / "workflow.loop.json"
            loop_path.write_text(json.dumps(result.workflow, indent=1))
            record["workflow"] = str(loop_path)
        (out / "loop.json").write_text(json.dumps(record, indent=1))
        say(f"{brief_id}: loop {record}")
        return record
    finally:
        runner.close()
        trace.close("ok")


def main():
    say("phase 1: normal builds and Freya scores, no patch loop")
    for brief_id in FAKES:
        try:
            if not build(brief_id):
                continue
            score_path = ROOT / "results" / "tcc90" / "fakes" / brief_id / "score.json"
            if score_path.exists():
                say(f"{brief_id}: score already written")
                continue
            score(brief_id, ROOT / "results" / "tcc90" / "fakes" / brief_id / "workflow.json", "score.json")
        except Exception:
            say(f"{brief_id}: normal run failed\n{traceback.format_exc()[-1200:]}")
    say("phase 2: patch loop, then a second Freya score only if a patch is committed")
    for brief_id in FAKES:
        out = ROOT / "results" / "tcc90" / "fakes" / brief_id
        if not workflow_ok(out / "workflow.json"):
            continue
        try:
            record = improve(brief_id)
            if record.get("committed") and not (out / "score-loop.json").exists():
                score(brief_id, out / "workflow.loop.json", "score-loop.json")
        except Exception:
            say(f"{brief_id}: patch loop failed\n{traceback.format_exc()[-1200:]}")
    say("done")


if __name__ == "__main__":
    main()
