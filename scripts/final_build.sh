#!/bin/bash
# One command for the final night: fresh extraction (claude_cli:sonnet) -> compile with the kept flags -> lint -> report.
# Usage: scripts/final_build.sh <brief files...> <tool-api.json> <out-dir>
#   FEATURES=...   kept flag string (default: the features of out/template_cc/p4/brief-a/build_report.json)
#   FINAL_VOTES=3  number of independent extractions; id_decline and attempt_plan[*].cap_at_deadline are settled over them by
#                  builder.template_cc.final_vote (brief-text rule, else majority, else a verifier quote). 1 = a single extraction.
#   CONFIG=...     optional testbed config.json: also prints the policy_critical misses (scoring only; the builder never reads it)
#   RULE_COVERAGE=off      skip the rule-coverage step; RULE_COVERAGE_ENFORCE=1 makes it fail the build (default 0: report only)
# After the compile: unknown_rules.py injects every normative sentence that fits no rule type (verbatim, into its stage's
# prompt) and writes UNKNOWN.md; rule_coverage.py then lists the rules the compiler enforces only in prompt text or not at
# all. Report only: it never fails the build unless RULE_COVERAGE_ENFORCE=1.
# Nothing is sent to Freya (--save none; FREYA_API_KEY is removed from the build's environment).
cd "$(dirname "$0")/.." || exit 2
# --disable f17_x,f18_y : switch flags off for THIS brief only (a misbehaving rule), without touching the compiler
DISABLE=""; REST=()
while [ $# -gt 0 ]; do case "$1" in --disable) DISABLE="$2"; shift 2;; --disable=*) DISABLE="${1#--disable=}"; shift;; *) REST+=("$1"); shift;; esac; done
set -- "${REST[@]}"
[ $# -ge 3 ] || { echo "usage: $0 [--disable flag,flag] <brief files...> <tool-api.json> <out-dir>"; exit 2; }
ARGS=("$@"); OUT=${ARGS[$#-1]}; API=${ARGS[$#-2]}; BRIEFS=("${ARGS[@]:0:$#-2}")
FEATURES=${FEATURES:-$(bash scripts/final_flags.sh . 2>/dev/null || python3 -c "import json;print(','.join(json.load(open('out/template_cc/p4/brief-a/build_report.json'))['features']))")}
{
  FEATURES=$(python3 - "$FEATURES" "$DISABLE" <<'PY'
import sys
sys.path.insert(0, ".")
from builder.template_cc import features as F
on = F.parse(sys.argv[1]) - F.parse(",".join(f for f in sys.argv[2].split(",") if f))
print(",".join(sorted(on)))
PY
)
  [ -n "$DISABLE" ] && echo "disabled for this brief: $DISABLE"
}
mkdir -p "$OUT"; T0=$(date +%s)
echo "features: $FEATURES"
[ -n "${FINAL_BUILD_DRY:-}" ] && exit 0
VOTES=${FINAL_VOTES:-3}; POLICY_ARG=()
#   POLICY=file: skip extraction and the vote, compile this policy.json (self-test repairs; same flags, checked by policy_check)
[ -n "${POLICY:-}" ] && { VOTES=1; POLICY_ARG=(--policy "$POLICY"); }
do_vote() {
if [ "$VOTES" -gt 1 ]; then
  echo "final vote: $VOTES independent extractions"
  PIDS=(); for i in $(seq 1 "$VOTES"); do
    env -u FREYA_API_KEY .venv310/bin/python -m builder.template_cc.run "${BRIEFS[@]}" "$API" --save none --audit off --features "$FEATURES" \
      --slot "${FINAL_SLOT:-final-$(basename "$OUT")}-e$i" --out "$OUT/ext$i" > "$OUT/ext$i.log" 2>&1 & PIDS+=($!)
  done; for p in "${PIDS[@]}"; do wait "$p"; done
  POLS=(); for i in $(seq 1 "$VOTES"); do [ -f "$OUT/ext$i/policy.json" ] && POLS+=("$OUT/ext$i/policy.json"); done
  BARGS=(); for b in "${BRIEFS[@]}"; do BARGS+=(--brief "$b"); done
  rm -f "$OUT/policy.voted.json"
  [ ${#POLS[@]} -ge 2 ] && .venv310/bin/python -m builder.template_cc.final_vote "${POLS[@]}" "${BARGS[@]}" -o "$OUT/policy.voted.json" 2>&1 | tee "$OUT/final_vote.log"
  if [ -f "$OUT/policy.voted.json" ]; then
    POLICY_ARG=(--policy "$OUT/policy.voted.json")
  else echo "final vote: fewer than 2 extractions finished or the vote failed; extracting again in the final build"; fi
fi
# deterministic fixups from explicit brief phrases (id_decline, cap_at_deadline); the single-extraction path applies them inside extract.py
if [ -f "$OUT/policy.voted.json" ]; then
  BARGS2=(); for b in "${BRIEFS[@]}"; do BARGS2+=(--brief "$b"); done
  .venv310/bin/python -m builder.template_cc.policy_fixups "$OUT/policy.voted.json" "${BARGS2[@]}" | tee -a "$OUT/final_vote.log"
fi
}
do_vote
# policy sanity (generic): a degenerate extraction (placeholder bands, empty attempt_plan, generic lines, "not supplied" notes) is extracted once more
if [ -z "${POLICY:-}" ] && [ -f "$OUT/policy.voted.json" ]; then
  if ! .venv310/bin/python scripts/policy_sanity.py "$OUT/policy.voted.json" --out "$OUT/sanity.json" > "$OUT/sanity.txt" 2>&1; then
    echo "policy sanity: DEGENERATE policy, extracting once more:"; sed 's/^/  - /' "$OUT/sanity.txt"
    cp "$OUT/sanity.json" "$OUT/sanity.first.json"; rm -rf "$OUT"/ext[0-9]* "$OUT/policy.voted.json"; do_vote
    if [ -f "$OUT/policy.voted.json" ] && ! .venv310/bin/python scripts/policy_sanity.py "$OUT/policy.voted.json" --out "$OUT/sanity.json" > "$OUT/sanity.txt" 2>&1; then
      echo "DEGENERATE: the policy is still degenerate after the second extraction (the build goes on):"; sed 's/^/  - /' "$OUT/sanity.txt"
    else echo "policy sanity: the second extraction is sane"; fi
  fi
fi
#   FINAL_SAVE=freya: keep FREYA_API_KEY and save to Freya (slot $FINAL_SLOT, default final-<out dir>): the workflow id and agent id go into the trace
if [ "${FINAL_SAVE:-none}" = freya ]; then SAVE_ARGS=(--save freya); UNSET=(); else SAVE_ARGS=(--save none); UNSET=(-u FREYA_API_KEY); fi
env "${UNSET[@]}" .venv310/bin/python -m builder.template_cc.run "${BRIEFS[@]}" "$API" "${SAVE_ARGS[@]}" --audit off --features "$FEATURES" "${POLICY_ARG[@]}" \
  --slot "${FINAL_SLOT:-final-$(basename "$OUT")}" --out "$OUT" > "$OUT/build.log" 2>&1; RC=$?
WALL=$(( $(date +%s) - T0 ))
# the policy that was compiled: the loud line for a degenerate one (also when there was no vote / no second extraction)
if [ -f "$OUT/policy.json" ]; then
  if .venv310/bin/python scripts/policy_sanity.py "$OUT/policy.json" --out "$OUT/sanity.json" > "$OUT/sanity.txt" 2>&1; then echo "policy sanity: ok"
  else echo "DEGENERATE: the compiled policy looks degenerate:"; sed 's/^/  - /' "$OUT/sanity.txt"; fi
fi
BRIEFS_CSV=$(IFS=,; echo "${BRIEFS[*]}") python3 - "$OUT" "$RC" "$WALL" "$API" "${CONFIG:-}" <<'PY'
import json, re, subprocess, sys
from pathlib import Path
out, rc, wall, api, config = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5]
rep = json.loads((out / "build_report.json").read_text()) if (out / "build_report.json").exists() else {}
trace = Path(rep.get("trace", "")) / "trace.txt"
lines = trace.read_text().splitlines() if trace.exists() else []
def sec(l):
    m = re.match(r"(\d\d):(\d\d):(\d\d)", l); return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None
t = [(sec(l), l) for l in lines if sec(l) is not None]
start = t[0][0] if t else 0
ext = [s for s, l in t if "model call" in l and "extract" in l]
lint = next((s for s, l in t if "lint baseline" in l), None)
end = t[-1][0] if t else start
fmt = lambda a, b: f"{(b - a) % 86400}s" if a is not None and b is not None else "?"
print(f"stage extract (readings, verifier, pass 2, fix): {fmt(start, max(ext) if ext else None)}")
print(f"stage compile + lint:                            {fmt(max(ext) if ext else None, lint)}")
print(f"total wall: {wall}s (exit {rc}), status {rep.get('status')}")
print(f"lint: {rep.get('lint')}")
human = []
if rc != 0 or rep.get("status") != "ok": human.append("the build did not finish ok")
if (rep.get("lint") or {}).get("errors"): human.append("lint errors")
if (rep.get("extract") or {}).get("repairs"): human.append("policy defaults were applied: " + "; ".join(rep["extract"]["repairs"])[:200])
if any("policy default" in l for _, l in t): human.append("policy_check could not validate some fields")
print("needed a human:", "; ".join(human) if human else "no")
if config:
    r = subprocess.run(["python3", "scripts/policy_critical.py", str(out / "policy.json"), config, "--api", api] + sum([["--brief", b] for b in __import__("os").environ.get("BRIEFS_CSV", "").split(",") if b], []), capture_output=True, text=True)
    print("policy_critical:", r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[:200])
PY

RC2=0
if [ "${RULE_COVERAGE:-on}" != "off" ] && [ -f "$OUT/workflow.json" ] && [ -f "$OUT/policy.json" ]; then
  echo "--- rule coverage ---"
  env -u FREYA_API_KEY .venv310/bin/python -m builder.template_cc.unknown_rules "${BRIEFS[@]}" "$OUT/policy.json" "$OUT/workflow.json" \
    || echo "unknown_rules failed (exit $?)"
  RULE_COVERAGE_ENFORCE="${RULE_COVERAGE_ENFORCE:-0}" .venv310/bin/python scripts/rule_coverage.py "${BRIEFS[@]}" "$OUT/policy.json" "$OUT/workflow.json" --out "$OUT" --quiet | tee "$OUT/rule_coverage.txt"
  RC2=${PIPESTATUS[0]}
  [ "$RC2" -eq 0 ] || echo "needed a human: rule types the compiler does not enforce in code (see $OUT/rule_coverage.txt, GAPS.md)"
fi
[ "$RC2" -eq 0 ] || exit "$RC2"
[ "$RC" -eq 0 ] || exit "$RC"
