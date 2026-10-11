"""Hard simulations of a T-CC agent: DeepSeek plays the customer, the vendor decision model (Jev) judges.

Cases come from OUR policy (testgen): medium and hard variants first, each played in one of the kit's hard talking
styles (numbers in pieces, self-corrections, talkative, questions back, everything in words). The agent is the
compiled workflow; the bank is the vendor mock bank with these cases; the ledger is scored with the vendor
score_ledger and the conversation with the vendor local judge. Infrastructure failures are reported apart.

    report = run_sims(workflow, policy, api, n=6, trace=trace, freya_mod=freya)
    python3 -m builder.template_cc.sims --workflow out/.../workflow.json --policy out/.../policy.json \
        --api tools/openapi.json --n 6
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder.template_cc import policy_check, testgen

HARD_STYLES = ("pieces", "corrects", "talkative", "asks_back", "in_words")
CUSTOMER_MODEL = "deepseek/deepseek-v4.1-flash"
LEVEL = {"hard": 0, "medium": 1, "easy": 2}


def hard_cases(policy: dict, n: int, seed: int = 11) -> list[dict]:
    """n cases, hardest first, spread over families, each with a hard talking style."""
    cases, _ = testgen.generate(policy, max(3 * n, 24), seed=seed)
    by: dict[str, list] = {}
    for c in sorted(cases, key=lambda c: (LEVEL.get(c.get("level"), 3), c["id"])):
        by.setdefault(c["family"], []).append(c)
    picked, i = [], 0
    while len(picked) < n and any(i < len(v) for v in by.values()):
        for fam in sorted(by, key=lambda f: LEVEL.get(by[f][0].get("level"), 3)):
            if i < len(by[fam]) and len(picked) < n:
                picked.append(by[fam][i])
        i += 1
    for k, c in enumerate(picked):
        c["style"] = HARD_STYLES[k % len(HARD_STYLES)]
    return picked


def summarize(results: list, cases: list[dict]) -> dict:
    by = {c["id"]: c for c in cases}
    scored = [r for r in results if not r.infra]
    rows = []
    for r in results:
        c = by.get(r.case_id, {})
        rows.append({"case": r.case_id, "family": c.get("family"), "level": c.get("level"), "style": c.get("style"),
                     "result": "UNSCORED" if r.infra else ("PASS" if r.passed else "FAIL"),
                     "reasons": [x[:200] for x in r.failed_reasons[:4]], "script": (c.get("script") or "")[:200]})
    return {"passed": sum(r.passed for r in scored), "scored": len(scored), "unscored": len(results) - len(scored),
            "rows": rows}


def render(rep: dict) -> str:
    lines = [f"{rep['passed']}/{rep['scored']} hard sims passed ({rep['unscored']} unscored: infrastructure)"]
    for r in rep["rows"]:
        lines.append(f"{r['result']:<8} {r['case']} {r['family']} [{r['level']}, style {r['style']}]")
        if r["result"] == "FAIL":
            lines.append(f"         customer: {r['script']}")
            lines += [f"         - {x}" for x in r["reasons"]]
    return "\n".join(lines)


def run_sims(workflow: dict, policy: dict, api, n: int, trace, freya_mod, slot: str = "tcc-sims", reps: int = 1,
             customer_model: str = CUSTOMER_MODEL, runner=None, concurrency: int = 2) -> dict:
    from builder.template_cc.vendor_runner import VendorRunner
    p = policy_check.complete(policy, api)
    cases = hard_cases(p, n)
    own = runner is None
    if own:
        runner = VendorRunner(slot, p, api, trace, freya_mod, slot_suffix="", concurrency=concurrency, port=8795)
    runner.customer_model = customer_model
    try:
        results = runner.run(workflow, cases, {}, p, reps)
    finally:
        if own:
            runner.close()
    rep = summarize(results, cases)
    trace.selftest(f"hard sims ({customer_model} as the customer, vendor decision model as the judge): "
                   f"{rep['passed']}/{rep['scored']} passed, {rep['unscored']} unscored")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m builder.template_cc.sims")
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--api", required=True)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--customer-model", default=CUSTOMER_MODEL)
    ap.add_argument("--slot", default="tcc-sims")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    from builder.template_cc.apimodel import ApiModel
    from builder.template_cc.io import REPO
    from builder.template_cc.llm import load_env
    from builder.template_cc.trace import Trace
    load_env()
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import freya
    import time
    trace = Trace(REPO / "traces" / f"tcc-sims-{time.strftime('%Y%m%d-%H%M%S')}", {"workflow": a.workflow, "n": a.n})
    rep = run_sims(json.loads(Path(a.workflow).read_text()), json.loads(Path(a.policy).read_text()),
                   ApiModel.load(a.api), a.n, trace, freya, slot=a.slot, reps=a.reps, customer_model=a.customer_model)
    print(render(rep))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    trace.close("ok", passed=rep["passed"], scored=rep["scored"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
