"""One rep of the cases the last tcc90 scores failed. Recompiled workflows only."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from envfile import load_env

load_env()

import freya
from tcc90.boot import install

install()

A_WORKFLOW = "27c0c5f0-826d-4781-86dd-30a5fa66b50c"
A_AGENT = "b95351b5-e9ea-4887-bd36-bc81fd5c8aa7"
FAILED = {
    "f1-nordhavn": ["F1-009"],
    "f2-ostral": ["F2-002", "F2-003", "F2-006", "F2-007", "F2-008", "F2-009"],
    "f3-kestrel": ["F3-003", "F3-005"],
}


def say(text: str) -> None:
    print(text, flush=True)


def publish_a() -> None:
    workflow = json.loads((ROOT / "results/tcc90/rerun/A/workflow.json").read_text())
    version = freya.update_workflow(A_WORKFLOW, workflow)
    say(f"A published version {version}")


def score_fake(brief_id: str, wanted: list[str]) -> None:
    from run_fake import _stop, bank_http, personas_for, start_bank, start_tunnel
    from score import score_ledger
    from tools.driver.mockbank import load_testbed
    from tcc90.freya_sim import FreyaSimRunner

    cases, _config = load_testbed(brief_id)
    cases = [case for case in cases if case["id"] in wanted]
    workflow = json.loads((ROOT / "results/tcc90/rerun" / brief_id / "workflow.json").read_text())
    bank = tunnel = None
    try:
        say(f"{brief_id}: {', '.join(case['id'] for case in cases)}")
        bank = start_bank(brief_id)
        tunnel, origin = start_tunnel()
        say(f"{brief_id}: {origin}")
        runner = FreyaSimRunner(
            freya, slot=f"{brief_id}-score", origin=origin,
            session_fn=lambda case: {}, ledger_fn=lambda variables: {},
            score_fn=score_ledger,
        )
        saved = runner._publish(workflow)
        persona_ids = personas_for(cases)
        variables = {}
        for case in cases:
            opened = bank_http(origin, "/v1/practice/sessions", {"case": case["id"]})
            variables[case["id"]] = opened["variables"]
        mapping = {persona_ids[case["id"]]: variables[case["id"]] for case in cases}
        sim_id, it_id, status = freya.run_simulation(
            saved["agent_id"], f"rerun {brief_id}", mapping, max_seconds=900, concurrency=5)
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
                    scored["failed"].append("conversation: " + " ".join(str(reason).split())[:500])
                    scored["pass"] = False
                row = {"id": case["id"], "family": case.get("family"), "pass": scored["pass"],
                       "failed": scored["failed"],
                       "tools": [item.get("tool") for item in ledger.get("calls") or []]}
            rows.append(row)
            say(("  PASS " if row["pass"] else "  FAIL ") + case["id"])
            for line in row.get("failed") or []:
                say("     " + " ".join(str(line).split())[:240])
        passed = sum(row["pass"] for row in rows)
        report = {"brief": brief_id, "reps": 1, "agent_id": saved["agent_id"],
                  "workflow_id": saved.get("workflow_id"), "passed": passed, "total": len(rows),
                  "rows": rows}
        (ROOT / "results/tcc90/rerun" / brief_id / "score.json").write_text(json.dumps(report, indent=1))
        say(f"{brief_id}: {passed}/{len(rows)}")
    finally:
        _stop(tunnel)
        _stop(bank)


def main() -> None:
    publish_a()
    import subprocess
    say("A: PUB-10")
    subprocess.run([
        sys.executable, "-u", "tools/run_cases.py", A_AGENT,
        "--brief", "A", "--only", "PUB-10", "--reps", "1",
        "--out", "results/tcc90/rerun/A/score.json",
    ], check=False)
    for brief_id, ids in FAILED.items():
        score_fake(brief_id, ids)
    say("RERUN DONE")


if __name__ == "__main__":
    main()
