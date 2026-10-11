"""Failed GATE cases by case family and failed-criterion category, from the shared result rows only (no transcripts, no quotes).

    python3 scripts/gate_families.py [tag ...]      default: pipe9g1 p6g1   (p6g1 is restricted to the briefs the FIRST tag finished)
Prints per tag: family -> failed count -> briefs -> categories, and flags a family with >= 4 failures across >= 3 briefs.
"""
import collections
import glob
import json
import sys

args = sys.argv[1:] or ["pipe9g1", "p6g1"]
# mixed mode: arguments "tag:brief,brief" combine failures of different tags (e.g. m2bg1:brief-a,f1-nordhavn pipe9g1:f2-ostral)
MIXED = [(a.split(":")[0], a.split(":")[1].split(",")) for a in args if ":" in a]
tags = [t for t, _ in MIXED] or args


def rows(tag):
    out = {}
    for f in glob.glob(f"out/template_cc/eval/freya/*.{tag}.json"):
        d = json.load(open(f))
        out[d["brief"]] = d["rows"]
    return out


if MIXED:
    fam = {}
    total = failed = 0
    for t, bs in MIXED:
        r = rows(t)
        for b in bs:
            for row in r.get(b, []):
                total += 1
                if not row["pass"]:
                    failed += 1
                    f = fam.setdefault(row.get("family") or "?", {"n": 0, "briefs": set(), "cats": collections.Counter()})
                    f["n"] += 1
                    f["briefs"].add(b)
                    f["cats"].update(row.get("failed") or [])
    print(f"=== mixed: {failed} failed of {total} cases ({'; '.join(t + ': ' + ','.join(b.split('-')[0] if b.startswith('f') else b for b in bs) for t, bs in MIXED)}) ===")
    print(f"{'family':24s}{'failed':>7s}  {'briefs':26s} categories")
    flag = []
    for name, v in sorted(fam.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        bs = ",".join(sorted(b.split("-")[0] if b.startswith("f") else b for b in v["briefs"]))
        print(f"{name[:23]:24s}{v['n']:>7d}  {bs:26s} " + " ".join(f"{c}:{n}" for c, n in v["cats"].most_common()))
        if v["n"] >= 3 and len(v["briefs"]) >= 3:
            flag.append(name)
    print("families with >= 3 failures across >= 3 briefs:", ", ".join(flag) or "none")
    sys.exit(0)

first = rows(tags[0])
briefs = sorted(first)
for t in tags:
    r = rows(t)
    fam = collections.defaultdict(lambda: {"n": 0, "briefs": set(), "cats": collections.Counter()})
    total = failed = 0
    for b in briefs:
        for row in r.get(b, []):
            total += 1
            if not row["pass"]:
                failed += 1
                f = fam[row.get("family") or "?"]
                f["n"] += 1
                f["briefs"].add(b)
                f["cats"].update(row.get("failed") or [])
    print(f"\n=== {t}: {failed} failed of {total} cases, briefs {', '.join(b.split('-')[0] if b.startswith('f') else b for b in briefs)} ===")
    print(f"{'family':24s}{'failed':>7s}  {'briefs':26s} categories")
    flag = []
    for name, v in sorted(fam.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        bs = ",".join(sorted(b.split("-")[0] if b.startswith("f") else b for b in v["briefs"]))
        cats = " ".join(f"{c}:{n}" for c, n in v["cats"].most_common())
        print(f"{name[:23]:24s}{v['n']:>7d}  {bs:26s} {cats}")
        if v["n"] >= 4 and len(v["briefs"]) >= 3:
            flag.append(name)
    print("families with >= 4 failures across >= 3 briefs:", ", ".join(flag) or "none")
