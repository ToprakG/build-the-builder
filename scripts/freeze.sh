#!/bin/bash
# Freeze: tag the submitted commit `final-submit` and push main + the tag to origin (NEVER to the firstday remote).
#   scripts/freeze.sh            checks, then tags and pushes
#   scripts/freeze.sh --wait     waits until 20:40 and until the dress run has a verdict (gives up at 20:55), then does the same
# Preconditions: FINAL_FLAGS.txt exists; out/dress2/report.txt says "VERDICT: ALL OK"; no tracked file is modified (traces/ ignored); the secret scan of the push is clean.
cd "$(dirname "$0")/.." || exit 1
now() { date +%H%M | sed 's/^0*//'; }
LOG=out/freeze.log; mkdir -p out; log() { echo "$(date +%T) $*" | tee -a $LOG; }
if [ "${1:-}" = --wait ]; then
  while [ "$(now)" -lt 2040 ]; do sleep 15; done
  while ! grep -q "VERDICT" out/dress2/report.txt 2>/dev/null; do [ "$(now)" -ge 2055 ] && { log "FREEZE NOT DONE: no dress-run verdict by 20:55"; exit 3; }; sleep 15; done
fi
[ -f FINAL_FLAGS.txt ] || { log "FREEZE NOT DONE: no FINAL_FLAGS.txt"; exit 2; }
grep -q "VERDICT: ALL OK" out/dress2/report.txt 2>/dev/null || { log "FREEZE NOT DONE: dress run is not ALL OK ($(grep VERDICT out/dress2/report.txt 2>/dev/null))"; exit 2; }
dirty=$(git status --porcelain --untracked-files=no | grep -v ' traces/' | head -5)
[ -z "$dirty" ] || { log "FREEZE NOT DONE: tracked files modified: $(echo $dirty | tr '\n' ' ')"; exit 2; }
git fetch origin -q 2>&1 | tail -1
if git merge-base --is-ancestor origin/main HEAD; then PUSH_RANGE=origin/main..main; else log "FREEZE NOT DONE: origin/main is not an ancestor of main (would not fast-forward)"; exit 2; fi
.venv310/bin/python scripts/secret_scan.py $PUSH_RANGE | tee -a $LOG || { log "FREEZE NOT DONE: the secret scan found something"; exit 4; }
HEAD_SHA=$(git rev-parse HEAD)
if git rev-parse -q --verify refs/tags/final-submit > /dev/null; then
  [ "$(git rev-parse final-submit^{commit})" = "$HEAD_SHA" ] || { log "FREEZE NOT DONE: tag final-submit already exists on another commit"; exit 2; }
else
  git tag -a final-submit -m "final submission: FINAL_FLAGS.txt $(tr -d '\n' < FINAL_FLAGS.txt | cut -c1-60)..., dress run ALL OK" || exit 2
fi
git push origin main 2>&1 | tail -1 | tee -a $LOG
git push origin final-submit 2>&1 | tail -1 | tee -a $LOG
log "FROZEN: final-submit = $(git rev-parse --short HEAD) pushed to origin ($(git remote get-url origin | sed -E 's#//[^@/]*@#//#'))"
