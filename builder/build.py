"""Our hackathon builder: brief material + a tool API file -> a Freya workflow phone agent.

    python3 builder/build.py <case_folder> <mock_url> [--members "A,B"] [options]      (the final format: ONE case folder + the mock tool API URL)
    python3 builder/build.py <brief> [<more briefs> ...] <tools.openapi.json> [options]

The tool API is the one argument whose JSON has a top-level `openapi` key; every other argument is brief material in
override order (Brief B: `brief-a.md brief-b.md`). Variants register in VARIANTS; `tcc` (the template compiler + self-test + repair pipeline,
scripts/final_run.sh) is the default and the official run command; `agentic` is the earlier Claude Code builder;
`plan` compiles a procedure plan (steps, tool branches, spoken lines, money) extracted from the documents.
Exit code 0 when a lint-clean workflow was produced (and saved, with --save freya).

The tcc variant needs: `python3 -m venv .venv310` with the packages of README_RUN.md, the Claude login (claude_cli), FREYA_API_KEY in the environment or .env,
cloudflared. A brief that builds on another ("Brief B builds on Brief A") is built together with it: the referenced brief is taken from the call or from briefs/.
Results: <out>/workflow.json (last-green), the Freya export workflows/<slot>.workflow.json (committed path), the trace under traces/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from builder.common.io import InputError, slot_name, split_inputs  # noqa: E402


def run_agentic(args, briefs, api) -> dict:
    from builder.agentic.run import BuildOptions, run  # noqa: PLC0415

    extra = {}
    led = Path(args.out) / "conflict_ledger.json"
    if led.exists():
        extra["ledger"] = str(led)
    return run(BuildOptions(briefs=briefs, api=api, slot=args.slot, out_dir=Path(args.out), save=args.save,
                            max_minutes=args.max_minutes, model=args.model, max_usd=args.max_usd,
                            review=args.review == "on", review_minutes=args.review_minutes, fix_minutes=args.fix_minutes,
                            unit=args.unit == "on", localsim=args.localsim == "on",
                            keep_workspace=args.keep_workspace, self_test=args.self_test, extra=extra))


def tcc_command(args, briefs, api) -> tuple[list[str], dict]:
    """The final_run.sh command line and environment for this build (pure; unit-tested)."""
    env = dict(os.environ)
    mode = (REPO / "FINAL_MODE.txt")
    env.setdefault("FINAL_MODE", mode.read_text().strip() if mode.exists() else "patcher_on")
    env.setdefault("FINAL_VOTES", "3")
    budget = int(args.max_minutes)
    cmd = ["bash", str(REPO / "scripts" / "final_run.sh"), "--budget-min", str(budget), "--ship-min", str(max(1, budget - 4))]
    if args.save == "none":
        cmd.append("--offline")
    cmd += [str(b) for b in briefs] + [str(api), str(args.out)]
    return cmd, env


def run_tcc(args, briefs, api) -> dict:
    cmd, env = tcc_command(args, briefs, api)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(cmd, cwd=REPO, env=env).returncode
    report = {}
    try:
        report = json.loads((out / "build_report.json").read_text())
    except (OSError, ValueError):
        pass
    exports = sorted(out.glob("*.workflow.json"))
    saved = None
    if exports:   # the Freya export, copied to a committed path
        dest = REPO / "workflows"
        dest.mkdir(exist_ok=True)
        saved = dest / f"{args.slot}.workflow.json"
        shutil.copyfile(exports[-1], saved)
    ok = rc == 0 and (out / "workflow.json").exists() and (report.get("status") in (None, "ok"))
    return {"status": "ok" if ok else "failed", "slot": args.slot, "saved": str(saved) if saved else None, "fallback_used": None,
            "message": None if ok else f"final_run.sh exit {rc}; see {out}/final_run.log", "trace": report.get("trace"), "out": str(out)}


def run_plan(args, briefs, api) -> dict:
    from builder.plan.run import build  # noqa: PLC0415

    text = "\n\n".join(Path(b).read_text(encoding="utf-8") for b in briefs)
    out = Path(args.out)
    report = build(text, Path(api), out)
    saved = None
    wf = out / "workflow.json"
    if report.get("status") == "ok" and wf.exists():
        dest = REPO / "workflows"
        dest.mkdir(exist_ok=True)
        saved = dest / f"{args.slot}.workflow.json"
        shutil.copyfile(wf, saved)
    report["saved"] = str(saved) if saved else None
    report["slot"] = args.slot
    report["fallback_used"] = None
    return report


VARIANTS = {"tcc": run_tcc, "agentic": run_agentic, "plan": run_plan}


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("inputs", nargs="+", help="brief files (any text format) and exactly one tool API file")
    p.add_argument("--variant", default="tcc", choices=sorted(VARIANTS))
    p.add_argument("--save", default="freya", choices=["freya", "none"], help="none: write files only, touch nothing on Freya")
    p.add_argument("--slot", help="Freya slot name; default <variant>-<last brief stem>")
    p.add_argument("--max-minutes", type=float, default=None, help="hard wall clock (tcc: 30, shipped at minute -4; agentic: 15)")
    p.add_argument("--unit", default="on", choices=["on", "off"], help="run the builder's own unit tests on Freya (a -unit copy)")
    p.add_argument("--localsim", default="on", choices=["on", "off"], help="hard local simulations from the builder's own policy; failures join the fix round")
    p.add_argument("--review", default="on", choices=["on", "off"], help="fresh-context review + one fix round")
    p.add_argument("--review-minutes", type=float, default=5.0)
    p.add_argument("--fix-minutes", type=float, default=6.0)
    p.add_argument("--model", default=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"))
    p.add_argument("--max-usd", type=float, default=None, help="optional cost cap for the agent run")
    p.add_argument("--members", default="", help="team members for submission.json, comma separated (default: MEMBERS.txt)")
    p.add_argument("--out", help="output directory; default out/<variant>/<slot>/")
    p.add_argument("--self-test", default="none", choices=["none"], help="later: local, freya")
    p.add_argument("--keep-workspace", action="store_true", help="keep the temp workspace for inspection")
    return p.parse_args(argv)


def write_submission(args, report: dict, mock_url: str) -> Path:
    """submission.json: members[], workflow (the exported Freya workflow, else the last-green file), trace, mock_url. In <out>/ and at the repo root."""
    members = [m.strip() for m in (args.members or "").split(",") if m.strip()]
    mf = REPO / "MEMBERS.txt"
    if not members and mf.exists():
        members = [m.strip() for m in mf.read_text().replace("\n", ",").split(",") if m.strip()]
    wf = report.get("saved") or str(Path(args.out) / "workflow.json")
    sub = {"members": members, "workflow": wf, "trace": report.get("trace"), "mock_url": mock_url}
    (Path(args.out) / "submission.json").write_text(json.dumps(sub, indent=1, ensure_ascii=False))
    (REPO / "submission.json").write_text(json.dumps(sub, indent=1, ensure_ascii=False))
    return REPO / "submission.json"


def _reexec_in_venv():
    """`python3 builder/build.py ...` may run on a Python without the converters (python-docx, openpyxl, pypdf): run under .venv310 when it exists."""
    venv = REPO / ".venv310"
    py = venv / "bin" / "python"
    if py.exists() and Path(sys.prefix).resolve() != venv.resolve() and not os.environ.get("BUILD_PY_REEXEC"):
        os.environ["BUILD_PY_REEXEC"] = "1"
        os.execv(str(py), [str(py), *sys.argv])


def main(argv=None) -> int:
    if argv is None:
        _reexec_in_venv()
    args = parse(argv)
    mock_url = ""
    ing = None
    if Path(args.inputs[0]).is_dir():   # final format: <case_folder> <mock_url>
        from builder.ingest import ingest  # noqa: PLC0415
        if len(args.inputs) != 2 or not args.inputs[1].startswith("http"):
            print("error: usage: builder/build.py <case_folder> <mock_url>", file=sys.stderr)
            return 2
        folder, mock_url = Path(args.inputs[0]), args.inputs[1]
        args.slot = args.slot or "case-" + "".join(c if c.isalnum() else "-" for c in folder.resolve().name.lower()).strip("-")[:40]
        args.out = args.out or str(REPO / "out" / args.variant / args.slot)
        try:
            ing = ingest(folder, mock_url, Path(args.out) / "ingest")
        except Exception as e:  # noqa: BLE001
            print(f"error: ingest failed: {e}", file=sys.stderr)
            return 2
        for n in ing["notes"]:
            if "FAILED" in n:
                print(f"WARNING ingest: {n}", file=sys.stderr)
        print(f"ingest: {ing['files']} file(s), {ing['chars']} chars, tool API from {ing['api_source']}", file=sys.stderr)
        briefs, api = [Path(ing["brief"])], Path(ing["api"])
    else:
        try:
            briefs, api = split_inputs(args.inputs)
        except InputError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    args.max_minutes = args.max_minutes if args.max_minutes is not None else (30.0 if args.variant == "tcc" else 15.0)
    args.slot = args.slot or slot_name(briefs, args.variant)
    args.out = args.out or str(REPO / "out" / args.variant / args.slot)
    from builder.ledger import sources_from_paths, write_ledger  # noqa: PLC0415
    sources = ing["sources"] if ing else sources_from_paths(briefs)
    ledger = write_ledger(sources, Path(args.out) / "conflict_ledger.json")
    open_n = sum(1 for r in ledger["rules"] if r.get("open"))
    print(f"ledger: {len(ledger['rules'])} rule(s), {open_n} open", file=sys.stderr)
    report = VARIANTS[args.variant](args, briefs, api)
    if mock_url:
        report["submission"] = str(write_submission(args, report, mock_url))
    print(json.dumps({k: report.get(k) for k in ("status", "slot", "saved", "fallback_used", "message", "trace", "submission")}, default=str))
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
