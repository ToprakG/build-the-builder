#!/bin/bash
# Stop an eval chain WITHOUT orphaning a Freya simulation: pause the chain parent (SIGSTOP, so it cannot start the next brief),
# let the running tcc_eval_freya.py (its child) finish its simulation and write its result, then kill the chain. Max 15 minutes.
# Usage: scripts/stop_chain_gracefully.sh [chain-pid]       (default: the eval_chain.sh of this checkout)
ROOT=$(cd "$(dirname "$0")/.." && pwd)
LOG=${LOG:-/dev/stdout}
chain=${1:-$(pgrep -f "^(/bin/bash )?scripts/eval_chain\.sh " | head -1)}
[ -z "$chain" ] && { echo "$(date +%H:%M:%S) no chain running" >> $LOG; exit 0; }
kill -STOP "$chain"; echo "$(date +%H:%M:%S) paused chain $chain" >> $LOG
for i in $(seq 1 180); do
  # the chain's running python child (timeout wrapper -> python): any descendant named tcc_eval_freya.py
  if ! pgrep -P "$chain" > /dev/null; then break; fi
  sleep 5
done
kill -KILL "$chain" 2>/dev/null; echo "$(date +%H:%M:%S) killed chain $chain after the running brief ended" >> $LOG
