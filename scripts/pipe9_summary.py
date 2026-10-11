"""Per brief: build time, lint, what fixups / pre-check changed, trace check. Reads <dir>/<brief>/{final_run.log,build_report.json,precheck.out,final_vote.log}."""
import json, re, subprocess, sys
from pathlib import Path

d = Path(sys.argv[1])
print(f"{'brief':16s} {'build s':>7s} {'lint':>9s} {'extra s':>7s} fixups / pre-check                                   trace")
for b in sorted(p.name for p in d.iterdir() if p.is_dir()):
    x = d / b
    log = (x / "final_run.log").read_text() if (x / "final_run.log").exists() else ""
    m = re.search(r"stage build: (\d+)s", log)
    total = re.search(r"offline run done: (\d+)s", log)
    rep = json.loads((x / "build_report.json").read_text()) if (x / "build_report.json").exists() else {}
    lint = rep.get("lint") or {}
    fx = []
    try:
        fv = (x / "r0" / "final_vote.log").read_text()
        fx += [l.strip()[:90] for l in fv.splitlines() if "policy_fixups" in l or "->" in l or "changed" in l.lower()][:3]
    except Exception:  # noqa: BLE001
        pass
    try:
        pc = json.loads((x / "precheck.out").read_text().strip().splitlines()[-1])
        fx.append(f"pre-check: {pc['changed']} changed, {len(pc['mismatches'])} flagged" + "".join(f" [{k['pointer']} {'-> ' + str(k['new']) if 'new' in k else 'kept'}]" for k in pc["mismatches"]))
    except Exception:  # noqa: BLE001
        fx.append("pre-check: no output")
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("check_trace.py")), str(x)], capture_output=True, text=True)
    miss = [l[8:] for l in r.stdout.splitlines() if l.startswith("MISSING")]
    print(f"{b:16s} {m[1] if m else '?':>7s} {str(lint.get('errors', '?')) + 'e/' + str(lint.get('warnings', '?')) + 'w':>9s} {total[1] if total else '?':>7s} {'; '.join(fx)[:150]:150s} {'ok' if not miss else 'MISSING ' + ','.join(miss)}")
