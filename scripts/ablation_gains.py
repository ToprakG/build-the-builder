"""Which ablated flags gained >= +1 on f4 or f6 over the m2b baseline (m2bg1)?   python3 scripts/ablation_gains.py   -> prints the flag names, one per line.
Baselines: f4 and f6 = m2bg1. Read from the shared result rows (pass counts only)."""
import json
import os

E = "out/template_cc/eval/freya"
FLAGS = "f18_side_resume f19_refusal_rule f20_once_flags f21_spoken_answers f24_day_once".split()


def score(b, tag):
    try:
        return json.load(open(f"{E}/{b}.{tag}.json"))["passed"]
    except (OSError, KeyError, ValueError):
        return None


base = {b: score(b, "m2bg1") for b in ("f4-brightwell", "f6-corvid-memo")}
for f in FLAGS:
    gains = {b: (score(b, f"abl_{f}_g1"), base[b]) for b in base}
    d = {b: (g[0] - g[1]) for b, g in gains.items() if g[0] is not None and g[1] is not None}
    import sys
    print(f"{f}: " + ", ".join(f"{b.split('-')[0]} {gains[b][0]} vs {gains[b][1]}" for b in base), file=sys.stderr)
    if any(v >= 1 for v in d.values()):
        print(f)
