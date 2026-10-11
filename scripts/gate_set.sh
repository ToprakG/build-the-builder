#!/bin/bash
# GATE x1 of one compiled set, brief by brief (same rules as gate_m1.sh phase 1): a failed brief is retried ONCE after 5 minutes, never in a
# tight loop; "no free lane" waits 60 s without counting. Finished briefs are skipped. Exits when all 9 are done or every unfinished brief is abandoned.
#   GATE_END=<HHMM>  (optional) stop waiting / starting briefs at that time of day, e.g. GATE_END=1645
#   scripts/gate_set.sh <set> <tag-prefix>        e.g. scripts/gate_set.sh pipe9 pipe9g      (result tag <prefix>1, sealed because it ends in g1)
cd "$(dirname "$0")/.." || exit 1
REP=${REP:-1}   # REP=2: the tag is <prefix>2 (a repeat; finished reps are skipped)
SET=${1:?set}; PFX=${2:?tag prefix}; TAG=${PFX}${REP}
E=out/template_cc/eval; LOG=$E/gate_$SET.log; ST=$E/gate_$SET.state; mkdir -p $ST
BRIEFS=${GATE_BRIEFS:-"brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"}   # GATE_BRIEFS="brief-b f3-kestrel ...": a subset, in that order
N=$(echo $BRIEFS | wc -w | tr -d " ")
log() { echo "$(date +%H:%M) $*" >> $LOG; }
late() { [ -n "${GATE_END:-}" ] && [ "$(date +%H%M | sed 's/^0*//')" -ge "$GATE_END" ]; }
n_done() { n=0; for b in $BRIEFS; do [ -f $E/freya/$b.$TAG.json ] && n=$((n+1)); done; echo $n; }
log "gate $SET ($TAG): waiting for $SET/<brief>/workflow.json for all 9 briefs"
have() { n=0; for b in $BRIEFS; do [ -f out/template_cc/$SET/$b/workflow.json ] && n=$((n+1)); done; echo $n; }
while [ "$(have)" -lt "$N" ]; do late && { log "GATE_END reached before the set was complete"; exit 0; }; sleep 60; done
log "set $SET is complete: starting"
while [ "$(n_done)" -lt "$N" ] && ! late; do
  progressed=0
  for b in $BRIEFS; do
    [ -f $E/freya/$b.$TAG.json ] && continue
    [ -f $ST/$b.abandoned ] && continue
    late && break
    tries=$(cat $ST/$b.tries 2>/dev/null || echo 0); echo $((tries + 1)) > $ST/$b.tries
    log "$TAG $b (try $((tries + 1)))"
    ONLY_BRIEFS=$b scripts/run_cases.sh . $SET $PFX --skip 10 --limit 10 $REP >> $LOG 2>&1; rc=$?
    if [ "$rc" = 3 ]; then echo $tries > $ST/$b.tries; log "lanes busy: waiting 60 s (not a failed try)"; sleep 60; continue; fi
    if [ -f $E/freya/$b.$TAG.json ]; then progressed=1; continue; fi
    if [ "$tries" -ge 1 ]; then touch $ST/$b.abandoned; log "$TAG $b failed twice: abandoned"
    else log "$TAG $b failed: one retry in 5 minutes"; sleep 300; fi
  done
  [ "$progressed" = 0 ] && [ "$(ls $ST/*.abandoned 2>/dev/null | wc -l | tr -d ' ')" -ge $((N - $(n_done))) ] && break
  sleep 60
done
log "gate $SET finished: $(n_done)/$N briefs"
