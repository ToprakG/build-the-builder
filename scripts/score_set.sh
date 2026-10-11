#!/bin/bash
# Score every policy of out/template_cc/<tag>/ with score_policy.py (percent) and policy_critical.py (misses).
cd "$(dirname "$0")/.." || exit 1
tag=$1
for b in brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes; do
  case $b in brief-*) cfg=testbed/configs/$b.config.json ;; *) cfg=testbed/fake_briefs/$b/config.json ;; esac
  p=out/template_cc/$tag/$b/policy.json
  [ -f $p ] || { echo "$b: no policy"; continue; }
  pct=$(.venv310/bin/python builder/eval/score_policy.py $p $cfg | tail -1 | grep -o '([0-9.]*%)')
  crit=$(.venv310/bin/python scripts/policy_critical.py $p $cfg | tail -1)
  echo "$b | $pct | $crit"
  [ -n "$VERBOSE" ] && .venv310/bin/python scripts/policy_critical.py $p $cfg | grep MISS
done
