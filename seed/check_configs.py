"""Build every one of the 9 canonical configs and check the workflow.

Static checks and a local run of every code node and hook live in seed/workflow_check.py.
Pass --dry-run to also send each workflow to the Freya API's dry-run validator.

    python3 seed/check_configs.py
    python3 seed/check_configs.py --dry-run
"""
import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from seed.graph import build_workflow
from seed.workflow_check import check_workflow, iter_configs, iter_snippets


def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        import freya
    all_ok = True
    for name, policy, tools_path in iter_configs():
        tools_api = json.loads(pathlib.Path(tools_path).read_text())
        try:
            workflow = build_workflow(policy, tools_api)
        except Exception as error:
            print(f"FAIL {name}: build_workflow raised {type(error).__name__}: {error}")
            all_ok = False
            continue
        problems = check_workflow(workflow, policy)
        if problems:
            print(f"FAIL {name}: {len(problems)} problem(s)")
            for problem in problems[:20]:
                print("   -", problem)
            if len(problems) > 20:
                print(f"   - ... {len(problems) - 20} more")
            all_ok = False
            continue
        if dry_run:
            try:
                result = freya.validate_workflow(f"check-{name}", workflow)
                if result.get("valid"):
                    print(f"PASS {name}: dry-run valid ({len(workflow['nodes'])} nodes)")
                else:
                    print(f"FAIL {name}: dry-run invalid: {json.dumps(result)[:500]}")
                    all_ok = False
            except Exception as error:
                print(f"FAIL {name}: dry-run error: {error}")
                all_ok = False
        else:
            snippets = list(iter_snippets(workflow))
            print(f"PASS {name}: static and runtime OK ({len(workflow['nodes'])} nodes, {len(snippets)} snippets)")
    print()
    print("ALL 9 CONFIGS OK" if all_ok else "SOME CONFIGS FAILED")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
