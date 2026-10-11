"""Compact transcripts of Freya eval calls: python3 scripts/dump_calls.py <tag> <case-id> ...  -> out/template_cc/eval/calls/<tag>.<case>.txt
Needs FREYA_API_KEY (and FREYA_STATE_FILE if the run used a separate workspace state). Read-only (GET /call)."""
import glob, json, os, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for p in (REPO / "vendor/build-the-builder/tools/driver", REPO / "vendor/build-the-builder/tools", REPO / "vendor/build-the-builder"):
    sys.path.insert(0, str(p))
import freya  # noqa: E402

tag, want = sys.argv[1], sys.argv[2:]
rows = {}
for f in glob.glob(str(REPO / f"out/template_cc/eval/freya/*.{tag}.json")):
    for r in json.load(open(f))["rows"]:
        rows[r["case"]] = r
out = REPO / "out/template_cc/eval/calls"; out.mkdir(parents=True, exist_ok=True)


def go(cid):
    r = rows[cid]; c = freya.get_call(r["call_id"])
    lines = [f"### {tag} {cid} pass={r['pass']} family={r['family']} failed={r['failed']} tools={r['tools']}"]
    for m in c["messages"]:
        role, msg = m.get("role"), m.get("message") or ""
        if role in ("user", "assistant"):
            lines.append(f"{role.upper()}[{m.get('nodeId')}]: {msg[:230]}"); continue
        try:
            e = json.loads(msg)
        except Exception:
            continue
        ev = e.get("event")
        if ev == "node_enter": lines.append(f"  -> {e.get('node')}")
        elif ev == "variable_extracted": lines.append(f"  x {e.get('name')}={str(e.get('value'))[:80]}")
        elif ev == "function_call_complete":
            res = e.get("result") or {}
            lines.append(f"  fn {e.get('function')} {str(res.get('data'))[:140]} {res.get('validation_errors') or ''}")
        elif ev == "function_call_start" and e.get("function") != "code_exec":
            lines.append(f"  call {e.get('function')} {json.dumps(e.get('args'))[:180]}")
    (out / f"{tag}.{cid}.txt").write_text("\n".join(lines))


with ThreadPoolExecutor(6) as ex:
    list(ex.map(go, want))
print("wrote", len(want), "transcripts to", out)
