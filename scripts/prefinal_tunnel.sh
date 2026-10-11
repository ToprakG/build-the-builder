#!/bin/bash
# 19:50 pre-final check: restart the shared mock bank + a FRESH cloudflared tunnel, then 10 health checks through the public URL.
#   scripts/prefinal_tunnel.sh [--wait]     --wait: sleep until 19:50 first.   Log: out/prefinal_tunnel.log   Exit 0 only if 10/10 answered 200.
# (The final build's self-test starts its own bank + tunnel; this proves a fresh quick tunnel works from this machine right before the final.)
cd "$(dirname "$0")/.." || exit 1
LOG=out/prefinal_tunnel.log; mkdir -p out; log() { echo "$(date +%T) $*" | tee -a $LOG; }
now() { date +%H%M | sed 's/^0*//'; }
[ "${1:-}" = --wait ] && while [ "$(now)" -lt 1950 ]; do sleep 10; done
pkill -f "tcc_shared_bank.py" 2>/dev/null; kill $(lsof -nP -tiTCP:8860 -sTCP:LISTEN) 2>/dev/null; pkill -f "cloudflared tunnel --url http://127.0.0.1:8860" 2>/dev/null; sleep 2
BRIEFS="brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"
nohup .venv310/bin/python scripts/tcc_shared_bank.py --port 8860 $BRIEFS > out/prefinal_bank.log 2>&1 &
for i in $(seq 1 40); do O=$(cat out/template_cc/eval/shared_origin.txt 2>/dev/null); [ -n "$O" ] && [ out/template_cc/eval/shared_origin.txt -nt out/prefinal_bank.log ] && break; sleep 3; done
O=$(cat out/template_cc/eval/shared_origin.txt 2>/dev/null); log "fresh origin: $O"
ok=0
for i in $(seq 1 10); do c=$(curl -s -o /dev/null -m 10 -w '%{http_code}' "$O/openapi.json"); log "check $i/10: HTTP $c"; [ "$c" = 200 ] && ok=$((ok+1)); sleep 3; done
if [ "$ok" = 10 ]; then log "PREFINAL TUNNEL OK (10/10)"; exit 0; fi
log "PREFINAL TUNNEL PROBLEM: $ok/10 answered 200"; exit 1
