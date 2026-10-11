"""Per-case stability over repeated runs of the same version, and accept/reject between two versions.

    python3 scripts/stability.py f149b f149b2 f149b3                 # one version, 3 runs: solid / flaky / dead per case
    python3 scripts/stability.py --base f149b,f149b2,f149b3 --new s2a,s2b,s2c   # compare two versions, ×3 each

Reads out/template_cc/eval/freya/<brief>.<tag>.json. No LLM calls.
- solid = passed every run, flaky = some runs, dead = none.
- pass^k estimate = share of cases that passed all k runs (the final scoring needs 3 of 3).
Accept rule (TCC_STRUCTURAL_PLAN.md, section 1): on base-flaky+dead cases, wins - losses >= 3
(a case counts as a win when its pass count rises, a loss when it falls), and no brief loses more than 1 base-solid case.
"""
import json
import pathlib
import sys
from collections import defaultdict

REPO = pathlib.Path(__file__).resolve().parents[1]
EV = REPO / "out" / "template_cc" / "eval" / "freya"


def load(tag):
    rows = {}
    for f in sorted(EV.glob(f"*.{tag}.json")):
        d = json.loads(f.read_text())
        for r in d["rows"]:
            rows[r["case"]] = (d["brief"], bool(r["pass"]), r.get("family", "?"))
    if not rows:
        sys.exit(f"no results for tag {tag}")
    return rows


def counts(tags):
    runs = [load(t) for t in tags]
    common = set.intersection(*(set(r) for r in runs))
    out = {}
    for c in common:
        brief, _, fam = runs[0][c]
        out[c] = (brief, fam, sum(r[c][1] for r in runs), len(runs))
    return out


def label(p, n):
    return "solid" if p == n else ("dead" if p == 0 else "flaky")


def report(tags):
    cs = counts(tags)
    k = len(tags)
    groups = defaultdict(list)
    for c, (b, fam, p, n) in sorted(cs.items()):
        groups[label(p, n)].append(c)
    allk = len(groups["solid"])
    print(f"{len(cs)} cases × {k} runs ({', '.join(tags)})")
    print(f"single-run pass {sum(v[2] for v in cs.values())}/{len(cs) * k}   "
          f"pass^{k} estimate {allk}/{len(cs)} = {allk / max(1, len(cs)):.0%}")
    for g in ("solid", "flaky", "dead"):
        print(f"  {g:<6} {len(groups[g]):>3}  {' '.join(groups[g]) if g != 'solid' else ''}")
    per = defaultdict(lambda: [0, 0])
    for c, (b, fam, p, n) in cs.items():
        per[b][0] += p == n
        per[b][1] += 1
    print("  by brief (all runs passed):", ", ".join(f"{b} {a}/{t}" for b, (a, t) in sorted(per.items())))
    return cs


def compare(base_tags, new_tags):
    base, new = counts(base_tags), counts(new_tags)
    common = sorted(set(base) & set(new))
    wins, losses, solid_lost = [], [], defaultdict(list)
    for c in common:
        b, fam, pb, nb = base[c]
        _, _, pn, nn = new[c]
        rb, rn = pb / nb, pn / nn
        if label(pb, nb) == "solid":
            if rn < 1.0:
                solid_lost[b].append(c)
        elif rn > rb:
            wins.append(c)
        elif rn < rb:
            losses.append(c)
    base_all = sum(1 for c in common if base[c][2] == base[c][3])
    new_all = sum(1 for c in common if new[c][2] == new[c][3])
    worst = max((len(v) for v in solid_lost.values()), default=0)
    ok = (len(wins) - len(losses) >= 3) and worst <= 1
    print(f"all-runs-pass: base {base_all}/{len(common)} -> new {new_all}/{len(common)}")
    print(f"on base flaky/dead: wins {len(wins)} {wins}  losses {len(losses)} {losses}")
    print(f"base-solid cases that dropped: {dict(solid_lost) or '-'}  (re-run these once before believing them)")
    print("ACCEPT" if ok else "REJECT", "(rule: wins - losses >= 3 and no brief loses more than 1 solid case)")
    return ok


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--base" in a and "--new" in a:
        compare(a[a.index("--base") + 1].split(","), a[a.index("--new") + 1].split(","))
    elif a:
        report(a)
    else:
        sys.exit(__doc__)
