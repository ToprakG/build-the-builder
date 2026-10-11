"""Rep policy groups from two reps of one tag (default p6r): dead = 0/2 (run x1), flaky = 1/2 (run x2), solid = 2/2.
    python3 scripts/case_groups.py [prefix]      -> out/template_cc/eval/groups/{dead,flaky,solid,canary,canary_small}.txt
canary = scripts/canary.txt (20, 2/2 in p6); canary_small = scripts/canary_small.txt (10, 1-2 per brief). Reads DEV runs only."""
import json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
pre = sys.argv[1] if len(sys.argv) > 1 else "p6r"
EV = REPO / "out/template_cc/eval/freya"
B = "brief-a brief-b f1-nordhavn f2-ostral f3-kestrel f4-brightwell f5-halden-email f6-corvid-memo f7-lumen-codes".split()
n = {}
for b in B:
    for r in (1, 2):
        for row in json.loads((EV / f"{b}.{pre}{r}.json").read_text())["rows"]:
            n[row["case"]] = n.get(row["case"], 0) + int(bool(row["pass"]))
out = REPO / "out/template_cc/eval/groups"; out.mkdir(parents=True, exist_ok=True)
groups = {"dead": [c for c, k in n.items() if k == 0], "flaky": [c for c, k in n.items() if k == 1], "solid": [c for c, k in n.items() if k == 2]}
for name, ids in groups.items():
    (out / f"{name}.txt").write_text(",".join(sorted(ids)))
for name in ("canary", "canary_small"):
    f = REPO / "scripts" / f"{name}.txt"
    (out / f"{name}.txt").write_text(",".join(l.strip() for l in f.read_text().splitlines() if l.strip() and not l.startswith("#")))
print({k: len(v) for k, v in groups.items()})
