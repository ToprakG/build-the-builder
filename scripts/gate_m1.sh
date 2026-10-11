#!/bin/bash
# Single-lane plan: from 12:45 (and once out/template_cc/m1 exists) run the m1 GATE x1 (tag m1g1) on the same 9 briefs as p6g1, one brief
# at a time, as normal (non-background) runs on lane 1. Finished briefs are skipped.
#   Rule G (gate):   all 9 briefs done        -> ship m1 if m1g1 >= p6g1 over all 9 briefs, else p6.
#   Rule C (canary): the gate cannot complete by 15:30 (API errors) -> as soon as the Freya API answers, run the full scripts/canary.txt
#                    (20 cases, tag m1c1) on m1 -> ship m1 if canary >= 19/20, else p6.
#   Rule D (default): neither rule could decide by 16:30 (the API outage blocks both) -> ship m1: it led on the finished GATE briefs and the targeted
#                     runs / canary_small showed no loss ("DECISION m1 (no further data)").
# A brief that fails (API error, timeout, no result) is retried ONCE after 5 minutes, never in a tight loop; after that it is left for rule C.
# Each decision line says which rule decided: out/template_cc/eval/gate_m1.log (last line: DECISION ...).
# FLAGS: m1 is built with the flags in M1_FLAGS.txt; this script refuses to run unless out/template_cc/m1/brief-a/build_report.json lists all of them.
# STANDING RULE: m1 (and m1x) are built ONLY from out/template_cc/policies_fixed/ (scripts/make_policies_fixed.py; build_set.sh and
# build_set_x.sh enforce it for set names m1*). This script refuses to run on a set whose id_decline / cap_at_deadline differ from it.
cd "$(dirname "$0")/.." || exit 1
E=out/template_cc/eval; LOG=$E/gate_m1.log; ST=$E/gate_m1.state; mkdir -p $ST
BRIEFS="brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"
log() { echo "$(date +%H:%M) $*" >> $LOG; }
now() { date +%H%M | sed 's/^0*//'; }          # 1245, 1530 ... (numeric)
n_done() { ls $E/freya/*.m1g1.json 2>/dev/null | wc -l | tr -d ' '; }
api_ok() { (set -a; . ./.env; set +a; .venv310/bin/python -c "import freya; freya.api('GET', '/api/v2/workspaces')" >/dev/null 2>&1); }
tot() { python3 - "$1" <<'PY'
import glob, json, sys
t = n = 0
for f in glob.glob(f"out/template_cc/eval/freya/*.{sys.argv[1]}.json"):
    d = json.load(open(f)); t += d["passed"]; n += d["cases"]
print(t, n)
PY
}

log "waiting for the m1 build (out/template_cc/m1/.built)"
# Early start: as soon as the m1 build is complete (.built, written by build_set.sh) - no waiting for 12:45. The guard below then
# checks that it matches M1_FLAGS.txt and policies_fixed, and refuses otherwise.
while [ ! -f out/template_cc/m1/.built ]; do sleep 60; done

python3 - >> $LOG <<'PY' || { log "REFUSED: set m1 was not built from policies_fixed (rebuild: scripts/build_set.sh m1 <features>)"; exit 3; }
import json, sys
bad = []
want = [x for x in open("M1_FLAGS.txt").read().replace("\n", "").split(",") if x]
try:
    have = json.load(open("out/template_cc/m1/brief-a/build_report.json")).get("features") or []
except OSError as e:
    have = []; bad.append(f"build_report.json: {e}")
if set(want) - set(have):
    bad.append(f"m1 build lacks flags from M1_FLAGS.txt: {sorted(set(want) - set(have))}")
for b in ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]:
    try:
        f = json.load(open(f"out/template_cc/policies_fixed/{b}/policy.json")); m = json.load(open(f"out/template_cc/m1/{b}/policy.json"))
    except OSError as e:
        bad.append(f"{b}: {e}"); continue
    key = lambda p: ((p.get("ask_limits") or {}).get("id_decline"), [s.get("cap_at_deadline") for s in p.get("attempt_plan") or []])
    if key(f) != key(m):
        bad.append(f"{b}: policies_fixed {key(f)} vs m1 {key(m)}")
if bad:
    print("\n".join(bad)); sys.exit(1)
PY

# --- phase 1: the gate, brief by brief, until all 9 are done or it is 15:30 (moved from 14:00: Freya analysis takes 5-8 min per brief) ---
while [ "$(n_done)" -lt 9 ] && [ "$(now)" -lt 1530 ]; do
  progressed=0
  for b in $BRIEFS; do
    [ -f $E/freya/$b.m1g1.json ] && continue
    [ "$(now)" -ge 1530 ] && break
    [ -f $ST/$b.abandoned ] && continue
    tries=$(cat $ST/$b.tries 2>/dev/null || echo 0)
    log "m1g1 $b (try $((tries + 1)))"
    echo $((tries + 1)) > $ST/$b.tries
    ONLY_BRIEFS=$b scripts/run_cases.sh . m1 m1g --skip 10 --limit 10 1 >> $LOG 2>&1; rc=$?
    if [ "$rc" = 3 ]; then echo $tries > $ST/$b.tries; log "lanes busy: waiting 60 s (not a failed try)"; sleep 60; continue; fi   # exit 3 = no free lane
    if [ -f $E/freya/$b.m1g1.json ]; then progressed=1; continue; fi
    if grep -E "50[234]|Bad Gateway|Gateway Time|Service Unavailable" $E/chain-m1g.log 2>/dev/null | tail -5 | grep -q .; then why="API error (5xx)"; else why="no result"; fi
    if [ "$tries" -ge 1 ]; then touch $ST/$b.abandoned; log "m1g1 $b failed twice ($why): left for rule C"
    else log "m1g1 $b failed ($why): one retry in 5 minutes"; sleep 300; fi
  done
  [ "$progressed" = 0 ] && [ "$(ls $ST/*.abandoned 2>/dev/null | wc -l | tr -d ' ')" -ge $((9 - $(n_done))) ] && break   # nothing left to try
  sleep 60
done

# --- decision ---
if [ "$(n_done)" -ge 9 ]; then
  set -- $(tot p6g1); P=$1; set -- $(tot m1g1); M=$1; N=$2
  [ "$M" -ge "$P" ] && W=m1 || W=p6
  log "DECISION $W  rule G (gate): m1g1 $M/$N vs p6g1 $P over all 9 briefs"; exit 0
fi
log "gate incomplete at $(now) ($(n_done)/9 briefs): rule C (canary) - waiting for the API"
while ! api_ok; do
  [ "$(now)" -ge 1630 ] && { log "DECISION m1  rule D (no further data): the API did not answer by 16:30; gate $(n_done)/9 finished briefs (m1 led: $(tot m1g1 | cut -d" " -f1) vs p6 on the same briefs not recomputed here)"; exit 0; }
  sleep 300
done
IDS=$(grep -v '^#' scripts/canary.txt | tr -s ' \n' ',' | sed 's/^,//; s/,$//')
for attempt in 1 2; do
  scripts/run_cases.sh . m1 m1c --cases "$IDS" 1 >> $LOG 2>&1
  [ "$(ls $E/freya/*.m1c1.json 2>/dev/null | wc -l | tr -d ' ')" -ge 1 ] && break
  [ "$attempt" = 1 ] && { log "canary run failed: one retry in 5 minutes"; sleep 300; }
done
set -- $(tot m1c1); C=$1; CN=$2
if [ "$CN" -ge 20 ] && [ "$C" -ge 19 ]; then W=m1; else W=p6; fi
log "DECISION $W  rule C (canary): m1c1 $C/$CN (needs >= 19/20)$([ "$CN" -lt 20 ] && echo '; fewer than 20 cases ran')"
