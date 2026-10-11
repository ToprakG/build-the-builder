#!/bin/zsh
# The 7 fake briefs over the Freya chat API (no simulation queue), vendor local judge, in parallel,
# with a watchdog that stops these runs when this run's OpenRouter spend reaches $BUDGET.
cd /Users/alinebidal/yc-hackathon-vapi
set -a; source .env; set +a
BUDGET=${BUDGET:-3}
usage() { curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY" | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['usage'])"; }
START=$(usage); echo "OpenRouter usage at start: $START, budget \$$BUDGET"
FAKES=(f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes)
pids=()
i=0
for b in $FAKES; do
  .venv310/bin/python scripts/tcc_eval_testbed.py --brief $b --limit 10 --concurrency 2 --port $((8841 + i)) --tag chat10 \
    > out/template_cc/eval/$b.chat10.log 2>&1 &
  pids+=($!)
  i=$((i + 1)); sleep 15
done
while true; do
  alive=0; for p in $pids; do kill -0 $p 2>/dev/null && alive=1; done
  [ $alive = 0 ] && break
  NOW=$(usage); SPENT=$(python3 -c "print(round($NOW - $START, 2))")
  if python3 -c "import sys; sys.exit(0 if $NOW - $START >= $BUDGET else 1)"; then
    echo "budget reached (\$$SPENT): stopping the fake-brief chat runs"
    for p in $pids; do pkill -TERM -P $p 2>/dev/null; kill -TERM $p 2>/dev/null; done
    break
  fi
  sleep 30
done
echo "done, OpenRouter spent \$$(python3 -c "print(round($(usage) - $START, 2))")"
.venv310/bin/python scripts/tcc_eval_report.py chat10
