"""S7 check: one manual analyzer -> unit tests -> patcher -> gate pass on runs/v1 and runs/testsets."""

from __future__ import annotations

import argparse

from agentforge.analyze.analyzer import analyze
from agentforge.config import RUNS_DIR
from agentforge.loop.gate import gate
from agentforge.patch.patcher import patch_agent
from agentforge.platform import get_platform
from agentforge.run import runner
from agentforge.patch.patcher import next_label
from agentforge.schemas import AgentConfig, Assertion, Persona, UnitTest
from agentforge.store import load_model, load_models, save_json
from agentforge.testgen.unit_tests import generate_unit_tests, save_unit_tests


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="vapi")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--patch-mode", choices=["surgical", "free"], default="surgical")
    parser.add_argument("--gate-only", action="store_true", help="reuse the saved candidate and unit tests")
    args = parser.parse_args()

    platform = get_platform(args.platform)
    v1 = load_model(RUNS_DIR / "v1" / "agent.json", AgentConfig)
    personas = load_models(RUNS_DIR / "testsets" / "personas.json", Persona)
    assertions = load_models(RUNS_DIR / "testsets" / "assertions.json", Assertion)
    train = [p for p in personas if "train" in p.tags]
    heldout = [p for p in personas if "heldout" in p.tags]

    if args.gate_only:
        candidate = load_model(RUNS_DIR / next_label(v1.version_label) / "agent.json", AgentConfig)
        tests = load_models(RUNS_DIR / v1.version_label / "unit_tests.json", UnitTest)
        return run_gate(platform, candidate, v1, heldout, assertions, tests, args.runs)

    scored = runner.run_suite(platform, v1, train, assertions, runs_per_persona=args.runs, set_name="train")
    runner.report(scored, f"{v1.version_label} train")
    failed = [s for s in scored if not s.passed]

    report = analyze(v1.version_label, failed, personas)
    save_json(RUNS_DIR / v1.version_label / "failure_report.json", report)
    for bug in report.bugs:
        print(f"[{bug.culprit}/{bug.layer}] {bug.bug_id}: {bug.generalized_rule}")

    tests = generate_unit_tests(report, failed)
    save_unit_tests(v1.version_label, tests)
    print(f"{len(tests)} unit tests")

    candidate, patches = patch_agent(v1, report, mode=args.patch_mode)
    save_json(RUNS_DIR / candidate.version_label / "agent.json", candidate)
    save_json(RUNS_DIR / candidate.version_label / "patches.json", patches)
    print(f"{candidate.version_label}: {len(patches)} patches, prompt {len(v1.system_prompt)} -> {len(candidate.system_prompt)} chars")

    run_gate(platform, candidate, v1, heldout, assertions, tests, args.runs)


def run_gate(platform, candidate, best, heldout, assertions, tests, runs) -> None:
    decision = gate(platform, candidate, best, heldout, assertions, tests, runs_per_persona=runs)
    print("COMMIT" if decision.commit else "DISCARD")
    for reason in decision.reasons:
        print(" -", reason)


if __name__ == "__main__":
    main()
