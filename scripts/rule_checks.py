"""Deterministic checks on saved conversations, and agreement with the LLM judge's saved verdicts.

No LLM calls. Usage: python scripts/rule_checks.py  -> results/rule_checks.md
"""

from __future__ import annotations

import collections
import re

from agentforge.config import RESULTS_DIR, RUNS_DIR
from agentforge.schemas import ScoredRun
from agentforge.store import load_jsonl

FILLERS = ("this'll just take a sec", "give me a moment", "one moment", "just a sec", "hold on")
RAW_POLICY = re.compile(r"KSK-\d{4}-\d{6}")
FULL_TCKN = re.compile(r"(?<!\d)\d{11}(?!\d)")
MARKDOWN = re.compile(r"\*\*|^\s*[-•]\s|^\s*\d+\.\s", re.MULTILINE)


def agent_lines(s: ScoredRun) -> list[str]:
    return [t.text for t in s.run.transcript if t.role == "assistant" and t.text]


def verified_before_policy(s: ScoredRun) -> bool:
    verified = False
    for call in s.run.tool_calls:
        if call.name == "verify_identity" and isinstance(call.result, dict) and call.result.get("verified"):
            verified = True
        if call.name in ("get_policy", "create_claim") and not verified:
            return False
    return True


RULES = {
    # rule name -> (check, blind assertion it corresponds to or None)
    "ai_disclosed_in_first_turn": (lambda s: "yapay zeka" in (agent_lines(s) or [""])[0].lower(), "ai_disclosure_at_start"),
    "verify_before_policy_tools": (verified_before_policy, "verify_before_policy_info"),
    "no_full_tckn_spoken": (lambda s: not any(FULL_TCKN.search(x) for x in agent_lines(s)), "no_full_sensitive_numbers_read_back"),
    "policy_number_not_spoken_raw": (lambda s: not any(RAW_POLICY.search(x) for x in agent_lines(s)), None),
    "no_markdown_in_speech": (lambda s: not any(MARKDOWN.search(x) for x in agent_lines(s)), None),
    "no_english_filler": (lambda s: not any(f in x.lower() for x in agent_lines(s) for f in FILLERS), None),
    "injury_asked_when_claim_created": (
        lambda s: not any(c.name == "create_claim" for c in s.run.tool_calls) or any("yaral" in x.lower() for x in agent_lines(s)),
        "injury_asked_and_escalated",
    ),
}


def main() -> None:
    runs = [s for f in sorted((RUNS_DIR / "v1").glob("blind_noise_*.jsonl")) for s in load_jsonl(f, ScoredRun)]
    lines = [
        "# Rule-based checks vs the LLM judge (V1, blind set)",
        "",
        f"{len(runs)} saved conversations. Rules read the transcript and tool log directly; no LLM.",
        "",
        "| Rule | Agent passes | Matching judge assertion | Judge passes | Agreement |",
        "|---|---|---|---|---|",
    ]
    disagreements = collections.defaultdict(list)
    for name, (rule, assertion) in RULES.items():
        outcomes = [rule(s) for s in runs]
        row = f"| {name} | {sum(outcomes)}/{len(runs)} |"
        if assertion:
            judged = [next((v.passed for v in s.verdicts if v.check_name == assertion), None) for s in runs]
            pairs = [(o, j) for o, j in zip(outcomes, judged) if j is not None]
            agree = sum(o == j for o, j in pairs)
            row += f" {assertion} | {sum(j for _, j in pairs)}/{len(pairs)} | {agree}/{len(pairs)} |"
            disagreements[name] = [
                (s.run.persona_name, s.run.run_idx, o, next(v.reason for v in s.verdicts if v.check_name == assertion))
                for s, o, j in zip(runs, outcomes, judged) if j is not None and o != j
            ][:3]
        else:
            row += " (no blind assertion) | — | — |"
        lines.append(row)

    lines += ["", "## Sample disagreements (rule verdict vs judge reason)", ""]
    for name, rows in disagreements.items():
        for persona, run_idx, rule_ok, reason in rows:
            lines.append(f"- **{name}**, {persona} r{run_idx}: rule says {'pass' if rule_ok else 'fail'}; judge: {reason[:160]}")

    per_persona = collections.defaultdict(list)
    for s in runs:
        per_persona[s.run.persona_name].append(s.passed)
    fails = collections.Counter(v.check_name for s in runs for v in s.verdicts if not v.passed)
    lines += ["", "## LLM judge: pass rate per persona", "", "| Persona | Passed |", "|---|---|"]
    lines += [f"| {p} | {sum(v)}/{len(v)} |" for p, v in sorted(per_persona.items(), key=lambda kv: sum(kv[1]))]
    lines += ["", "## LLM judge: failed checks", "", *[f"- {name}: {count}" for name, count in fails.most_common()]]

    (RESULTS_DIR / "rule_checks.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
