#!/bin/bash
# Run the self-test scenarios of one build on a FREE Freya lane (one eval per workspace at a time, timeout 1800).
#   scripts/selftest_run.sh <build dir> [--cases FILE] [--api FILE] [--only ID,ID] [--results NAME] [--tag T]
# <build dir> holds policy.json + workflow.json (a final_build.sh output). Defaults: cases <dir>/selftest/cases.json, then
# <dir>/../selftest/cases.json; api $SELFTEST_API. Result: <dir>/selftest/results.json (+ transcripts/). Exit 3 = no lane free
# within LANE_WAIT seconds (default 0); exit 124 = the 1800 s timeout.
# Lanes are the same lock dirs as run_cases.sh (out/template_cc/eval/lane{1,2}.lock under the MAIN checkout): lane 1 = this
# checkout's .env key, lane 2 = ../yc-w2/w2_env.sh when it exists. A BACKGROUND run on a lane is stopped after its running brief.
set -u
HERE=$(cd "$(dirname "$0")/.." && pwd)
MAIN=$(cd "$(git -C "$HERE" rev-parse --git-common-dir)/.." && pwd)
[ $# -ge 1 ] || { sed -n 2,9p "$0"; exit 2; }
DIR=$(cd "$1" && pwd); shift
CASES=""; API=${SELFTEST_API:-}; PASS=()
while [ $# -gt 0 ]; do case "$1" in --cases) CASES=$2; shift 2;; --api) API=$2; shift 2;; *) PASS+=("$1"); shift;; esac; done
[ -n "$CASES" ] || for c in "$DIR/selftest/cases.json" "$DIR/../selftest/cases.json"; do [ -f "$c" ] && { CASES=$c; break; }; done
[ -f "$CASES" ] && [ -f "$API" ] && [ -f "$DIR/workflow.json" ] && [ -f "$DIR/policy.json" ] || { echo "need cases ($CASES), api ($API), $DIR/workflow.json, $DIR/policy.json"; exit 2; }
CASES=$(cd "$(dirname "$CASES")" && pwd)/$(basename "$CASES"); API=$(cd "$(dirname "$API")" && pwd)/$(basename "$API")
LOCKS=$MAIN/out/template_cc/eval; mkdir -p "$LOCKS"
TAG="selftest-$(basename "$DIR")"
take() { l=$LOCKS/lane$1.lock
  if [ -d "$l" ] && ! kill -0 "$(cat "$l/pid" 2>/dev/null)" 2>/dev/null; then rm -rf "$l"; fi
  mkdir "$l" 2>/dev/null || return 1
  echo $$ > "$l/pid"; echo "$TAG" > "$l/tag"; return 0; }
lane_ok() { [ "$1" = 1 ]; }   # ONE Freya key: lane 2 (the W2 key) is never used in the final path
preempt() { for n in ${SELFTEST_LANES:-1}; do l=$LOCKS/lane$n.lock
    [ -e "$l/background" ] || continue
    chain=$(pgrep -P "$(cat "$l/pid")" -f "eval_chain\.sh" | head -1)
    echo "lane $n holds a background run: stopping it after its running brief" >&2
    [ -n "$chain" ] && LOG=/dev/stderr "$MAIN/scripts/stop_chain_gracefully.sh" "$chain"
    for i in $(seq 1 300); do [ -d "$l" ] || { echo $n; return 0; }; sleep 5; kill -0 "$(cat "$l/pid" 2>/dev/null)" 2>/dev/null || { rm -rf "$l"; echo $n; return 0; }; done
  done; return 1; }
LANE=${LANE:-}; WAIT_UNTIL=$(( $(date +%s) + ${LANE_WAIT:-0} ))
while :; do
  if [ -n "$LANE" ]; then take "$LANE"
  else
    for n in ${SELFTEST_LANES:-1}; do lane_ok $n && take $n && { LANE=$n; break; }; done
    [ -n "$LANE" ] || { n=$(preempt | tail -1); [[ "$n" =~ ^[12]$ ]] && take "$n" && LANE=$n; }
  fi
  [ -n "$LANE" ] && [ "$(cat "$LOCKS/lane$LANE.lock/pid" 2>/dev/null)" = $$ ] && break
  [ -n "${FORCED_LANE:-}" ] || true
  [ "$(date +%s)" -ge "$WAIT_UNTIL" ] && { echo "no lane free"; exit 3; }
  sleep 30
done
trap 'rm -rf "$LOCKS/lane$LANE.lock"' EXIT
echo "$(date +%H:%M:%S) lane $LANE, $DIR, $(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))))" "$CASES") scenarios"
cd "$HERE" || exit 2
set -a; [ -f .env ] && . ./.env; set +a
unset FREYA_API_KEY_W2; export FREYA_STATE_FILE=$MAIN/.freya-agents.ali.json
export FREYA_SIM_CONCURRENCY=10 SELFTEST_PERSONA_DIR=$MAIN/out/template_cc/eval/freya
timeout ${SELFTEST_TIMEOUT:-1800} .venv310/bin/python scripts/selftest_run.py --out-dir "$DIR" --cases "$CASES" --api "$API" ${PASS[@]+"${PASS[@]}"}
RC=$?
[ $RC -eq 124 ] && echo "selftest_run: TIMEOUT after 1800 s"
exit $RC
