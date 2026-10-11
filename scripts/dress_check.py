"""Check one final_run.sh --offline call with exactly brief-a + brief-b (the final's shape).   python3 scripts/dress_check.py <out dir> [flags file]
  1. the flags the build used == the flags of FINAL_FLAGS.txt (default M1_FLAGS.txt); f18_opening_fallback off, f28_tool_failure on when named
  2. no opening_resume node; promise_try / promise_error present (f28)
  3. brief-b (built ALONE with builder/build.py) resolved "Brief A" from briefs/ (or the call); no DEGENERATE line anywhere
  5. a trace file under traces/ and, in a real run, the Freya export at workflows/<slot>.workflow.json (the committed path)
  4. lint 0 errors
Prints OK / PROBLEM per line and a final verdict. Reads only the build outputs."""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
from builder.template_cc import features as F  # noqa: E402

out = Path(sys.argv[1])
OFFLINE = "--offline" in sys.argv
OFFLINE_BRIEFS = set(sys.argv[sys.argv.index("--offline-briefs") + 1].split(",")) if "--offline-briefs" in sys.argv else set()
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if OFFLINE_BRIEFS:
    args = [a for a in args if a not in {",".join(sorted(OFFLINE_BRIEFS)), *OFFLINE_BRIEFS}]
flags_file = Path(args[1]) if len(args) > 1 else (Path("FINAL_FLAGS.txt") if Path("FINAL_FLAGS.txt").exists() else Path("M1_FLAGS.txt"))
want = F.parse(flags_file.read_text().replace("\n", "").strip())
problems = []
API_FILE = Path(sys.argv[sys.argv.index("--api") + 1]) if "--api" in sys.argv else Path("tools/openapi.json")
BAD_URL = re.compile(r"trycloudflare|localhost|127\.0\.0\.1|0\.0\.0\.0|\.invalid|ngrok|loca\.lt", re.I)


def tool_urls(wf):
    """Every url of a workflow tool (the default of its `url` parameter, or a plain string url)."""
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "url" and isinstance(v, dict):
                    if isinstance(v.get("default"), str):
                        found.append(v["default"])
                elif k == "url" and isinstance(v, str):
                    found.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(wf)
    return found


def check_tool_urls(label, path):
    """Shipped workflow: each tool URL starts with the OpenAPI servers[].url and is not our tunnel / shared origin / localhost."""
    try:
        servers = [s["url"].rstrip("/") for s in json.load(open(API_FILE)).get("servers", []) if s.get("url")]
        wf = json.load(open(path))
    except (OSError, ValueError) as e:
        line(False, f"{label}: tool URLs: cannot read {path} or {API_FILE} ({e})")
        return
    wf = wf.get("workflow", wf)
    origins = [o for f in Path("out").glob("**/shared_origin*.txt") for o in [f.read_text().strip()] if o.startswith("http")]
    urls = tool_urls(wf)
    bad = [u for u in urls if BAD_URL.search(u) or any(o.rstrip("/") in u for o in origins) or not any(u.startswith(sv + "/") for sv in servers)]
    line(bool(urls) and not bad, f"{label}: {len(urls)} tool URL(s) all under the OpenAPI server {servers}" + ("" if urls and not bad else f"; BAD {sorted(set(bad))[:3] or 'no tool URL found'}"))


def line(ok, msg):
    print(("OK      " if ok else "PROBLEM ") + msg)
    if not ok:
        problems.append(msg)


def build_dir(b):
    d = out / b
    for sub in ("r0b", "r0"):
        if (d / sub / "build_report.json").exists() and (d / sub / "workflow.json").exists():
            return d / sub
    return d


print(f"flags file: {flags_file}  ({len(want)} flags; f18_opening_fallback {'ON' if 'f18_opening_fallback' in want else 'off'}, f28_tool_failure {'ON' if 'f28_tool_failure' in want else 'off'})")
for b in ("brief-a", "brief-b"):
    d = build_dir(b)
    try:
        rep = json.load(open(d / "build_report.json"))
        wf = json.load(open(d / "workflow.json"))
    except OSError as e:
        line(False, f"{b}: no build output ({e})")
        continue
    wf = wf.get("workflow", wf)
    got = frozenset(rep.get("features") or [])
    line(got == want, f"{b}: flags used == flags file ({len(got)} vs {len(want)})" + ("" if got == want else f"; extra {sorted(got - want)}, missing {sorted(want - got)}"))
    nodes = set(wf["nodes"])
    line("opening_resume" not in nodes if "f18_opening_fallback" not in want else "opening_resume" in nodes, f"{b}: opening_resume node {'absent' if 'opening_resume' not in nodes else 'present'}")
    if "f28_tool_failure" in want:
        line({"promise_try", "promise_error"} <= nodes, f"{b}: promise_try / promise_error nodes present")
    check_tool_urls(f"{b} workflow.json", d / "workflow.json")
    lint = (rep.get("lint") or {}).get("errors")
    line(lint == 0, f"{b}: lint errors {lint}")
    line(rep.get("status") == "ok", f"{b}: build status {rep.get('status')}")
    bp = out / f"{b}.build_py.json"
    try:
        info = json.loads(bp.read_text().strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        info = {}
    line(info.get("status") == "ok", f"{b}: builder/build.py reported status {info.get('status')} ({info.get('message') or ''})".strip())
    tr = Path(info.get("trace") or rep.get("trace") or "")
    line(bool(str(tr)) and (tr / "trace.txt").exists() and "traces" in tr.parts, f"{b}: trace file under traces/ ({tr / 'trace.txt'})")
    if not OFFLINE and b not in OFFLINE_BRIEFS:
        sv = Path(info.get("saved") or "x")
        line(sv.exists() and sv.parent.name == "workflows", f"{b}: Freya export at the committed path ({info.get('saved')})")
        if sv.exists():
            check_tool_urls(f"{b} Freya-saved export", sv)
    logs = " ".join(p.read_text(errors="ignore") for p in list(out.glob(f"{b}/**/*.log")) + list(out.glob(f"{b}/**/trace.txt")) + list(out.glob(f"{b}/*.out"))[:3])
    line("DEGENERATE" not in logs, f"{b}: no DEGENERATE line")
res = None
for p in (out / "brief-b").rglob("brief_resolution.json"):
    res = json.load(open(p))
    break
if res is None:
    line(False, "brief-b: no brief_resolution.json")
else:
    entries = [e for e in res.get("log", []) if e.get("ref") == "A"]
    ok = bool(entries) and all(e.get("status") == "resolved" and e.get("found_in") in ("call", "repo") and "brief-a" in e.get("path", "") for e in entries)
    line(ok, f"brief-b: reference 'Brief A' resolved {[(e.get('status'), e.get('found_in'), e.get('path')) for e in entries]}")
print("\nVERDICT:", "ALL OK" if not problems else f"{len(problems)} PROBLEM(S)")
sys.exit(0 if not problems else 1)
