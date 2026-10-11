#!/bin/bash
# m2final = m2b + every flag whose slot said KEEP (recompiled from the m2b policies, no claude, no Freya), then GATE x1 on f6 (tag m2fg1) as the interaction check.
# Compared with the best single-fix result on f6 (m2cg1 scam, m2rg1 refusal, m2dg1 third party, whatever exists): a score 2+ below it is reported loudly.
#   scripts/m2final.sh --wait   waits until the slots are finished (every started slot has its verdict and no slot waits: after the scheduler stopped at 18:40, or 4 slots done), gives up at 19:00
#   scripts/m2final.sh          runs now with the verdicts found in out/template_cc/eval/gate_reserved.log
# slot name -> flag and (if not yet on main) branch:  scam=f29_scam_open (merged)  refusal=f33_attempt_ladder@fix-refusal-f33  third_party=f22_forbidden_disclosure (merged)
# multi_product = <flag from FLAG= / BRANCH= in its slot env file>
cd "$(dirname "$0")/.." || exit 1
E=out/template_cc/eval; LOG=$E/m2final.log
now() { date +%H%M | sed 's/^0*//'; }
log() { echo "$(date +%H:%M) $*" | tee -a $LOG; }
if [ "${1:-}" = --wait ]; then
  while true; do
    [ "$(now)" -ge 1900 ] && { log "19:00 reached before the slots finished: m2final not built (tell S0)"; exit 3; }
    started=$(ls $E/reserved_slot*.env.done 2>/dev/null | wc -l | tr -d ' '); verd=$(ls $E/reserved_slot*.env.verdict 2>/dev/null | wc -l | tr -d ' ')
    pend=$(for f in $E/reserved_slot*.env; do [ -f "$f.done" ] || echo x; done | wc -l | tr -d ' ')
    # every started slot has its verdict, and no slot is waiting: after the scheduler stopped (18:40) or when 4 slots are done
    [ "$verd" -ge "$started" ] && [ "$pend" -eq 0 ] && { grep -q "scheduler stopped" $E/gate_reserved.log 2>/dev/null || [ "$started" -ge 4 ]; } && break
    sleep 30
  done
fi
# KEEP verdicts (latest line per slot)
KEEP=$(.venv310/bin/python - <<'PY'
import re
last = {}
for l in open("out/template_cc/eval/gate_reserved.log", errors="ignore"):
    m = re.match(r"slot (\S+) \(set .*-> (KEEP|DROP|INCOMPLETE)\s*$", l.strip())
    if m:
        last[m.group(1)] = m.group(2)
print(" ".join(n for n, v in last.items() if v == "KEEP"))
PY
)
log "slots with KEEP: ${KEEP:-none}"
FLAGS=""; MERGE=""
for n in $KEEP; do
  case $n in
    scam) FLAGS="$FLAGS,f29_scam_open";;
    refusal) FLAGS="$FLAGS,f33_attempt_ladder"; MERGE="$MERGE fix-refusal-f33";;
    third_party) FLAGS="$FLAGS,f22_forbidden_disclosure";;
    multi_product|*) f=$E/reserved_slot_$n.env; fl=$(grep -E '^FLAG=' $f 2>/dev/null | cut -d= -f2- | tr -d '"'); br=$(grep -E '^BRANCH=' $f 2>/dev/null | cut -d= -f2- | tr -d '"')
       if [ -n "$fl" ]; then FLAGS="$FLAGS,$fl"; [ -n "$br" ] && MERGE="$MERGE $br"; else log "slot '$n' is KEEP but its env file has no FLAG= (add FLAG= and BRANCH=): not included"; fi;;
  esac
done
if [ -n "$MERGE" ]; then
  log "merging kept branches into main:$MERGE"
  python3 scripts/merge_final.py $MERGE >> $LOG 2>&1 || { log "MERGE FAILED (tell S0)"; exit 4; }
  .venv310/bin/python -m pytest tests/template_cc -q 2>&1 | tail -2 | tee -a $LOG
fi
FLAGS=${FLAGS#,}
log "building m2final = m2b + [${FLAGS:-nothing}]"
scripts/recompile_set.sh m2b m2final "$FLAGS" f6-corvid-memo brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f7-lumen-codes
GATE_END=1925 GATE_BRIEFS="f6-corvid-memo" scripts/gate_set.sh m2final m2fg
.venv310/bin/python - <<'PY' | tee -a out/template_cc/eval/m2final.log
import json
E = "out/template_cc/eval/freya"
def f6(tag):
    try:
        return json.load(open(f"{E}/f6-corvid-memo.{tag}.json"))["passed"]
    except (OSError, KeyError, ValueError):
        return None
single = {t: f6(t) for t in ("m2bg1", "m2cg1", "m2rg1", "m2dg1")}
best = max((v for t, v in single.items() if v is not None and t != "m2bg1"), default=None)
now = f6("m2fg1")
print(f"f6: m2final {now}; single-fix results {single}; best single fix {best}")
if now is not None and best is not None and now <= best - 2:
    print(f"!! INTERACTION WARNING: m2final f6 {now} is {best - now} below the best single fix ({best}). Tell S0 before FINAL_FLAGS.txt is written.")
else:
    print("interaction check ok (not 2+ below the best single fix)")
PY
# info only, no claude calls. Phase 1: from the f6 check until the dress run needs the lane (no new brief after 19:10): f3, brief-b, f4, f5, brief-a.
GATE_END=1910 GATE_BRIEFS="f3-kestrel brief-b f4-brightwell f5-halden-email brief-a" scripts/gate_set.sh m2final m2fg
# Phase 2: resume after the dress run (it has the lane priority) only if it can finish by 19:40: no new brief after 19:35; the lane must be idle by 19:45.
while [ "$(now)" -lt 1935 ] && ! grep -q " done$" out/dress/report.txt 2>/dev/null; do sleep 20; done
if [ "$(now)" -lt 1935 ]; then
  GATE_END=1935 GATE_BRIEFS="f1-nordhavn f2-ostral f7-lumen-codes f3-kestrel brief-b f4-brightwell f5-halden-email brief-a" scripts/gate_set.sh m2final m2fg
fi
.venv310/bin/python scripts/m2final_report.py | tee -a $LOG
