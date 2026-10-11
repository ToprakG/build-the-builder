"""Offline checks on saved conversations (no voice platform needed).

1. Analyze V1's blind-set failures into recurring bug types.
2. Judge consistency: re-score the same runs and count verdict flips.
3. Judge strictness: score the same runs with our generated assertions vs the blind ones.

Writes results/blind_analysis.json and results/judge_check.md.
"""

from __future__ import annotations

import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentforge.analyze import analyzer
from agentforge.config import DOMAIN_DIR, RESULTS_DIR, RUNS_DIR
from agentforge.run.scorer import score_run
from agentforge.schemas import Assertion, Persona, ScoredRun
from agentforge.store import load_jsonl, load_models, save_json


def rescore(runs: list[ScoredRun], personas: dict[str, Persona], assertions: list[Assertion]) -> list[ScoredRun]:
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda s: score_run(s.run, personas[s.run.persona_name], assertions), runs))


def pass_rate(scored: list[ScoredRun]) -> float:
    return sum(s.passed for s in scored) / len(scored)


def check_rates(scored: list[ScoredRun]) -> dict[str, float]:
    totals, passes = collections.Counter(), collections.Counter()
    for s in scored:
        for v in s.verdicts:
            totals[v.check_name] += 1
            passes[v.check_name] += v.passed
    return {name: passes[name] / totals[name] for name in totals}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-file", default="blind_noise_0.jsonl", help="runs used for the judge checks")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()

    kasko = DOMAIN_DIR / "kasko"
    blind_personas = {p.name: p for p in load_models(kasko / "blind_personas.json", Persona)}
    blind_assertions = load_models(kasko / "blind_assertions.json", Assertion)
    ours = load_models(RUNS_DIR / "testsets" / "assertions.json", Assertion)
    all_runs = [s for f in sorted((RUNS_DIR / "v1").glob("blind_noise_*.jsonl")) for s in load_jsonl(f, ScoredRun)]
    sample = load_jsonl(RUNS_DIR / "v1" / args.sample_file, ScoredRun)

    # 1. Bug analysis. The analyzer refuses non-train personas to protect the loop; this is a one-off read,
    # so relabel copies in memory. The blind files on disk keep their tag.
    failed = [s for s in all_runs if not s.passed]
    as_train = [p.model_copy(update={"tags": ["train"]}) for p in blind_personas.values()]
    report = analyzer.analyze("v1-blind", failed, as_train)
    fail_counts = collections.Counter(v.check_name for s in all_runs for v in s.verdicts if not v.passed)
    persona_rates = {
        name: sum(s.passed for s in all_runs if s.run.persona_name == name) / max(1, sum(s.run.persona_name == name for s in all_runs))
        for name in blind_personas
    }
    save_json(RESULTS_DIR / "blind_analysis.json", {
        "runs": len(all_runs), "pass_rate": pass_rate(all_runs),
        "failed_checks": dict(fail_counts.most_common()), "persona_pass_rates": persona_rates,
        "bugs": report.model_dump(mode="json")["bugs"],
    })

    # 2. Consistency: original verdicts vs fresh re-scores of the same transcripts.
    rounds = [sample] + [rescore(sample, blind_personas, blind_assertions) for _ in range(args.repeats)]
    flips, total = collections.Counter(), collections.Counter()
    run_flips = 0
    for i in range(len(sample)):
        outcomes = {r[i].passed for r in rounds}
        run_flips += len(outcomes) > 1
        for j, verdict in enumerate(rounds[0][i].verdicts):
            seen = {r[i].verdicts[j].passed for r in rounds if j < len(r[i].verdicts)}
            total[verdict.check_name] += 1
            flips[verdict.check_name] += len(seen) > 1

    # 3. Strictness: the same transcripts under our generated assertions.
    ours_scored = rescore(sample, blind_personas, ours)

    lines = [
        "# Judge check (offline, saved V1 blind-set conversations)",
        "",
        f"Judge: same transcripts, {len(rounds)} scorings each, {len(sample)} conversations from `{args.sample_file}`.",
        "",
        "## Consistency",
        "",
        f"Run verdict (pass/fail) changed between scorings on **{run_flips}/{len(sample)}** conversations.",
        f"Run pass rate per scoring: {', '.join(f'{pass_rate(r):.2f}' for r in rounds)}.",
        "",
        "| Check | Flipped |",
        "|---|---|",
        *[f"| {name} | {flips[name]}/{total[name]} |" for name in total],
        "",
        "## Strictness: blind assertions vs our generated assertions (same transcripts)",
        "",
        f"Run pass rate: blind set **{pass_rate(sample):.2f}**, ours **{pass_rate(ours_scored):.2f}**.",
        "",
        "| Our assertion | Pass rate |",
        "|---|---|",
        *[f"| {name} | {rate:.2f} |" for name, rate in sorted(check_rates(ours_scored).items(), key=lambda kv: kv[1])],
        "",
        "| Blind assertion | Pass rate |",
        "|---|---|",
        *[f"| {name} | {rate:.2f} |" for name, rate in sorted(check_rates(sample).items(), key=lambda kv: kv[1])],
    ]
    (RESULTS_DIR / "judge_check.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n{len(report.bugs)} bug types -> results/blind_analysis.json")


if __name__ == "__main__":
    main()
