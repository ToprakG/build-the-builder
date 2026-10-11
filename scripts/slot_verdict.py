"""Verdict of one reserved fix-verification slot.   python3 scripts/slot_verdict.py <name>   (reads out/template_cc/eval/reserved_slot_<name>.env)
Env file: SET=<compiled set> PFX=<tag prefix ending in g> BRIEFS="<b1> <b2>" and optionally FAMILY="<target case family>" (e.g. Scam question).
Rule (a fix goes in if ALL hold): (a) at least one case of its target family flips fail->pass vs the baseline (any family if FAMILY is not given),
(b) the total over its briefs is >= the baseline total, (c) no brief drops by 2 or more (a -1 on one brief alone is noise).
Baseline per brief: m2bg1 (m2b), else m2a0g1, else pipe9g1. Pass/fail counts and the shared case labels only."""
import json
import re
import sys

name = sys.argv[1]
env = open(f"out/template_cc/eval/reserved_slot_{name}.env").read()


def get(k, default=None):
    m = re.search(rf'^{k}=("?)(.*?)\1\s*$', env, re.M)
    return m.group(2) if m else default


import os

SET, PFX, FAMILY = get("SET"), get("PFX"), get("FAMILY")
E = os.environ.get("SLOT_E", "out/template_cc/eval/freya")
ALL = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]
BRIEFS = [next((a for a in ALL if a == t or a.startswith(t + "-")), t) for t in get("BRIEFS").split()]   # short ids (f3) -> full names


def rows(b, tag):
    try:
        return {r["case"]: r for r in json.load(open(f"{E}/{b}.{tag}.json"))["rows"]}
    except (OSError, KeyError, ValueError):
        return None


tot_new = tot_base = 0
worst = 0
flips = []
parts = []
complete = True
for b in BRIEFS:
    new = rows(b, f"{PFX}1")
    base, src = next(((rows(b, t), t) for t in ("m2bg1", "m2a0g1", "pipe9g1") if rows(b, t)), (None, "-"))
    if new is None or base is None:
        parts.append(f"{b}: no result")
        complete = False
        continue
    n, o = sum(r["pass"] for r in new.values()), sum(r["pass"] for r in base.values())
    tot_new, tot_base, worst = tot_new + n, tot_base + o, min(worst, n - o)
    flips += [c for c in new if new[c]["pass"] and c in base and not base[c]["pass"] and (FAMILY is None or new[c].get("family") == FAMILY)]
    parts.append(f"{b}: {n} vs {o} ({src}) {n - o:+d}")
a, bb, c = bool(flips), tot_new >= tot_base, worst > -2
verdict = "INCOMPLETE" if not complete else ("KEEP" if a and bb and c else "DROP")
print(f"slot {name} (set {SET}, family {FAMILY or 'any'}): " + "; ".join(parts) + f" | (a) target-family fail->pass {flips or 'none'} {a}; (b) total {tot_new} >= {tot_base} {bb}; (c) worst brief {worst:+d} {c} -> {verdict}")
