"""Evaluate T-CC agents on the testbed (Brief A/B personas and the 7 fake briefs) with build-the-builder's tester.

DEV EVALUATION ONLY, outside the builder: it reads the testbed answers (cases.json, config.json) to score the agent.
The builder itself never sees them.

For each brief: the vendor mock bank serves that brief's own cases with its ground-truth config behind a cloudflared
tunnel; the compiled T-CC workflow is published to `tcc-<brief>-eval` with its tool URLs pointed at that bank; the
cases are played over the chat API with the vendor LLM customer, the ledger is scored with the vendor score_ledger
and the conversation is judged by the vendor local judge (Judge C: coded checks + the Jev decision model).

    .venv310/bin/python scripts/tcc_eval_testbed.py --brief f1-nordhavn --limit 10
    .venv310/bin/python scripts/tcc_eval_testbed.py --brief all-fake --limit 10
"""
import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from builder.template_cc.apimodel import ApiModel  # noqa: E402
from builder.template_cc.trace import Trace  # noqa: E402
from builder.template_cc.vendor_runner import VendorRunner  # noqa: E402

SHARED = ""
FAKE = sorted(p.name for p in (REPO / "testbed" / "fake_briefs").iterdir() if p.is_dir())


def sources(brief: str):
    """(cases, config, tool api path, compiled workflow path) for a testbed brief."""
    tb = REPO / "testbed"
    if brief in ("brief-a", "brief-b"):
        x = brief[-1]
        return (json.loads((tb / "personas" / f"{brief}.cases.json").read_text()),
                json.loads((tb / "configs" / f"{brief}.config.json").read_text()),
                REPO / "tools" / "openapi.json", REPO / "out" / "template_cc" / f"judge-{x}" / "workflow.json")
    d = tb / "fake_briefs" / brief
    return (json.loads((d / "cases.json").read_text()), json.loads((d / "config.json").read_text()),
            d / "tools.openapi.json", REPO / "out" / "template_cc" / "fake" / brief / "workflow.json")


def pick(cases: list, limit: int) -> list:
    """Round-robin over families, in case-id order: a spread sample, the same every time."""
    if not limit or limit >= len(cases):
        return cases
    by: dict = {}
    for c in sorted(cases, key=lambda c: c["id"]):
        by.setdefault(c["family"], []).append(c)
    out, i = [], 0
    while len(out) < limit:
        for fam in sorted(by):
            if i < len(by[fam]) and len(out) < limit:
                out.append(by[fam][i])
        i += 1
    return out


def evaluate(brief: str, limit: int, reps: int, concurrency: int, port: int, freya, exclude_sample: int = 0,
             tag: str = "") -> dict:
    cases, config, api_path, wf_path = sources(brief)
    if not wf_path.exists():
        return {"brief": brief, "error": f"no compiled workflow at {wf_path}"}
    wf = json.loads(wf_path.read_text())
    if exclude_sample:
        done = {c["id"] for c in pick(cases, exclude_sample)}
        cases = [c for c in cases if c["id"] not in done]
    chosen = pick(cases, limit)
    out_dir = REPO / "out" / "template_cc" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(REPO / "traces" / f"tcc-eval-{brief}-{time.strftime('%Y%m%d-%H%M%S')}", {"brief": brief, "cases": len(chosen)})
    runner = VendorRunner(f"tcc-{brief}", config, ApiModel.load(api_path), trace, freya, port=port,
                          concurrency=concurrency, slot_suffix="-eval", run_timeout_s=7200)
    runner.label = [brief, tag]
    if SHARED:
        runner.use_shared(SHARED.rstrip("/") + "/" + brief)
    t0 = time.time()
    try:
        results = runner.run(wf, chosen, {}, config, reps)
    finally:
        runner.close()
    scored = [r for r in results if not r.infra]
    rep = {"brief": brief, "cases": len(chosen), "reps": reps, "passed": sum(r.passed for r in scored),
           "scored": len(scored), "unscored": len(results) - len(scored), "seconds": round(time.time() - t0),
           "rows": [dict(r.as_dict(), family=next(c["family"] for c in chosen if c["id"] == r.case_id)) for r in results]}
    (out_dir / f"{brief}{'.' + tag if tag else ''}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    trace.close("ok", passed=rep["passed"], scored=rep["scored"])
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True, help="brief-a, brief-b, a fake brief id, or all-fake")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--exclude-sample", type=int, default=0, help="skip the first N cases --limit N would pick")
    ap.add_argument("--tag", default="")
    ap.add_argument("--shared-origin", default="", help="use a shared multi-brief bank (scripts/tcc_shared_bank.py)")
    a = ap.parse_args()
    import freya
    global SHARED
    SHARED = a.shared_origin
    briefs = FAKE if a.brief == "all-fake" else [a.brief]
    summary = []
    for b in briefs:
        rep = evaluate(b, a.limit, a.reps, a.concurrency, a.port, freya, a.exclude_sample, a.tag)
        line = (f"{b}: {rep['passed']}/{rep['scored']} passed ({rep['unscored']} unscored) in {rep['seconds']}s"
                if "error" not in rep else f"{b}: {rep['error']}")
        print(line, flush=True)
        summary.append(line)
    print("\n".join(summary))


if __name__ == "__main__":
    main()
