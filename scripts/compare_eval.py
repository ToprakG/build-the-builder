"""Paired comparison of two T-CC Freya eval tags on the same cases.

    python3 scripts/compare_eval.py s10 f14
Reads out/template_cc/eval/freya/<brief>.<tag>.json for both tags.
Prints per brief, per family and per style, the wins/losses, and a one-sided sign test.
"""
import glob
import json
import math
import pathlib
import sys
from collections import Counter

REPO = pathlib.Path(__file__).resolve().parents[1]
EV = REPO / "out" / "template_cc" / "eval" / "freya"


def load(tag):
    rows = {}
    for f in sorted(EV.glob(f"*.{tag}.json")):
        d = json.loads(f.read_text())
        for r in d["rows"]:
            rows[r["case"]] = (d["brief"], r)
    return rows


def styles():
    s = {}
    for f in glob.glob(str(REPO / "testbed" / "personas" / "*.json")) + glob.glob(str(REPO / "testbed" / "fake_briefs" / "*" / "cases.json")):
        for c in json.load(open(f)):
            s[c["id"]] = c.get("style") or "none"
    return s


def sign_p(w, l):
    n = w + l
    return 1.0 if n == 0 else sum(math.comb(n, k) for k in range(w, n + 1)) / 2 ** n


def main(a, b):
    A, B, st = load(a), load(b), styles()
    common = sorted(set(A) & set(B))
    if not common:
        sys.exit(f"no common cases between tags {a} and {b}")
    wins, losses = [], []
    per = {"brief": Counter(), "family": Counter(), "style": Counter()}
    tot = {"brief": Counter(), "family": Counter(), "style": Counter()}
    for cid in common:
        brief, ra = A[cid]
        _, rb = B[cid]
        keys = {"brief": brief, "family": ra["family"], "style": st.get(cid, "?")}
        for k, v in keys.items():
            tot[k][(v, a)] += ra["pass"]
            tot[k][(v, b)] += rb["pass"]
            per[k][v] += 1
        if rb["pass"] and not ra["pass"]:
            wins.append(cid)
        if ra["pass"] and not rb["pass"]:
            losses.append((cid, "; ".join(map(str, rb["failed"]))[:160]))
    pa = sum(A[c][1]["pass"] for c in common)
    pb = sum(B[c][1]["pass"] for c in common)
    print(f"{len(common)} common cases: {a} {pa}  ->  {b} {pb}   wins {len(wins)} losses {len(losses)}   "
          f"one-sided sign test p = {sign_p(len(wins), len(losses)):.3f}")
    for k in ("brief", "family", "style"):
        print(f"\nby {k}:")
        for v in sorted(per[k]):
            print(f"  {v:<22} {tot[k][(v, a)]:>2} -> {tot[k][(v, b)]:>2}  / {per[k][v]}")
    print("\nwins:", ", ".join(wins) or "-")
    print("losses (re-run these once before believing them):")
    for cid, why in losses:
        print("  ", cid, why)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s10", sys.argv[2] if len(sys.argv) > 2 else "f14")
