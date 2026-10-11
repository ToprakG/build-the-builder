"""Final build: settle the two fields that flip between extractions (attempt_plan[*].cap_at_deadline, ask_limits.id_decline).

Order per field: (1) a deterministic rule where the brief text is explicit, (2) the majority over the extractions,
(3) on a tie, one verifier call whose answer needs a quote found in the brief, (4) the first extraction's value.
No brief value in code: only phrasing patterns.

    python3 -m builder.template_cc.final_vote <policy.json> <policy.json> [<policy.json> ...] --brief <brief file> [--brief ...] -o <out.json>
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from builder.template_cc import policy_check, policy_fixups

# ask_limits.id_decline = the number of times the agent re-asks after the FIRST refusal (explanation said once on the first
# refusal); the call closes on refusal number id_decline + 1. "Explain once, close after the second refusal" -> 1.
# Defined in builder/template_cc/policy_fixups.py, which also holds the brief-text rules used here.
rule_id_decline = lambda brief: (policy_fixups.id_decline(brief) or (None,))[0]  # noqa: E731
rule_cap_at_deadline = lambda brief, k: (policy_fixups.cap_at_deadline(brief, k) or (None,))[0]  # noqa: E731


def _get(p: dict, ptr: str):
    cur = p
    for part in ptr.strip("/").split("/"):
        if isinstance(cur, list):
            cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _set(p: dict, ptr: str, value) -> None:
    parts = ptr.strip("/").split("/")
    cur = p
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur.setdefault(part, {})
    if isinstance(cur, list):
        cur[int(parts[-1])] = value
    else:
        cur[parts[-1]] = value


def majority(values: list):
    """(value, True) when one value has a strict majority of the non-null votes; (None, False) otherwise."""
    votes = Counter(json.dumps(v) for v in values if v is not None)
    if not votes:
        return None, False
    (top, n), *rest = votes.most_common()
    if rest and rest[0][1] == n:
        return None, False
    return json.loads(top), n * 2 > sum(votes.values()) or len(votes) == 1


def fields_of(policies: list[dict]) -> list[str]:
    n = max((len(p.get("attempt_plan") or []) for p in policies), default=0)
    return [f"/attempt_plan/{i}/cap_at_deadline" for i in range(n)] + ["/ask_limits/id_decline"]


def vote(policies: list[dict], brief: str, verify=None) -> tuple[dict, list[str]]:
    """Returns (policy, log). `verify(ptr, candidates) -> (value, quote) | None` settles a tie; its quote must be in the brief."""
    policies = [p for p in policies if isinstance(p, dict)]
    out = json.loads(json.dumps(policies[0]))
    log = []
    for ptr in fields_of(policies):
        cands = [_get(p, ptr) for p in policies]
        rule = rule_id_decline(brief) if ptr.endswith("id_decline") else rule_cap_at_deadline(brief, int(ptr.split("/")[2]) + 1)
        if rule is None and ptr.endswith("cap_at_deadline") and _get(out, ptr.rsplit("/", 1)[0] + "/date") == "deadline":
            rule, why = True, "attempt date is the deadline: the cap changes nothing, fixed to true"
        else:
            why = "brief text is explicit"
        if rule is not None:
            _set(out, ptr, rule)
            log.append(f"{ptr}: {rule!r} (rule: {why}; extractions {cands})")
            continue
        value, ok = majority(cands)
        if value is not None and ok:
            _set(out, ptr, value)
            log.append(f"{ptr}: {value!r} (majority of {cands})")
            continue
        ans = verify(ptr, [c for c in cands if c is not None]) if verify else None
        if ans and policy_check.quote_in(str(ans[1] or ""), brief):
            _set(out, ptr, ans[0])
            log.append(f"{ptr}: {ans[0]!r} (verifier, quote found; extractions {cands})")
        else:
            first = next((c for c in cands if c is not None), None)
            if first is not None:
                _set(out, ptr, first)
            log.append(f"{ptr}: {first!r} (tie, no verified quote: first extraction; extractions {cands})")
    return out, log


def llm_verify(brief: str, model: str, trace=None):
    from builder.template_cc import llm  # noqa: PLC0415

    schema = {"type": "object", "required": ["value", "quote"], "properties": {"quote": {"type": "string"}}}

    def verify(ptr, cands):
        r = llm.call_llm("You settle a disagreement between readings of a bank's brief. Answer with the value the brief supports and a "
                         "quote copied EXACTLY from the brief that states it. Reply {\"value\", \"quote\"}.",
                         f"# Brief\n\n{brief}\n\n# Field {ptr}\nCandidates: {json.dumps(cands)}\n"
                         "(ask_limits.id_decline = how many times the agent explains why it asks the identity questions; "
                         "cap_at_deadline = the attempt's date may not be later than the deadline)",
                         model, json_schema=schema, temperature=0, max_tokens=2000, effort="high", trace=trace,
                         purpose=f"final vote verifier: {ptr}")
        return r.get("value"), r.get("quote")
    return verify


def main(argv=None) -> int:
    import argparse
    import os

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("policies", nargs="+")
    ap.add_argument("--brief", action="append", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args(argv)
    brief = "\n\n".join(Path(b).read_text(encoding="utf-8") for b in a.brief)
    pols = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.policies]
    model = os.environ.get("TCC_EXTRACT_MODEL", "claude_cli:claude-sonnet-5-5")
    out, log = vote(pols, brief, llm_verify(brief, model))
    Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("\n".join("final vote " + l for l in log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
