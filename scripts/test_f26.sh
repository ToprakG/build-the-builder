#!/bin/bash
# After the m1 gate: does f26_amount_assembly help? Runs the SAME cases on m1 and on m1f26 (m1 + f26), one run each, then prints the diff.
#   cases: B-015, F5-018 (the amount cases) + scripts/canary_small.txt
#   Keep f26 (m2 = m1 + f26) only on a clear win with no loss: every case m1 passes, m1f26 passes too, and at least one more passes.
# Build first (offline): scripts/build_set.sh m1f26 x          Usage: scripts/test_f26.sh
cd "$(dirname "$0")/.." || exit 1
IDS="B-015,F5-018,$(grep -v '^#' scripts/canary_small.txt | tr -s ' \n' ',' | sed 's/^,//; s/,$//')"
E=out/template_cc/eval
for pair in "m1 m1d" "m1f26 f26d"; do set -- $pair
  echo "== set $1 (tag $2)"; scripts/run_cases.sh . "$1" "$2" --cases "$IDS" 1
done
python3 - <<'PY'
import glob, json
def rows(tag):
    out = {}
    for f in glob.glob(f"out/template_cc/eval/freya/*.{tag}1.json"):
        for r in json.load(open(f))["rows"]:
            out[r["case"]] = bool(r["pass"])
    return out
a, b = rows("m1d"), rows("f26d")
common = sorted(set(a) & set(b))
gain = [c for c in common if b[c] and not a[c]]
loss = [c for c in common if a[c] and not b[c]]
print(f"cases {len(common)}: m1 {sum(a[c] for c in common)} pass, m1f26 {sum(b[c] for c in common)} pass; gained {gain}; lost {loss}")
print("DECISION:", "m2 = m1 + f26 (clear win, no loss)" if gain and not loss else "m1 stays final (no clear win or a loss)")
PY
