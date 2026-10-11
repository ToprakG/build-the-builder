#!/bin/zsh
# Compile the 9 testbed workflows from the cached policies (no LLM, no Freya) into out/template_cc/<set>/<brief>/
# and print lint errors per brief. Then install them for the eval with: scripts/use_workflows.sh <set>
# Usage: scripts/build_set.sh <set-name> <features>      e.g. scripts/build_set.sh s23 f1,f2,f3,f4,f9,f11_promise_typed,f12_slot_summary
set -e
cd "$(dirname "$0")/.."
SET=${1:?set name}; F=${2:?features}
# STANDING RULE: sets m1* use the flags in M1_FLAGS.txt (the argument is ignored for them); S2 may remove a dropped flag from that file.
case $SET in m1*) [ -f M1_FLAGS.txt ] || { echo "M1_FLAGS.txt missing"; exit 1; }; F=$(tr -d ' \n' < M1_FLAGS.txt); case $SET in m1f26) F="$F,f26_amount_assembly";; m1b) F="$F,-f18_opening_fallback";; esac; echo "set $SET: flags from M1_FLAGS.txt${F##*f25_dispute_wins}";; esac
PY=${PY:-.venv310/bin/python}
# STANDING RULE: sets m1* (m1, m1x) are built ONLY from out/template_cc/policies_fixed/ (policy_fixups applied), never from cached policies.
fx() { case $SET in m1*) f=out/template_cc/policies_fixed/$1/policy.json; [ -f "$f" ] || { echo "m1 needs $f (scripts/make_policies_fixed.py)" >&2; exit 1; }; echo "$f";; *) echo "$2";; esac; }
mkdir -p out/template_cc/$SET; rm -f out/template_cc/$SET/.built
b() { n=$1; pol=$2; api=$3; shift 3
  if ! $PY -m builder.template_cc.run "$@" "$api" --save none --audit off --policy "$pol" --features "$F" --out out/template_cc/$SET/$n > out/template_cc/$SET/$n.log 2>&1; then
    echo "$n BUILD FAILED (see out/template_cc/$SET/$n.log)"; fi; }
b brief-a "$(fx brief-a out/template_cc/policy-a.json)" tools/openapi.json briefs/brief-a.md
b brief-b "$(fx brief-b out/template_cc/policy-b.json)" tools/openapi.json briefs/brief-a.md briefs/brief-b.md
for d in testbed/fake_briefs/*; do id=$(basename $d); b $id "$(fx $id out/template_cc/fake/$id/policy.json)" $d/tools.openapi.json $d/brief.md; done
$PY - "$SET" <<'E'
import glob, json, sys
sys.path.insert(0, '.')
from builder.agentic.tools import lint, openapi_summary
S = sys.argv[1]; bad = 0
for p in sorted(glob.glob(f'out/template_cc/{S}/*/workflow.json')):
    b = p.split('/')[-2]
    api = 'tools/openapi.json' if b.startswith('brief') else f'testbed/fake_briefs/{b}/tools.openapi.json'
    w = json.load(open(p)); w = w.get('workflow', w)
    s = openapi_summary.summarize(json.load(open(api))) if hasattr(openapi_summary, 'summarize') else openapi_summary.summary(api)
    e = lint.lint(w, s)['errors']; bad += len(e)
    print(f"{b:<16} lint errors {len(e)}" + (f"   first: {e[0]}" if e else ""))
print("ALL CLEAN" if not bad else f"{bad} LINT ERRORS")
E
touch out/template_cc/$SET/.built   # the set is complete (build + lint done)
