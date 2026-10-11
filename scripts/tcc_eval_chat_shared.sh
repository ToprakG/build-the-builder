#!/bin/zsh
# Fake briefs over the Freya chat API through ONE shared bank (scripts/tcc_shared_bank.py + any tunnel), vendor local
# judge, in parallel, stopped when the OpenRouter spend since $START reaches $BUDGET.
cd /Users/alinebidal/yc-hackathon-vapi
set -a; source .env; set +a
ORIGIN=$(cat out/template_cc/eval/shared_origin.txt)
BUDGET=${BUDGET:-3}; START=${START:?}
usage() { curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $OPENROUTER_API_KEY" | python3 -c "import json,sys;print(json.load(sys.stdin)['data']['usage'])"; }
pids=()
for b in "$@"; do
  .venv310/bin/python scripts/tcc_eval_testbed.py --brief $b --limit 10 --concurrency 2 --tag chat10 --shared-origin $ORIGIN \
    > out/template_cc/eval/$b.chat10.log 2>&1 &
  pids+=($!); sleep 10
done
while true; do
  alive=0; for p in $pids; do kill -0 $p 2>/dev/null && alive=1; done
  [ $alive = 0 ] && break
  NOW=$(usage)
  if python3 -c "import sys; sys.exit(0 if $NOW - $START >= $BUDGET else 1)"; then
    echo "budget reached: stopping"; for p in $pids; do pkill -TERM -P $p 2>/dev/null; kill -TERM $p 2>/dev/null; done; break
  fi
  sleep 30
done
echo "done, OpenRouter spent since start \$$(python3 -c "print(round($(usage) - $START, 2))")"
