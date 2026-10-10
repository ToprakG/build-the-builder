"""The real builder for Briefs A and B.

    python3 seed/build_ab.py briefs/brief-a.md                      -> prints {workflow_id, agent_id}
    python3 seed/build_ab.py briefs/brief-a.md briefs/brief-b.md     -> prints {workflow_id, agent_id}

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
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from seed.graph import build_workflow
from seed.policy_ab import POLICY


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

    workflow = build_workflow(POLICY, tools_api)

    if "--print" in args:
        print(json.dumps(workflow, indent=1))
        return

    if "--dry-run" in args:
        result = freya.validate_workflow("+".join(b.stem for b in briefs), workflow)
        print(json.dumps(result, indent=1))
        return

    slot = "+".join(b.stem for b in briefs)
    print(json.dumps(freya.save_workflow(slot, workflow)))


if __name__ == "__main__":
    main()
