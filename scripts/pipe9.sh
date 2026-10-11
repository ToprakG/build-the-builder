#!/bin/bash
# The final pipeline minus Freya on the 9 dev briefs (treated as unseen): final_run.sh --offline, FINAL_VOTES=3, M1_FLAGS, static pre-check on.
#   scripts/pipe9.sh [parallel=3] [set=pipe9]      -> out/template_cc/<set>/<brief>/ (workflow.json policy.json zones.final.json build_report.json ...)
# 3 briefs at a time = 9 concurrent claude_cli extractions (3 votes each). No Freya call. Then a summary table.
cd "$(dirname "$0")/.." || exit 2
P=${1:-3}; SET=${2:-pipe9}; MAIN=$(cd "$(git rev-parse --git-common-dir)/.." && pwd)
FLAGS=$(bash scripts/final_flags.sh . "$MAIN")
BASE=out/template_cc/$SET; mkdir -p "$BASE"
run_one() { b=$1
  case $b in brief-a|brief-b) BR=briefs/$b.md; API=tools/openapi.json;; *) BR=testbed/fake_briefs/$b/brief.md; API=testbed/fake_briefs/$b/tools.openapi.json;; esac
  rm -rf "$BASE/$b"; FINAL_MODE=patcher_on bash scripts/final_run.sh --offline --budget-min 30 --ship-min 26 --set-flags "$FLAGS" "$BR" "$API" "$BASE/$b" > "$BASE/$b.stdout" 2>&1
  d=$BASE/$b; src=$d/r0; [ -f "$d/r0b/workflow.json" ] && src=$d/r0b
  for f in policy.json workflow.json zones.final.json zones.json build_report.json; do [ -f "$src/$f" ] && cp "$src/$f" "$d/$f"; done   # the layout of a compiled set
  echo "$(date +%H:%M:%S) $b done"; }
export -f run_one; export BASE FLAGS
if [ -n "${ONLY:-}" ]; then echo "$ONLY" | tr ' ,' '\n\n' | grep . > "$BASE/.all"; else { printf 'brief-a\nbrief-b\n'; ls testbed/fake_briefs | grep '^f[1-7]-'; } > "$BASE/.all"; fi
xargs -P "$P" -I{} bash -c 'run_one {}' < "$BASE/.all"
.venv310/bin/python scripts/pipe9_summary.py "$BASE"
