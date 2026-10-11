#!/bin/zsh
# All testbed briefs with the vendor local judge. Phase 1: 10 cases per brief, 9 briefs in parallel. Phase 2: the rest.
cd /Users/alinebidal/yc-hackathon-vapi
set -a; source .env; set +a
BRIEFS=(brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes)
mkdir -p out/template_cc/eval
phase() {  # $1 tag, then extra args
  local tag=$1; shift
  local i=0
  for b in $BRIEFS; do
    port=$((8801 + i))
    .venv310/bin/python scripts/tcc_eval_testbed.py --brief $b --concurrency 2 --port $port --tag $tag "$@" \
      > out/template_cc/eval/$b.$tag.log 2>&1 &
    i=$((i + 1))
    sleep 20   # staggered: the Freya saves do not race on .freya-agents.json
  done
  wait
  echo "phase $tag done $(date +%T)"
}
phase s10 --limit 10
.venv310/bin/python scripts/tcc_eval_report.py s10
phase rest --limit 0 --exclude-sample 10
.venv310/bin/python scripts/tcc_eval_report.py s10 rest
