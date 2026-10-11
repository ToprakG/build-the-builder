"""Check that a build trace carries every field the kit asks for (trace.py): start/end time, every model call (model + purpose, tokens and
cost when known), the deterministic checks, what was created on Freya (or why nothing), the self-test (or "no self-test"), the totals.

    python3 scripts/check_trace.py <out dir | trace dir> [--freya] [--selftest]
--freya: also require the saved workflow id + agent id. --selftest: also require selftest_scenario / patcher_action events.
Exit 1 when a field is missing. Prints one line per field.
"""
import json, sys
from pathlib import Path

a = [x for x in sys.argv[1:] if not x.startswith("--")]
t = Path(a[0])
if (t / "build_report.json").exists():
    t = Path(json.loads((t / "build_report.json").read_text()).get("trace") or "")
ev = [json.loads(x) for x in (t / "events.jsonl").read_text().split("\n") if x.strip()]
txt = (t / "trace.txt").read_text()
by = {}
for e in ev:
    by.setdefault(e["type"], []).append(e)
mc = by.get("model_call", [])
end = (by.get("end") or [{}])[-1]
fields = {
    "start time": bool(by.get("start") and by["start"][0].get("time")),
    "inputs (briefs, tool api, models)": bool(by.get("start") and all(k in by["start"][0] for k in ("briefs", "tool_api", "models"))),
    "flags (features)": bool(by.get("features")),
    "model calls (model + purpose)": bool(mc) and all(m.get("model") and m.get("purpose") for m in mc),
    "tokens/cost on the model calls": bool(mc) and any(m.get("tokens_in") is not None or m.get("cost_usd") is not None for m in mc),
    "deterministic checks (lint, policy_check)": any("lint" in c.get("name", "") for c in by.get("check", [])) and any("policy_check" in c.get("name", "") for c in by.get("check", [])),
    "Freya: saved or why nothing": bool(by.get("freya")) or "nothing saved" in txt,
    "self-test: run or 'no self-test'": bool(by.get("selftest")) or "self-test" in txt,
    "end time + status": bool(end.get("time") and end.get("status")),
    "totals (wall, model calls, tokens, cost)": all(k in end for k in ("model_calls", "tokens_in", "tokens_out", "cost_usd")) and "wall=" in txt,
}
if "--freya" in sys.argv:
    fr = [e for e in by.get("freya", []) if isinstance(e.get("detail"), dict) or e.get("workflow_id")]
    fields["Freya workflow id + agent id"] = any((e.get("detail") or e).get("workflow_id") and (e.get("detail") or e).get("agent_id") for e in fr)
if "--selftest" in sys.argv:
    fields["self-test scenarios in the trace"] = bool(by.get("selftest_scenario"))
    fields["patcher actions (or none needed)"] = bool(by.get("patcher_action")) or "patcher" in txt or bool(by.get("selftest_run"))
bad = 0
for k, v in fields.items():
    print(("ok      " if v else "MISSING ") + k)
    bad += not v
print(f"trace: {t}  ({len(ev)} events, {len(mc)} model calls)")
sys.exit(1 if bad else 0)
