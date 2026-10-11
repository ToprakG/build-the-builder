#!/bin/bash
# One reserved fix-verification slot: scripts/run_slot.sh out/template_cc/eval/reserved_slot_<name>.env      (DRY=1: validate and PRINT, run nothing)
# Env file: SET=<compiled set under out/template_cc/>  PFX=<tag prefix ending in g>  BRIEFS="<b1> <b2>"  [FAMILY="<target case family>"]
# BRIEFS may be short ids (f3, f6, brief-a) or full names (f3-kestrel); they are resolved here. An invalid slot (missing set/workflow, tag not ending in g,
# unknown brief) is REFUSED with a message instead of waiting for it forever.
cd "$(dirname "$0")/.." || exit 1
E=out/template_cc/eval; F=${1:?env file}
. "$F"
ALL="brief-a brief-b $(ls testbed/fake_briefs | tr '\n' ' ')"
FULL=""
for t in $BRIEFS; do
  full=""
  for a in $ALL; do if [ "$a" = "$t" ] || [ "${a#"$t"-}" != "$a" ]; then full=$a; break; fi; done
  [ -n "$full" ] || { echo "REFUSED: unknown brief '$t' in $F"; exit 2; }
  [ -f "out/template_cc/$SET/$full/workflow.json" ] || { echo "REFUSED: out/template_cc/$SET/$full/workflow.json does not exist"; exit 2; }
  FULL="$FULL $full"
done
FULL=${FULL# }
case $PFX in *g) ;; *) echo "REFUSED: PFX '$PFX' must end in g (the tag <PFX>1 then ends in g1: sealed GATE tag)"; exit 2;; esac
if [ -n "${DRY:-}" ]; then
  echo "DRY: set $SET, tag ${PFX}1, briefs: $FULL, family: ${FAMILY:-any}"
  echo "DRY: GATE_END=1925 GATE_BRIEFS=\"$FULL\" scripts/gate_set.sh $SET $PFX"
  for b in $FULL; do
    echo "DRY:   gate_set runs per brief:  ONLY_BRIEFS=$b scripts/run_cases.sh . $SET $PFX --skip 10 --limit 10 1"
    echo "DRY:   which runs (eval_chain.sh): timeout 1800 .venv310/bin/python scripts/tcc_eval_freya.py --brief $b --limit 10 --or-budget 0 --tag ${PFX}1 --shared-origin <origin> --skip 10 --limit 10"
  done
  exit 0
fi
GATE_END=1925 GATE_BRIEFS="$FULL" scripts/gate_set.sh "$SET" "$PFX"
