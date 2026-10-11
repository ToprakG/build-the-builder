"""Publish the current compiler for Ostral and score the same 10 cases.

Reads the already extracted policy. Does not extract again. Writes
workflow-retry.json and score-retry.json. The official score slot is not touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
os.environ["PORT"] = "8793"
os.environ.setdefault("PYTHONUNBUFFERED", "1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from envfile import load_env

load_env()

from tcc90.boot import install

install()

import freya
from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from run_fake import bank_http, personas_for, start_bank, start_tunnel
from run_fakes import _stop
from run_rest import bounded_run_simulation
from score import score_ledger
from tools.driver.mockbank import load_testbed
from tcc90.assemble import assemble
from tcc90.retarget import retarget

BRIEF = "f2-ostral"
OUT = ROOT / "results" / "tcc90" / "fakes" / BRIEF
SLOT = "f2-ostral-retry"
LIMIT = 10
REPS = 3
STUCK = "0603eadb-eb72-47b8-9972-34f51842f6e4"


def say(text):
    print(text, flush=True)


def compile_workflow() -> dict:
    policy = json.loads((OUT / "policy.json").read_text())
    brief = (OUT / "input" / "brief.md").read_text()
    api = ApiModel.load(OUT / "input" / "tools.openapi.json")
    workflow, _book, _planned = assemble(policy, api, brief, compiler.compile)
    names = set(workflow.get("nodes") or {})
    missing = {"verify_retry", "promise_retry"} - names
    if missing:
        raise SystemExit(f"compiled workflow is missing {sorted(missing)}")
    blob = json.dumps(workflow)
    if "service_error" not in blob or "id_state'] = 'service_retry'" not in blob and "id_state\"] = 'service_retry'" not in blob:
        if "service_retry" not in blob:
            raise SystemExit("compiled workflow has no service_retry")
    path = OUT / "workflow-retry.json"
    path.write_text(json.dumps(workflow, indent=1, ensure_ascii=False) + "\n")
    say(f"wrote {path.name} nodes={len(names)} verify_retry promise_retry present")
    return workflow


def _release_ghost(label: str):
    """A 504 on create can still leave a simulation running. Pause it so the retry can start."""
    try:
        page = freya.api("GET", "/api/v2/simulations", workspaceId=freya.workspace_id(), limit=5)
    except Exception:
        return
    items = page.get("simulations") or page.get("items") or []
    for row in items:
        if row.get("name") == label and row.get("status") == "running":
            try:
                freya.api("PUT", f"/api/v2/simulations/{row['id']}", {"status": "paused"})
                say(f"paused unfinished sim {row['id']}")
            except Exception:
                say("could not pause the unfinished sim")


def pause_stuck():
    try:
        row = freya.api("GET", f"/api/v2/simulations/{STUCK}")
    except Exception as error:
        say(f"stuck sim not read ({type(error).__name__})")
        return
    row = row.get("simulation") or row
    status = row.get("status")
    say(f"stuck sim status={status}")
    if status != "running":
        return
    try:
        freya.api("PUT", f"/api/v2/simulations/{STUCK}", {"status": "paused"})
        say(f"paused {STUCK}")
    except Exception as error:
        say(f"pause failed ({type(error).__name__}); the new run will wait if it is still holding the workspace")


def score(workflow: dict) -> dict:
    cases, _config = load_testbed(BRIEF)
    cases = cases[:LIMIT]
    bank = tunnel = None
    try:
        say(f"mock bank on 8793, {len(cases)} cases, {REPS} reps")
        bank = start_bank(BRIEF)
        tunnel, origin = start_tunnel()
        say(f"public {origin}")
        pointed = retarget(workflow, origin)
        saved = None
        last = None
        for attempt in range(1, 7):
            try:
                saved = freya.save_workflow("tcc90-" + SLOT, pointed, state_file=str(ROOT / "tcc90" / ".freya-agents.json"))
                break
            except Exception as error:
                last = error
                say(f"publish attempt {attempt} failed")
                if attempt == 6 or not any(word in str(error) for word in ("502", "503", "504", "429", "timed out")):
                    raise
                time.sleep(20)
        if saved is None:
            raise last
        agent_id = saved["agent_id"]
        say(f"agent {agent_id} workflow {saved.get('workflow_id')}")
        persona_ids = personas_for(cases)
        reps = []
        for rep in range(1, REPS + 1):
            say(f"rep {rep}")
            variables = {}
            for case in cases:
                opened = bank_http(origin, "/v1/practice/sessions", {"case": case["id"]})
                variables[case["id"]] = opened["variables"]
            mapping = {persona_ids[case["id"]]: variables[case["id"]] for case in cases}
            label = f"tcc90 {BRIEF} retry r{rep}"
            sim_id = it_id = status = None
            for attempt in range(1, 6):
                try:
                    sim_id, it_id, status = bounded_run_simulation(
                        agent_id, label, mapping, max_seconds=420, concurrency=5)
                    break
                except Exception as error:
                    say(f"sim start attempt {attempt} failed")
                    if attempt == 5 or not any(word in str(error) for word in ("502", "503", "504", "429", "timed out", "concurrent")):
                        raise
                    _release_ghost(label)
                    time.sleep(20)
            say(f"rep {rep} sim {sim_id} status {status}")
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
            "brief": BRIEF, "reps": REPS, "limit": LIMIT, "slot": "tcc90-" + SLOT,
            "agent_id": agent_id, "workflow_id": saved.get("workflow_id"),
            "passed": passed, "total": len(final), "final": final, "reps_rows": reps,
        }
        path = OUT / "score-retry.json"
        path.write_text(json.dumps(report, indent=1))
        say(f"{passed}/{len(final)} strict")
        return report
    finally:
        _stop(tunnel)
        _stop(bank)


def main():
    workflow = compile_workflow()
    pause_stuck()
    score(workflow)


if __name__ == "__main__":
    main()
