"""Refusal / scam probe: a DEV happy-path case whose persona script is replaced by a synthetic refusal script (own wording, not a testbed case).
Prints the node path, tool calls and the agent lines. No judge (diagnosis only). DEV cases only, shared local-sim budget.

    .venv310/bin/python scripts/refusal_probe.py --set pipe9fx --brief f4-brightwell --variant flat [--rep 1]
variants: flat (refuses today, refuses again), all (refuses every proposal), nottalk (says twice it does not want to talk), scam (asks if it is a scam, then pays)"""
import argparse
import copy
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import local_sim as L  # noqa: E402

SCRIPTS = {
    "flat": "{ok} Says no to paying today and gives no date or amount. Refuses again when the agent proposes another date.",
    "all": "{ok} Says no to paying today and refuses every proposal flatly, without giving a date or amount.",
    "nottalk": "{ok} After hearing the amount, says {he} does not want to talk about this. When the agent continues, says it again.",
    "scam": "{ok} Right after the greeting, before answering any security question, asks 'is this a scam?'. Accepts the answer, then pays the full amount today and confirms.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--variant", required=True, choices=sorted(SCRIPTS))
    ap.add_argument("--rep", type=int, default=1)
    a = ap.parse_args()
    L.llm.load_env()
    cases, config = L.sources(a.brief)
    case = copy.deepcopy(next(c for c in L.dev_cases(cases) if c["family"] == "Happy path"))
    ok = case["script"].split(".")[0] + "." + (" Answers every security question correctly when asked." )
    he = "he" if case["variables"].get("title") in ("Mr",) else "she"
    case["script"] = SCRIPTS[a.variant].format(ok=ok, he=he)
    case["id"] = case["id"] + "-" + a.variant
    wf = json.loads((REPO / "out/template_cc" / a.set / a.brief / "workflow.json").read_text())
    wf = wf.get("workflow", wf)
    bank = L.vendor()["mockbank"].Bank([case], config)
    with L.Slot():
        if L.budget_claim() is None:
            print("cap reached"); return 2
        r = L.run_conversation(wf, case, config, bank, a.rep)
    out = REPO / "out/template_cc/localsim/refusal" / a.set / a.brief
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.variant}.{a.rep}.json").write_text(json.dumps(r, indent=1, default=str, ensure_ascii=False))
    print(f"== {a.brief} {a.variant} error={r['error']}  nodes={r.get('nodes')}")
    for e in r["tool_calls"]:
        if e["event"] == "function_call_start" and e["function"] != "code_exec":
            print("   call", e["function"], json.dumps((e.get("args") or {}).get("body"))[:140])
    for who, m in r["transcript"]:
        print(f"   {who[:1].upper()}: {m[:200]}")


if __name__ == "__main__":
    sys.exit(main())
