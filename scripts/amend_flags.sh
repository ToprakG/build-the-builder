#!/bin/bash
# Amend the final flag list, flags only (no code through this door):   scripts/amend_flags.sh "<flag string>"        (DRY=1: everything except git, FINAL_FLAGS.txt untouched)
#  1. refuses after 19:45 (AMEND_FAKE_NOW=HHMM for tests) and when tracked files other than FINAL_FLAGS.txt differ from the final-submit tag (no code changes)
#  2. parses the list with the code build.py / run.py use (builder.template_cc.features.parse); an unknown name refuses
#  3. compiles + lints all 9 briefs from the m2b policies with exactly that list (no claude, no Freya) in out/template_cc/amend_check; lint must be 0 on all 9 and
#     opening_resume must be absent from every workflow (f18_opening_fallback stays off)
#  4. rewrites FINAL_FLAGS.txt as "<flags>,-f18_opening_fallback" (the permanent ending is kept; one given in the argument is not doubled)
#  5. commits ONLY FINAL_FLAGS.txt, moves the tag final-submit to that commit, runs the secret scan, pushes main + the tag to origin (never to firstday)
cd "$(dirname "$0")/.." || exit 1
NEW=${1:?usage: scripts/amend_flags.sh "<flag string>"}
now() { if [ -n "${AMEND_FAKE_NOW:-}" ]; then echo "$AMEND_FAKE_NOW" | sed 's/^0*//'; else date +%H%M | sed 's/^0*//'; fi; }
LOG=out/amend_flags.log; mkdir -p out; log() { echo "$(date +%T) $*" | tee -a $LOG; }
fail() { log "AMEND REFUSED: $*"; exit 2; }
[ -z "${DRY:-}" ] && [ "$(now)" -ge 1945 ] && fail "it is after 19:45 (the window closed)"
[ -n "${DRY:-}" ] && [ "$(now)" -ge 1945 ] && fail "it is after 19:45 (DRY: the same rule)"
NEW=$(echo "$NEW" | tr -d ' \n'); NEW=${NEW%,-f18_opening_fallback}; NEW=${NEW#,}
[ -n "$NEW" ] || fail "empty flag list"
# 1. no code changes: tracked files differ from the tag only in FINAL_FLAGS.txt
if git rev-parse -q --verify refs/tags/final-submit > /dev/null; then
  code=$(git diff --name-only final-submit -- . ':!FINAL_FLAGS.txt' ':!traces' | head -3 | tr '\n' ' ')
  [ -z "$code" ] || fail "tracked files changed since the tag final-submit (code is not amended through this script): $code"
fi
dirty=$(git status --porcelain --untracked-files=no | grep -v ' traces/' | grep -v ' FINAL_FLAGS.txt$' | head -3 | tr '\n' ' ')
[ -z "$dirty" ] || fail "uncommitted tracked changes: $dirty"
# 2. same parser as the build
CHK=$(.venv310/bin/python - "$NEW" <<'PY' 2>&1
import sys
sys.path.insert(0, ".")
from builder.template_cc import features as F
try:
    on = F.parse(sys.argv[1] + ",-f18_opening_fallback")
except ValueError as e:
    print("PARSE ERROR:", e); sys.exit(1)
print(len(on), "flags:", ",".join(sorted(on)))
PY
) || fail "$CHK"
log "flag list parses: $CHK" | cut -c1-200
# 3. compile + lint all 9 briefs from the m2b policies with exactly this list
FEAT="$NEW,-f18_opening_fallback"; D=out/template_cc/amend_check; rm -rf $D; mkdir -p $D
one() { b=$1
  case $b in brief-a) in="briefs/brief-a.md tools/openapi.json";; brief-b) in="briefs/brief-a.md briefs/brief-b.md tools/openapi.json";; *) in="testbed/fake_briefs/$b/brief.md testbed/fake_briefs/$b/tools.openapi.json";; esac
  env -u FREYA_API_KEY .venv310/bin/python -m builder.template_cc.run $in --save none --audit off --policy out/template_cc/m2b/$b/policy.json --features "$FEAT" --slot amend-$b --out $D/$b > $D/$b.log 2>&1 || echo "$b BUILD FAILED"; }
export -f one; export FEAT D
printf '%s\n' brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes | xargs -P 3 -I{} bash -c 'one {}'
BAD=$(.venv310/bin/python - <<'PY'
import json
bad = []
for b in "brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes".split():
    try:
        r = json.load(open(f"out/template_cc/amend_check/{b}/build_report.json"))
        w = json.load(open(f"out/template_cc/amend_check/{b}/workflow.json")); w = w.get("workflow", w)
    except OSError:
        bad.append(f"{b}: no build"); continue
    if r.get("status") != "ok" or r["lint"]["errors"] != 0:
        bad.append(f"{b}: status {r.get('status')} lint {r['lint']['errors']}")
    if "opening_resume" in w["nodes"]:
        bad.append(f"{b}: opening_resume node present")
print("; ".join(bad))
PY
)
[ -z "$BAD" ] || fail "the new list does not compile clean: $BAD"
log "compile + lint: all 9 briefs ok, lint 0, no opening_resume"
# 4. rewrite
OUTF=FINAL_FLAGS.txt; [ -n "${DRY:-}" ] && { mkdir -p out/amend_dry; OUTF=out/amend_dry/FINAL_FLAGS.txt; }
OLD=$(cat FINAL_FLAGS.txt 2>/dev/null || cat M1_FLAGS.txt | tr -d '\n')
echo "$NEW,-f18_opening_fallback" > "$OUTF"
log "$OUTF: ${OLD:0:70}... -> $(cut -c1-70 "$OUTF")..."
if [ -n "${DRY:-}" ]; then log "DRY: stopping here (no commit, no tag, no push)"; exit 0; fi
# 5. commit only that file, move the tag, scan, push
git add FINAL_FLAGS.txt && git commit -q -m "amend FINAL_FLAGS.txt (flags only)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" FINAL_FLAGS.txt || fail "git commit failed"
git fetch origin -q 2>&1 | tail -1
git merge-base --is-ancestor origin/main HEAD || fail "origin/main would not fast-forward (committed locally, NOT pushed)"
.venv310/bin/python scripts/secret_scan.py origin/main..main | tee -a $LOG || fail "secret scan found something (committed locally, NOT pushed)"
git tag -f -a final-submit -m "final submission (flags amended $(date +%H:%M)): $(cut -c1-60 FINAL_FLAGS.txt)..." 
git push origin main 2>&1 | tail -1 | tee -a $LOG
git push -f origin refs/tags/final-submit 2>&1 | tail -1 | tee -a $LOG
log "AMENDED: final-submit = $(git rev-parse --short HEAD), pushed to origin; re-run the dress check if the flag list changed materially"
