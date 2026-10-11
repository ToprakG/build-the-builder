"""Finish the unfinished scores. Extraction is the API. Claude Code is not used.

f2 already has a workflow, so it is only scored. f4-f7 and public Brief B are
extracted with the API (structured policy schema) and then scored on Freya.
This script does not start the patch loop.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from envfile import load_env

load_env()

from tcc90.boot import install

install()

import freya
from run_fakes import copy_inputs, score, workflow_ok

BUILD = ("f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes")
WAIT_SECONDS = 14 * 60


def say(text):
    print(text, flush=True)


def _calls_settled(sim_id: str, expected: int) -> bool:
    its = freya.api("GET", f"/api/v2/simulations/{sim_id}/iterations")
    items = its.get("iterations") or its.get("items") or its
    if not isinstance(items, list) or not items:
        return False
    iteration = freya.get_iteration(sim_id, items[0]["id"])
    runs = iteration.get("runs") or []
    if len(runs) < expected:
        return False
    done = {"pass", "fail", "inconclusive", "error", "completed"}
    for run in runs:
        outcome = run.get("outcome") or run.get("status")
        if outcome not in done:
            return False
    return True


def bounded_run_simulation(agent_id, name, variables_by_persona, max_seconds=420, concurrency=5):
    """Same start as freya.run_simulation. A stuck `running` status is paused once every call has an outcome."""
    while True:
        try:
            sim = freya.api("POST", "/api/v2/simulations", {
                "agentId": agent_id, "name": name, "mode": "chat",
                "selectedPersonaIds": list(variables_by_persona),
                "customerVariablesMap": variables_by_persona,
                "maxConcurrentCalls": concurrency, "runsPerPersona": 1,
                "maxRetries": 0, "maxCallDurationSeconds": max_seconds,
                "assistantLlmOverride": freya.AGENT_LLM,
                "routingLlmOverride": freya.AGENT_LLM,
                "extractionLlmOverride": freya.AGENT_LLM,
                "personaLlmOverride": freya.SIM_LLM,
                "analysisLlmOverride": freya.SIM_LLM,
            })
            break
        except RuntimeError as error:
            if "concurrent simulations" not in str(error):
                raise
            say("another simulation is running, waiting 30s")
            time.sleep(30)
    sim_id = (sim.get("simulation") or sim)["id"]
    expected = len(variables_by_persona)
    deadline = time.time() + WAIT_SECONDS
    status = "running"
    while time.time() < deadline:
        time.sleep(10)
        row = freya.api("GET", f"/api/v2/simulations/{sim_id}")
        row = row.get("simulation") or row
        status = row.get("status") or status
        if status in ("completed", "failed", "paused"):
            break
        if _calls_settled(sim_id, expected):
            freya.api("PUT", f"/api/v2/simulations/{sim_id}", {"status": "paused"})
            say(f"paused {sim_id}: every call had an outcome while the simulation stayed running")
            status = "paused"
            break
    else:
        freya.api("PUT", f"/api/v2/simulations/{sim_id}", {"status": "paused"})
        say(f"paused {sim_id}: still running after {WAIT_SECONDS}s")
        status = "paused"
    its = freya.api("GET", f"/api/v2/simulations/{sim_id}/iterations")
    items = its.get("iterations") or its.get("items") or its
    return sim_id, items[0]["id"], status


def build_local(brief_id: str, brief_paths: list[Path], api_path: Path, out: Path) -> bool:
    from builder.template_cc import compiler, llm
    from builder.template_cc.apimodel import ApiModel
    from builder.template_cc.io import Models
    from builder.template_cc.trace import Trace
    from tcc90.assemble import assemble
    from tcc90.run import _write, extract_twice

    out.mkdir(parents=True, exist_ok=True)
    if workflow_ok(out / "workflow.json") and (out / "policy.json").exists():
        say(f"{brief_id}: workflow already built")
        return True
    brief = "\n\n".join(path.read_text(encoding="utf-8") for path in brief_paths)
    api = ApiModel.load(api_path)
    model = Models.load(None).extract
    trace = Trace(out / "trace-api", {"variant": "tcc90-api", "brief": brief_id, "model": model})
    say(f"{brief_id}: API extract {model}")
    try:
        policy, notes = extract_twice(
            brief, api, llm.call_llm, model, trace, time.time() + 25 * 60, out)
        workflow, _book, planned = assemble(policy, api, brief, compiler.compile)
        _write(out / "policy.json", planned)
        _write(out / "reconcile.json", notes)
        _write(out / "workflow.json", workflow)
        say(f"{brief_id}: workflow written")
        trace.close("ok")
        return workflow_ok(out / "workflow.json")
    except Exception:
        trace.close("error")
        raise


def score_f2():
    out = ROOT / "results" / "tcc90" / "fakes" / "f2-ostral"
    score_path = out / "score.json"
    if score_path.exists():
        current = json.loads(score_path.read_text())
        if current.get("status") != "skipped" and "passed" in current and "final" in current:
            say("f2-ostral: score already written")
            return
        score_path.rename(out / "score.skipped.json")
        say("f2-ostral: kept the skip note, scoring the existing workflow")
    score("f2-ostral", out / "workflow.json", "score.json")


def score_public_b(workflow_path: Path):
    import run_cases
    from tcc90.freya_sim import FreyaSimRunner

    workflow = json.loads(workflow_path.read_text())
    runner = FreyaSimRunner(
        freya, slot="brief-b-local", origin=None,
        session_fn=lambda case: {}, ledger_fn=lambda variables: {},
        score_fn=lambda case, led: {"pass": True, "failed": []},
    )
    saved = runner._publish(workflow)
    agent_id = saved["agent_id"]
    say(f"brief-b: agent {agent_id}")
    out = ROOT / "results" / "tcc90" / "brief-b-local-score.json"
    sys.argv = ["run_cases.py", agent_id, "--brief", "B", "--reps", "3", "--out", str(out)]
    run_cases.main()


def _run(label, fn):
    """Retry when OpenRouter still has requests in flight. Do not print the error body."""
    for attempt in range(1, 4):
        try:
            return fn()
        except Exception as error:
            if "in_flight" in str(error) and attempt < 3:
                say(f"{label}: OpenRouter in-flight budget, waiting 150s")
                time.sleep(150)
                continue
            say(f"{label}: failed ({type(error).__name__}: {str(error).split(chr(10))[0][:180]})")
            return None


def main():
    freya.run_simulation = bounded_run_simulation
    say("API extract and Freya. Claude Code is not called. No patch loop.")
    _run("f2-ostral", score_f2)
    for brief_id in BUILD:
        out = ROOT / "results" / "tcc90" / "fakes" / brief_id

        def one(brief_id=brief_id, out=out):
            inputs = copy_inputs(brief_id)
            if not build_local(brief_id, [inputs / "brief.md"], inputs / "tools.openapi.json", out):
                say(f"{brief_id}: build produced no workflow")
                return
            score_path = out / "score.json"
            if score_path.exists() and "passed" in json.loads(score_path.read_text()):
                say(f"{brief_id}: score already written")
                return
            score(brief_id, out / "workflow.json", "score.json")

        _run(brief_id, one)
    out = ROOT / "results" / "tcc90" / "brief-b-local"
    api = ROOT / "tools" / "openapi.json"
    brief = ROOT / "briefs" / "brief-b.md"

    def public_b():
        if build_local("brief-b", [brief], api, out):
            if not (ROOT / "results" / "tcc90" / "brief-b-local-score.json").exists():
                score_public_b(out / "workflow.json")
            else:
                say("brief-b: score already written")

    _run("brief-b", public_b)
    say("done")


if __name__ == "__main__":
    main()
