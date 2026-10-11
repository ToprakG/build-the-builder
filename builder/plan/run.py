"""Run the procedure compiler: documents + a tool API -> plan.json + workflow.json.

    python3 -m builder.plan.run <documents> <tools.openapi.json> <out dir>
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from builder.agentic.tools import lint as alint
from builder.agentic.tools.openapi_summary import render, summarize
from builder.plan.compile import compile_plan, dump, used_tools
from builder.plan.extract import extract_with_llm, from_text
from builder.plan.schema import PlanError, complete, validate


def build(documents: str, api_path: Path, out: Path, *, model: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(Path(api_path).read_text(encoding="utf-8"))
    summary = summarize(spec)
    trace = ["procedure plan"]
    existing = from_text(documents)
    if existing is not None:
        plan = existing
        trace.append("plan: the documents are already a plan")
    else:
        model = model or os.environ.get("PLAN_EXTRACT_MODEL") or os.environ.get("TCC_EXTRACT_MODEL") or "claude_cli:sonnet"
        trace.append(f"extract: {model}")
        plan, notes = extract_with_llm(documents, render(summary), model)
        trace.extend(notes)
    plan = complete(plan)
    errors = validate(plan, summary)
    (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if errors:
        trace.append("plan errors: " + "; ".join(errors))
        (out / "trace.txt").write_text("\n".join(trace) + "\n", encoding="utf-8")
        return {"status": "failed", "message": "; ".join(errors), "trace": str(out / "trace.txt"), "out": str(out)}
    try:
        wf = compile_plan(plan, summary)
    except PlanError as exc:
        trace.append("compile: " + "; ".join(exc.errors))
        (out / "trace.txt").write_text("\n".join(trace) + "\n", encoding="utf-8")
        return {"status": "failed", "message": str(exc), "trace": str(out / "trace.txt"), "out": str(out)}
    (out / "workflow.json").write_text(dump(wf), encoding="utf-8")
    unused = sorted(set(summary.get("tools") or []) - used_tools(plan))
    report = alint.lint(wf, summary, plan.get("call_variables"), unused)
    (out / "lint.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    trace.append(f"lint: {len(report['errors'])} errors, {len(report['warnings'])} warnings")
    (out / "trace.txt").write_text("\n".join(trace) + "\n", encoding="utf-8")
    ok = not report["errors"]
    return {"status": "ok" if ok else "failed", "message": None if ok else "lint errors",
            "trace": str(out / "trace.txt"), "out": str(out), "workflow": str(out / "workflow.json")}


def main(argv=None) -> int:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        print("usage: python3 -m builder.plan.run <documents> <tools.openapi.json> <out dir>", file=sys.stderr)
        return 2
    text = Path(args[0]).read_text(encoding="utf-8")
    report = build(text, Path(args[1]), Path(args[2]))
    print(json.dumps({k: report.get(k) for k in ("status", "message", "trace", "workflow")}))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
