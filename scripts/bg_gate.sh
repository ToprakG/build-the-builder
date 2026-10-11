#!/bin/bash
# Background p6 GATE x1 (tag p6g1): runs only on an idle lane and never delays a targeted run (run_cases.sh stops a BACKGROUND run after
# its running brief when a targeted run needs the lane). From 12:45 it is a normal run (cannot be preempted), alongside the m1 GATE.
# Finished briefs are skipped, so it resumes where it was preempted. Exits when all 9 briefs are done.
cd "$(dirname "$0")/.." || exit 1
E=out/template_cc/eval
while true; do
  [ "$(ls $E/freya/*.p6g1.json 2>/dev/null | wc -l)" -ge 9 ] && { echo "$(date +%H:%M) p6g1 complete" >> $E/bg_gate.log; exit 0; }
  busy=0; for n in 1 2; do [ "$(cat $E/lane$n.lock/tag 2>/dev/null)" = p6g ] && busy=1; done
  if [ $busy = 0 ]; then
    if [ "$(date +%H%M)" -ge 1245 ]; then BG=""; else BG=1; fi
    echo "$(date +%H:%M) starting p6g1 (background=${BG:-no})" >> $E/bg_gate.log
    BACKGROUND=$BG scripts/run_cases.sh . p6 p6g --skip 10 --limit 10 1 >> $E/bg_gate.log 2>&1
  fi
  sleep 60
done
