#!/bin/zsh
# Put a compiled set (out/template_cc/<set>/<brief>/workflow.json) where scripts/tcc_eval_freya.py reads workflows.
# Usage: scripts/use_workflows.sh f149      (then run tcc_eval_freya.py ... --tag f149)
# Never run this while an eval is running: the eval reads these files brief by brief.
set -e
cd "$(dirname "$0")/.."
S=${1:?set name, e.g. f149}
dst_of() { case $1 in brief-a) echo out/template_cc/judge-a/workflow.json;; brief-b) echo out/template_cc/judge-b/workflow.json;; *) echo out/template_cc/fake/$1/workflow.json;; esac; }
for d in out/template_cc/$S/*/; do
  b=$(basename $d); dst=$(dst_of $b)
  [ -f "$dst.s10.json" ] || cp "$dst" "$dst.s10.json"
  cp "$d/workflow.json" "$dst"
  echo "$b <- $S"
done
