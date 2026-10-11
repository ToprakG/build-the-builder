"""Side by side per brief: python3 scripts/gate_report.py [tag ...]   (default: m1g1 pipe9g1 p6g1). Pass/fail counts only (the shared result files)."""
import glob
import json
import sys

tags = sys.argv[1:] or ["m1g1", "pipe9g1", "p6g1"]
BRIEFS = ["brief-a", "brief-b", "f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes"]
res = {t: {} for t in tags}
for t in tags:
    for f in glob.glob(f"out/template_cc/eval/freya/*.{t}.json"):
        d = json.load(open(f))
        res[t][d["brief"]] = (d["passed"], d["cases"])
print(f"{'brief':16s}" + "".join(f"{t:>10s}" for t in tags))
for b in BRIEFS:
    print(f"{b:16s}" + "".join(f"{('%d/%d' % res[t][b]) if b in res[t] else '-':>10s}" for t in tags))
common = [b for b in BRIEFS if all(b in res[t] for t in tags)]
print(f"{'sum (briefs in all)':16s}" + "".join(f"{sum(res[t][b][0] for b in common):>10d}" for t in tags) + f"   over {len(common)} briefs")
print(f"{'sum (all finished)':16s}" + "".join(f"{sum(v[0] for v in res[t].values()):>10d}" for t in tags))


# --- flips by family: every tag except the LAST is compared with the LAST (the baseline, p6g1 by default) ---
def case_rows(tag):
    out = {}
    for f in glob.glob(f"out/template_cc/eval/freya/*.{tag}.json"):
        d = json.load(open(f))
        for r in d["rows"]:
            out[(d["brief"], r["case"])] = (r.get("family") or "?", bool(r["pass"]), "/".join(r.get("failed") or []))
    return out


base = case_rows(tags[-1])
for t in tags[:-1]:
    cur = case_rows(t)
    common = sorted(set(cur) & set(base))
    if not common:
        continue
    fam = {}
    for k in common:
        v = fam.setdefault(cur[k][0], {"down": [], "up": []})
        if base[k][1] and not cur[k][1]:
            v["down"].append((k[0], f"{k[1]}({cur[k][2]})"))
        if cur[k][1] and not base[k][1]:
            v["up"].append((k[0], k[1]))
    print(f"\nflips by family, {t} vs {tags[-1]} over {len(common)} common cases:")
    print(f"{'family':24s}{'pass->FAIL':>11s}{'FAIL->pass':>11s}   cases")
    for name, v in sorted(fam.items(), key=lambda kv: -len(kv[1]["down"])):
        if v["down"] or v["up"]:
            print(f"{name[:23]:24s}{len(v['down']):>11d}{len(v['up']):>11d}   down: {', '.join(c for _, c in v['down']) or '-'} | up: {', '.join(c for _, c in v['up']) or '-'}")
    rep = sorted(n for n, v in fam.items() if len({b for b, _ in v["down"]}) >= 2)
    print(f"families that went pass->FAIL in 2+ briefs ({t}):", ", ".join(rep) or "none")
