#!/bin/bash
# Run chosen cases of a set on a FREE Freya lane (one eval per workspace at a time) and print the pass table.
#   scripts/run_cases.sh <worktree> <set> <tag-prefix> (--cases ID,ID,... | --skip N --limit M) <reps>
# Results are tagged <tag-prefix><rep> (p6g 1 -> p6g1). A tag ending in g<n> or t<n> is SEALED (GATE/TEST): only pass/fail and the
# failed-criterion category reach out/template_cc/eval/freya/, the rest goes to out/template_cc/eval/sealed/.
# Lanes: 1 = the main key + bank 8860 (this checkout's .env), 2 = the W2 key + bank 8861 (../yc-w2/w2_env.sh). Lock dirs
# out/template_cc/eval/lane{1,2}.lock (a lane with out/template_cc/eval/lane<n>.disabled is never used); if both are busy this exits 3 without starting anything. LANE=1|2 forces a lane.
set -u
export FREYA_SIM_CONCURRENCY=10  # one wave per 10-case brief; never above 10
MAIN=$(cd "$(dirname "$0")/.." && pwd)
[ $# -ge 6 ] || { sed -n 2,9p "$0"; exit 2; }
WT=$(cd "$1" && pwd); SET=$2; PREFIX=$3; shift 3
ARGS=("$@"); REPS=${ARGS[$#-1]}; ARGS=("${ARGS[@]:0:$#-1}")
EVAL_ARGS="${ARGS[*]}"; CHAIN_BRIEFS=""
if [ "${ARGS[0]:-}" = "--cases" ]; then
  for id in $(echo "${ARGS[1]}" | tr ',' ' '); do
    case $id in A-*) b=brief-a;; B-*) b=brief-b;; F[1-7]-*) n=${id:1:1}; b=$(ls "$MAIN/testbed/fake_briefs" | grep "^f$n-");; *) echo "unknown case id $id"; exit 2;; esac
    case " $CHAIN_BRIEFS " in *" $b "*) ;; *) CHAIN_BRIEFS="$CHAIN_BRIEFS $b";; esac
  done
fi
[ -n "${ONLY_BRIEFS:-}" ] && CHAIN_BRIEFS=" $ONLY_BRIEFS"   # ONLY_BRIEFS="f2-ostral": run just these briefs of a --skip/--limit run
LOCKS=$MAIN/out/template_cc/eval
take() { l=$LOCKS/lane$1.lock
  [ -e "$LOCKS/lane$1.disabled" ] && return 1   # lane$1.disabled: nothing is routed to that lane
  if [ -d "$l" ] && ! kill -0 "$(cat "$l/pid" 2>/dev/null)" 2>/dev/null; then rm -rf "$l"; fi   # stale lock
  mkdir "$l" 2>/dev/null || return 1
  echo $$ > "$l/pid"; echo "$PREFIX" > "$l/tag"; [ -n "${BACKGROUND:-}" ] && touch "$l/background"; return 0; }
# a targeted run always gets a lane first: a BACKGROUND run (the p6 GATE) on a busy lane is stopped after its running brief
preempt() { for n in 1 2; do l=$LOCKS/lane$n.lock
    [ -e "$LOCKS/lane$n.disabled" ] && continue
    [ -e "$l/background" ] || continue
    chain=$(pgrep -P "$(cat "$l/pid")" -f "eval_chain\.sh" | head -1)
    echo "lane $n holds a background run (tag $(cat "$l/tag" 2>/dev/null)): stopping it after its running brief"
    [ -n "$chain" ] && LOG=/dev/stdout "$MAIN/scripts/stop_chain_gracefully.sh" "$chain"
    for i in $(seq 1 300); do [ -d "$l" ] || { echo $n; return 0; }; sleep 5; kill -0 "$(cat "$l/pid" 2>/dev/null)" 2>/dev/null || { rm -rf "$l"; echo $n; return 0; }; done
  done; return 1; }
LANE=${LANE:-}
if [ -n "$LANE" ]; then take "$LANE" || { echo "lane $LANE is busy"; exit 3; }
else
  take 1 && LANE=1 || { take 2 && LANE=2; } || {
    if [ -z "${BACKGROUND:-}" ]; then n=$(preempt | tail -1); [[ "$n" =~ ^[12]$ ]] && take "$n" && LANE=$n; fi; }
  [ -n "$LANE" ] || { echo "both lanes are busy"; exit 3; }
fi
trap 'rm -rf "$LOCKS/lane$LANE.lock"' EXIT
echo "lane $LANE, worktree $WT, set $SET, tag $PREFIX x$REPS, args: ${EVAL_ARGS:-<none>}"
if [ "$LANE" = 1 ]; then
  export LANE_ENV="" ORIGIN_OVERRIDE=$(cat "${ORIGIN_FILE:-$MAIN/out/template_cc/eval/shared_origin.txt}") FREYA_STATE_FILE=$MAIN/.freya-agents.ali.json   # ORIGIN_FILE: another bank (final_eval.sh)
  PSRC=$MAIN/out/template_cc/eval/freya
else
  export LANE_ENV=$MAIN/../yc-w2/w2_env.sh ORIGIN_OVERRIDE=$(cat "$MAIN/../yc-w2/out/template_cc/eval/shared_origin.txt")
  PSRC=$MAIN/../yc-w2/out/template_cc/eval/freya
fi
mkdir -p "$WT/out/template_cc/eval/freya"
cp -n "$PSRC"/personas*.json "$WT/out/template_cc/eval/freya/" 2>/dev/null   # personas are per workspace: do not recreate them
[ -d "$WT/out/template_cc/$SET" ] || cp -R "$MAIN/out/template_cc/$SET" "$WT/out/template_cc/$SET" || { echo "no set $SET"; exit 2; }
cd "$WT" || exit 2
scripts/use_workflows.sh "$SET" > /dev/null || { echo "use_workflows failed"; exit 2; }
export EVAL_ARGS CHAIN_BRIEFS
[ -n "$CHAIN_BRIEFS" ] || unset CHAIN_BRIEFS
scripts/eval_chain.sh - "$PREFIX" "$REPS" > /dev/null 2>&1
echo "--- results ($PREFIX)"
python3 - "$WT" "$PREFIX" "$REPS" "$MAIN" <<'PY'
import glob, json, shutil, sys
from pathlib import Path
wt, pre, reps, main = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
tot = n = 0
for r in range(1, reps + 1):
    for f in sorted((wt / "out/template_cc/eval/freya").glob(f"*.{pre}{r}.json")):
        d = json.loads(f.read_text()); tot += d["passed"]; n += d["cases"]
        fails = [x["case"] + ("(" + ",".join(x["failed"]) + ")" if d.get("sealed") else "") for x in d["rows"] if not x["pass"]]
        print(f"{d['brief']:16s} {pre}{r}  {d['passed']}/{d['cases']}  fails: {' '.join(fails) or '-'}")
        if wt != main:
            for src, dst in ((f, main / "out/template_cc/eval/freya"), (wt / "out/template_cc/eval/sealed" / f.name, main / "out/template_cc/eval/sealed")):
                if src.exists():
                    dst.mkdir(parents=True, exist_ok=True)
                    if not (dst / f.name).exists():
                        shutil.copy(src, dst / f.name)
print(f"TOTAL {tot}/{n}")
PY
