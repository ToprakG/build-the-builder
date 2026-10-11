"""PATCHER: a headless Claude Code session writes structured prompt patches, only into unlocked zones.

Input in the workspace: the brief, the tool API, current policy/workflow/zones, report/bugs.json (the analyzer's
generalized bugs) and report/failures/*.json (trimmed transcripts, tool calls and failure reasons computed against
OUR policy). Output: out/patches.json = [{id, bug_ids, zone, op, before, after, brief_quote, rationale}].
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from builder.template_cc import session

SYSTEM = """You improve a bank's automated collections phone agent by editing the text of its instructions. The
agent is a fixed graph of steps; you may only edit text zones that are unlocked. Code, routing, enums, tool fields,
fixed spoken sentences and computed facts are locked: do not try to change them.

Material (read it all): input/ (the brief, later parts override earlier ones; the tool API), current/policy.json,
current/zones.json (every zone: id, locked, max_chars, current text, purpose), report/bugs.json (generalized bugs
found in test calls, each with evidence turns), report/failures/ (trimmed transcripts of failed test calls).

How to patch (lessons that worked before):
- One minimal edit per bug, in the zone of the step where the behaviour happens (node:<step>/rules for if/then
  rules, node:<step>/examples for sample phrasing, tool:<name>/description for when to call a tool,
  hook:<tool>/msg/<key> for what the agent is told after a tool result, extract:... for what a routing value means).
- If the rule already exists and was ignored, replace it with a concrete version (exactly what to say or do); do not
  restate it. Use op "replace" with "before" copied exactly from the zone's current text (it must occur once).
  Otherwise use op "append" with before "".
- Never put a particular customer's name, amount, date or id into a rule: write the general situation.
- Every numbered value must come from the brief or the facts. Spoken English, short sentences.
- Each patch quotes the brief: "brief_quote" is copied character for character from input/ (it is checked).
- Do not fix a bug whose expected behaviour the brief does not require.

Write out/patches.json: a JSON list of
{"id": "p1", "bug_ids": ["b1"], "zone": "<zone id>", "op": "append"|"replace", "before": "<exact current text or empty>",
 "after": "<new text>", "brief_quote": "<exact brief words>", "rationale": "<one sentence>"}.
Call preview_patches to check the file (schema, lint, growth budget, diff) and fix what it reports. Call zones to see
the current zone texts and time_left when unsure. When run_sims is available, call it once (n 2 to 4) after
preview_patches to try your patches on hard simulations; if a patch makes things worse, change or drop it. Stop when the
file is written and previewed."""

TASK = "Read report/bugs.json and write out/patches.json for the bugs you can fix. You have {secs} seconds."


def run_patcher(briefs, brief_text: str, api_path: Path, api, policy: dict, workflow: dict, book, bug_report: dict,
                failures: list, models, trace, deadline: float, out_dir: Path, iteration: int,
                keep_workspace: bool = False, query_fn=None, server=None, seconds: float = 240, sims_fn=None) -> dict:
    """Returns {"patches": [...], "session": {...}, "error": ...}. Never raises."""
    rec = {"patches": [], "session": None, "error": None}
    ws = None
    try:
        ws = session.make_workspace(briefs, api_path, policy, workflow, book, report=bug_report, failures=failures)
        if sims_fn is not None:
            seconds = max(seconds, 900)  # a run_sims call takes minutes
        secs = max(0.0, min(seconds, deadline - time.time() - 60))
        bound = session.BoundTools(ws, "patch", time.time() + secs, policy, api, brief_text, workflow, book,
                                   on_event=trace.event, sims_fn=sims_fn)
        rec["session"] = session.run(TASK.format(secs=int(secs)), SYSTEM, ws, bound, models.patch, trace,
                                     f"patcher iter {iteration}", secs, max_turns=40, max_usd=models.patch_budget_usd,
                                     query_fn=query_fn, server=server)
        path = ws / "out" / "patches.json"
        items = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if not isinstance(items, list):
            raise ValueError("out/patches.json is not a list")
        rec["patches"] = [p for p in items if isinstance(p, dict)]
        (out_dir / "patches" / f"iter{iteration}.proposed.json").write_text(
            json.dumps(rec["patches"], indent=1, ensure_ascii=False), encoding="utf-8")
        trace.line(f"patcher iter {iteration}: {len(rec['patches'])} patch(es) proposed")
    except Exception as e:  # noqa: BLE001
        rec["error"] = repr(e)
        trace.warn(f"patcher iter {iteration} failed: {e!r}")
    finally:
        if ws is not None:
            if keep_workspace:
                rec["workspace"] = str(ws)
            else:
                session.remove_workspace(ws)
    return rec
