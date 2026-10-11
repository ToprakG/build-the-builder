"""Submission flow for one brief: score the real cases, patch loop on those cases, score them again.

Real cases are the public set for brief A and the first 10 testbed cases for a fake brief.
Brief B has no extracted policy, so it is not in this queue.
One rep. The patch loop gets 25 minutes.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "results" / "tcc90")]

from run_patch import claude_call, resolve, say

QUEUE = ["brief-a", "f1-nordhavn", "f2-ostral", "f3-kestrel"]
LOOP_MINUTES = 25
A_WORKFLOW = "27c0c5f0-826d-4781-86dd-30a5fa66b50c"
A_AGENT = "b95351b5-e9ea-4887-bd36-bc81fd5c8aa7"


def real_cases(brief: str) -> tuple[list, dict]:
    if brief == "brief-a":
        rows = json.loads((ROOT / "public-test-set.json").read_text())
        cases = [row for row in rows if row.get("brief") == "A"]
        config = json.loads((ROOT / "testbed" / "configs" / "brief-a.config.json").read_text())
        return cases, config
    from tools.driver.mockbank import load_testbed
    cases, config = load_testbed(brief)
    return cases[:10], config


def score_public(workflow: dict, out: Path) -> None:
    import freya
    from run_rest import bounded_run_simulation
    import run_cases

    freya.run_simulation = bounded_run_simulation
    version = freya.update_workflow(A_WORKFLOW, workflow)
    say(f"brief-a: published version {version}")
    argv = sys.argv
    try:
        sys.argv = ["run_cases.py", A_AGENT, "--brief", "A", "--reps", "1", "--out", str(out)]
        run_cases.main()
    finally:
        sys.argv = argv


def score_fake(brief: str, workflow_path: Path, out_name: str) -> None:
    import freya
    import run_fakes
    from run_rest import bounded_run_simulation

    freya.run_simulation = bounded_run_simulation
    run_fakes.LIMIT = 10
    run_fakes.REPS = 1
    run_fakes.score(brief, workflow_path, out_name)


def _tally(path: Path) -> tuple[int, int]:
    data = json.loads(path.read_text())
    return int(data.get("passed") or 0), int(data.get("total") or 0)


def one(brief: str) -> None:
    import freya
    from builder.template_cc import compiler, gate, loop
    from builder.template_cc.apimodel import ApiModel
    from builder.template_cc.io import Models, brief_text, read_briefs
    from builder.template_cc.judge import LocalDecisionBackend, LocalDecisionJudge
    from builder.template_cc.trace import Trace
    from builder.template_cc.vendor_runner import VendorRunner
    from run_fakes import _FreyaLoopRunner
    from run_rest import bounded_run_simulation
    from tcc90.assemble import assemble

    freya.run_simulation = bounded_run_simulation
    _src, brief_path, api_path, policy_path = resolve(brief)
    out = ROOT / "results" / "tcc90" / "submit" / brief
    out.mkdir(parents=True, exist_ok=True)
    cases, bank_cfg = real_cases(brief)
    say(f"{brief}: {len(cases)} real case(s)")
    briefs = read_briefs([brief_path])
    text = brief_text(briefs)
    api = ApiModel.load(api_path)
    policy = json.loads(policy_path.read_text())
    workflow, book, _planned = assemble(policy, api, text, compiler.compile)
    before_path = out / "workflow.before.json"
    before_score = out / "before.json"
    if not (before_score.exists() and _tally(before_score)[1]):
        before_path.write_text(json.dumps(workflow, indent=1, ensure_ascii=False) + "\n")
        if brief == "brief-a":
            score_public(workflow, before_score)
        else:
            score_fake(brief, before_path, "before.json")
    passed, total = _tally(before_score)
    say(f"{brief}: before {passed}/{total}")
    if total and passed == total:
        record = {"committed": 0, "stop_reason": "already passing", "skipped": True,
                  "passed": passed, "total": total}
        (out / "loop.json").write_text(json.dumps(record, indent=1))
        say(f"{brief}: loop skipped, {passed}/{total} already passing")
        return

    models = Models()
    models.patch = models.analyze = models.judge = "sonnet"
    trace = Trace(out / "loop", {"variant": "tcc90-submit", "brief": brief})
    deadline = time.time() + LOOP_MINUTES * 60
    judge = LocalDecisionJudge(LocalDecisionBackend(claude_call, "sonnet", 1, trace, deadline), trace=trace)
    vendor = VendorRunner(f"tcc90-{brief}-submit", bank_cfg, api, trace, freya, port=8791)
    runner = _FreyaLoopRunner(vendor, freya)
    (out / "patches").mkdir(parents=True, exist_ok=True)
    try:
        deps = loop.LoopDeps(
            runner=runner, judge=judge, call_llm=claude_call, models=models, trace=trace,
            briefs=briefs, brief_text=text, api_path=api_path, api=api,
            tool_api=json.loads(api_path.read_text()), policy=policy,
            out_dir=out, deadline=deadline,
        )
        result = loop.run_loop(
            workflow, book, deps, max_iters=1, reps=1, p_threshold=gate.WIN_P,
            ablation="units", cases=cases)
        record = {
            "committed": result.committed, "stop_reason": result.stop_reason,
            "cases": result.cases, "iterations": result.iterations,
        }
        (out / "loop.json").write_text(json.dumps(record, indent=1, default=str))
        say(f"{brief}: loop {record['stop_reason']} committed={record['committed']}")
        after = result.workflow
    finally:
        runner.close()
        trace.close("ok")
    after_path = out / "workflow.json"
    after_path.write_text(json.dumps(after, indent=1, ensure_ascii=False) + "\n")
    if brief == "brief-a":
        score_public(after, out / "after.json")
    else:
        score_fake(brief, after_path, "after.json")
    say(f"{brief}: before and after written")


def main() -> None:
    briefs = sys.argv[1:] or QUEUE
    for brief in briefs:
        started = time.time()
        try:
            one(brief)
        except Exception as error:
            say(f"{brief}: FAILED {type(error).__name__}: {error}")
            traceback.print_exc()
        say(f"{brief}: wall {time.time() - started:.0f}s")
    say("SUBMIT DONE")


if __name__ == "__main__":
    main()
