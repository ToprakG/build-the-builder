#!/bin/bash
# Unattended eval queue: f149b, p2, p3, p4 (x2 each) as soon as the Freya workspace is free.
# Every 5 minutes until $QUEUE_UNTIL (default 09:00) or until all 72 result files exist: check the tunnel (restart bank+tunnel if dead), start the next
# unfinished set via scripts/eval_chain.sh (which skips finished briefs and stops itself after 10 min of "another simulation is
# running"), and log one line. Never runs two evals at once.
cd "$(dirname "$0")/.." || exit 2
set -a; [ -f .env ] && . ./.env; set +a
# a fresh workspace (a rotated Freya key) must not reuse the saved agent/slot ids or personas of the old one
export FREYA_STATE_FILE=${FREYA_STATE_FILE:-.freya-agents.newkey.json}
OUT=out/template_cc/eval; LOG=$OUT/night_queue.log
BRIEFS="brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"
# set:tag-prefix:reps. Order: p4 (all flags, cached policies), p4x (the same flags on fresh x1 policies), p5 (p4x + intents), then p2, p3.
# The baseline f149br was run once already (f149br1 complete).
QUEUE="p6:p6r:2 p10:p10r:2 p10x:p10xr:2 p11:p11r:2 p2:p2r:2 p3:p3r:2"  # p8 = p6 + the fixes of the p6r1 run; p8x = x1 policies; p9 = p8x + intents
TOTAL=108
DEADLINE=$(date -j -f "%Y-%m-%d %H:%M" "$(date +%Y-%m-%d) ${QUEUE_UNTIL:-09:00}" +%s)
[ "$DEADLINE" -le "$(date +%s)" ] && DEADLINE=$((DEADLINE + 86400))
BLOCKED_SINCE=0; SWITCHED=0
log() { echo "$(date +%m-%d\ %H:%M:%S) $*" >> "$LOG"; }

qset() { echo "${1%%:*}"; }; qpre() { x=${1#*:}; echo "${x%%:*}"; }; qreps() { echo "${1##*:}"; }
done_count() { n=0; for q in $QUEUE; do p=$(qpre $q); for r in $(seq 1 $(qreps $q)); do for b in $BRIEFS; do [ -f "$OUT/freya/$b.$p$r.json" ] && n=$((n+1)); done; done; done; echo $n; }
set_done() { p=$(qpre $1); for r in $(seq 1 $(qreps $1)); do for b in $BRIEFS; do [ -f "$OUT/freya/$b.$p$r.json" ] || return 1; done; done; return 0; }
# a REAL process: the pattern is anchored at the start of the command line, so a shell whose command text merely mentions the script
# (like the one that launched this queue) does not count
running() { pgrep -f "^(/bin/bash )?scripts/eval_chain\.sh " > /dev/null || pgrep -f "^[^ ]*python[^ ]* scripts/tcc_eval_freya\.py" > /dev/null; }
tunnel_ok() { o=$(cat $OUT/shared_origin.txt 2>/dev/null); [ -n "$o" ] && curl -s -m 15 "$o/brief-a/openapi.json" | grep -q '"openapi"'; }

restart_bank() {
  log "tunnel dead: restarting the bank and tunnel"
  pkill -f "^(/bin/bash )?scripts/eval_chain\.sh " 2>/dev/null; pkill -f "^[^ ]*python[^ ]* scripts/tcc_eval_freya\.py" 2>/dev/null
  # only what this project started: the shared bank script, its cloudflared, the ssh tunnel to 8860, and whatever holds port 8860
  pkill -f "scripts/tcc_shared_bank.py" 2>/dev/null; pkill -f "cloudflared tunnel --url http://127.0.0.1:8860" 2>/dev/null
  pkill -f "ssh .* -R 80:localhost:8860 " 2>/dev/null; sleep 2
  for pid in $(lsof -ti tcp:8860 2>/dev/null); do kill "$pid" 2>/dev/null; done; sleep 2
  : > /tmp/sharedbank.log
  nohup .venv310/bin/python scripts/tcc_shared_bank.py --port 8860 $BRIEFS > /tmp/sharedbank.log 2>&1 < /dev/null &
  for i in $(seq 1 18); do grep -qE "ORIGIN|ERROR|Traceback" /tmp/sharedbank.log && break; sleep 10; done
  if grep -q "^ORIGIN" /tmp/sharedbank.log; then log "new origin $(grep ^ORIGIN /tmp/sharedbank.log | tail -1)"; return; fi
  if curl -s -m 10 http://127.0.0.1:8860/brief-a/openapi.json | grep -q '"openapi"'; then
    log "no quick tunnel; trying localhost.run"
    : > /tmp/lhr.log
    nohup ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:localhost:8860 nokey@localhost.run > /tmp/lhr.log 2>&1 < /dev/null &
    for i in $(seq 1 12); do u=$(grep -o 'https://[a-z0-9]*\.lhr\.life' /tmp/lhr.log | tail -1); [ -n "$u" ] && break; sleep 10; done
    [ -n "$u" ] && { echo "$u" > $OUT/shared_origin.txt; log "new origin $u (localhost.run)"; return; }
  fi
  log "bank/tunnel restart FAILED (see /tmp/sharedbank.log)"
}

log "night queue start (deadline $(date -r $DEADLINE +%H:%M)), done $(done_count)/$TOTAL"
while [ "$(date +%s)" -lt "$DEADLINE" ] && [ "$(done_count)" -lt "$TOTAL" ]; do
  tunnel_ok || restart_bank
  if running; then
    log "done $(done_count)/$TOTAL: a chain is running"
  else
    NEXT=""; for q in $QUEUE; do set_done $q || { NEXT=$q; break; }; done
    if [ -z "$NEXT" ]; then break; fi
    S=$(qset $NEXT); P=$(qpre $NEXT); R=$(qreps $NEXT)
    if [ -f "$OUT/chain-$P.log" ] && tail -n 1 "$OUT/chain-$P.log" | grep -q "another simulation"; then
      [ "$BLOCKED_SINCE" -eq 0 ] && BLOCKED_SINCE=$(date +%s)
      MIN=$(( ($(date +%s) - BLOCKED_SINCE) / 60 ))
      log "done $(done_count)/$TOTAL: blocked (another simulation) for $MIN min; trying $S again"
      if [ -n "$FREYA_API_KEY_ALT" ] && [ "$MIN" -ge 45 ] && [ "$SWITCHED" -eq 0 ]; then
        export FREYA_API_KEY=$FREYA_API_KEY_ALT FREYA_STATE_FILE=.freya-agents-alt.json; SWITCHED=1
        log "SWITCHED to FREYA_API_KEY_ALT (state file .freya-agents-alt.json)"
      fi
    else
      BLOCKED_SINCE=0
      log "done $(done_count)/$TOTAL: starting chain $S $P x$R"
    fi
    nohup scripts/eval_chain.sh "$S" "$P" $R >> "$OUT/night_chains.out" 2>&1 < /dev/null &
  fi
  sleep 300
done
log "night queue end: done $(done_count)/$TOTAL"
