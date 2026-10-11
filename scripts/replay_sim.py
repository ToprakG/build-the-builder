"""Replay the REAL Freya customer turns of a DEV transcript through the local engine (Sonnet agent, no customer/judge model).

    .venv310/bin/python scripts/replay_sim.py --set g1 --brief brief-a --case A-025 [--tag p6r1]
Reads out/template_cc/eval/dev_transcripts/<brief>/<case>.<tag>.json (DEV only, from scripts/dev_transcripts.py). Prints the node path, the human/side
flags at each node and whether the agent spoke of a scheduled/recorded payment without a create_promise call. Diagnosis only."""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import local_sim as L  # noqa: E402
from builder.agentic.localsim.engine import Engine  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--brief", required=True)
    ap.add_argument("--case", required=True)
    ap.add_argument("--tag", default="p6r1")
    ap.add_argument("--transcripts", default=str(REPO / "out/template_cc/eval/dev_transcripts"))
    a = ap.parse_args()
    L.llm.load_env()
    d = json.loads((Path(a.transcripts) / a.brief / f"{a.case}.{a.tag}.json").read_text())
    turns = [m["text"] for m in d["messages"] if m["role"] == "user"]
    cases, config = L.sources(a.brief)
    dev = L.dev_cases(cases)
    case = next(c for c in dev if c["id"] == a.case)            # refuses non-DEV cases
    wf = json.loads((REPO / "out/template_cc" / a.set / a.brief / "workflow.json").read_text())
    wf = wf.get("workflow", wf)
    V = L.vendor()
    bank = V["mockbank"].Bank(dev, config)
    session = bank.open_session(L.TEAM, case["id"])

    def bank_call(name, body):
        h = V["mockbank"].HANDLERS.get(name)
        if h is None:
            return 404, {"ok": False, "error": f"unknown tool {name}"}
        body = dict(body or {})
        t = bank.session_for(str(body.get("customer_id") or "")) or session
        with bank.lock:
            return h(t, body, config)

    eng = Engine(wf, dict(session["variables"]), bank_call, L.ClaudeModel())
    print("AGENT:", eng.start())
    used = 0
    for u in turns:
        if eng.ended:
            break
        used += 1
        print("CUST :", u)
        print("AGENT:", eng.user_says(u))
    eng.finish()
    ev = [json.loads(e["message"]) for e in eng.events if e["role"] == "system" and e["message"].startswith("{")]
    print("\nNODES:", " > ".join(e["node"] for e in ev if e.get("event") == "node_enter"))
    for e in ev:
        if e.get("event") == "variable_extracted" and e["name"] in ("side", "turn_intent", "rb_answer", "human_choice"):
            print("  var", e["name"], "=", e["value"], "@", e.get("source_node"))
    led = bank.ledger(L.TEAM, session["variables"]["customer_id"])
    calls = [c.get("tool") or c.get("name") or str(c) for c in (led.get("calls") or [])]
    print("LEDGER calls:", [str(c)[:60] for c in (led.get("calls") or [])])
    says = " ".join(e["message"] for e in eng.events if e["role"] == "assistant")
    promised = [c for c in calls if "create_promise" in c]
    claims = bool(re.search(r"scheduled|recorded|noted down", says, re.I))
    print("customer turns used", used, "of", len(turns), "| create_promise calls:", len(promised), "| claims scheduled/recorded:", claims)
    print("VERDICT:", "BAD (claim without promise)" if claims and not promised else "ok" if promised else "no promise, no claim")


main()
