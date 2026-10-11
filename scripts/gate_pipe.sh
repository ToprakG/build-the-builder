#!/bin/bash
# GATE (sealed) on the workflow final_run.sh SHIPPED for f6, next to m1's f6 GATE score.
#   scripts/gate_pipe.sh <final_run out dir> [brief=f6-corvid-memo] [set=f6pipe]    e.g.  out/rehearsal/f3 f3-kestrel f3full -> tag f3full_g1
#   (needs <dir>/workflow.json and policy.json; the tag is <set>_g1; the f6 defaults reproduce f6pipe_g1)
# Puts the shipped build in set f6pipe, runs the f6 GATE cases (--skip 10 --limit 10, ONLY_BRIEFS=f6-corvid-memo), tag f6pipe_g1
# (a tag ending in g<n> is SEALED: only pass/fail and the failed-criterion category are written outside sealed/), then prints it
# next to m1g1's f6 row. Takes a lane like every eval (run_cases.sh lock wait).
cd "$(dirname "$0")/.." || exit 2
SRC=${1:?final_run out dir}; B=${2:-f6-corvid-memo}; SET=${3:-f6pipe}
[ -f "$SRC/workflow.json" ] || { echo "no $SRC/workflow.json: run final_run.sh first"; exit 2; }
MAIN=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
D=out/template_cc/$SET/$B; mkdir -p "$D"
cp "$SRC/workflow.json" "$SRC/policy.json" "$D/"; cp "$SRC/build_report.json" "$D/" 2>/dev/null
echo "$(date +%H:%M:%S) ${SET}_g1 on the shipped workflow $(shasum "$D/workflow.json" | cut -c1-8)"
ONLY_BRIEFS=$B scripts/run_cases.sh . $SET ${SET}_g --skip 10 --limit 10 1 | tail -4
echo "--- $B: m1g1 / pipe9g1 / ${SET}_g1:"
f=$MAIN/out/template_cc/eval/freya/$B.m1g1.json; [ -f "$f" ] || f=out/template_cc/eval/freya/$B.m1g1.json
E=out/template_cc/eval/freya; P=$MAIN/$E/$B.pipe9g1.json; [ -f "$P" ] || P=$E/$B.pipe9g1.json
python3 - "$f" "$P" "$E/$B.${SET}_g1.json" "${SET}_g1" <<'PY'
import json, os, sys
for name, f in (("m1g1", sys.argv[1]), ("pipe9g1", sys.argv[2]), (sys.argv[4], sys.argv[3])):
    if not os.path.exists(f):
        print(f"{name}: not available ({f})"); continue
    d = json.load(open(f))
    print(f"{name}: {d['passed']}/{d['cases']}  fails: " + " ".join(x["case"] + "(" + ",".join(x.get("failed", [])) + ")" for x in d["rows"] if not x["pass"]))
PY
