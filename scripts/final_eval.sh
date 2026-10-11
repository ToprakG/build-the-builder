#!/bin/bash
# The evaluation step for a newly placed brief, exactly like our GATE runs (tcc_eval_freya via run_cases.sh: lane lock, use_workflows, personas, mock bank + tunnel,
# Freya simulation, bank ledger scoring). No claude_cli (the local judge budget is 0).
#   scripts/final_eval.sh <name> [reps]
# Files, placed like the dev briefs (see RUNBOOK_FINAL.md):  testbed/fake_briefs/<name>/{brief.md, tools.openapi.json, cases.json, config.json}
# Workflow: $WORKFLOW (default out/final/<name>/workflow.json, the last-green of final_run.sh). Cases: the first $CASES (default 20) of the family-spread order.
# Result: out/template_cc/eval/freya/<name>.finalr<rep>.json and the pass table. Needs cloudflared (a bank of its own on port ${BANK_PORT:-8862}).
cd "$(dirname "$0")/.." || exit 1
NAME=${1:?usage: scripts/final_eval.sh <name> [reps]}; REPS=${2:-1}
D=testbed/fake_briefs/$NAME; WF=${WORKFLOW:-out/final/$NAME/workflow.json}; PORT=${BANK_PORT:-8862}; LIMIT=${CASES:-20}
for f in tools.openapi.json cases.json config.json; do [ -f "$D/$f" ] || { echo "missing $D/$f (see RUNBOOK_FINAL.md, section 20:00 files)"; exit 2; }; done
[ -f "$WF" ] || { echo "missing workflow $WF (run scripts/final_run.sh first, or set WORKFLOW=)"; exit 2; }
set -a; [ -f .env ] && . ./.env; set +a
mkdir -p out/template_cc/final/$NAME out/template_cc/eval/final_eval
cp "$WF" out/template_cc/final/$NAME/workflow.json
# a bank of its own for this brief (the main bank on 8860 only serves the dev briefs)
LOGB=out/template_cc/eval/final_eval/bank-$NAME.log; ORIGIN_F=out/template_cc/eval/final_eval/origin-$NAME.txt; : > $LOGB; rm -f $ORIGIN_F
nohup .venv310/bin/python scripts/tcc_shared_bank.py --port "$PORT" --origin-file "$ORIGIN_F" "$NAME" > $LOGB 2>&1 < /dev/null &
BANK=$!
trap 'kill $BANK 2>/dev/null; pkill -f "cloudflared tunnel --url http://127.0.0.1:$PORT" 2>/dev/null; pkill -f "vendor_bankd.*--port $PORT" 2>/dev/null' EXIT
for i in $(seq 1 60); do [ -s $ORIGIN_F ] && break; grep -qE "ERROR|Traceback" $LOGB && break; sleep 5; done
[ -s $ORIGIN_F ] || { echo "no bank tunnel (see $LOGB)"; exit 3; }
echo "bank for $NAME at $(cat $ORIGIN_F)   (the main bank's out/template_cc/eval/shared_origin.txt is not touched)"
ORIGIN_FILE=$ORIGIN_F scripts/run_cases.sh . final finalr --skip 0 --limit "$LIMIT" "$REPS"
