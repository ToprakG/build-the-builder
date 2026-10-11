"""COUNTS ONLY: per run of a Freya simulation, how many messages carry a tool-call infra error (HTTP 530/5xx, cloudflare, function_call_error) and how many tool calls there were.
Prints no transcript text (GATE transcripts stay sealed).   python3 scripts/infra_counts.py <simulation id>"""
import json, re, sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
V = REPO / "vendor" / "build-the-builder"
for p in (str(REPO), str(V / "tools" / "driver"), str(V / "tools"), str(V)):
    sys.path.insert(0, p)
import freya  # noqa: E402
sim = sys.argv[1]
its = freya.api("GET", f"/api/v2/simulations/{sim}/iterations")
its = its.get("iterations") or its.get("items") or its
it = (its[-1].get("id") if isinstance(its, list) and its else None)
if not it:
    print("no iteration found", list(s)[:8]); sys.exit(1)
PATS = {"http5xx": re.compile(r"Error code: 5\d\d|\b5\d\d (Server|Bad|Service|Gateway)|status[^a-z]{0,3}5\d\d", re.I), "cloudflare": re.compile(r"cloudflare|trycloudflare", re.I),
        "fn_call_error": re.compile(r"function_call_error", re.I)}
ERR = re.compile("|".join(p.pattern for p in PATS.values()), re.I)
tot = {"http5xx": 0, "fn_call_error": 0, "runs": 0, "runs_with_err": 0, "err_msgs": 0, "tool_calls": 0}
for r in freya.get_iteration(sim, it)["runs"]:
    if not r.get("callId"):
        continue
    msgs = freya.get_call(r["callId"]).get("messages") or []
    err = sum(1 for m in msgs if ERR.search(json.dumps(m, ensure_ascii=False)))
    calls = sum(1 for m in msgs if m.get("tool_calls") or m.get("role") == "tool" or "function" in str(m.get("type", "")))
    tot["http5xx"] += kinds["http5xx"]; tot["fn_call_error"] += kinds["fn_call_error"]
    tot["runs"] += 1; tot["runs_with_err"] += err > 0; tot["err_msgs"] += err; tot["tool_calls"] += calls
    kinds = {k: sum(1 for m in msgs if p.search(json.dumps(m, ensure_ascii=False))) for k, p in PATS.items()}
    print(kinds, end=" ")
    print(f"{r.get('personaId','?')[:8]} outcome={r.get('outcome')} messages={len(msgs)} tool_msgs={calls} infra_err_msgs={err}")
print(tot)
