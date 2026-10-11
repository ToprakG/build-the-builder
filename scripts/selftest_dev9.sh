#!/bin/bash
# selftest_cases + selftest_run once on the 9 dev briefs with a compiled set (default p10), one lane at a time (lock wait), then clusters.
#   scripts/selftest_dev9.sh [set=p10] [out=out/selftest_dev9]
cd "$(dirname "$0")/.." || exit 2
MAIN=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
SET=${1:-p10}; OUT=${2:-out/selftest_dev9}; mkdir -p "$OUT"; export LANE_WAIT=${LANE_WAIT:-3600}
for b in brief-a brief-b $(ls testbed/fake_briefs | grep '^f[1-7]-'); do
  case $b in brief-a|brief-b) BR=briefs/$b.md; API=tools/openapi.json;; *) BR=testbed/fake_briefs/$b/brief.md; API=testbed/fake_briefs/$b/tools.openapi.json;; esac
  SRC=$MAIN/out/template_cc/$SET/$b; D=$OUT/$b; mkdir -p "$D"
  cp "$SRC"/{policy.json,workflow.json,zones.final.json,build_report.json} "$D/" || { echo "$b: no $SET build"; continue; }
  echo "$(date +%H:%M:%S) $b"
  .venv310/bin/python scripts/selftest_cases.py $BR "$D/policy.json" --out "$D/selftest/cases.json" | head -1
  [ "$b" = brief-b ] && true
  bash scripts/selftest_run.sh "$D" --cases "$D/selftest/cases.json" --api "$API" > "$D/run.out" 2>&1; tail -n +2 "$D/run.out" | head -1
done
python3 scripts/selftest_cluster.py "$OUT" --json "$OUT/clusters.json"
