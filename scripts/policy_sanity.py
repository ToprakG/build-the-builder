"""Is the extracted policy.json degenerate (the brief was read without what it depends on)?

    python3 scripts/policy_sanity.py POLICY.json [--out sanity.json]       exit 0 = sane, 1 = DEGENERATE (reasons printed, one per line)
Generic signals, no brief names: placeholder bands (0-day windows, name "unknown", no deadline), empty attempt_plan, a one-word generic
opening line, null outcome-map entries for the transfers, and extraction notes saying a referenced brief "is not supplied".
"""
import json
import re
import sys
from pathlib import Path

GENERIC_LINES = {"hello", "hello.", "hi", "hi.", "hello, this is a call.", "good day."}
NOT_SUPPLIED = re.compile(r"not supplied|not provided|is unknown|are unknown|was not given|missing from (?:this|the) brief", re.I)


def _texts(p):
    for k in ("attempt1", "notes_on_restructuring", "product_id_format"):
        if isinstance(p.get(k), str):
            yield k, p[k]
    for i, r in enumerate(p.get("extra_rules") or []):
        if isinstance(r, dict):
            yield f"extra_rules[{i}]", str(r.get("statement") or "")


def check(p):
    """List of reasons; empty = sane."""
    out = []
    bands = p.get("bands") or []
    real = [b for b in bands if isinstance(b, dict) and b.get("name") != "unknown" and (b.get("max_days") or 0) > 0 and (b.get("deadline_business_days") or 0) > 0]
    if not bands:
        out.append("no bands")
    elif not real:
        out.append("placeholder bands: every band is 0 days, has no deadline or is named unknown")
    elif any(b.get("name") == "unknown" for b in bands):
        out.append("a band named unknown")
    if not (p.get("attempt_plan") or []):
        out.append("attempt_plan is empty")
    op = ((p.get("lines") or {}).get("opening") or {}).get("text")
    if isinstance(op, str) and op.strip().lower() in GENERIC_LINES:
        out.append(f"generic opening line {op!r}")
    om = p.get("outcome_map") or {}
    nulls = [k for k in ("tr_human", "tr_support") if k in om and om[k] is None]
    if nulls:
        out.append("null outcome map entries: " + ", ".join(nulls))
    for k, t in _texts(p):
        if NOT_SUPPLIED.search(t):
            out.append(f"{k} says something is not supplied: {t[:80]!r}")
    return out


def main():
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else ""
    if out and out in a:
        a.remove(out)
    reasons = check(json.loads(Path(a[0]).read_text()))
    if out:
        Path(out).write_text(json.dumps({"degenerate": bool(reasons), "reasons": reasons}, indent=1))
    for r in reasons:
        print(r)
    return 1 if reasons else 0


if __name__ == "__main__":
    sys.exit(main())
