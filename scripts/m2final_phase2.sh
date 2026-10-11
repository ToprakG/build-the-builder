#!/bin/bash
# f3 regression isolation (m2final f3 = 3/10, 0 infra errors): after the running brief ends and a fresh tunnel passes 10 checks, leave-one-out on f3,
# recompiled from the m2final f3 policy, in this order, stopping at the first variant that scores >= 8 (that flag is the culprit):
#   f3m22g1 = m2final -f22_forbidden_disclosure   f3m29g1 = -f29_scam_open   f3m28g1 = -f28_tool_failure
cd "$(dirname "$0")/.." || exit 1
E=out/template_cc/eval; L=$E/m2final_phase2.log; log() { echo "$(date +%T) $*" | tee -a $L; }
while pgrep -f "tcc_eval_freya.py" > /dev/null; do sleep 10; done
log "no eval running: restarting bank + fresh tunnel"
[ -n "${SKIP_RESTART:-}" ] || scripts/prefinal_tunnel.sh >> $L 2>&1 || { log "fresh tunnel failed its 10 checks: STOP (tell S0)"; exit 3; }
for v in "f3m28:f28_tool_failure" "f3m22:f22_forbidden_disclosure" "f3m29:f29_scam_open"; do   # order: f28 first (S3: f22/f29 nodes never entered on f3 DEV), then f22, f29 if time
  S=${v%%:*}; F=${v##*:}
  log "$S = m2final -$F on f3"
  scripts/recompile_set.sh m2final $S "-$F" f3-kestrel >> $L 2>&1
  GATE_END=1925 GATE_BRIEFS="f3-kestrel" scripts/gate_set.sh $S ${S}g
  P=$(python3 -c "import json;print(json.load(open('$E/freya/f3-kestrel.${S}g1.json'))['passed'])" 2>/dev/null)
  log "$S (m2final -$F) f3: ${P:-no result}/10"
  [ -n "$P" ] && [ "$P" -ge 8 ] && log "$F: f3 back to $P/10 without it (a candidate culprit)"
done
log "no variant reached 8: the isolation found no single culprit among f22/f29/f28"
