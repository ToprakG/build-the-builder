#!/bin/bash
# The dress rehearsal with the FINAL interface:  python3 builder/build.py <case_folder> <mock_url>  on a fake Turkish-style case folder
# (brief-a split into BRIEF.md + docx + xlsx + README + SSS + api/openapi.json, scripts/make_dress_case.py), mock_url = our local bank. REAL run (Freya save + self-test + export).
# From the frozen flags (FINAL_FLAGS.txt). Then scripts/dress_check_case.py -> out/dress/report.txt.
#   scripts/dress_run.sh [mock_url]      DRESS_MIN (default 22) = the build budget in minutes
cd "$(dirname "$0")/.." || exit 1
[ -f FINAL_FLAGS.txt ] || { echo "no FINAL_FLAGS.txt"; exit 2; }
MOCK=${1:-http://127.0.0.1:8860}
set -a; [ -f .env ] && . ./.env; set +a; unset FREYA_API_KEY_W2
export LANE_WAIT=${LANE_WAIT:-300}
rm -rf out/dress; mkdir -p out/dress
.venv310/bin/python scripts/make_dress_case.py out/dress/case > /dev/null
{ echo "$(date +%T) dress run from commit $(git rev-parse --short HEAD) ($(git status --porcelain --untracked-files=no | grep -v ' traces/' | wc -l | tr -d ' ') tracked changes)"; echo "FINAL_FLAGS.txt: $(cat FINAL_FLAGS.txt)"
  echo "command: python3 builder/build.py out/dress/case $MOCK --members dress-test --max-minutes ${DRESS_MIN:-22} --out out/dress/build"; } > out/dress/report.txt
t=$(date +%s)
python3 builder/build.py out/dress/case "$MOCK" --members dress-test --max-minutes ${DRESS_MIN:-22} --out out/dress/build > out/dress/build.out 2>&1; rc=$?
tail -1 out/dress/build.out > out/dress/build_py.json
echo "$(date +%T) build.py: exit $rc, $(( $(date +%s) - t )) s: $(cut -c1-300 out/dress/build_py.json)" >> out/dress/report.txt
.venv310/bin/python scripts/dress_check_case.py out/dress FINAL_FLAGS.txt >> out/dress/report.txt 2>&1
echo "$(date +%T) done" >> out/dress/report.txt
