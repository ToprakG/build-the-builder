#!/bin/bash
# SECOND dress run (the real one): python3 builder/build.py mock/rehearsal-tr/FreyaBank-Test <mock_url>  with FINAL_FLAGS + FINAL_MODE.txt (patcher_off).
#   scripts/dress_run2.sh <mock_url>            live Worker tenant URL (https://<worker>/rehearsal-tr): tool URLs must be <mock_url>/v1/...
#   scripts/dress_run2.sh --local               our local bank instead (the folder is copied and given api/openapi.json = tools/openapi.json; no live mock)
# Result: out/dress2/report.txt (scripts/dress_check_tr.py). Freeze waits for THIS verdict.
cd "$(dirname "$0")/.." || exit 1
[ -f FINAL_FLAGS.txt ] || { echo "no FINAL_FLAGS.txt"; exit 2; }
set -a; [ -f .env ] && . ./.env; set +a; unset FREYA_API_KEY_W2
export LANE_WAIT=${LANE_WAIT:-300}
rm -rf out/dress2; mkdir -p out/dress2
if [ "${1:-}" = --local ]; then
  MOCK=http://127.0.0.1:8860; CASE=out/dress2/case; cp -R mock/rehearsal-tr/FreyaBank-Test $CASE; mkdir -p $CASE/api; cp tools/openapi.json $CASE/api/openapi.json; EXPECT=""; MODE=local-bank
else
  MOCK=${1:?mock_url or --local}; CASE=mock/rehearsal-tr/FreyaBank-Test; EXPECT="${MOCK%/}/v1/"; MODE=live-mock
fi
{ echo "$(date +%T) dress run 2 ($MODE) from commit $(git rev-parse --short HEAD), FINAL_MODE=$(cat FINAL_MODE.txt)"; echo "FINAL_FLAGS.txt: $(cat FINAL_FLAGS.txt)"
  echo "command: python3 builder/build.py $CASE $MOCK --members dress-test --max-minutes ${DRESS_MIN:-22} --out out/dress2/build"; } > out/dress2/report.txt
t=$(date +%s)
python3 builder/build.py $CASE "$MOCK" --members dress-test --max-minutes ${DRESS_MIN:-22} --out out/dress2/build > out/dress2/build.out 2>&1; rc=$?
tail -1 out/dress2/build.out > out/dress2/build_py.json
echo "$(date +%T) build.py: exit $rc, $(( $(date +%s) - t )) s: $(cut -c1-300 out/dress2/build_py.json)" >> out/dress2/report.txt
.venv310/bin/python scripts/dress_check_tr.py out/dress2 FINAL_FLAGS.txt "$MODE" "$EXPECT" >> out/dress2/report.txt 2>&1
echo "$(date +%T) done" >> out/dress2/report.txt
