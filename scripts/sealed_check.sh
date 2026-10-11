#!/bin/bash
# The 18:45 anti-overfit check: m1 vs m2 on GATE cases 11-20 of 4 briefs, local sim, COUNTS ONLY (no transcript is written or printed).
#   scripts/sealed_check.sh <m2 set> [<m1 set, default m1>]      4 briefs x 10 cases x 2 sets = 80 conversations (shared budget; stops at 19:15)
cd "$(dirname "$0")/.." || exit 1
M2=${1:?m2 set name}; M1=${2:-m1}
BR=brief-b,f3-kestrel,f5-halden-email,f7-lumen-codes
for s in "$M1" "$M2"; do
  [ -d "out/template_cc/$s" ] || { echo "no set $s"; exit 2; }
  .venv310/bin/python scripts/local_sim.py --sealed --set "$s" --briefs $BR --parallel 4 | grep -E "^sealed counts|not started|STOP"
done
.venv310/bin/python - "$M1" "$M2" <<'PY'
import json, sys
d = json.load(open("out/template_cc/localsim/sealed_counts.json"))
m1, m2 = sys.argv[1], sys.argv[2]
a, b = sum(v["passed"] for v in d.get(m1, {}).values()), sum(v["passed"] for v in d.get(m2, {}).values())
print(f"{m1}: {a}   {m2}: {b}   ->", f"OK, {m2} >= {m1}" if b >= a else f"{m2} < {m1}: remove the flags of {m2} one at a time, last kept first, and re-check each step")
PY
