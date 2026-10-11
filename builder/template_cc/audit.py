"""POLICY AUDIT: a headless Claude Code session re-reads the brief against policy.json and proposes policy fixes,
each with a verbatim brief quote. Every proposal goes through the patch judge; accepted ones are applied in order.

Output of the session: out/policy_patches.json = [{id, path, old_value, new_value, brief_quote, rationale}].
Policy errors cannot be found by the self-test loop (its expected results come from the policy), so this is the only
place where they are caught.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from builder.template_cc import patches, policy_check, session
from builder.template_cc.judge import JudgeContext

SYSTEM = """You are a careful reviewer. A bank's specification for an outbound collections phone call (the brief, in
input/, possibly several parts: later parts override earlier ones) was turned into a structured policy
(current/policy.json) that a code generator compiles into the phone agent. The tool API is input/tools.openapi.json.

Your job: find every value in current/policy.json that does not match the brief or the tool API, and propose a fix.
Check especially: holiday dates, band limits, deadlines in business days, attempt counts and the attempt plan, the
callback window and same-day rule, identity questions and the wrong-answer limit, the outcome/reason or code table,
queue and note values, the close rule for several products, notes around transfers, verbatim sentences (lines with
verbatim=true must be copied exactly), forbidden words before verification, field names, overrides by later material.

Rules
- Read every file in input/ and current/policy.json. Use only Read, Glob, Grep and Write.
- Write your proposals to out/policy_patches.json: a JSON list of
  {"id": "a1", "path": "<JSON pointer into policy.json>", "old_value": <current value>, "new_value": <fixed value>,
   "brief_quote": "<exact words copied from the brief that state the value>", "rationale": "<one sentence>"}.
- One patch per wrong value, at the most specific pointer. Change only what the brief proves wrong. Never invent.
- The quote must be copied character for character from the brief (it is checked).
- Call the tool preview_patches to check your file; fix what it reports. Call time_left when unsure.
- If everything is right, write an empty list []. Stop when the file is written and previewed."""

TASK = "Audit current/policy.json against the brief in input/ and write out/policy_patches.json. You have {secs} seconds."


def run_audit(briefs, brief_text: str, api_path: Path, api, policy: dict, models, trace, judge, deadline: float,
              out_dir: Path, keep_workspace: bool = False, query_fn=None, server=None, seconds: float = 300) -> tuple[dict, dict]:
    """Returns (policy after accepted patches, record). Never raises: errors are logged and the policy is kept."""
    record = {"proposed": [], "verdicts": [], "applied": [], "error": None}
    ws = None
    try:
        ws = session.make_workspace(briefs, api_path, policy)
        secs = max(0.0, min(seconds, deadline - time.time() - 60))
        bound = session.BoundTools(ws, "audit", time.time() + secs, policy, api, brief_text, on_event=trace.event)
        state = session.run(TASK.format(secs=int(secs)), SYSTEM, ws, bound, models.patch, trace, "audit", secs,
                            max_turns=40, max_usd=models.audit_budget_usd, query_fn=query_fn, server=server)
        record["session"] = state
        path = ws / "out" / "policy_patches.json"
        proposed = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(proposed, list):
            raise ValueError("out/policy_patches.json is not a list")
        record["proposed"] = proposed
        (out_dir / "patches" / "audit.proposed.json").write_text(json.dumps(proposed, indent=1, ensure_ascii=False), encoding="utf-8")
        trace.line(f"audit: {len(proposed)} policy patch(es) proposed")
        current = policy
        ctx = JudgeContext(brief_text=brief_text, policy=current, api=api, log_path=out_dir / "patch_log.jsonl", iteration=0)
        for p in proposed:
            if judge is None:
                record["verdicts"].append({"id": p.get("id"), "accepted": False, "reasons": ["no judge: logged only"]})
                continue
            ctx.policy = current
            v = judge.judge_policy(p, ctx)
            record["verdicts"].append({"id": p.get("id"), **v.as_dict()})
            if v.accepted:
                current = policy_check.complete(patches.apply_policy(current, p), api)
                record["applied"].append(p)
        (out_dir / "patches" / "audit.verdicts.json").write_text(json.dumps(record["verdicts"], indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        (out_dir / "patches" / "audit.applied.json").write_text(json.dumps(record["applied"], indent=1, ensure_ascii=False), encoding="utf-8")
        trace.line(f"audit: {len(record['applied'])} of {len(proposed)} policy patch(es) accepted")
        return current, record
    except Exception as e:  # noqa: BLE001 - the audit may fail; the build goes on with the extracted policy
        record["error"] = repr(e)
        trace.warn(f"audit failed: {e!r}; keeping the extracted policy")
        return policy, record
    finally:
        if ws is not None:
            if keep_workspace:
                record["workspace"] = str(ws)
            else:
                session.remove_workspace(ws)
