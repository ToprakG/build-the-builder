"""The real builder for Briefs A and B.

    python3 seed/build_ab.py briefs/brief-a.md                      -> prints {workflow_id, agent_id}
    python3 seed/build_ab.py briefs/brief-a.md briefs/brief-b.md     -> prints {workflow_id, agent_id}

After a publish the builder syncs the unit suite and runs it on Freya. --no-unit-tests skips that run.

Unlike examples/minimal_builder.py, the brief text itself is not pasted into a prompt: every rule
this builder enforces (bands, deadlines, floors, identity, callback window, outcomes, sentences,
transfer queues) is already encoded as data in seed/policy_ab.py, and seed/graph.py turns that data
plus the brief's own tools/openapi.json into the workflow graph. The brief files on the command
line only pick which saved-workflow slot this run updates and, for a final brief, which
tools/openapi.json-equivalent file to read if one is given with --tools.

The same graph and policy handle one product or three: Brief B's up-to-three-product rules are a
strict generalisation of Brief A's one-product rules (see seed/policy_ab.py's docstring), so
"brief-a.md" and "brief-a.md brief-b.md" build the same correct agent; only the slot name differs,
matching run_cases.py's --brief A / --brief B practice sets.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from seed.graph import build_workflow
from seed.policy_ab import POLICY_A, POLICY_B
from seed.trace import Trace
from seed.workflow_check import check_workflow, iter_snippets


def _python():
    venv = ROOT / ".venv" / "bin" / "python"
    return str(venv if venv.exists() else pathlib.Path(sys.executable))

def _run_test(trace, name, args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    detail = (result.stdout or result.stderr or "").strip().splitlines()
    tail = detail[-1] if detail else "no output"
    mark = "pass" if result.returncode == 0 else "fail"
    trace.line(f"- {name}: {mark}. {tail}")
    return result.returncode == 0

def _self_tests(trace, workflow, policy):
    trace.section("Self-tests")
    ok = True
    problems = check_workflow(workflow, policy)
    snippets = list(iter_snippets(workflow))
    if problems:
        ok = False
        trace.line(f"- workflow check: fail. {len(problems)} problem(s). First: {problems[0]}")
    else:
        trace.line(f"- workflow check: pass. {len(workflow['nodes'])} nodes, {len(snippets)} code snippets ran.")
    py = _python()
    ok = _run_test(trace, "seed/test_datefns.py", [py, "seed/test_datefns.py"]) and ok
    ok = _run_test(trace, "seed/unit_tests.py --print", [py, "seed/unit_tests.py", "--print"]) and ok
    ok = _run_test(trace, "tools/driver/mockbank.py --self-test", [py, "tools/driver/mockbank.py", "--self-test"]) and ok
    return ok

def main():
    args = sys.argv[1:]
    briefs = [pathlib.Path(a) for a in args if not a.startswith("--")]
    if not briefs:
        print(__doc__)
        sys.exit(1)
    for b in briefs:
        if not b.exists():
            print(f"no such file: {b}", file=sys.stderr)
            sys.exit(1)

    tools_path = ROOT / "tools" / "openapi.json"
    if "--tools" in args:
        tools_path = pathlib.Path(args[args.index("--tools") + 1])
    tools_api = json.load(open(tools_path))

    policy = POLICY_B if len(briefs) > 1 else POLICY_A
    workflow = build_workflow(policy, tools_api)

    if "--print" in args:
        print(json.dumps(workflow, indent=1))
        return

    slot = "+".join(b.stem for b in briefs)
    trace = Trace(ROOT / "results" / "traces" / f"{slot}.txt", slot)
    try:
        tests_ok = _self_tests(trace, workflow, policy)
        trace.section("Freya")
        trace.line(f"- agent model: {freya.AGENT_LLM['provider']} / {freya.AGENT_LLM['model']} (set in freya.py; this build does not call it)")
        if not tests_ok:
            trace.line("- not published. A self-test failed.")
            print(f"self-tests failed. Trace: {trace.path}")
            sys.exit(1)
        if "--dry-run" in args:
            result = freya.validate_workflow(slot, workflow)
            trace.line(f"- dry-run valid: {bool(result.get('valid'))}")
            print(json.dumps(result, indent=1))
            return
        saved = freya.save_workflow(slot, workflow)
        trace.line(f"- slot: {slot}")
        trace.line(f"- workflow_id: {saved.get('workflow_id')}")
        trace.line(f"- agent_id: {saved.get('agent_id')}")
        trace.line(f"- version: {saved.get('version')}")
        print(json.dumps(saved))
        if "--no-unit-tests" in args:
            trace.section("Unit tests")
            trace.line("- skipped (--no-unit-tests)")
        else:
            from seed.unit_tests import sync_and_run
            trace.section("Unit tests")
            try:
                report = sync_and_run(saved["workflow_id"], policy, trace=trace)
            except Exception as error:
                trace.line(f"- unit tests error: {error}")
                print(f"unit tests error: {error}. Trace: {trace.path}")
                sys.exit(1)
            if report["passed"] != report["total"]:
                print(f"unit tests failed ({report['passed']}/{report['total']}). Trace: {trace.path}")
                sys.exit(1)
        print(f"trace: {trace.path}")
    finally:
        trace.finish()


if __name__ == "__main__":
    main()
