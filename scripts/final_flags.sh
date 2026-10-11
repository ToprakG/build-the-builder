#!/bin/bash
# The flag list for the final build, one line, no spaces. First file that exists wins:
#   <root>/FINAL_FLAGS.txt, <main>/FINAL_FLAGS.txt, <root>/M1_FLAGS.txt, <main>/M1_FLAGS.txt     (switching to m2 = write FINAL_FLAGS.txt)
#   scripts/final_flags.sh [root=. [main=root]]     exit 1 (no output) when none exists or the file is empty
R=${1:-.}; M=${2:-$R}
for f in "$R/FINAL_FLAGS.txt" "$M/FINAL_FLAGS.txt" "$R/M1_FLAGS.txt" "$M/M1_FLAGS.txt"; do
  [ -f "$f" ] || continue
  v=$(tr -d ' \r\n' < "$f"); [ -n "$v" ] && { echo "$v"; exit 0; }
done
exit 1
