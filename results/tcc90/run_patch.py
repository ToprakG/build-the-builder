"""One patch-loop pass on an already extracted policy. No OpenRouter.

Analyze and judge go through `claude -p`. The patcher is a Claude Code session.
The cases are generated from the policy. The score is a Freya simulation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "results" / "tcc90"))
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from envfile import load_env

load_env()

from tcc90.boot import install

install()

BRIEF = sys.argv[1] if len(sys.argv) > 1 else "f2-ostral"
MINUTES = 30


def say(text):
    print(text, flush=True)


def claude_call(system, user, model, json_schema=None, temperature=0, max_tokens=4096, effort="low", *,
                trace=None, purpose="llm call", deadline=None, attempts=2):
    """One JSON answer from local Claude Code. This does not call OpenRouter."""
    from builder.template_cc.llm import parse_json, unwrap_schema_echo

    if "openrouter" in str(model).lower():
        raise RuntimeError("refusing OpenRouter")
    last = None
    for attempt in range(attempts):
        left = None if deadline is None else deadline - time.time()
        if left is not None and left < 20:
            raise TimeoutError(f"no time left for {purpose}")
        cmd = [
            "claude", "-p", "--model", "sonnet", "--effort", "low", "--tools", "",
            "--system-prompt", system, "--output-format", "json",
            "--no-session-persistence", "--disable-slash-commands",
            "--permission-mode", "dontAsk", "--setting-sources", "user", "--strict-mcp-config",
        ]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema)]
        started = time.time()
        try:
            proc = subprocess.run(cmd, input=user, text=True, capture_output=True, timeout=min(420, left or 420))
        except subprocess.TimeoutExpired as error:
            last = error
            if trace:
                trace.warn(f"{purpose}: claude timed out")
            continue
        if proc.returncode != 0:
            last = RuntimeError(f"claude exit {proc.returncode}: {(proc.stderr or '')[-240:]}")
            if trace:
                trace.warn(f"{purpose}: {last}")
            continue
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as error:
            last = error
            continue
        text = payload.get("result") or ""
        if isinstance(text, dict):
            parsed = text
        else:
            parsed = None
        usage = payload.get("usage") or {}
        if trace:
            trace.model_call("sonnet", purpose + (f" (retry {attempt})" if attempt else ""),
                             usage.get("input_tokens"), usage.get("output_tokens"),
                             payload.get("total_cost_usd"), time.time() - started)
        if payload.get("is_error"):
            last = RuntimeError(str(text)[:240])
            continue
        if json_schema is None:
            return text if isinstance(text, str) else json.dumps(text)
        try:
            if parsed is None:
                parsed = parse_json(text)
            return unwrap_schema_echo(parsed, json_schema)
        except ValueError as error:
            last = error
            if trace:
                trace.warn(f"{purpose}: {error}")
    raise last if last else RuntimeError("claude call failed")


def resolve(brief: str) -> tuple[Path, Path, Path, Path]:
    """out dir, brief, openapi, policy. A fake brief keeps its copied inputs. A public brief uses briefs/ and tools/."""
    fake = ROOT / "results" / "tcc90" / "fakes" / brief
    if (fake / "policy.json").exists():
        return fake, fake / "input" / "brief.md", fake / "input" / "tools.openapi.json", fake / "policy.json"
    found = sorted((ROOT / "results" / "tcc90").glob(f"{brief}*/policy.json"), key=lambda path: len(str(path)))
    if not found:
        raise SystemExit(f"no extracted policy for {brief}")
    out = ROOT / "results" / "tcc90" / f"{brief}-loop"
    return out, ROOT / "briefs" / f"{brief}.md", ROOT / "tools" / "openapi.json", found[0]


def main():
    import freya
    from run_rest import bounded_run_simulation
    freya.run_simulation = bounded_run_simulation
    from builder.template_cc import compiler, gate, loop
    from builder.template_cc.apimodel import ApiModel
    from builder.template_cc.io import Models, brief_text, read_briefs
    from builder.template_cc.judge import LocalDecisionBackend, LocalDecisionJudge
    from builder.template_cc.trace import Trace
    from builder.template_cc.vendor_runner import VendorRunner, availability
    from run_fakes import _FreyaLoopRunner
    from tcc90.assemble import assemble

    out, brief_path, api_path, policy_path = resolve(BRIEF)
    ok, why = availability()
    if not ok:
        say(f"patch loop skipped ({why})")
        return
    if not brief_path.exists() or not api_path.exists():
        raise SystemExit(f"missing {brief_path} or {api_path}")
    out.mkdir(parents=True, exist_ok=True)
    briefs = read_briefs([brief_path])
    text = brief_text(briefs)
    api = ApiModel.load(api_path)
    policy = json.loads(policy_path.read_text())
    workflow, book, _planned = assemble(policy, api, text, compiler.compile)
    models = Models()
    models.patch = "sonnet"
    models.analyze = "sonnet"
    models.judge = "sonnet"
    trace = Trace(out / "loop-local", {"variant": "tcc90-patch-local", "brief": BRIEF})
    deadline = time.time() + MINUTES * 60
    the_judge = LocalDecisionJudge(
        LocalDecisionBackend(claude_call, "sonnet", 1, trace, deadline), trace=trace)
    vendor = VendorRunner(f"tcc90-{BRIEF}-patch", policy, api, trace, freya, port=8791)
    runner = _FreyaLoopRunner(vendor, freya)
    (out / "patches").mkdir(parents=True, exist_ok=True)
    say(f"{BRIEF}: patch loop on the saved policy, local Claude Code, units mode, 1 iteration")
    try:
        deps = loop.LoopDeps(
            runner=runner, judge=the_judge, call_llm=claude_call, models=models, trace=trace,
            briefs=briefs, brief_text=text, api_path=api_path, api=api,
            tool_api=json.loads(api_path.read_text()), policy=policy,
            out_dir=out, deadline=deadline,
        )
        result = loop.run_loop(
            workflow, book, deps, max_iters=1, reps=1, n_cases=6, p_threshold=gate.WIN_P, ablation="units")
        record = {
            "started": True, "committed": result.committed, "stop_reason": result.stop_reason,
            "cases": result.cases, "iterations": result.iterations,
        }
        (out / "loop-local.json").write_text(json.dumps(record, indent=1, default=str))
        say(f"{BRIEF}: loop {record['stop_reason']} committed={record['committed']} cases={record['cases']}")
    finally:
        runner.close()
        trace.close("ok")


if __name__ == "__main__":
    main()
