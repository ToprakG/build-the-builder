#!/usr/bin/env python3
"""Rule coverage: which normative sentences of the brief are enforced in CODE by the compiled workflow?

    scripts/rule_coverage.py <brief.md>... <policy.json> <workflow.json> [--out DIR] [--llm on|off] [--model M] [--quiet]
    scripts/rule_coverage.py --all <dir-with-p6-builds> [--out DIR]       # the 9 repo briefs against <dir>/<stem>/{policy,workflow}.json

Output: a table brief | sentence | type | enforced-by (code/prompt/missing), per-type counts, the gaps, the UNKNOWN sentences.
Exit code 1 when a normative sentence is only prompt-enforced or missing (a rule type the compiler cannot enforce),
2 on bad usage. RULE_COVERAGE_ENFORCE=0 turns the exit code 1 into a warning (exit 0).
Reads briefs, policy.json and workflow.json only. Classification: regex pre-pass + one claude_cli:sonnet call per brief (cached).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from builder.template_cc import rulecov as rc  # noqa: E402

REPO_BRIEFS = [("brief-a", "briefs/brief-a.md"), ("brief-b", "briefs/brief-b.md")] + [
    (d, f"testbed/fake_briefs/{d}/brief.md") for d in
    ("f1-nordhavn", "f2-ostral", "f3-kestrel", "f4-brightwell", "f5-halden-email", "f6-corvid-memo", "f7-lumen-codes")]


def run_one(name: str, brief_paths: list[Path], policy: dict, wf: dict, args, cache: Path) -> tuple[list[dict], str]:
    sents: list[dict] = []
    for bp in brief_paths:
        part = rc.split_brief(bp.read_text(encoding="utf-8"), name)
        for s in part:
            s["id"] += len(sents)
        sents += part
    src = rc.classify(sents, name, args.model, cache, use_llm=args.llm == "on")
    return rc.check(sents, policy, wf), src


def clip(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def print_table(rows: list[dict]):
    print(f"{'brief':<16} {'ln':>3}  {'type':<20} {'by':<8} sentence")
    for r in rows:
        t = r["types"][0] if r["types"] else "UNKNOWN"
        print(f"{r['brief']:<16} {r['line']:>3}  {t:<20} {r['enforced_by']:<8} {clip(r['text'], 110)}")


def report(all_rows: list[dict], sources: dict, out: Path | None, quiet: bool) -> int:
    if not quiet:
        print_table(all_rows)
    tab = rc.summary(all_rows)
    print("\n== per rule type (primary type of each normative sentence) ==")
    print(f"{'type':<22}{'sentences':>10}{'code':>7}{'prompt':>8}{'missing':>9}{'  code%':>8}")
    tot = {"sentences": 0, "code": 0, "prompt": 0, "missing": 0}
    for t, v in tab.items():
        for k in tot:
            tot[k] += v[k]
        pct = f"{100 * v['code'] / v['sentences']:.0f}%" if v["sentences"] else "-"
        print(f"{t:<22}{v['sentences']:>10}{v['code']:>7}{v['prompt']:>8}{v['missing']:>9}{pct:>8}")
    print(f"{'TOTAL':<22}{tot['sentences']:>10}{tot['code']:>7}{tot['prompt']:>8}{tot['missing']:>9}"
          f"{(' ' + str(round(100 * tot['code'] / max(1, tot['sentences']))) + '%'):>8}")
    gp = rc.gaps(all_rows)
    print("\n== gaps: capabilities (or lack of them) behind the non-code sentences ==")
    cap_gaps: dict[str, dict] = {}
    for r in all_rows:
        if r["enforced_by"] in ("code", "unknown"):
            continue
        cids = [c["id"] for c in r["caps"] if not c["engaged"]] or [f"no-capability/{(r['types'] or ['?'])[0]}"]
        for cid in cids:
            g = cap_gaps.setdefault(cid, {"rows": [], "briefs": set()})
            g["rows"].append(r)
            g["briefs"].add(r["brief"])
    for cid, g in sorted(cap_gaps.items(), key=lambda kv: (-len(kv[1]["briefs"]), -len(kv[1]["rows"]))):
        mark = "  <-- 2+ briefs" if len(g["briefs"]) >= 2 else ""
        print(f"- {cid}: {len(g['rows'])} sentence(s) in {len(g['briefs'])} brief(s) {sorted(g['briefs'])}{mark}")
        for r in g["rows"][:2]:
            print(f"    [{r['brief']} l{r['line']}, {r['enforced_by']}] {clip(r['text'], 150)}")
    unknown = [r for r in all_rows if r["enforced_by"] == "unknown"]
    print(f"\n== UNKNOWN ({len(unknown)}): normative sentences that fit no library type; injected verbatim into their stage's prompt ==")
    for r in unknown:
        print(f"- [{r['brief']} l{r['line']}, stage {r['stage']}] {clip(r['text'], 200)}")
    bad = [r for r in all_rows if r["enforced_by"] in ("prompt", "missing")]
    print(f"\nclassifier: {sources}")
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "rule_coverage.json").write_text(json.dumps({"summary": tab, "sources": sources, "rows": all_rows, "unknown": unknown},
                                                           indent=1, ensure_ascii=False, default=list), encoding="utf-8")
    types_bad = sorted({(r["types"] or ["?"])[0] for r in bad})
    if bad:
        print(f"RULE COVERAGE: {len(bad)} sentence(s) not enforced in code; rule types: {', '.join(types_bad)}")
    return 1 if bad and os.environ.get("RULE_COVERAGE_ENFORCE", "1") != "0" else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="<brief.md>... <policy.json> <workflow.json>")
    ap.add_argument("--all", metavar="BUILD_DIR", help="check the 9 repo briefs against BUILD_DIR/<stem>/{policy,workflow}.json")
    ap.add_argument("--out", help="write rule_coverage.json here")
    ap.add_argument("--llm", choices=["on", "off"], default="on")
    ap.add_argument("--model", default="claude_cli:sonnet")
    ap.add_argument("--quiet", action="store_true", help="no per-sentence table")
    ap.add_argument("--cache", default=str(REPO / "out" / "rule_coverage" / "cache"))
    args = ap.parse_args(argv)
    cache = Path(args.cache)
    all_rows, sources = [], {}
    if args.all:
        for stem, rel in REPO_BRIEFS:
            d = Path(args.all) / stem
            rows, src = run_one(stem, [REPO / rel], json.loads((d / "policy.json").read_text()),
                                json.loads((d / "workflow.json").read_text()), args, cache)
            all_rows += rows
            sources[stem] = src
    else:
        if len(args.files) < 3:
            ap.print_usage(sys.stderr)
            return 2
        *briefs, pol, wfp = args.files
        name = rc.brief_label(briefs)
        all_rows, src = run_one(name, [Path(b) for b in briefs], json.loads(Path(pol).read_text()),
                                json.loads(Path(wfp).read_text()), args, cache)
        sources[name] = src
    return report(all_rows, sources, Path(args.out) if args.out else None, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
