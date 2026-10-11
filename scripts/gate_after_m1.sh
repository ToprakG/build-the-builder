#!/bin/bash
# Waits for the m1 gate's DECISION line, then runs the pipe9 GATE (tag pipe9g1) as soon as set pipe9 is complete, then prints the side-by-side report.
cd "$(dirname "$0")/.." || exit 1
until grep -q "DECISION" out/template_cc/eval/gate_m1.log 2>/dev/null; do sleep 60; done
scripts/gate_set.sh pipe9 pipe9g
.venv310/bin/python scripts/gate_report.py m1g1 pipe9g1 p6g1 > out/template_cc/eval/gate_report.txt 2>&1
