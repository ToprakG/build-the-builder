"""Scoreboard of scripts/tcc_eval_testbed.py results: per brief and per family, for the given tags (merged)."""
import collections
import json
import sys
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "out" / "template_cc" / "eval"
tags = sys.argv[1:] or ["s10"]
LIVE = {}  # (brief, tag) -> rows from running workers (*.partial.jsonl next to their job files)
import glob as _g, os as _o, tempfile as _t
for part in _g.glob(_o.path.join(_t.gettempdir(), "tcc-selftest-*", "job-*.out.json.partial.jsonl")):
    meta = Path(part[: -len(".out.json.partial.jsonl")] + ".json")
    try:
        job = json.loads(meta.read_text())
        brief, tag = job.get("label", ["?", "?"])
    except Exception:
        continue
    LIVE.setdefault((brief, tag), []).extend(json.loads(l) for l in open(part) if l.strip())
tot = collections.Counter()
fam = collections.defaultdict(collections.Counter)
print(f"{'brief':<18}{'passed':>8}{'scored':>8}{'unscored':>10}")
for f in sorted({p.name.split('.')[0] for p in D.glob("*.json")} | {k[0] for k in LIVE}):
    rows = []
    for t in tags:
        p = D / f"{f}.{t}.json"
        if p.exists():
            rows += json.loads(p.read_text())["rows"]
        else:  # still running: the live partial results of its worker
            rows += LIVE.get((f, t), [])
    if not rows:
        continue
    sc = [r for r in rows if not r.get("infra")]
    ok = sum(r["passed"] for r in sc)
    print(f"{f:<18}{ok:>8}{len(sc):>8}{len(rows) - len(sc):>10}")
    tot.update(passed=ok, scored=len(sc), unscored=len(rows) - len(sc))
    for r in sc:
        fam[r.get("family")].update(scored=1, passed=int(r["passed"]))
print(f"{'TOTAL':<18}{tot['passed']:>8}{tot['scored']:>8}{tot['unscored']:>10}"
      + (f"   ({100 * tot['passed'] / tot['scored']:.0f}%)" if tot["scored"] else ""))
print("\nby family:")
for k, c in sorted(fam.items(), key=lambda kv: kv[1]["passed"] / max(1, kv[1]["scored"])):
    print(f"  {str(k):<20}{c['passed']}/{c['scored']}")
