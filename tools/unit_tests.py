"""Sync a unit suite onto a workflow, run it, and collect each verdict.

    python3 tools/unit_tests.py --time --slot brief-a
    python3 tools/unit_tests.py --slot brief-a --limit 15

`--time` runs the full config suite once and writes results/unit-run-timing.json.
"""
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seed.unit_tests import run_tests, sync_tests

TIMING = ROOT / "results" / "unit-run-timing.json"


def execute(workflow_id, tests):
    """Sync these tests and run them. Returns (results, seconds)."""
    started = time.time()
    ids = sync_tests(workflow_id, tests)
    results = run_tests(workflow_id, tests, ids)
    return results, time.time() - started


def index_results(results):
    return {row["name"]: row for row in results}


def rerun(workflow_id, tests, names):
    chosen = [test for test in tests if test["name"] in set(names)]
    if not chosen:
        return []
    results, _seconds = execute(workflow_id, chosen)
    return results


def write_timing(n, seconds, passed):
    TIMING.parent.mkdir(parents=True, exist_ok=True)
    TIMING.write_text(json.dumps({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n": n,
        "run_s": round(seconds, 1),
        "passed": passed,
    }, indent=1))


def plan_from_timing(path=TIMING):
    """25 tests over about 5 minutes means a smaller suite and one repair round."""
    if not path.exists():
        return 15, 1, "no timing sample; using 15 tests and 1 repair round"
    row = json.loads(path.read_text())
    n = max(int(row.get("n") or 1), 1)
    seconds = float(row.get("run_s") or 0)
    estimate = seconds / n * 24
    if estimate > 300:
        return 15, 1, f"{n} tests took {seconds:.0f}s, so 24 tests would take about {estimate:.0f}s. Using 15 tests and 1 repair round."
    return 24, 2, f"{n} tests took {seconds:.0f}s, so 24 tests would take about {estimate:.0f}s. Using 24 tests and 2 repair rounds."


def _workflow_id(argv):
    if "--workflow-id" in argv:
        return argv[argv.index("--workflow-id") + 1]
    slot = argv[argv.index("--slot") + 1] if "--slot" in argv else "brief-a"
    state = json.loads((ROOT / ".freya-agents.json").read_text())
    return state[slot]["workflow_id"]


def main():
    from tools.envfile import load_env
    load_env()
    from seed.policy_ab import POLICY_A
    from selftest.gen_tests import generate
    argv = sys.argv[1:]
    policy = POLICY_A
    if "--config" in argv:
        policy = json.loads(pathlib.Path(argv[argv.index("--config") + 1]).read_text())
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if "--time" in argv:
        limit = None
    tests = generate(policy, limit=limit)
    workflow_id = _workflow_id(argv)
    print(f"running {len(tests)} tests", flush=True)
    results, seconds = execute(workflow_id, tests)
    passed = sum(row["outcome"] == "pass" for row in results)
    print(f"{passed}/{len(results)} passed in {seconds:.0f}s")
    if "--time" in argv:
        write_timing(len(results), seconds, passed)
        print(f"wrote {TIMING}")
        limit, rounds, reason = plan_from_timing()
        print(reason)
        print(f"plan: limit={limit} rounds={rounds}")
        return
    sys.exit(0 if passed == len(results) and results else 1)


if __name__ == "__main__":
    main()
