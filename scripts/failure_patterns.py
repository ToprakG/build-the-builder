"""Group the failures of a T-CC Freya eval tag into mechanisms, and compare clusters between two tags.

    python3 scripts/failure_patterns.py f14            # clusters of one tag
    python3 scripts/failure_patterns.py s10 f14        # clusters of f14, with the change vs s10 per cluster

No LLM calls. Reads out/template_cc/eval/freya/<brief>.<tag>.json and the testbed cases (family, style, expected).
A failure's signature = which part was wrong (outcome got->expected, promise, note, transfer, callback, or judge only)
plus where it stopped (last tools). Clusters are sorted by size, each with its cases, styles and one sample reason.
"""
import glob
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
EV = REPO / "out" / "template_cc" / "eval" / "freya"


def cases():
    out = {}
    for f in glob.glob(str(REPO / "testbed" / "personas" / "*.json")) + glob.glob(str(REPO / "testbed" / "fake_briefs" / "*" / "cases.json")):
        for c in json.load(open(f)):
            out[c["id"]] = c
    return out


def load(tag):
    rows = {}
    for f in sorted(EV.glob(f"*.{tag}.json")):
        d = json.loads(f.read_text())
        for r in d["rows"]:
            rows[r["case"]] = (d["brief"], r)
    return rows


OUT_RE = re.compile(r"outcome \(([^)]*)\), expected \(([^)]*)\)")


def canon_outcome(text):
    # "'not_verified', 'declined'" -> "not_verified/declined"; codes like '52-NV-DEC' kept
    parts = [p.strip().strip("'\"") for p in text.split(",")]
    return "/".join(p for p in parts if p and p != "None")


def signature(r, case):
    reasons = [str(x) for x in r.get("failed") or []]
    joined = " ; ".join(reasons)
    parts = []
    m = OUT_RE.search(joined)
    if m:
        got, exp = canon_outcome(m.group(1)), canon_outcome(m.group(2))
        parts.append(f"closed as {got}" if got != exp else "outcome right")
    for k in ("promises", "notes", "transfers", "callbacks"):
        if re.search(rf"(^|; ){k} ", joined):
            parts.append(f"{k} wrong")
    if not r.get("ledger_pass", True) and not parts:
        parts.append("records wrong (other)")
    if r.get("ledger_pass") and not r.get("freya_judge", True):
        parts.append("judge only")
    tools = r.get("tools") or []
    stop = "no tools" if not tools else ("ended after identity" if "get_account" not in tools and "verify_identity" in tools
                                         else "ended before identity" if "verify_identity" not in tools
                                         else "after account")
    n_verify = sum(t == "verify_identity" for t in tools)
    if n_verify >= 3:
        stop += f", {n_verify}x verify_identity"
    return " | ".join(parts) + f"  [{stop}]", joined


def judge_gist(joined):
    m = re.search(r"conversation \(Freya judge\):(.*)", joined)
    return " ".join(m.group(1).split())[:180] if m else ""


def clusters(tag, cs):
    rows = load(tag)
    groups = defaultdict(list)
    for cid, (brief, r) in rows.items():
        if r["pass"]:
            continue
        sig, joined = signature(r, cs.get(cid, {}))
        groups[sig].append((cid, brief, r, joined))
    return rows, groups


def main():
    tags = sys.argv[1:]
    if not tags or len(tags) > 2:
        sys.exit(__doc__)
    cs = cases()
    tag = tags[-1]
    rows, groups = clusters(tag, cs)
    old = None
    if len(tags) == 2:
        old_rows, old_groups = clusters(tags[0], cs)
        old = {sig: len(v) for sig, v in old_groups.items()}
        common = set(rows) & set(old_rows)
        print(f"{tags[0]}: {sum(old_rows[c][1]['pass'] for c in common)}/{len(common)}   {tag}: {sum(rows[c][1]['pass'] for c in common)}/{len(common)}   (common cases)\n")
    total_fail = sum(len(v) for v in groups.values())
    print(f"{tag}: {len(rows) - total_fail}/{len(rows)} passed, {total_fail} failures in {len(groups)} clusters\n")
    for sig, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        delta = "" if old is None else f"   (was {old.get(sig, 0)})"
        fam = Counter(cs.get(c, {}).get("family", "?") for c, *_ in items)
        exp = Counter(canon_outcome(m.group(2)) for m in (OUT_RE.search(j) for *_, j in items) if m)
        sty = Counter((cs.get(c, {}).get("style") or "none") for c, *_ in items)
        print(f"[{len(items)}] {sig}{delta}")
        print(f"     families: {dict(fam)}   styles: {dict(sty)}" + (f"   expected: {dict(exp)}" if exp else ""))
        print(f"     cases: {', '.join(f'{c}' for c, *_ in items)}")
        g = judge_gist(items[0][3])
        if g:
            print(f"     judge ({items[0][0]}): {g}")
        print()
    if old is not None:
        gone = [s for s in old if s not in groups]
        if gone:
            print("clusters that disappeared:")
            for s in gone:
                print(f"  [{old[s]}] {s}")
    by_style = defaultdict(lambda: [0, 0])
    for cid, (brief, r) in rows.items():
        s = cs.get(cid, {}).get("style") or "none"
        by_style[s][0] += r["pass"]
        by_style[s][1] += 1
    print("\npass by style:", ", ".join(f"{s} {p}/{n}" for s, (p, n) in sorted(by_style.items())))


if __name__ == "__main__":
    main()
