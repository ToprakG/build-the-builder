"""ANALYZER: failed self-test results -> generalized bugs with evidence (one LLM call).

Input is capped and trimmed. Failure reasons are computed against OUR policy (testgen), never testbed data.
Note: a failure caused by a wrong policy value cannot show up here (the expected result comes from the same policy);
the policy audit exists for that.
"""

from __future__ import annotations

import json

MAX_FAILURES = 12
MAX_TURNS = 40
MAX_TURN_CHARS = 300

SCHEMA = {
    "type": "object",
    "required": ["bugs"],
    "properties": {"bugs": {"type": "array", "items": {
        "type": "object",
        "required": ["bug_id", "behaviour", "expected_per_brief", "brief_quote", "evidence", "suspected_zone", "severity"],
        "properties": {
            "bug_id": {"type": "string"}, "behaviour": {"type": "string"}, "expected_per_brief": {"type": "string"},
            "brief_quote": {"type": "string"},
            "evidence": {"type": "array", "items": {"type": "object", "required": ["case_id", "rep", "turn"],
                                                    "properties": {"case_id": {"type": "string"}, "rep": {"type": "integer"},
                                                                   "turn": {"type": "integer"}}}},
            "suspected_zone": {"type": "string"}, "severity": {"type": "string", "enum": ["high", "medium", "low"]}}}}},
}

SYSTEM = """You analyse failed test calls of a bank's automated collections phone agent. Group the failures into
GENERALIZED behavioural bugs: what the agent does wrong in a general situation, not in one customer's story.
For each bug give: behaviour (what the agent did), expected_per_brief (what the brief requires), brief_quote (exact
words from the brief), evidence (at least one {case_id, rep, turn} where it happens; turn = index in the transcript),
suspected_zone (the id of the zone where an edit would fix it, from the zone list; "" if none fits) and severity.
Ignore failures that look like the simulated customer's fault or a tool outage. Never put customer names, amounts or
dates into a bug description."""


def trim(failure: dict) -> dict:
    t = failure.get("transcript") or []
    turns = [{"i": i, "role": m.get("role"), "text": str(m.get("text") or m.get("content") or "")[:MAX_TURN_CHARS]}
             for i, m in enumerate(t)][-MAX_TURNS:]
    return {"case_id": failure.get("case_id"), "rep": failure.get("rep", 1), "failed_reasons": failure.get("failed_reasons", []),
            "tool_calls": [{"tool": c.get("tool"), "args": c.get("args"), "ok": (c.get("result") or {}).get("ok")}
                           for c in (failure.get("tool_calls") or [])][:30],
            "transcript": turns}


def analyze(failures: list, brief_text: str, book, call_llm, model: str, trace, deadline=None) -> dict:
    """Returns {"bugs": [...], "dropped": [...]}. Only bugs with >= 1 valid evidence turn are kept."""
    picked = [trim(f) for f in failures[:MAX_FAILURES]]
    zone_list = [{"id": k, "locked": z["locked"], "purpose": z.get("purpose", "")} for k, z in book.zones.items() if not z["locked"]]
    user = (f"# Brief\n{brief_text}\n\n# Patchable zones\n{json.dumps(zone_list, ensure_ascii=False)}\n\n"
            f"# Failed test calls ({len(picked)} of {len(failures)})\n{json.dumps(picked, ensure_ascii=False)}")
    out = call_llm(SYSTEM, user, model, json_schema=SCHEMA, temperature=0, max_tokens=6000, effort="medium",
                   trace=trace, purpose=f"analyzer: {len(picked)} failed call(s) -> generalized bugs", deadline=deadline)
    full = {(f.get("case_id"), f.get("rep", 1)): len(f.get("transcript") or []) for f in failures}
    bugs, dropped = [], []
    for b in out.get("bugs") or []:
        ev = [e for e in b.get("evidence") or [] if (e.get("case_id"), e.get("rep", 1)) in full
              and 0 <= int(e.get("turn", -1)) < full[(e.get("case_id"), e.get("rep", 1))]]
        if not ev:
            dropped.append({"bug": b, "why": "no valid evidence turn"})
            continue
        if b.get("suspected_zone") and b["suspected_zone"] not in book.zones:
            b["suspected_zone"] = ""
        bugs.append({**b, "evidence": ev})
    trace.line(f"analyzer: {len(bugs)} bug(s), {len(dropped)} dropped")
    return {"bugs": bugs, "dropped": dropped}


def evidence_text(bugs: list, failures: list) -> dict:
    """bug_id -> the evidence turns as text, for the judge."""
    by = {(f.get("case_id"), f.get("rep", 1)): f.get("transcript") or [] for f in failures}
    out = {}
    for b in bugs:
        lines = []
        for e in b.get("evidence") or []:
            t = by.get((e["case_id"], e.get("rep", 1))) or []
            for i in range(max(0, int(e["turn"]) - 2), min(len(t), int(e["turn"]) + 2)):
                m = t[i]
                lines.append(f"{e['case_id']} rep {e.get('rep', 1)} turn {i} {str(m.get('role', '')).upper()}: "
                             f"{str(m.get('text') or m.get('content') or '')[:MAX_TURN_CHARS]}")
        out[b["bug_id"]] = "\n".join(lines)
    return out
