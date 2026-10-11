"""Score an extracted policy.json against a testbed config.json, field by field.

    python3 builder/eval/score_policy.py <policy.json> <config.json> [--json]

Leaf fields are compared after normalisation: `null` and missing are equal, numbers compare by value, strings ignore
case and surrounding space, unordered lists (holidays, forbidden words) ignore order, and free-text fields (the
`sentences.*` and `attempt1`) match on word overlap. Prints match / mismatch / missing per field and the share matched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IGNORED = {"id", "name", "kind", "_note", "memo_changes", "_sources", "_overrides"}
UNORDERED = {"holidays", "forbidden_before_verify", "unused_tools", "call_variables"}
FUZZY_PREFIXES = ("sentences.", "attempt1")
FUZZY_MIN = 0.6


def flatten(obj, prefix="") -> dict:
    """Leaf paths -> values. Dicts recurse; lists of dicts recurse by index; other lists are leaves."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in IGNORED or str(k).startswith("_source"):
                continue
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}{i}."))
    else:
        out[prefix[:-1]] = obj
    return out


def _norm(v):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        try:
            return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else s.lower()
        except ValueError:
            return s.lower()
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


STOP = {"the", "a", "an", "of", "to", "and", "or", "is", "are", "from", "that", "on", "in", "with", "our", "your",
        "their", "this", "it", "be", "by", "for", "at", "as"}


def _words(text) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text).lower().replace("'s", ""))
    return {w for w in words if w not in STOP}


def similar(a, b) -> float:
    """Overlap coefficient of content words: a paraphrase that keeps the key words scores high."""
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / max(min(len(wa), len(wb)), 1)


def compare(path: str, got, want) -> str:
    if want is None and got is None:
        return "match"
    if got is None:
        return "missing"
    top = path.split(".")[0]
    if path.startswith(FUZZY_PREFIXES) and isinstance(want, str):
        return "match" if similar(got, want) >= FUZZY_MIN else "mismatch"
    g, w = _norm(got), _norm(want)
    if top in UNORDERED and isinstance(g, list) and isinstance(w, list):
        return "match" if sorted(map(json.dumps, g)) == sorted(map(json.dumps, w)) else "mismatch"
    return "match" if g == w else "mismatch"


def score(policy: dict, config: dict) -> dict:
    want, got = flatten(config), flatten(policy)
    rows = [{"field": p, "status": compare(p, got.get(p), w), "got": got.get(p), "want": w} for p, w in sorted(want.items())]
    matched = sum(r["status"] == "match" for r in rows)
    extra = sorted(set(got) - set(want))
    return {"matched": matched, "total": len(rows), "percent": round(100 * matched / max(len(rows), 1), 1),
            "rows": rows, "extra_fields": extra}


def render(result: dict, show_matches: bool = True) -> str:
    lines = []
    for r in result["rows"]:
        if r["status"] == "match" and not show_matches:
            continue
        detail = "" if r["status"] == "match" else f"  got={json.dumps(r['got'], ensure_ascii=False)}  want={json.dumps(r['want'], ensure_ascii=False)}"
        lines.append(f"{r['status']:<8} {r['field']}{detail}")
    lines.append(f"\n{result['matched']}/{result['total']} fields match ({result['percent']}%)"
                 + (f"; {len(result['extra_fields'])} extra field(s) not in the config" if result["extra_fields"] else ""))
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("policy")
    p.add_argument("config")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--only-problems", action="store_true", help="hide matching fields")
    a = p.parse_args(argv)
    result = score(json.loads(Path(a.policy).read_text()), json.loads(Path(a.config).read_text()))
    print(json.dumps(result, indent=1, ensure_ascii=False) if a.json else render(result, not a.only_problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
