#!/bin/bash
# Package the final build for the submission form.
#   scripts/package_submission.sh <out dir>        <out dir> holds one build per brief: <out dir>/<brief>/build_report.json (or <out dir>/build_report.json)
# For every build: export the Freya workflow (freya.export_workflow; the ONE Freya key, FREYA_API_KEY), copy workflow.json, build_report.json
# and the trace (trace.txt + events.jsonl) into <out dir>/submission/<brief>/, then print the form fields.
# Never copies keys: the .env is not read into the package, and the copied files are scanned for the value of every *KEY*/*TOKEN*/*SECRET*
# environment variable (and the .env values) - the script stops if one is found.
cd "$(dirname "$0")/.." || exit 2
DRY=""; [ "${1:-}" = --dry ] && { DRY=1; shift; }   # --dry: copy and scan, but call no Freya API; report what WOULD be exported
OUT=${1:?usage: scripts/package_submission.sh [--dry] <out dir>}
[ -d "$OUT" ] || { echo "no such directory: $OUT"; exit 2; }
unset FREYA_API_KEY_W2          # one Freya key only: the second workspace's key is never part of a final path
set -a; [ -f .env ] && . ./.env; set +a; unset FREYA_API_KEY_W2
PKG="$OUT/submission"; rm -rf "$PKG"; mkdir -p "$PKG"
DRY=$DRY .venv310/bin/python - "$OUT" "$PKG" <<'PY'
import json, os, re, shutil, subprocess, sys
from pathlib import Path

out, pkg = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, ".")
reports = sorted(p for p in list(out.glob("*/build_report.json")) + list(out.glob("build_report.json")) if "submission" not in p.parts)
if not reports:
    sys.exit(f"no build_report.json under {out}")
freya = None
if os.environ.get("FREYA_API_KEY") and not os.environ.get("DRY"):
    import freya  # noqa: PLC0415
rows, problems = [], []
for rp in reports:
    rep = json.loads(rp.read_text())
    brief = rp.parent.name if rp.parent != out else rep.get("slot", "build")
    d = pkg / brief
    d.mkdir(parents=True, exist_ok=True)
    for name in ("workflow.json", "build_report.json", "policy.json"):
        if (rp.parent / name).exists():
            shutil.copy(rp.parent / name, d / name)
    trace = Path(rep.get("trace") or "")
    if trace.is_dir():
        for name in ("trace.txt", "events.jsonl"):
            if (trace / name).exists():
                shutil.copy(trace / name, d / name)
    else:
        problems.append(f"{brief}: no trace directory ({rep.get('trace')!r})")
    saved = rep.get("saved") or {}
    wid = saved.get("workflow_id") if isinstance(saved, dict) else None
    exported = None
    if wid and freya:
        try:
            exported = freya.export_workflow(wid, str(d / "freya_export.workflow.json"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{brief}: export_workflow failed: {e!r}")
    elif wid and os.environ.get("DRY"):
        problems.append(f"{brief}: DRY run: would export Freya workflow {wid} to {d / 'freya_export.workflow.json'}")
    elif not wid:
        problems.append(f"{brief}: no Freya workflow id in build_report.json (was it built with --save freya?)")
    elif not freya:
        problems.append(f"{brief}: FREYA_API_KEY missing, workflow not exported")
    rows.append({"brief": brief, "status": rep.get("status"), "workflow_id": wid, "agent_id": saved.get("agent_id") if isinstance(saved, dict) else None,
                 "workflow": str(d / ("freya_export.workflow.json" if exported else "workflow.json")), "trace": str(d / "trace.txt")})

# secrets must not be in the package
secrets = {v for k, v in os.environ.items() if re.search(r"KEY|TOKEN|SECRET|PASSWORD", k) and len(v) >= 8}
for line in (Path(".env").read_text().splitlines() if Path(".env").exists() else []):
    if "=" in line and not line.lstrip().startswith("#"):
        v = line.split("=", 1)[1].strip().strip("\"'")
        if len(v) >= 8:
            secrets.add(v)
for f in pkg.rglob("*"):
    if f.is_file():
        text = f.read_text(errors="ignore")
        if any(s in text for s in secrets):
            sys.exit(f"STOP: a secret value appears in {f}; nothing may be submitted from {pkg}")

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
remote = re.sub(r"//[^/@]*@", "//", git("remote", "get-url", "origin") or "<repo URL>")   # drop credentials from the URL
dirty = git("status", "--porcelain", "--untracked-files=no")
print("\n=== submission form ===")
print("team name:    <TEAM NAME>")
print(f"repo URL:     {remote}")
print(f"commit:       {git('rev-parse', 'HEAD')}" + ("   (WARNING: uncommitted changes in tracked files)" if dirty else ""))
print("run command:  python3 builder/build.py {brief_path} {tool_api_path}      (a brief that builds on another: python3 builder/build.py {base_brief_path} {brief_path} {tool_api_path})")
for r in rows:
    print(f"- {r['brief']}: status {r['status']}, workflow id {r['workflow_id']}, agent id {r['agent_id']}\n    workflow: {r['workflow']}\n    trace:    {r['trace']}")
if problems:
    print("\nPROBLEMS:\n" + "\n".join("- " + p for p in problems))
print(f"\npackage: {pkg}")
PY
