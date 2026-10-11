#!/bin/bash
# Recompile a set from another set's policies (NO re-extraction, NO claude call, NO Freya): scripts/recompile_set.sh <src set> <new set> "<flag changes>" [briefs...]
#   flag changes are appended to the source build's own flag list, e.g. "f28_tool_failure"  or  "-f21_spoken_answers"  or  "f28_tool_failure,-f19_refusal_rule"
cd "$(dirname "$0")/.." || exit 1
SRC=${1:?src set}; NEW=${2:?new set}; CH=${3:-}; shift 3
BR=${*:-"f3-kestrel f4-brightwell brief-b brief-a f1-nordhavn f2-ostral f5-halden-email f6-corvid-memo f7-lumen-codes"}
one() { b=$1
  case $b in brief-a) in="briefs/brief-a.md tools/openapi.json";; brief-b) in="briefs/brief-a.md briefs/brief-b.md tools/openapi.json";; *) in="testbed/fake_briefs/$b/brief.md testbed/fake_briefs/$b/tools.openapi.json";; esac
  FEAT=$(python3 -c "import json;print(','.join(json.load(open('out/template_cc/$SRC/$b/build_report.json'))['features']))")
  mkdir -p out/template_cc/$NEW
  env -u FREYA_API_KEY .venv310/bin/python -m builder.template_cc.run $in --save none --audit off --policy out/template_cc/$SRC/$b/policy.json --features "$FEAT${CH:+,$CH}" --slot $NEW-$b --out out/template_cc/$NEW/$b > out/template_cc/$NEW/$b.log 2>&1 || echo "$b BUILD FAILED"
}
export -f one; export SRC NEW CH
printf '%s\n' $BR | xargs -P 3 -I{} bash -c 'one {}'
