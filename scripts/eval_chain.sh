#!/bin/bash
# Run the Freya eval brief by brief, so a stuck simulation costs one brief and not the night.
# Usage: scripts/eval_chain.sh <set-or-"-"> <tag-prefix> <reps>      e.g.  scripts/eval_chain.sh p2 p2r 2
#   - set "-" keeps the installed workflows; otherwise scripts/use_workflows.sh <set> runs first.
#   - for rep in 1..reps, for each brief: skipped when out/template_cc/eval/freya/<brief>.<prefix><rep>.json exists,
#     else tcc_eval_freya.py for that single brief under `timeout 1800`.
#   - the chain stops (reason in the log) when a run times out, or prints "another simulation is running" for 10 minutes in a row.
# Origin: out/template_cc/eval/shared_origin.txt, checked with curl before starting.
cd "$(dirname "$0")/.." || exit 2
set -a; [ -f .env ] && . ./.env; [ -n "$LANE_ENV" ] && . "$LANE_ENV"; set +a  # LANE_ENV: another lane's key/state/bank identity wins over .env
export FREYA_SIM_CONCURRENCY=10  # one wave per 10-case brief; never above 10
SET=${1:?set or -}; PREFIX=${2:?tag prefix}; REPS=${3:?reps}
BRIEFS=${CHAIN_BRIEFS:-"brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"}  # CHAIN_BRIEFS: only these
EVAL_ARGS=${EVAL_ARGS:-}  # extra tcc_eval_freya.py arguments, e.g. "--skip 10 --limit 10" or "--cases A-001,F1-004"
OUT=out/template_cc/eval; LOG=$OUT/chain-$PREFIX.log
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }
stop() { say "CHAIN STOPPED: $*"; exit 1; }

[ "$SET" != "-" ] && { scripts/use_workflows.sh "$SET" >> "$LOG" 2>&1 || stop "use_workflows.sh $SET failed"; say "installed set $SET"; }
ORIGIN=${ORIGIN_OVERRIDE:-$(cat $OUT/shared_origin.txt 2>/dev/null)}
[ -n "$ORIGIN" ] || stop "no origin in $OUT/shared_origin.txt"
curl -s -m 20 "$ORIGIN/brief-a/openapi.json" | grep -q '"openapi"' || stop "origin $ORIGIN does not answer with the bank's openapi.json"
say "chain $PREFIX reps=$REPS origin=$ORIGIN"

for rep in $(seq 1 "$REPS"); do
  for b in $BRIEFS; do
    TAG=$PREFIX$rep
    [ -f "$OUT/freya/$b.$TAG.json" ] && { say "skip $b $TAG (done)"; continue; }
    RUN=$OUT/chain-run.$$.log; : > "$RUN"
    say "run $b $TAG"
    timeout 1800 .venv310/bin/python scripts/tcc_eval_freya.py --brief "$b" --limit 10 --or-budget 0 --tag "$TAG" --shared-origin "$ORIGIN" $EVAL_ARGS > "$RUN" 2>&1 &
    PID=$!; WAITS=0
    while kill -0 $PID 2>/dev/null; do
      sleep 15
      # consecutive "another simulation" lines at the end of the output = time spent waiting for a free slot (30 s each)
      WAITS=$(tail -n 40 "$RUN" | awk '/another simulation is running/{n++; next} {n=0} END{print n+0}')
      if [ "$WAITS" -ge 20 ]; then kill $PID 2>/dev/null; wait $PID 2>/dev/null; cat "$RUN" >> "$LOG"; stop "$b $TAG: waited more than 10 minutes for another simulation to finish"; fi
    done
    wait $PID; RC=$?
    tail -n 3 "$RUN" | cut -c1-160 | tee -a "$LOG"
    [ $RC -eq 124 ] && { cat "$RUN" >> "$LOG"; stop "$b $TAG: timed out after 1800 s"; }
    [ -f "$OUT/freya/$b.$TAG.json" ] || { cat "$RUN" >> "$LOG"; stop "$b $TAG: no result file (exit $RC); see the log"; }
  done
done
rm -f "$OUT"/chain-run.$$.log
say "chain $PREFIX done"
