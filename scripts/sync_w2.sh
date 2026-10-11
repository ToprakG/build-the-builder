#!/bin/bash
# Copy the second lane's results (../yc-w2) into the main eval folder, never overwriting an existing file.
cd "$(dirname "$0")/.." || exit 1
SRC=../yc-w2/out/template_cc/eval/freya; DST=out/template_cc/eval/freya; n=0
for f in "$SRC"/*.p2r1.json "$SRC"/*.p3r1.json "$SRC"/*.f149br2.json; do
  [ -f "$f" ] || continue
  [ -e "$DST/$(basename "$f")" ] && continue
  cp "$f" "$DST/" && n=$((n+1)) && echo "copied $(basename "$f")"
done
echo "sync_w2: $n file(s) copied"
