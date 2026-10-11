"""Fresh-context review of a built workflow, and the findings for one fix round.

The reviewer is a separate SDK `query()` in its own temp workspace: it sees the brief files, the tool API, the kit docs
and the workflow, but **not** the builder's rules.json or policy.json, so it does not inherit the builder's reading of
the brief. It writes its own rule list with verdicts. Code then compares that list with the builder's rules.json:
every brief rule the builder never listed, and every defect the reviewer found, becomes a finding with node ids.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from builder.agentic.tools.coverage import normalize
from builder.common.io import KIT_DOCS, REPO

VERDICTS = ("ok", "missing", "wrong")
SITUATIONS = ("the happy path", "a refusal and each persuasion attempt", "a lower amount", "a date after the deadline",
              "a weekend or holiday date", "a vague date", "hardship", "a dispute", "already paid", "restructuring",
              "a request for a person", "a scam question", "a robot question", "someone else answers", "a wrong number",
              "the customer has died", "identity fails", "identity declined", "a tool failure",
              "several products (if the brief allows them)", "the end of the call")


def build_review_workspace(ws: Path, briefs, api: Path, workflow: Path) -> None:
    ws = Path(ws)
    (ws / "input").mkdir(parents=True)
    for i, b in enumerate(briefs, 1):
        shutil.copyfile(b, ws / "input" / f"brief-{i}{Path(b).suffix.lower() or '.txt'}")
    shutil.copyfile(api, ws / "input" / "tools.openapi.json")
    shutil.copyfile(workflow, ws / "input" / "workflow.json")
    (ws / "docs").mkdir()
    for name in KIT_DOCS:
        shutil.copyfile(REPO / "docs" / name, ws / "docs" / name)
    (ws / "out").mkdir()


ROLE = ("You review a Freya workflow phone agent against a bank's brief. You work only inside the current directory, "
        "read files, and write only out/review.json. There is no shell and no web access.")


def review_prompt(n_briefs: int, minutes: float) -> str:
    files = ", ".join(f"input/brief-{i}.*" for i in range(1, n_briefs + 1))
    situations = "; ".join(SITUATIONS)
    return f"""Review the phone agent in input/workflow.json against the brief.

Inputs: the brief is {files} (in override order: a later file overrides an earlier one). The bank's tool API is input/tools.openapi.json. The workflow format is described in docs/workflow-format.md and docs/tools-and-code.md.

Time budget: {minutes:g} minutes. Write the file early and improve it; an unfinished review is better than none.

1. Make your OWN list of every rule in the brief: each numbered line, each table row (each closing row is a rule), each memo or later-mail change. Copy a short exact quote from the brief for each.
2. For each rule, find where the workflow implements it (node ids) and decide: ok, missing (nothing implements it), or wrong (implemented in a way that breaks the rule).
3. Walk the call graph for each of these situations and check what the agent says first, which tools run, and what ends the call: {situations}. Pay special attention to:
   - a route that ends the call on the same message that triggers it, before the agent says what the brief requires;
   - limits (attempts, amounts, dates, windows) left to the model instead of computed or counted in code;
   - offers or fixed sentences worded by the model instead of quoted from the brief;
   - a way the call cannot end (no end_call tool, a completion flag that may never be set);
   - a value sent to a tool that is not in the tool API.

Write out/review.json:
{{"rules": [{{"id": "V1", "source_quote": "<exact words from the brief>", "statement": "<the rule>", "verdict": "ok|missing|wrong", "node_ids": ["<node id>", ...], "defect": "<what is wrong, if not ok>", "fix": "<the concrete change, if not ok>"}}, ...]}}

Report only what you can point to in the workflow. Do not change input/workflow.json."""


def parse_review(path: Path) -> dict:
    """The reviewer's file, validated loosely. Invalid input gives an empty rule list and an error note."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"rules": [], "error": f"no usable review: {e}"}
    rules = data.get("rules") if isinstance(data, dict) else data
    if not isinstance(rules, list):
        return {"rules": [], "error": "review.json has no rules list"}
    clean = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        verdict = str(r.get("verdict", "")).strip().lower()
        clean.append({"id": str(r.get("id") or f"V{i + 1}"), "source_quote": str(r.get("source_quote") or ""),
                      "statement": str(r.get("statement") or ""), "verdict": verdict if verdict in VERDICTS else "ok",
                      "node_ids": [str(n) for n in (r.get("node_ids") or []) if n],
                      "defect": str(r.get("defect") or ""), "fix": str(r.get("fix") or "")})
    return {"rules": clean}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", normalize(text)) if len(w) > 2}


def covered(quote: str, builder_quotes: list[str], min_overlap: float = 0.6) -> bool:
    """A reviewer quote is covered when a builder quote contains it, is contained in it, or shares most words."""
    q = normalize(quote)
    if len(q) < 8:
        return True  # too short to judge; do not raise a finding on it
    qw = _words(quote)
    for b in builder_quotes:
        nb = normalize(b)
        if not nb:
            continue
        if q in nb or nb in q:
            return True
        bw = _words(b)
        if qw and bw and len(qw & bw) / min(len(qw), len(bw)) >= min_overlap:
            return True
    return False


def compare(review: dict, builder_rules) -> list[dict]:
    """Findings: brief rules the builder never listed, and defects the reviewer found."""
    if isinstance(builder_rules, dict):
        builder_rules = builder_rules.get("rules") or []
    quotes = [str(r.get("source_quote") or "") for r in builder_rules or [] if isinstance(r, dict)]
    findings = []
    for r in review.get("rules", []):
        missed = not covered(r["source_quote"], quotes)
        if r["verdict"] == "ok" and not missed:
            continue
        kind = "defect" if r["verdict"] != "ok" else "unlisted_rule"
        if missed and r["verdict"] != "ok":
            kind = "unlisted_rule_and_defect"
        findings.append({"kind": kind, "review_id": r["id"], "quote": r["source_quote"], "rule": r["statement"],
                         "verdict": r["verdict"], "node_ids": r["node_ids"], "defect": r["defect"], "fix": r["fix"]})
    return findings


MODEL_LIMIT = re.compile(r"left to the model|by the model|only (as|in) text|not counted|no code|in code", re.I)


def priority(f: dict) -> tuple:
    """Fix order: defects that leave a limit to the model first, then other defects, then unlisted rules."""
    text = f"{f.get('defect', '')} {f.get('fix', '')}"
    if f["kind"] == "unit_test_failed":
        return (0, f.get("review_id", ""))  # a failed test on the real engine is the most concrete finding
    if f["kind"] == "local_sim_failed":
        return (1, f.get("review_id", ""))  # then a whole simulated call that failed
    is_defect = f["kind"] != "unlisted_rule"
    return (2 if is_defect and MODEL_LIMIT.search(text) else 3 if is_defect else 4, f.get("review_id", ""))


def fix_prompt(findings: list[dict], minutes: float) -> str:
    findings = sorted(findings, key=priority)
    lines = []
    for i, f in enumerate(findings, 1):
        where = ", ".join(f["node_ids"]) or "no node yet"
        lines.append(f"{i}. [{f['kind']}] rule: {f['rule'] or f['quote']}\n   quote: \"{f['quote']}\"\n   nodes: {where}\n"
                     f"   problem: {f['defect'] or 'not in your rules.json'}\n   suggested fix: {f['fix'] or 'implement the rule'}")
    return (f"Your unit tests ran on Freya's real engine, hard simulated calls from your own policy ran locally, and an "
            f"independent reviewer read the brief and your workflow (it did not see your rules.json). Failed unit tests "
            f"come first, then failed simulated calls: for those, find the cause in your workflow, do not patch the one "
            f"conversation. "
            f"Fix these findings in out/workflow.json, in the order given (the first ones matter most); add any rule "
            f"missing from out/rules.json with its exact quote and location. Ignore a finding only if the brief clearly "
            f"contradicts it, and say why in one line.\n\n"
            + "\n".join(lines) +
            f"\n\nTime budget: {minutes:g} minutes. Then run mcp__fb__lint_workflow and mcp__fb__check_coverage until both "
            f"report 0 errors. If time runs short, keep out/workflow.json lint-clean above all.")
