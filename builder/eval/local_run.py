"""Play practice cases against one of our workflows on this machine's mock bank, and score them.

    python3 builder/eval/local_run.py out/agentic/agentic-brief-a/workflow.json --brief A [--reps 3] [--only PUB-01,PUB-04]
    python3 builder/eval/local_run.py out/agentic/agentic-f3-kestrel/workflow.json --fake f3-kestrel [--sample 12]

How it runs (reusing T-CC's VendorRunner and the teammate's build-the-builder tester in vendor/build-the-builder):
- the vendor mock bank serves the cases on 127.0.0.1, published through a cloudflared quick tunnel;
- a copy of the workflow, with its tool URLs pointed at that bank, is saved to its own Freya slot `<slot>-selftest`
  (the real agent is never touched) and each case is played turn by turn over Freya's chat API, so the workflow runs
  on the event's agent model;
- the customer is an LLM persona (default DeepSeek v4.1 Flash on OpenRouter, LOCAL_CUSTOMER_MODEL to change it);
- the ledger is scored with the kit's score_ledger and the conversation with the vendor's Judge C.

This is a development tool: the mock bank gets the brief's correct testbed config. Inside a build, the self-test must
use cases and config generated from the builder's own policy.json instead (no answer leak).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.template_cc.vendor_runner import VENDOR, VendorRunner, availability  # noqa: E402

CONFIGS = {"A": REPO / "testbed" / "configs" / "brief-a.config.json", "B": REPO / "testbed" / "configs" / "brief-b.config.json"}


class PrintTrace:
    """The small trace interface VendorRunner uses."""

    def selftest(self, text):
        print(f"[{time.strftime('%H:%M:%S')}] {text}", flush=True)

    def freya(self, what, detail=None):
        print(f"[{time.strftime('%H:%M:%S')}] freya: {what} {json.dumps(detail, default=str)[:200]}", flush=True)


def spread(cases: list, n: int) -> list:
    """n cases, round-robin over the families, in file order (deterministic)."""
    fams = {}
    for c in cases:
        fams.setdefault(c.get("family", "?"), []).append(c)
    out, i = [], 0
    while len(out) < min(n, len(cases)):
        for rows in fams.values():
            if i < len(rows) and len(out) < n:
                out.append(rows[i])
        i += 1
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("workflow")
    p.add_argument("--brief", choices=["A", "B"])
    p.add_argument("--fake", help="a testbed fake brief id, e.g. f3-kestrel (cases and config from testbed/fake_briefs/<id>/)")
    p.add_argument("--sample", type=int, default=0, help="fake briefs: play this many cases, spread over the families")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--slot", help="Freya slot of the self-test copy; default agentic-brief-<a|b>")
    p.add_argument("--port", type=int, default=8797)
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--out", help="write the results as JSON here")
    a = p.parse_args(argv)

    os.environ.setdefault("LOCAL_RUN_PYTHON", sys.executable)
    ok, why = availability(VENDOR)
    if not ok:
        print(f"local run unavailable: {why}", file=sys.stderr)
        return 2
    import freya  # noqa: PLC0415  (kit client; reads FREYA_API_KEY)

    if bool(a.brief) == bool(a.fake):
        print("give exactly one of --brief or --fake", file=sys.stderr)
        return 2
    wf = json.loads(Path(a.workflow).read_text())
    wf = wf.get("workflow", wf)  # an exported file wraps the workflow
    if a.fake:
        folder = REPO / "testbed" / "fake_briefs" / a.fake
        cases, config, slot = json.loads((folder / "cases.json").read_text()), json.loads((folder / "config.json").read_text()), f"agentic-{a.fake}"
        if a.sample:
            cases = spread(cases, a.sample)
    else:
        cases = [c for c in json.loads((REPO / "public-test-set.json").read_text()) if c.get("brief") == a.brief]
        config, slot = json.loads(CONFIGS[a.brief].read_text()), f"agentic-brief-{a.brief.lower()}"
    if a.only:
        wanted = set(a.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    runner = VendorRunner(a.slot or slot, config, None, PrintTrace(), freya, port=a.port,
                          concurrency=a.concurrency, worker_python=str(REPO / "builder" / "eval" / "worker_launcher.sh"))
    try:
        results = runner.run(wf, cases, None, config, a.reps)
    finally:
        runner.close()
    scored = [r for r in results if not r.infra]
    for r in results:
        mark = "INFRA" if r.infra else "PASS" if r.passed else "FAIL"
        print(f"{mark} {r.case_id} rep{r.rep}  " + " | ".join(str(x)[:160] for x in r.failed_reasons[:3]))
    by_case = {}
    for r in scored:
        by_case.setdefault(r.case_id, []).append(r.passed)
    strict = sum(all(v) and len(v) == a.reps for v in by_case.values())
    print(f"\n{sum(r.passed for r in scored)}/{len(scored)} runs passed; {strict}/{len(cases)} cases passed every rep "
          f"({len(results) - len(scored)} unscored, infrastructure)")
    if a.out:
        Path(a.out).write_text(json.dumps([r.as_dict() for r in results], indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
