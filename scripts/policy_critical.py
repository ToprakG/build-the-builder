"""Score only the policy fields that change agent behaviour, as MATCH / MISS.

    python3 scripts/policy_critical.py <policy.json> <config.json> [--json]
    python3 scripts/policy_critical.py --diff <policy.json> <policy.json>

Reads the testbed config for scoring only (the builder never does). Key spelling is not a miss: map keys compare after
lowercasing and dropping non-alphanumerics; sets and unordered lists ignore order; numbers compare by value.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from builder.eval.score_policy import _norm  # noqa: E402


def _k(key) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _set(items) -> list:
    return sorted(json.dumps(_norm(x), sort_keys=True) for x in (items or []))


def _keyed(d) -> dict:
    return {_k(k): _norm(v) for k, v in (d or {}).items()}


def _band(b) -> dict:
    b = b or {}
    return {"min": _norm(b.get("min_days")), "max": _norm(b.get("max_days")),
            "deadline": _norm(b.get("deadline_business_days")), "attempts": _norm(b.get("attempts"))}


def _sub(d, *path):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
    return d


def fields(p: dict) -> dict:
    """Critical field name -> comparable value."""
    out = {"multi_product": _norm(p.get("multi_product")), "max_products": _norm(p.get("max_products")),
           "identity.questions": _set(_sub(p, "identity", "questions")),
           "identity.max_wrong": _norm(_sub(p, "identity", "max_wrong")),
           "bands.count": len(p.get("bands") or []), "holidays": _set(p.get("holidays")),
           "callback.start": _norm(_sub(p, "callback", "start")), "callback.end": _norm(_sub(p, "callback", "end")),
           "callback.same_day": _norm(_sub(p, "callback", "same_day")),
           "outcome_map": _set(v for v in (p.get("outcome_map") or {}).values() if v),
           "queue_map": _keyed(p.get("queue_map")), "note_map": _keyed(p.get("note_map")),
           "close_rule.kind": _norm(_sub(p, "close_rule", "kind")),
           "close_rule.order": _norm(_sub(p, "close_rule", "order")),
           "tools.create_promise.amount_unit": _norm(_sub(p, "tools", "create_promise", "amount_unit")),
           "tools.schedule_callback.mode": _norm(_sub(p, "tools", "schedule_callback", "mode")),
           "tools.close_call.mode": _norm(_sub(p, "tools", "close_call", "mode")),
           # policy-only extensions (a testbed config has neither): compared between two policies (--diff), not against a config
           "attempt_plan.cap_at_deadline": [_norm(a.get("cap_at_deadline")) for a in (p.get("attempt_plan") or []) if isinstance(a, dict)] or None,
           "ask_limits.id_decline": _norm(_sub(p, "ask_limits", "id_decline"))}
    for i, b in enumerate(p.get("bands") or []):
        out[f"bands.{i}"] = _band(b)
    return out


NEVER_NOTES = re.compile(r"(?:\bnever\b|\bnot\b|\bno longer\b|n't\b)[^.\n]{0,60}\b(?:add_note|notes?)\b|\b(?:add_note|notes?)\b[^.\n]{0,60}"
                         r"\b(?:never|unused|not used)\b", re.I)


def notes_not_applicable(policy: dict, brief_text: str = "", api_text: str | None = None) -> bool:
    """note_map is N/A when the policy has none AND (the brief says notes are never used OR the API has no add_note)."""
    if _keyed(policy.get("note_map")):
        return False
    if api_text is not None and "add_note" not in api_text:
        return True
    return bool(brief_text and "add_note" in brief_text.lower() and NEVER_NOTES.search(brief_text))


def compare(policy: dict, config: dict, brief_text: str = "", api_text: str | None = None) -> list[dict]:
    got, want = fields(policy), fields(config)
    for name in want:  # a band the policy lacks
        got.setdefault(name, None)
    rows = []
    for name, w in want.items():
        g = got.get(name)
        if name == "outcome_map" and not w:
            continue
        if name in ("attempt_plan.cap_at_deadline", "ask_limits.id_decline") and w is None:
            continue  # the config does not have it
        status = "MATCH" if g == w else "MISS"
        if status == "MISS" and name == "note_map" and notes_not_applicable(policy, brief_text, api_text):
            status = "N/A"
        if status == "MISS" and name in ("close_rule.kind", "close_rule.order") and not policy.get("multi_product") \
                and {str(got.get("close_rule.kind")), str(want.get("close_rule.kind"))} <= {"single", "first_non_paid"}:
            status = "EQUIV"  # one product: "single" and "first_non_paid" behave the same
        rows.append({"field": name, "status": status, "got": g, "want": w})
    for name in got:  # extra bands
        if name not in want:
            rows.append({"field": name, "status": "MISS", "got": got[name], "want": None})
    return rows


def diff(a: dict, b: dict) -> list[str]:
    """Critical fields on which two policies differ (build-to-build variance)."""
    fa, fb = fields(a), fields(b)
    return [k for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


def table(tag: str) -> int:
    repo = Path(__file__).resolve().parents[1]
    rows = []
    for b in ["brief-a", "brief-b"] + sorted(d.name for d in (repo / "testbed/fake_briefs").iterdir() if d.is_dir()):
        pol = repo / "out/template_cc" / tag / b / "policy.json"
        if not pol.exists():
            rows.append((b, None, None)); continue
        if b in ("brief-a", "brief-b"):
            cfg, api = repo / f"testbed/configs/{b}.config.json", repo / "tools/openapi.json"
            briefs = [repo / "briefs/brief-a.md"] + ([repo / "briefs/brief-b.md"] if b == "brief-b" else [])
        else:
            d = repo / "testbed/fake_briefs" / b
            cfg, api, briefs = d / "config.json", d / "tools.openapi.json", [d / "brief.md"]
        r = compare(json.loads(pol.read_text()), json.loads(cfg.read_text()), "\n".join(x.read_text() for x in briefs), api.read_text())
        rows.append((b, [x for x in r if x["status"] == "MISS"], len(r)))
    for b, miss, n in rows:
        print(f"{b:16s}", "no policy" if miss is None else f"{len(miss)} miss(es) of {n}: " + ", ".join(m["field"] for m in miss))
    return 0


def main(argv=None) -> int:
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--table":  # policy_critical.py --table x0 : the 9 briefs
        return table(sys.argv[2])
    if argv is None and len(sys.argv) > 1 and sys.argv[1] == "--diff":  # policy_critical.py --diff a.json b.json
        a, b = (json.loads(Path(x).read_text()) for x in sys.argv[2:4])
        print(", ".join(diff(a, b)) or "-")
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("policy")
    ap.add_argument("config")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--brief", nargs="*", default=[], help="brief files (for the note_map N/A rule)")
    ap.add_argument("--api", help="the tool API json (for the note_map N/A rule)")
    a = ap.parse_args(argv)
    brief = "\n".join(Path(f).read_text() for f in a.brief)
    rows = compare(json.loads(Path(a.policy).read_text()), json.loads(Path(a.config).read_text()), brief,
                   Path(a.api).read_text() if a.api else None)
    miss = [r for r in rows if r["status"] == "MISS"]
    if a.json:
        print(json.dumps({"misses": len(miss), "total": len(rows), "rows": rows}, ensure_ascii=False))
        return 0
    for r in rows:
        extra = "" if r["status"] == "MATCH" else f"  got={json.dumps(r['got'], ensure_ascii=False)}  want={json.dumps(r['want'], ensure_ascii=False)}"
        print(f"{r['status']:<5} {r['field']}{extra}")
    print(f"\n{len(miss)} critical miss(es) of {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
