"""Check the dress run of builder/build.py <case_folder> <mock_url>.   python3 scripts/dress_check_case.py out/dress [FINAL_FLAGS.txt]
 1. build.py exit status ok, workflow.json lint 0, flags used == FINAL_FLAGS.txt, no opening_resume
 2. ingest: case_brief.md has the FILE headers in order (BRIEF.md, README.md first), docx + xlsx converted (Turkish letters intact), tool API found
 3. submission.json (out + repo root): members[] non-empty, workflow file exists, trace dir exists under traces/, mock_url
 4. Freya export at workflows/<slot>.workflow.json; tool URLs of workflow.json and of the export under the OpenAPI servers[].url (not tunnel/localhost)
Prints OK / PROBLEM per line and a VERDICT."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, ".")
from builder.template_cc import features as F  # noqa: E402
out = Path(sys.argv[1]); b = out / "build"
flags_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("FINAL_FLAGS.txt")
want = F.parse(flags_file.read_text().replace("\n", "").strip())
problems = []
BAD = re.compile(r"trycloudflare|localhost|127\.0\.0\.1|0\.0\.0\.0|\.invalid|ngrok", re.I)


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


try:
    info = json.loads((out / "build_py.json").read_text().strip().splitlines()[-1])
except (OSError, ValueError, IndexError):
    info = {}
line(info.get("status") == "ok", f"build.py status {info.get('status')} ({info.get('message') or ''})")
try:
    rep = json.load(open(b / "build_report.json")); wf = json.load(open(b / "workflow.json")); wf = wf.get("workflow", wf)
except OSError as e:
    rep, wf = {}, {"nodes": {}}; line(False, f"no build output ({e})")
got = frozenset(rep.get("features") or [])
line(got == want, f"flags used == FINAL_FLAGS.txt ({len(got)} vs {len(want)})" + ("" if got == want else f"; extra {sorted(got - want)}, missing {sorted(want - got)}"))
line("opening_resume" not in wf.get("nodes", {}), "no opening_resume node")
line((rep.get("lint") or {}).get("errors") == 0, f"lint errors {(rep.get('lint') or {}).get('errors')}")
# 2 ingest
try:
    txt = (b / "ingest" / "case_brief.md").read_text(encoding="utf-8")
    heads = re.findall(r"(?m)^=== FILE: (.+) ===$", txt)
    line(heads[:2] == ["BRIEF.md", "README.md"] and "ek_kurallar.docx" in heads and "tablo.xlsx" in heads, f"ingest order {heads}")
    line("ş" in txt and "| Gün | Kural |" in txt, "docx/xlsx converted, Turkish letters intact")
    line("FAILED" not in (b / "ingest" / "ingest_notes.txt").read_text(), "no file conversion failed")
    line(bool(json.loads((b / "ingest" / "tools.openapi.json").read_text()).get("paths")), "tool API found with paths")
except OSError as e:
    line(False, f"ingest output missing ({e})")
# 3 submission
for p in (out / "build" / "submission.json", Path("submission.json")):
    try:
        s = json.load(open(p))
        line(bool(s.get("members")) and bool(s.get("mock_url")) and Path(str(s.get("workflow"))).exists() and Path(str(s.get("trace"))).exists() and "traces" in Path(str(s["trace"])).parts,
             f"{p}: members {s.get('members')}, workflow {s.get('workflow')}, trace {s.get('trace')}, mock_url {s.get('mock_url')}")
    except (OSError, ValueError):
        line(False, f"{p}: missing")
# 4 export + urls
sv = Path(info.get("saved") or "x")
line(sv.exists() and sv.parent.name == "workflows", f"Freya export at the committed path ({info.get('saved')})")
try:
    servers = [s["url"].rstrip("/") for s in json.load(open(b / "ingest" / "tools.openapi.json")).get("servers", [])]
except OSError:
    servers = []
for label, path in (("workflow.json", b / "workflow.json"), ("Freya export", sv)):
    try:
        u = urls(json.load(open(path)), [])
        bad = [x for x in u if not any(x.startswith(sv_ + "/") for sv_ in servers) or (BAD.search(x) and not any(BAD.search(sv_) for sv_ in servers))]
        line(bool(u) and not bad, f"{label}: {len(u)} tool URL(s) under the OpenAPI server {servers}" + (f"; BAD {sorted(set(bad))[:3]}" if bad else ""))
    except OSError:
        line(False, f"{label}: cannot read")
print("\nVERDICT:", "ALL OK" if not problems else f"{len(problems)} PROBLEM(S)")
sys.exit(0 if not problems else 1)
