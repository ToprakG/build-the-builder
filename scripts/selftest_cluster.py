"""Cluster the self-test failures of several briefs by rule type and failed part.

    python3 scripts/selftest_cluster.py <dir with one build dir per brief> [--known scripts/known_clusters.json]

Reads <dir>/<brief>/selftest/results.json. A cluster = (rule type, failed part: outcome / promises / notes / callbacks / transfers /
close / identity-order / judge). `new` = the cluster key "rule:part" or the bare rule type is not listed in the known file
(a JSON list of keys, the A-G clusters). Prints a table; with --json writes it too.
"""
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path


def part(reason):
    w = re.match(r"\w+", reason or "")
    w = w.group(0) if w else "other"
    return {"conversation": "judge", "the": "close", "get_account": "identity-order", "create_promise": "identity-order"}.get(w, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--known", default=str(Path(__file__).with_name("known_clusters.json")))
    ap.add_argument("--json")
    a = ap.parse_args()
    known = set(json.loads(Path(a.known).read_text())) if Path(a.known).exists() else set()
    cl, tot, passed = defaultdict(list), 0, 0
    for f in sorted(Path(a.dir).glob("*/selftest/results.json")):
        b = f.parts[-3]
        r = json.loads(f.read_text())
        tot += r["cases"]; passed += r["passed"]
        for x in r["rows"]:
            if x["pass"]:
                continue
            for p in sorted({part(y) for y in x["failed"]}) or ["other"]:
                cl[(x["rule"], p)].append((b, x["case"], next((y for y in x["failed"] if part(y) == p), "")[:160]))
    out = []
    for (rule, p), items in sorted(cl.items(), key=lambda kv: -len(kv[1])):
        new = f"{rule}:{p}" not in known and rule not in known
        out.append({"rule": rule, "part": p, "n": len(items), "briefs": sorted({i[0] for i in items}), "new": new, "sample": items[0][2], "cases": [i[1] for i in items]})
    print(f"{passed}/{tot} scenarios passed; {len(out)} failure clusters ({'known list: ' + str(len(known)) if known else 'NO known list: everything is marked new'})")
    for c in out:
        print(f"{'NEW ' if c['new'] else 'known'} {c['rule']:18s} {c['part']:14s} x{c['n']}  briefs {','.join(c['briefs'])}\n      e.g. {c['sample']}")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))


main()
