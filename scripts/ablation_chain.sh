#!/bin/bash
# Scheduler for the lane until 18:40 (one brief per step). PRIORITY 1: fix-verification slots out/template_cc/eval/reserved_slot_<name>.env (SET= PFX= BRIEFS= [FAMILY=]),
# up to 4, in the order scam -> refusal -> third_party -> multi_product (any other name after those, by arrival); a slot starts as soon as the brief currently running has
# finished; a slot started before 18:40 runs to its end. PRIORITY 2 (only when no slot waits): background items (f20 ablation on f4 + f6, a f5 confirmation if f20's removal
# gained >= +1 before 18:10, m2b on f3, m2b brief-b repeat tag m2bg2). Verdicts (scripts/slot_verdict.py) go to gate_reserved.log, also for a slot that was started by a
# previous scheduler instance (restart-safe: a .done slot without a .verdict marker gets its verdict when its results are in).
cd "$(dirname "$0")/.." || exit 1
now() { date +%H%M | sed 's/^0*//'; }
E=out/template_cc/eval
MAXSLOTS=4
slots=$(ls $E/reserved_slot*.env.done 2>/dev/null | wc -l | tr -d ' ')
next_slot() {
  for n in scam refusal third_party multi_product; do f=$E/reserved_slot_$n.env; [ -f "$f" ] && [ ! -f "$f.done" ] && { echo "$f"; return; }; done
  ls -tr $E/reserved_slot_*.env $E/reserved_slot.env 2>/dev/null | while read -r f; do [ -f "$f.done" ] || { echo "$f"; break; }; done
}
lane_busy() { [ -d $E/lane1.lock ] && kill -0 "$(cat $E/lane1.lock/pid 2>/dev/null)" 2>/dev/null; }
slot_name() { n=$(basename "$1" .env | sed 's/^reserved_slot_*//'); [ -z "$n" ] && n=default; echo "$n"; }
verdicts() {   # a started slot whose gate has ended and that has no verdict yet
  for d in $E/reserved_slot*.env.done; do [ -f "$d" ] || continue; f=${d%.done}; [ -f "$f.verdict" ] && continue
    ( . "$f"; pgrep -f "gate_set.sh $SET $PFX" > /dev/null && exit 1; exit 0 ) || continue
    n=$(slot_name "$f"); .venv310/bin/python scripts/slot_verdict.py "$n" >> $E/gate_reserved.log 2>&1; touch "$f.verdict"
  done
}
run_slot() { next=$1; name=$(slot_name "$next")
  touch "$next.done"; slots=$((slots + 1))
  [ "$name" = default ] && cp "$next" "$E/reserved_slot_default.env"
  echo "$(date +%H:%M) slot $slots/$MAXSLOTS '$name' (preempts the background items): $(tr '\n' ' ' < "$next")" >> $E/gate_reserved.log
  scripts/run_slot.sh "$next" >> $E/gate_reserved.log 2>&1
  .venv310/bin/python scripts/slot_verdict.py "$name" >> $E/gate_reserved.log 2>&1; touch "$next.verdict"; }
TASKS=("abl_f20_once_flags abl_f20_once_flags_g 1 f4-brightwell" "abl_f20_once_flags abl_f20_once_flags_g 1 f6-corvid-memo" "m2b m2bg 1 f3-kestrel" "m2b m2bg 2 brief-b")
task_done() { set -- $1; [ -f "$E/freya/$4.$2$3.json" ] || [ -f "$E/gate_$1.state/$4.abandoned" ]; }
confirmed=0
while [ "$(now)" -lt 1840 ]; do
  verdicts
  s=""; [ $slots -lt $MAXSLOTS ] && s=$(next_slot)
  if [ -n "$s" ]; then
    while lane_busy; do sleep 5; done        # the brief currently running finishes first
    run_slot "$s"; continue
  fi
  pick=""
  for t in "${TASKS[@]}"; do task_done "$t" || { pick=$t; break; }; done
  if [ -z "$pick" ] && [ $confirmed -eq 0 ]; then
    confirmed=1
    if [ "$(now)" -lt 1810 ] && .venv310/bin/python scripts/ablation_gains.py 2>> $E/ablation_gains.log | grep -q f20_once_flags; then
      echo "$(date +%H:%M) confirmation on f5: f20_once_flags removed" >> $E/ablation_gains.log
      scripts/recompile_set.sh m2b abl_f20_once_flags "-f20_once_flags" f5-halden-email
      TASKS=("abl_f20_once_flags abl_f20_once_flags_g 1 f5-halden-email" "${TASKS[@]}")
      continue
    fi
  fi
  if [ -z "$pick" ]; then sleep 20; continue; fi
  while lane_busy; do s=""; [ $slots -lt $MAXSLOTS ] && s=$(next_slot); [ -n "$s" ] && break; sleep 5; done
  [ -n "$s" ] && continue
  set -- $pick
  REP=$3 GATE_END=1840 GATE_BRIEFS="$4" scripts/gate_set.sh "$1" "$2"
done
verdicts
echo "$(date +%H:%M) scheduler stopped (18:40 cutoff; $slots slot(s) used)" >> $E/gate_reserved.log
