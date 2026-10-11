"""T-CC builder entry point.

    python3 -m builder.template_cc.run <brief> [<more brief material> ...] <tools.openapi.json> [options]

brief(s) + tool API -> EXTRACT -> policy.raw.json -> baseline compile -> POLICY AUDIT (Claude Code + judge)
-> policy.json -> COMPILE -> workflow.baseline.json -> [SELF-TEST LOOP] -> FINALIZE (lint, validate, save, export).
Only an extraction or compile failure is fatal; every later step falls back to the last good workflow.
Exit code 0 when a lint-clean workflow exists (and was saved, when --save freya).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from builder.template_cc import audit, compiler, extract, io, judge, llm, loop, policy_check
from builder.template_cc import features as feat
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.io import REPO
from builder.template_cc.trace import Trace

STRIP_KEYS = ("agent_config", "agentic_config")


def _write(path: Path, obj):
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1, ensure_ascii=False, default=str) + "\n",
                    encoding="utf-8")


def _import_freya():
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import freya  # noqa: PLC0415 - kit client; reads FREYA_API_KEY at import
    from builder.template_cc import freya_retry  # noqa: PLC0415

    freya_retry.install(freya)  # GET/PUT 5xx: 3 tries, 10 s apart

    return freya


def _default_validate(wf, name):
    from builder.agentic.tools import validate  # noqa: PLC0415

    return validate.validate(wf, name)


def compile_checked(policy, api, close_form, trace, label, features=None) -> tuple[dict | None, object, dict]:
    wf, book = compiler.compile(policy, api, close_form, features)
    active = frozenset() if features is None else (feat.parse(features) if isinstance(features, str) else frozenset(features))
    if "f11_promise_typed" in active and not policy.get("vague_date_offer"):
        trace.event("note", message="vague_date_offer missing, defaulting to latest date")
        trace.line("vague_date_offer missing, defaulting to latest date")
    report = compiler.lint(wf, policy, api)
    trace.check(f"lint {label}", not report["errors"], f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s)")
    for e in report["errors"][:10]:
        trace.line(f"  lint error {e['path']}: {e['msg']}")
    return (wf if not report["errors"] else None), book, report


def main(argv=None, *, call_llm=None, query_fn=None, sdk_server=None, freya_mod=None, runner=None, validate_fn=None,
         trace_root: Path | None = None) -> int:
    args = io.parse_args(argv if argv is not None else sys.argv[1:])
    t0 = time.time()
    deadline = t0 + args.max_minutes * 60
    call_llm = call_llm or llm.call_llm
    validate_fn = validate_fn or _default_validate
    try:
        briefs_paths, api_path = io.split_inputs(args.inputs)
    except io.InputError as e:
        print(f"input error: {e}", file=sys.stderr)
        return 2
    slot = args.slot or io.slot_name(briefs_paths)
    out = io.out_dir(args, slot)
    models = io.Models.load(args.models)
    trace = Trace((trace_root or REPO / "traces") / f"tcc-{slot}-{time.strftime('%Y%m%d-%H%M%S')}", {
        "variant": "T-CC (template compiler + Claude Code patcher + decision-model judge)", "slot": slot,
        "briefs": [str(p) for p in briefs_paths], "tool_api": str(api_path), "save": args.save, "audit": args.audit,
        "self_test": args.self_test, "max_minutes": args.max_minutes, "close_form": args.close_form,
        "models": {k: v for k, v in vars(models).items() if k != "extra"}})
    report: dict = {"slot": slot, "status": "failed", "out": str(out), "trace": str(trace.dir)}
    FEATURES = feat.parse(args.features)
    report["features"] = feat.names(FEATURES)
    trace.event("features", active=report["features"])
    exit_code = 1
    try:
        own_runner = None
        if args.self_test == "local" and runner is None:
            from builder.template_cc import vendor_runner  # noqa: PLC0415
            ok, why = vendor_runner.availability()
            if not ok:
                msg = (f"--self-test local needs the local tester: {why}. "
                       "No Freya simulation is used instead.")
                trace.warn(msg)
                print(msg, file=sys.stderr)
                report["message"] = msg
                return 2
            trace.line("self-test runner: vendor chat driver + mock bank + decision-model judge (build-the-builder)")
        api = ApiModel.load(api_path)
        tool_api = json.loads(Path(api_path).read_text(encoding="utf-8"))
        briefs = io.read_briefs(briefs_paths)
        brief_text = io.brief_text(briefs)
        trace.line(f"inputs: {len(briefs)} brief part(s), {len(brief_text)} chars; tool API {api.summary.get('title', '')!r} "
                   f"with {len(api.tools)} tools; missing kit tools: {api.summary['missing_kit_tools']}")
        the_judge = judge.make_judge(models, trace, call_llm, deadline)
        report["judge"] = the_judge.name if the_judge else None

        # EXTRACT (fatal on failure), or a given policy.json
        if args.policy:
            policy = policy_check.complete(json.loads(Path(args.policy).read_text(encoding="utf-8")), api)
            viol = policy_check.check(policy, api, brief_text)
            trace.line(f"extract: skipped, policy from {args.policy}")
            trace.check("policy_check (given policy)", not viol, f"{len(viol)} violation(s)")
            if viol:
                policy, repairs = policy_check.repair(policy, viol, api)
                policy = policy_check.complete(policy, api)
                for r in repairs:
                    trace.line(f"policy default: {r}")
            report["extract"] = {"skipped": args.policy, "violations": len(viol)}
        else:
            ex = extract.extract(brief_text, api, call_llm, models.extract, trace, deadline, out)
            policy = ex["policy"]
            report["extract"] = {"violations_first": len(ex["violations"]), "repairs": ex["repairs"]}
        _write(out / "policy.raw.json", policy)

        # baseline before any further LLM step: the fallback
        best, best_book, lint_report = compile_checked(policy, api, args.close_form, trace, "baseline (extracted policy)", FEATURES)
        if best is None:
            raise RuntimeError("the compiled baseline does not lint: " + "; ".join(e["msg"] for e in lint_report["errors"][:3]))
        _write(out / "workflow.baseline.json", compiler.dump(best))
        _write(out / "zones.json", best_book.to_json())

        # POLICY AUDIT
        if args.audit == "on":
            audited, rec = audit.run_audit(briefs, brief_text, api_path, api, policy, models, trace, the_judge, deadline, out,
                                           keep_workspace=args.keep_workspace, query_fn=query_fn, server=sdk_server)
            report["audit"] = {k: (len(v) if isinstance(v, list) else v) for k, v in rec.items() if k != "session"}
            if rec["applied"]:
                wf2, book2, _ = compile_checked(audited, api, args.close_form, trace, "baseline (audited policy)", FEATURES)
                if wf2 is not None:
                    policy, best, best_book = audited, wf2, book2
                    _write(out / "workflow.baseline.json", compiler.dump(best))
                    _write(out / "zones.json", best_book.to_json())
                else:
                    trace.warn("the audited policy does not compile lint-clean; keeping the extracted policy")
        else:
            trace.line("audit: off")
        _write(out / "policy.json", policy)

        # SELF-TEST LOOP
        if args.self_test == "local":
            try:
                sims_fn = None
                if runner is None:
                    own_runner = runner = vendor_runner.VendorRunner(slot, policy, api, trace, freya_mod or _import_freya())
                    from builder.template_cc import sims as S  # noqa: PLC0415
                    own_runner.customer_model = S.CUSTOMER_MODEL  # DeepSeek plays the customer in every self-test chat
                    fm = freya_mod or _import_freya()
                    sims_fn = lambda wf, n: S.render(S.run_sims(wf, policy, api, n, trace, fm, runner=own_runner))  # noqa: E731
                unit = None
                if args.unit_gate == "on" and own_runner is not None:
                    from builder.template_cc.unittests import UnitSuite  # noqa: PLC0415
                    unit = UnitSuite(slot, policy, api, freya_mod or _import_freya(), trace)
                d = loop.LoopDeps(runner=runner, judge=the_judge, call_llm=call_llm, models=models, trace=trace, briefs=briefs,
                                  brief_text=brief_text, api_path=Path(api_path), api=api, tool_api=tool_api, policy=policy,
                                  out_dir=out, deadline=deadline, keep_workspace=args.keep_workspace, query_fn=query_fn,
                                  server=sdk_server, unit=unit, sims_fn=sims_fn)
                res = loop.run_loop(best, best_book, d, max_iters=args.max_iters, reps=args.self_test_reps,
                                    n_cases=args.self_test_cases, p_threshold=args.gate_p)
                report["self_test"] = {"iterations": len(res.iterations), "committed": res.committed,
                                       "stop_reason": res.stop_reason, "cases": res.cases, "gaps": res.gaps}
                best, best_book = res.workflow, res.book
            except Exception as e:  # noqa: BLE001 - keep the baseline
                trace.warn(f"self-test loop failed: {e!r}; keeping the baseline")
                report["self_test"] = {"error": repr(e)}
            finally:
                if own_runner is not None:
                    own_runner.close()
        else:
            trace.selftest("no self-test (none)")
            report["self_test"] = "none"

        # FINALIZE
        final = {k: v for k, v in best.items() if k not in STRIP_KEYS}
        lint_final = compiler.lint(final, policy, api)
        trace.check("lint final", not lint_final["errors"], f"{len(lint_final['errors'])} error(s), {len(lint_final['warnings'])} warning(s)")
        if lint_final["errors"]:
            trace.warn("the final workflow does not lint; falling back to the baseline")
            final = json.loads((out / "workflow.baseline.json").read_text(encoding="utf-8"))
            lint_final = compiler.lint(final, policy, api)
            if lint_final["errors"]:
                raise RuntimeError("no lint-clean workflow")
        _write(out / "workflow.json", compiler.dump(final))
        _write(out / "zones.final.json", best_book.to_json())
        report["lint"] = {"errors": len(lint_final["errors"]), "warnings": len(lint_final["warnings"])}
        wf_body = final.get("workflow", final) if isinstance(final, dict) else {}
        count = lambda x: len(x) if isinstance(x, (list, dict)) else 0  # noqa: E731
        report["workflow_stats"] = {"nodes": count(wf_body.get("nodes")), "global_nodes": count(wf_body.get("global_nodes")),
                                    "tools": count(wf_body.get("tools"))}
        trace.event("workflow_stats", **report["workflow_stats"])
        trace.line("workflow stats: " + ", ".join(f"{k} {v}" for k, v in report["workflow_stats"].items()))
        if os.environ.get("FREYA_API_KEY"):
            v = validate_fn(final, f"{slot}-dry-run")
            trace.check("freya validate (dry run)", not (isinstance(v, dict) and v.get("error")), v)
            report["validate"] = v
        else:
            trace.line("freya validate: skipped (no FREYA_API_KEY)")
            report["validate"] = "skipped"
        if args.save == "freya":
            fm = freya_mod or _import_freya()
            saved = fm.save_workflow(slot, final, state_file=str(REPO / ".freya-agents.json"))
            trace.freya("save_workflow", {"slot": slot, **(saved if isinstance(saved, dict) else {"result": saved})})
            wid = saved.get("workflow_id") if isinstance(saved, dict) else None
            export_path = out / f"{slot}.workflow.json"
            fm.export_workflow(wid, str(export_path))
            trace.freya("export_workflow", {"workflow_id": wid, "path": str(export_path)})
            report["saved"] = saved
        else:
            trace.line("freya: nothing saved (--save none)")
        report["status"] = "ok"
        exit_code = 0
    except Exception as e:  # noqa: BLE001
        report["message"] = f"build failed: {e!r}"
        trace.warn(report["message"])
        print(report["message"], file=sys.stderr)
        exit_code = 1
    finally:
        report["seconds"] = round(time.time() - t0, 1)
        report["cost_usd"] = round(trace.cost, 4)
        report["model_calls"] = trace.model_calls
        _write(out / "build_report.json", report)
        trace.close(report.get("status", "failed"), report=report)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
