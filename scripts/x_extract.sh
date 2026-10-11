#!/bin/bash
# Fresh extraction of briefs into out/template_cc/<tag>/<brief> (extract + compile; compile output ignored).
#   scripts/x_extract.sh <tag> [brief ...]      e.g. scripts/x_extract.sh x0     (FORCE=1 re-runs existing ones)
cd "$(dirname "$0")/.." || exit 1
tag=$1; shift
export TCC_EXTRACT_MODEL=${TCC_EXTRACT_MODEL:-claude_cli:claude-sonnet-5-5}
briefs=${*:-"brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes"}
mkdir -p out/template_cc/$tag
run() {
  b=$1
  case $b in
    brief-a) in="briefs/brief-a.md tools/openapi.json" ;;
    brief-b) in="briefs/brief-a.md briefs/brief-b.md tools/openapi.json" ;;
    *) in="testbed/fake_briefs/$b/brief.md testbed/fake_briefs/$b/tools.openapi.json" ;;
  esac
  [ -f out/template_cc/$tag/$b/policy.json ] && [ -z "$FORCE" ] && return
  .venv310/bin/python -m builder.template_cc.run $in --save none --audit off --slot x-$tag-$b --out out/template_cc/$tag/$b > out/template_cc/$tag/$b.log 2>&1
}
# OpenRouter reserves max_tokens per in-flight request; a low balance returns 402 when many run at once.
n=0
for b in $briefs; do
  run $b &
  n=$((n+1)); [ $((n % ${PAR:-2})) -eq 0 ] && wait
done
wait
