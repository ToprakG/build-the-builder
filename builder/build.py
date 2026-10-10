"""The shared builder entrypoint. Every brief, including ones never seen before, runs through
the same six stages:

    python3 builder/build.py <brief_file> <tool_api_file> [options]
    python3 builder/build.py briefs/brief-a.md tools/openapi.json --dry-run
    python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json   # later briefs override earlier ones

  1. extract     brief text + tool API -> canonical config      extractor/extract.py
  2. graph       config + tool API -> workflow JSON              seed/graph.py
  3. check       workflow_check (local) + validate_workflow (Freya dry run)
  4. publish     save_workflow, unless --dry-run or a check failed
  5. self-test   local checks, then after publish: unit tests, slot repair, and a second run
  6. export      freya.export_workflow, when --export is given

Options:
  --dry-run          stop after the dry-run validation; do not publish
  --print            print the workflow JSON and stop, before any check
  --print-config     print the extracted config JSON and stop, before the graph is built
  --config PATH      skip extraction; use this config JSON file instead (for comparing extractors)
  --model NAME       override the extraction model (sets EXTRACTOR_MODEL; see extractor/llm.py)
  --no-self-test     skip stage 5
  --export PATH      write the published workflow to this file (stage 6)
  --slot NAME        save_workflow slot (default: the brief file names, joined with "+")

Every run writes a trace to results/traces/<slot>.txt: start and end time, every model call this
build made and why, what it checked, what it published.

Each extraction strategy is a variant of this file: point --config at a different extractor's
output (e.g. extractor's own --static mode, code-only, no model), or swap the import of
extractor.extract.extract for another module with the same extract(paths, tools_path) -> {"config":
...} signature. Everything after stage 1 -- the graph, the checks, the publish, the trace shape --
stays identical, so builds from different strategies are directly comparable.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya
from extractor.extract import extract
from extractor.tools_api import load as load_tools_api
from seed.graph import build_workflow
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


def self_tests(trace, workflow, config):
    trace.section("Self-tests")
    ok = True
    problems = check_workflow(workflow, config)
    snippets = list(iter_snippets(workflow))
    if problems:
        ok = False
        trace.line(f"- workflow check: fail. {len(problems)} problem(s). First: {problems[0]}")
    else:
        trace.line(f"- workflow check: pass. {len(workflow['nodes'])} nodes, {len(snippets)} code snippets ran.")
    py = _python()
    ok = _run_test(trace, "seed/test_datefns.py", [py, str(ROOT / "seed" / "test_datefns.py")]) and ok
    ok = _run_test(trace, "mockbank.py --self-test", [py, str(ROOT / "tools" / "driver" / "mockbank.py"), "--self-test"]) and ok
    return ok


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help="one or more brief files, then the tool API file last")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--print", dest="print_workflow", action="store_true")
    parser.add_argument("--print-config", action="store_true")
    parser.add_argument("--config", help="skip extraction; use this config JSON file instead")
    parser.add_argument("--model", help="override the extraction model")
    parser.add_argument("--no-self-test", action="store_true")
    parser.add_argument("--export", help="write the published workflow to this file")
    parser.add_argument("--slot", help="save_workflow slot name")
    args = parser.parse_args(argv)
    *brief_args, tool_api_arg = args.files
    if not brief_args:
        parser.error("need at least one brief file and a tool API file")
    args.briefs = [pathlib.Path(p) for p in brief_args]
    args.tool_api = pathlib.Path(tool_api_arg)
    return args


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    for p in [*args.briefs, args.tool_api]:
        if not p.exists():
            print(f"no such file: {p}", file=sys.stderr)
            sys.exit(1)

    if args.model:
        os.environ["EXTRACTOR_MODEL"] = args.model

    api = load_tools_api(args.tool_api)
    slot = args.slot or "+".join(p.stem for p in args.briefs)
    trace = Trace(ROOT / "results" / "traces" / f"{slot}.txt", slot)
    try:
        trace.section("Extract")
        if args.config:
            config = json.loads(pathlib.Path(args.config).read_text())
            trace.line(f"- config read from {args.config}, no model call")
        else:
            result = extract(args.briefs, args.tool_api)
            config = result["config"]
            for row in result.get("trace") or []:
                trace.model(row.get("model", "?"), f"{row.get('purpose', '?')} ({row.get('seconds', '?')}s)")
            if result.get("checks"):
                trace.line(f"- checks after extraction: {len(result['checks'])} remaining. First: {result['checks'][0]}")
            else:
                trace.line("- checks after extraction: pass")
            if result.get("conflicts"):
                trace.line(f"- conflicts resolved (a later document overrode an earlier rule): {len(result['conflicts'])}")
            if result.get("unmapped"):
                trace.line(f"- brief rules the verifier could not map to a field: {len(result['unmapped'])}")
            trace.line(f"- config extracted from {', '.join(p.name for p in args.briefs)} and {args.tool_api.name}")
        if args.print_config:
            print(json.dumps(config, indent=1))
            return

        trace.section("Graph")
        workflow = build_workflow(config, api)
        trace.line(f"- {len(workflow['nodes'])} nodes built from the config")
        if args.print_workflow:
            print(json.dumps(workflow, indent=1))
            return

        if args.no_self_test:
            trace.section("Self-tests")
            trace.line("- skipped (--no-self-test)")
            tests_ok = True
        else:
            tests_ok = self_tests(trace, workflow, config)

        trace.section("Freya")
        trace.line(f"- agent model: {freya.AGENT_LLM['provider']} / {freya.AGENT_LLM['model']} (set in freya.py; this build does not call it)")
        if not tests_ok:
            trace.line("- not published. A self-test failed.")
            print(f"self-tests failed. Trace: {trace.path}")
            sys.exit(1)

        dry = freya.validate_workflow(slot, workflow)
        trace.line(f"- dry-run valid: {bool(dry.get('valid'))}")
        if not dry.get("valid"):
            trace.line("- not published. The dry run was invalid.")
            print(json.dumps(dry, indent=1))
            sys.exit(1)
        if args.dry_run:
            print(json.dumps(dry, indent=1))
            return

        saved = freya.save_workflow(slot, workflow)
        trace.line(f"- slot: {slot}")
        trace.line(f"- workflow_id: {saved.get('workflow_id')}")
        trace.line(f"- agent_id: {saved.get('agent_id')}")
        trace.line(f"- version: {saved.get('version')}")
        print(json.dumps(saved))

        if not args.no_self_test:
            from selftest.loop import run_loop
            try:
                workflow = run_loop(workflow, config, saved["workflow_id"], args.briefs, args.tool_api, trace)
            except Exception as error:
                trace.line(f"- unit self-test error: {error}")
                print(f"unit self-test error: {error}. Trace: {trace.path}")

        if args.export:
            path = freya.export_workflow(saved["workflow_id"], args.export)
            trace.line(f"- exported: {path}")
            print(f"exported: {path}")
        print(f"trace: {trace.path}")
    finally:
        trace.finish()


if __name__ == "__main__":
    main()
