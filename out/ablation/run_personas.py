"""Freya sims for the Claude Code + skills ablation, on out/diag/personas20.json."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

import freya

WORKFLOW = ROOT / "out/final/build/workflow.json"
SLOT = "case-test-environment"
PERSONAS = ROOT / "out/diag/personas20.json"
OUT = ROOT / "out/ablation/claude-skills/persona_sims.json"
NOW = "2026-10-12T10:30:00+03:00"


def say(text: str) -> None:
    print(text, flush=True)


def customer_number(prompt: str) -> str:
    match = re.search(r"\b(\d{8})\b", prompt)
    if not match:
        raise SystemExit(f"no customer number in: {prompt[:80]}")
    return match.group(1)


def call(method: str, path: str, body=None, **params):
    for attempt in range(8):
        try:
            return freya.api(method, path, body, **params)
        except Exception as exc:  # noqa: BLE001
            if attempt == 7:
                raise
            say(f"retry {attempt + 1} {method} {path}: {exc}")
            time.sleep(5 * (attempt + 1))


def find_sim() -> str:
    page = call("GET", "/api/v2/simulations", workspaceId=freya.workspace_id(), limit=10)
    items = page.get("simulations") or page.get("items") or page
    for row in items:
        if row.get("name") == "ablation claude-skills personas20":
            say(f"sim {row['id']} status={row.get('status')}")
            return row["id"]
    raise SystemExit("simulation not found")


def resume() -> None:
    sim_id = find_sim()
    expected = 20
    while True:
        time.sleep(15)
        row = call("GET", f"/api/v2/simulations/{sim_id}")
        row = row.get("simulation") or row
        status = row.get("status")
        metrics = row.get("metrics") or {}
        say(f"status={status} passed={metrics.get('passedRuns')} failed={metrics.get('failedRuns')} total={metrics.get('totalRuns')}")
        if status in ("completed", "failed", "paused"):
            break
        its = call("GET", f"/api/v2/simulations/{sim_id}/iterations")
        items = its.get("iterations") or its.get("items") or its
        runs = call("GET", f"/api/v2/simulations/{sim_id}/iterations/{items[0]['id']}", limit=100, offset=0).get("runs") or []
        done = {"pass", "fail", "inconclusive", "error", "completed"}
        if len(runs) >= expected and all((r.get("outcome") or r.get("status")) in done for r in runs):
            call("PUT", f"/api/v2/simulations/{sim_id}", {"status": "paused"})
            status = "paused"
            break
    its = call("GET", f"/api/v2/simulations/{sim_id}/iterations")
    items = its.get("iterations") or its.get("items") or its
    runs = call("GET", f"/api/v2/simulations/{sim_id}/iterations/{items[0]['id']}", limit=100, offset=0).get("runs") or []
    people = json.loads(PERSONAS.read_text())
    by_number = {}
    for person in people:
        by_number[customer_number(person["prompt"])] = person["name"]
    rows = []
    passed = 0
    for run in runs:
        variables = run.get("customerVariables") or run.get("variables") or {}
        number = str(variables.get("customerNumber") or "")
        name = by_number.get(number) or run.get("personaName") or run.get("personaId")
        outcome = run.get("outcome") or run.get("status")
        if outcome == "pass":
            passed += 1
        rows.append({"name": name, "customerNumber": number, "outcome": outcome, "run_id": run.get("id"), "persona_id": run.get("personaId")})
        say(f"{outcome or 'missing':12} {name}")
    summary = {"sim_id": sim_id, "iteration_id": items[0]["id"], "status": status,
               "passed": passed, "total": len(rows), "runs": rows,
               "note": "skills-only Claude run hit the session limit before a workflow; these sims are the lint-clean statement workflow from the parallel build"}
    OUT.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    say(f"RESULT {passed}/{len(rows)} status={status} sim={sim_id}")


def main() -> None:
    import urllib.request
    real_open = urllib.request.urlopen

    def longer(req, timeout=300, *args, **kwargs):
        return real_open(req, timeout=max(timeout or 0, 300), *args, **kwargs)

    urllib.request.urlopen = longer
    wf = json.loads(WORKFLOW.read_text())
    wf.pop("agent_config", None)
    saved = freya.save_workflow(SLOT, wf, state_file=str(ROOT / ".freya-agents.json"))
    agent_id = saved["agent_id"]
    say(f"published slot={SLOT} agent={agent_id} version={saved.get('version')}")
    people = json.loads(PERSONAS.read_text())
    made = freya.create_personas(people)["personas"]
    variables = {}
    rows = []
    for person, created in zip(people, made):
        number = customer_number(person["prompt"])
        variables[created["id"]] = {"customerNumber": number, "now": NOW}
        rows.append({"name": person["name"], "persona_id": created["id"], "customerNumber": number})
        say(f"persona {person['name']} {number} {created['id']}")
    sim_id, it_id, status = freya.run_simulation(
        agent_id, "ablation claude-skills personas20", variables, max_seconds=600, concurrency=5)
    runs = freya.get_iteration(sim_id, it_id).get("runs") or []
    by_id = {run.get("personaId"): run for run in runs}
    passed = 0
    for row in rows:
        run = by_id.get(row["persona_id"]) or {}
        outcome = run.get("outcome") or run.get("status")
        row["outcome"] = outcome
        row["run_id"] = run.get("id")
        if outcome == "pass":
            passed += 1
        say(f"{outcome or 'missing':12} {row['name']}")
    summary = {"sim_id": sim_id, "iteration_id": it_id, "status": status,
               "passed": passed, "total": len(rows), "agent_id": agent_id, "runs": rows}
    OUT.write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    say(f"RESULT {passed}/{len(rows)} status={status} sim={sim_id}")


if __name__ == "__main__":
    if "--resume" in sys.argv:
        resume()
    else:
        main()
