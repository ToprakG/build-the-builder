"""Check dress run 2 (builder/build.py mock/rehearsal-tr/FreyaBank-Test <mock_url>).   python3 scripts/dress_check_tr.py out/dress2 FINAL_FLAGS.txt <live-mock|local-bank> [<expected tool URL prefix>]
 1 build.py ok, flags == FINAL_FLAGS, lint 0, no opening_resume   2 policy.language == tr, callback end 18:00 (announcement), never the old 19:00
 3 scripts/check_turkish.py finds 0 spoken English   4 tool URLs: live = <mock_url>/v1/..., else the OpenAPI server   5 workflow exported (workflows/)
 6 submission.json fields   7 trace: start/end, model calls, workflow + agent ids, step/tool counts, self-test result (no outage)"""
import json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, ".")
from builder.template_cc import features as F  # noqa: E402
out = Path(sys.argv[1]); b = out / "build"
want = F.parse(Path(sys.argv[2]).read_text().replace("\n", "").strip())
mode = sys.argv[3] if len(sys.argv) > 3 else "local-bank"
expect = sys.argv[4] if len(sys.argv) > 4 else ""
problems = []


def line(ok, msg):
    print(("OK      " if ok else "PROBLEM ") + msg)
    if not ok:
        problems.append(msg)


def urls(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "url" and isinstance(v, dict) and isinstance(v.get("default"), str):
                acc.append(v["default"])
            elif k == "url" and isinstance(v, str):
                acc.append(v)
            else:
                urls(v, acc)
    elif isinstance(o, list):
        for x in o:
            urls(x, acc)
    return acc


print(f"mode: {mode}")
try:
    info = json.loads((out / "build_py.json").read_text().strip().splitlines()[-1])
except (OSError, ValueError, IndexError):
    info = {}
line(info.get("status") == "ok", f"build.py status {info.get('status')} ({info.get('message') or ''})")
try:
    rep = json.load(open(b / "build_report.json")); wf = json.load(open(b / "workflow.json")); wf = wf.get("workflow", wf)
    pol = json.load(open(b / "policy.json"))
except OSError as e:
    rep, wf, pol = {}, {"nodes": {}}, {}; line(False, f"no build output ({e})")
got = frozenset(rep.get("features") or [])
line(got == want, f"flags used == FINAL_FLAGS.txt ({len(got)} vs {len(want)})" + ("" if got == want else f"; extra {sorted(got - want)}, missing {sorted(want - got)}"))
line((rep.get("lint") or {}).get("errors") == 0 and "opening_resume" not in wf.get("nodes", {}), f"lint errors {(rep.get('lint') or {}).get('errors')}, no opening_resume")
# 2 policy
line(str(pol.get("language", "")).lower().startswith("tr"), f"policy.language = {pol.get('language')!r}")
cb = json.dumps(pol.get("callback"), ensure_ascii=False)
line("18:00" in cb and "19:00" not in cb, f"announced callback change in the policy: callback = {cb[:200]}")
# 3 turkish
r = subprocess.run([sys.executable, "scripts/check_turkish.py", str(b / "workflow.json"), "--policy", str(b / "policy.json")], capture_output=True, text=True)
tail = " | ".join(r.stdout.strip().splitlines()[-3:])[:300]
line(r.returncode == 0, f"check_turkish: exit {r.returncode}: {tail}")
# 4 tool urls
u = urls(wf, [])
if expect:
    bad = [x for x in u if not x.startswith(expect)]
else:
    try:
        sv = [s["url"].rstrip("/") for s in json.load(open(b / "ingest" / "tools.openapi.json")).get("servers", [])]
    except OSError:
        sv = []
    bad = [x for x in u if not any(x.startswith(s + "/") for s in sv)]
line(bool(u) and not bad, f"{len(u)} tool URL(s) " + (f"all start with {expect}" if expect else "all under the OpenAPI server") + (f"; BAD {sorted(set(bad))[:3]}" if bad else ""))
# 5 export
sv_ = Path(info.get("saved") or "x")
line(sv_.exists() and sv_.parent.name == "workflows", f"workflow exported at {info.get('saved')}")
# 6 submission
try:
    s = json.load(open(b / "submission.json"))
    line(bool(s.get("members")) and all(k in s for k in ("workflow", "trace", "mock_url")) and Path(str(s["workflow"])).exists() and Path(str(s["trace"])).exists(), f"submission.json keys {sorted(s)}: members {s.get('members')}, mock_url {s.get('mock_url')}")
except (OSError, ValueError):
    line(False, "submission.json missing")
# 7 trace
try:
    tr = Path(s["trace"]); ev = [json.loads(l) for l in open(tr / "events.jsonl")]
    kinds = {}
    for e in ev:
        kinds.setdefault(e.get("type"), []).append(e)
    fr = [e for e in kinds.get("freya", []) if (e.get("detail") or e).get("workflow_id")]
    ids = (fr[-1].get("detail") or fr[-1]) if fr else {}
    st = (kinds.get("workflow_stats") or [{}])[-1]
    sr = (kinds.get("selftest_run") or [{}])[-1]
    line("start" in kinds and "end" in kinds, f"trace has start and end ({len(ev)} events)")
    line(len(kinds.get("model_call", [])) > 0, f"trace has {len(kinds.get('model_call', []))} model calls")
    line(bool(ids.get("workflow_id")) and bool(ids.get("agent_id")), f"trace has workflow_id {str(ids.get('workflow_id'))[:8]} and agent_id {str(ids.get('agent_id'))[:8]}")
    line(bool(st), f"trace has step/tool counts: {json.dumps({k: v for k, v in st.items() if k not in ('type', 't')})[:160]}")
    line(bool(sr) and not json.dumps(sr).count("outage\": true"), f"trace has the self-test result: {json.dumps({k: v for k, v in sr.items() if k not in ('type', 't')})[:200]}")
except (OSError, KeyError, ValueError, NameError) as e:
    line(False, f"trace not readable ({e})")
# 8 live mock: did the self-test's calls reach the Worker?  (informational: the self-test runs against its OWN kit-shaped bank + tunnel, see NOTE)
if mode == "live-mock":
    import urllib.request
    try:
        mu = json.load(open(b / "submission.json"))["mock_url"].rstrip("/")
        recs = json.load(urllib.request.urlopen(urllib.request.Request(mu + "/records", headers={"user-agent": "Mozilla/5.0"}), timeout=20)).get("records", [])
        print(f"NOTE    GET {mu}/records: {len(recs)} record(s), tools {sorted({r.get('tool') for r in recs})} (self-test calls reach the Worker only if the self-test is pointed at it)")
    except Exception as e:  # noqa: BLE001
        print(f"NOTE    GET /records failed: {e}")
print("\nVERDICT:", "ALL OK" if not problems else f"{len(problems)} PROBLEM(S)")
sys.exit(0 if not problems else 1)
