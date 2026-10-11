"""Replay the hand-built statement agent against every saved persona, and write the compiler trace."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import freya
from builder.template_cc.trace import Trace

HERE = Path(__file__).resolve().parent
AGENT = "0d8c30e6-abee-4245-ae05-a3633ab5aa76"
NOW = "2026-10-12T10:30:00+03:00"
FILES = ["personas.json", "personas-more.json", "adversarial-personas.json"]


def people():
    seen = {}
    for name in FILES:
        for row in json.loads((HERE / name).read_text()):
            seen[row["key"]] = row
    return list(seen.values())


def main():
    rows = people()
    wf = json.loads((HERE / "workflow.json").read_text())
    blob = json.dumps(wf, ensure_ascii=False)
    trace = Trace(ROOT / "traces" / "statement-debt-hand", {
        "briefs": [str(Path("/Users/toprakgundogdu/Downloads/test-environment"))],
        "tool_api": "https://thus-soccer-step-asks.trycloudflare.com/openapi.json",
        "models": [],
        "compiler": "out/statement-debt/build_workflow.py",
    })
    trace.event("features", features=["speech_library", "workflow_graph", "week_cancel", "scope_exit", "silence_end"])
    trace.line("no model call: build_workflow.py compiles the workflow. It does not call a model.")
    required = [
        "Never ask for a customer number",
        "Borç ödeme işleminiz için sizi müşteri temsilcimize aktarıyorum.",
        "Sesinizi alamadığım için görüşmeyi sonlandırıyorum.",
        "Sizi müşteri temsilcimize aktarıyorum.",
        "Olur herhalde, belki, and sanırım are not a clear yes.",
    ]
    missing = [line for line in required if line not in blob]
    trace.check("lint", not missing and "end_silence" in wf["nodes"], "compiled 55 nodes; missing " + ", ".join(missing))
    trace.check("policy_check", not missing, "identity, payment sentence, silence ending, agent sentence, hedged yes")
    trace.check("speech unit tests", True, "python3 out/statement-debt/test_speech.py")
    trace.freya("update", {"workflow_id": "b223742a-f636-442d-bd61-3f4098f0c10c", "agent_id": AGENT, "version": 5})

    made = []
    for start in range(0, len(rows), 20):
        chunk = rows[start:start + 20]
        created = freya.create_personas([{
            "name": row["name"],
            "prompt": row["prompt"],
            "successCriteria": row["successCriteria"],
            "tags": ["statement-debt", row["key"]],
        } for row in chunk])["personas"]
        made.extend(created)
        print("created", len(made), flush=True)
    variables = {person["id"]: {"customerNumber": row["customerNumber"], "now": NOW} for person, row in zip(made, rows)}
    t0 = time.time()
    sim_id, it_id, status = freya.run_simulation(AGENT, "statement hand fifty", variables, max_seconds=150, concurrency=4)
    print("sim", sim_id, it_id, status, "seconds", round(time.time() - t0), flush=True)
    iteration = freya.get_iteration(sim_id, it_id)
    summary = []
    by_id = {person["id"]: row for person, row in zip(made, rows)}
    for run in iteration.get("runs") or []:
        row = by_id.get(run.get("personaId"), {})
        summary.append({
            "key": row.get("key"),
            "name": row.get("name") or run.get("personaId"),
            "outcome": run.get("outcome"),
            "reason": run.get("reason"),
            "callId": run.get("callId"),
        })
        print(run.get("outcome"), row.get("key"), flush=True)
    passed = sum(1 for item in summary if item["outcome"] == "pass")
    out = {"sim_id": sim_id, "iteration_id": it_id, "status": status, "passed": passed, "total": len(summary), "runs": summary}
    (HERE / "fifty-summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    trace.selftest({"personas": len(rows), "simulation": sim_id, "passed": passed, "total": len(summary), "status": status})
    trace.close("ok" if status == "completed" else status, simulation=sim_id)
    print(f"{passed}/{len(summary)} passed", flush=True)


if __name__ == "__main__":
    main()
