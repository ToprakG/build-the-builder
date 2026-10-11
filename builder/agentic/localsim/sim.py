"""Hard local simulations: our engine (DeepSeek as the agent model) + the vendor mock bank + the vendor LLM customer
(DeepSeek too, in a hard talking style) + the kit's ledger scorer + Judge C on the Jev decision model.

    results = run_cases(workflow, cases, config)            # -> list of per-case dicts
    cases = cases_from_policy(policy, n)                     # self-test: cases generated from OUR policy.json

Nothing runs on Freya. The vendor tester lives in vendor/build-the-builder (the teammate's repo).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from builder.agentic.localsim.engine import Engine
from builder.agentic.localsim.models import DEFAULT_MODEL, OpenRouterModel

REPO = Path(__file__).resolve().parents[3]
VENDOR = Path(os.environ.get("LOCALSIM_VENDOR", REPO / "vendor" / "build-the-builder"))
TEAM = "localsim-team"
MAX_TURNS = 30
HARD_STYLES = ("pieces", "corrects", "in_words", "asks_back", "talkative")
_LOCK = threading.Lock()
_V = {}


def vendor():
    """Import the vendor modules once (generic names: score, judge, run_cases, chat_driver, mockbank)."""
    with _LOCK:
        if _V:
            return _V
        for p in (VENDOR / "tools" / "driver", VENDOR / "tools", VENDOR):
            if str(p) not in sys.path:
                sys.path.insert(0, str(p))
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):  # the vendor customer then uses OpenRouter
            os.environ.pop(k, None)
        import chat_driver  # noqa: PLC0415
        import mockbank  # noqa: PLC0415
        import run_cases  # noqa: PLC0415
        from judge import judge_transcript  # noqa: PLC0415
        from score import score_ledger  # noqa: PLC0415

        chat_driver.OPENROUTER_CUSTOMER = os.environ.get("LOCALSIM_CUSTOMER_MODEL", DEFAULT_MODEL)
        _V.update(chat_driver=chat_driver, mockbank=mockbank, run_cases=run_cases, judge=judge_transcript, score=score_ledger)
        return _V


def available() -> tuple[bool, str]:
    if not (VENDOR / "tools" / "driver" / "mockbank.py").exists():
        return False, f"vendor tester not found at {VENDOR}"
    if not os.environ.get("OPENROUTER_API_KEY"):
        return False, "OPENROUTER_API_KEY is empty"
    return True, "ok"


def cases_from_policy(policy: dict, n: int = 12, seed: int = 7) -> list[dict]:
    """Hard cases generated from the builder's own policy.json (T-CC's generic testgen; no testbed data read)."""
    from builder.template_cc.testgen import generate  # noqa: PLC0415

    cases, _notes = generate(policy, n_cases=n, seed=seed)
    return cases


def run_case(workflow: dict, case: dict, config: dict, bank, style: str | None = None) -> dict:
    V = vendor()
    t0 = time.time()
    session = bank.open_session(TEAM, case["id"])
    if session is None:
        return {"id": case["id"], "pass": False, "error": "the mock bank does not know this case"}
    variables = session["variables"]

    def bank_call(name, body):
        handler = V["mockbank"].HANDLERS.get(name)
        if handler is None:
            return 404, {"ok": False, "error": f"unknown tool {name}"}
        body = dict(body or {})
        target = bank.session_for(str(body.get("customer_id") or "")) or session
        with bank.lock:
            return handler(target, body, config)

    model = OpenRouterModel()
    engine = Engine(workflow, dict(variables), bank_call, model)
    persona = V["run_cases"].persona_for(case, {"identity": case["identity"]}, style)["prompt"]
    persona += "\n\nWHEN YOU NAME A TIME: say the hour and the minutes, like 11:00. Do not say morning, afternoon, or around in place of that time."
    agent_lines, customer_lines, error = [], [], None
    try:
        first = engine.start()
        if first:
            agent_lines.append(first)
        for _ in range(MAX_TURNS):
            if engine.ended or not agent_lines:
                break
            reply = V["chat_driver"].customer_reply(persona, agent_lines, customer_lines)
            customer_lines.append(reply)
            said = engine.user_says(reply)
            if said:
                agent_lines.append(said)
            elif not engine.ended:
                break  # the agent said nothing: the customer would hang up
    except Exception as e:  # noqa: BLE001  (one broken case never stops the others)
        error = repr(e)[:400]
    try:
        engine.finish()
    except Exception as e:  # noqa: BLE001
        error = error or f"end_of_call: {e!r}"[:400]
    ledger = bank.ledger(TEAM, variables["customer_id"])
    led = V["score"](case, ledger)
    try:
        judged = V["judge"](engine.events, case)
    except Exception as e:  # noqa: BLE001
        judged = {"pass": None, "status": f"judge error {e!r}"[:200], "checks": []}
    failed_checks = [c["check"] for c in judged.get("checks") or [] if c.get("answer") == "no"]
    return {"id": case["id"], "family": case.get("family"), "style": style,
            "pass": bool(led["pass"] and judged.get("pass") is True and not error),
            "ledger_pass": led["pass"], "ledger_failed": led.get("failed") or [],
            "judge_pass": judged.get("pass"), "judge_status": judged.get("status"), "failed_checks": failed_checks,
            "error": error, "engine_errors": engine.errors[:5], "nodes": [json.loads(e["message"])["node"] for e in engine.events
                                                                          if e["role"] == "system" and '"node_enter"' in e["message"]],
            "transcript": [(e["role"], e["message"]) for e in engine.events if e["role"] in ("user", "assistant")],
            "model_calls": model.calls, "seconds": round(time.time() - t0, 1),
            "turns": engine.turn_log if not (led["pass"] and judged.get("pass") is True and not error) else []}


CASE_LIMIT_S = float(os.environ.get("LOCALSIM_CASE_LIMIT_S", "420"))


def run_cases(workflow: dict, cases: list[dict], config: dict, parallel: int = 4, hard: bool = True) -> list[dict]:
    bank = vendor()["mockbank"].Bank(cases, config)
    styles = [c.get("style") or (HARD_STYLES[i % len(HARD_STYLES)] if hard else None) for i, c in enumerate(cases)]
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        return list(pool.map(lambda pair: _bounded(workflow, pair[0], config, bank, pair[1]), zip(cases, styles)))


def _bounded(workflow, case, config, bank, style, limit_s: float = CASE_LIMIT_S) -> dict:
    """run_case with a wall-clock limit. OpenRouter keeps a slow request alive with whitespace, so socket timeouts
    alone never fire and one stuck case would hold the whole run; past the limit the case counts as an error."""
    box = {}
    worker = threading.Thread(target=lambda: box.update(r=run_case(workflow, case, config, bank, style)), daemon=True)
    worker.start()
    worker.join(limit_s)
    if "r" in box:
        return box["r"]
    return {"id": case.get("id"), "family": case.get("family"), "pass": False, "error": f"case took over {int(limit_s)} s",
            "turns": [], "failed_checks": [], "ledger_failed": []}


def derive_tests(results: list[dict], workflow: dict, per_case: int = 2, total: int = 8) -> list[dict]:
    """Regression unit tests from failed local cases: the call's last customer turn, and the first turn where the
    agent repeated an earlier line (a loop). Each starts in that turn's node with that turn's variables; the judge
    sentence is what the case failed on."""
    conv = {nid for nid, n in (workflow.get("nodes") or {}).items() if (n.get("node_type") or "conversation") == "conversation"}
    conv |= set(workflow.get("global_nodes") or {})
    out = []
    for r in results:
        if r.get("pass") or not r.get("turns"):
            continue
        turns = [t for t in r["turns"] if t.get("node") in conv and str(t.get("agent") or "").strip()]
        if not turns:
            continue
        picks, seen = [turns[-1]], set()
        for t in turns:
            if t["agent"] in seen and t is not turns[-1]:
                picks.insert(0, t)
                break
            seen.add(t["agent"])
        why = "; ".join([*(r.get("ledger_failed") or []), *(r.get("failed_checks") or [])])[:500]
        for i, t in enumerate(picks[:per_case]):
            variables = dict(list({k: (v[:2000] if isinstance(v, str) else v) for k, v in t["vars"].items()}.items())[:150])
            out.append({"name": f"sim {r['id']} turn {r['turns'].index(t) + 1}", "derived_from": r["id"], "start_node": t["node"],
                        "variables": variables, "messages": [{"role": "assistant", "text": t["agent"]}, {"role": "user", "text": t["user"]}],
                        "expect": f"In this turn the agent moves the call toward what the brief requires here. The full call failed on: {why}",
                        "checks": [], "tags": ["derived"]})
            if len(out) >= total:
                return out
    return out


def summary(results: list[dict]) -> dict:
    return {"passed": sum(r["pass"] for r in results), "total": len(results),
            "ledger_failed": sum(not r.get("ledger_pass") for r in results),
            "judge_failed": sum(r.get("judge_pass") is False for r in results),
            "errors": sum(bool(r.get("error")) for r in results)}


def render(results: list[dict], limit: int = 12, transcript_lines: int = 14) -> str:
    """Compact text for Claude Code: the summary, then each failed case with its failed checks and the call's end."""
    s = summary(results)
    out = [f"{s['passed']}/{s['total']} cases passed locally ({s['ledger_failed']} bank-record failures, {s['judge_failed']} "
           f"conversation-check failures, {s['errors']} errors). Agent and customer: {DEFAULT_MODEL} (not the platform's "
           "agent model: structural bugs reproduce, wording behaviour may differ)."]
    for r in [r for r in results if not r["pass"]][:limit]:
        out.append(f"\nFAIL {r['id']} ({r.get('family')}, style {r.get('style')}); nodes: {' > '.join(r.get('nodes') or [])[:400]}")
        out += [f"  - record: {x}" for x in r.get("ledger_failed") or []]
        out += [f"  - check: {x}" for x in r.get("failed_checks") or []]
        if r.get("error") or r.get("engine_errors"):
            out.append(f"  - error: {r.get('error') or ''} {'; '.join(r.get('engine_errors') or [])}"[:500])
        for role, text in (r.get("transcript") or [])[-transcript_lines:]:
            out.append(f"    {role.upper()}: {text[:220]}")
    return "\n".join(out)


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Hard local simulations of a workflow (no Freya).")
    p.add_argument("workflow")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--brief", choices=["A", "B"], help="the kit's practice cases with the brief's correct config (development)")
    src.add_argument("--testbed", choices=["A", "B"], help="the testbed's 100 (A) / 60 (B) cases with the correct config (development)")
    src.add_argument("--fake", help="a testbed fake brief id (development)")
    src.add_argument("--policy", help="a policy.json: generate hard cases from it (what the builder's self-test does)")
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--only")
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--out")
    a = p.parse_args(argv)
    ok, why = available()
    if not ok:
        print(f"unavailable: {why}", file=sys.stderr)
        return 2
    wf = json.loads(Path(a.workflow).read_text())
    wf = wf.get("workflow", wf)
    if a.brief:
        cases = [c for c in json.loads((REPO / "public-test-set.json").read_text()) if c.get("brief") == a.brief]
        config = json.loads((REPO / "testbed" / "configs" / f"brief-{a.brief.lower()}.config.json").read_text())
    elif a.testbed:
        cases = json.loads((REPO / "testbed" / "personas" / f"brief-{a.testbed.lower()}.cases.json").read_text())
        config = json.loads((REPO / "testbed" / "configs" / f"brief-{a.testbed.lower()}.config.json").read_text())
    elif a.fake:
        folder = REPO / "testbed" / "fake_briefs" / a.fake
        cases, config = json.loads((folder / "cases.json").read_text()), json.loads((folder / "config.json").read_text())
    else:
        config = json.loads(Path(a.policy).read_text())
        cases = cases_from_policy(config, a.n)
    if a.only:
        cases = [c for c in cases if c["id"] in set(a.only.split(","))]
    cases = cases[: a.n]
    results = run_cases(wf, cases, config, a.parallel)
    print(render(results, limit=30))
    if a.out:
        Path(a.out).write_text(json.dumps(results, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
