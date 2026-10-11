"""SELF-TEST LOOP: generate cases from our policy once, split train/heldout, then per iteration:
run train -> analyze failures -> patcher -> judge every patch -> apply accepted -> run train + heldout -> gate.

Stops on: no failures, no accepted patches, --max-iters, or less time left than the last iteration took. Any
exception inside an iteration keeps the best workflow so far.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from builder.template_cc import analyze, gate, patches, patcher, testgen, zones
from builder.template_cc.judge import JudgeContext
from builder.template_cc.runner import CaseResult, infra_ids, pass_counts


@dataclass
class LoopDeps:
    runner: object
    judge: object
    call_llm: object
    models: object
    trace: object
    briefs: list
    brief_text: str
    api_path: Path
    api: object
    tool_api: dict
    policy: dict
    out_dir: Path
    deadline: float
    keep_workspace: bool = False
    query_fn: object = None
    server: object = None
    unit: object = None  # UnitSuite: a fast unit-test gate before the full chats (optional)
    sims_fn: object = None  # (workflow, n) -> report text: the patcher's run_sims tool (optional)


@dataclass
class LoopResult:
    workflow: dict
    book: zones.ZoneBook
    iterations: list = field(default_factory=list)
    stop_reason: str = ""
    cases: int = 0
    gaps: list = field(default_factory=list)
    committed: int = 0


def _write(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def run_loop(wf: dict, book: zones.ZoneBook, d: LoopDeps, max_iters: int = 3, reps: int = 2, n_cases: int = 36,
             analyze_fn=analyze.analyze, patcher_fn=patcher.run_patcher, p_threshold: float = gate.WIN_P) -> LoopResult:
    tr = d.trace
    cases, gaps = testgen.generate(d.policy, n_cases=n_cases, trace=tr)
    res = LoopResult(wf, book, cases=len(cases), gaps=gaps)
    if not cases:
        res.stop_reason = "no cases"
        return res
    train, held = testgen.split(cases)
    by_id = {c["id"]: c for c in cases}
    t0 = time.time()
    best_train = d.runner.run(wf, train, d.tool_api, d.policy, reps)
    best_held = d.runner.run(wf, held, d.tool_api, d.policy, reps)
    tr.selftest(f"baseline: train {_score(best_train)}, heldout {_score(best_held)} ({len(train)}+{len(held)} cases x {reps})")
    best_unit = _unit_check(d, wf, "baseline")
    est = time.time() - t0
    for it in range(1, max_iters + 1):
        t_it = time.time()
        if d.deadline - time.time() < est:
            res.stop_reason = f"time: {d.deadline - time.time():.0f}s left < last iteration {est:.0f}s"
            break
        failures = [r.as_dict() if isinstance(r, CaseResult) else r for r in best_train
                    if not r.passed and not getattr(r, "infra", False)]
        if not failures:
            res.stop_reason = "no failures"
            break
        rec = {"iteration": it, "failures": len(failures)}
        try:
            report = analyze_fn(failures, d.brief_text, res.book, d.call_llm, d.models.analyze, tr, d.deadline)
            _write(d.out_dir / "patches" / f"iter{it}.bugs.json", report)
            if not report.get("bugs"):
                res.stop_reason = "analyzer found no generalized bug"
                res.iterations.append(rec)
                break
            kw = {"sims_fn": d.sims_fn} if d.sims_fn is not None else {}
            prop = patcher_fn(d.briefs, d.brief_text, d.api_path, d.api, d.policy, res.workflow, res.book, report, failures,
                              d.models, tr, d.deadline, d.out_dir, it, keep_workspace=d.keep_workspace,
                              query_fn=d.query_fn, server=d.server, **kw)
            proposed = prop.get("patches") or []
            rec["proposed"] = len(proposed)
            if d.judge is None:
                tr.warn(f"iteration {it}: no judge; {len(proposed)} patch(es) logged, none applied")
                res.stop_reason = "no judge"
                res.iterations.append(rec)
                break
            ctx = JudgeContext(brief_text=d.brief_text, policy=d.policy, workflow=res.workflow, book=res.book, api=d.api,
                               bugs={b["bug_id"]: b for b in report["bugs"]},
                               evidence_text=analyze.evidence_text(report["bugs"], failures),
                               case_tokens=testgen.persona_tokens([by_id[f["case_id"]] for f in failures if f["case_id"] in by_id]),
                               base_size=zones.model_text_size(res.book), log_path=d.out_dir / "patch_log.jsonl", iteration=it)
            cand_wf, cand_book, applied, verdicts = res.workflow, res.book, [], []
            for p in proposed:
                ctx.workflow, ctx.book = cand_wf, cand_book
                v = d.judge.judge(p, ctx)
                verdicts.append({"id": p.get("id"), **v.as_dict()})
                if v.accepted:
                    cand_wf, cand_book = patches.apply_one(cand_wf, cand_book, p)
                    applied.append(p)
            _write(d.out_dir / "patches" / f"iter{it}.verdicts.json", verdicts)
            _write(d.out_dir / "patches" / f"iter{it}.applied.json", applied)
            rec["accepted"] = len(applied)
            if not applied:
                res.stop_reason = "no accepted patches"
                res.iterations.append(rec)
                break
            if best_unit is not None:
                cand_unit = _unit_check(d, cand_wf, f"iteration {it} candidate")
                regs = d.unit.regressions(best_unit, cand_unit) if cand_unit is not None else []
                rec["unit"] = {"best": best_unit.get("passed"), "candidate": (cand_unit or {}).get("passed"), "regressions": regs}
                if regs:
                    tr.selftest(f"iteration {it}: DISCARD before the chats: unit tests that passed now fail: {regs}")
                    res.iterations.append(rec)
                    est = time.time() - t_it
                    continue
            cand_train = d.runner.run(cand_wf, train, d.tool_api, d.policy, reps)
            cand_held = d.runner.run(cand_wf, held, d.tool_api, d.policy, reps)
            skip = infra_ids(best_held) | infra_ids(cand_held)  # unscored runs say nothing about either workflow
            bh = {k: v for k, v in pass_counts(best_held).items() if k not in skip}
            ch = {k: v for k, v in pass_counts(cand_held).items() if k not in skip}
            if skip:
                tr.selftest(f"gate ignores {len(skip)} heldout case(s) with infrastructure errors: {sorted(skip)}")
            dec = gate.decide(bh, ch, reps, p_threshold)
            rec["gate"] = dec.as_dict()
            tr.selftest(f"iteration {it}: candidate train {_score(cand_train)}, heldout {_score(cand_held)}; "
                        f"{'COMMIT' if dec.commit else 'DISCARD'}: {' | '.join(dec.reasons)}")
            if dec.commit:
                res.workflow, res.book, best_train, best_held = cand_wf, cand_book, cand_train, cand_held
                if best_unit is not None and rec.get("unit", {}).get("candidate") is not None:
                    best_unit = cand_unit
                res.committed += 1
        except Exception as e:  # noqa: BLE001 - keep the best workflow
            rec["error"] = repr(e)
            tr.warn(f"iteration {it} failed: {e!r}; keeping the best workflow")
            res.iterations.append(rec)
            res.stop_reason = "error"
            break
        res.iterations.append(rec)
        est = time.time() - t_it
    else:
        res.stop_reason = res.stop_reason or "max iterations"
    _write(d.out_dir / "patches" / "loop.json", {"iterations": res.iterations, "stop_reason": res.stop_reason,
                                                 "cases": res.cases, "gaps": res.gaps, "committed": res.committed})
    return res


def _unit_check(d: LoopDeps, wf: dict, label: str):
    """The unit suite on a workflow, or None when there is no suite or it cannot run (never blocks the loop)."""
    if d.unit is None:
        return None
    try:
        return d.unit.check(wf)
    except Exception as e:  # noqa: BLE001
        d.trace.warn(f"unit tests ({label}) could not run: {e!r}; the unit gate is skipped")
        return None


def _score(results) -> str:
    scored = [r for r in results if not getattr(r, "infra", False)]
    extra = f" ({len(results) - len(scored)} unscored)" if len(scored) != len(results) else ""
    return f"{sum(r.passed for r in scored)}/{len(scored)}{extra}"
