#!/bin/bash
# THE gate (single lane plan): pipe9 = S4's offline final_run builds of all 9 briefs (m1 code + patcher). GATE x1, tag pipe9g1, sealed.
#   DECISION: ship the pipeline (pipe9) if pipe9g1 >= p6g1 on the briefs that finished, else ship p6.
#   No new brief is started after GATE_END (17:00), so the lane is free for S4's f3 rehearsal; a running brief finishes. No FINAL_*.txt is written here: the decision line is only a log line.
# Logs "DECISION ..." to out/template_cc/eval/gate_pipe9.log. Needs out/template_cc/pipe9/<brief>/workflow.json for all 9 briefs.
cd "$(dirname "$0")/.." || exit 1
SET=${GATE_SET:-pipe9}   # GATE_SET=pipe9fx: the same builds with the right_party / fixups corrections
GATE_END=${GATE_END:-1700} scripts/gate_set.sh $SET pipe9g
E=out/template_cc/eval
python3 - >> $E/gate_pipe9.log <<'PY'
import glob, json
def tot(tag):
    r = {}
    for f in glob.glob(f"out/template_cc/eval/freya/*.{tag}.json"):
        d = json.load(open(f)); r[d["brief"]] = d["passed"]
    return r
n, p = tot("pipe9g1"), tot("p6g1")
if not n:
    print("DECISION pipeline  rule D: no pipe9g1 result by the cutoff (Freya not back or set missing)")
else:
    a, b = sum(n.values()), sum(p[k] for k in n if k in p)
    print(f"DECISION {'pipeline' if a >= b else 'p6'}  rule G: pipe9g1 {a} vs p6g1 {b} over {len(n)} finished brief(s): {sorted(n)}")
PY
.venv310/bin/python scripts/gate_report.py pipe9g1 p6g1 > $E/gate_report.txt 2>&1
