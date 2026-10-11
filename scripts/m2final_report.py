"""m2final (tag m2fg1): each brief against its closest same-policy set (not best-of across sets: best-of is biased upward by noise), the plain total
over the briefs that ran, and every brief 2+ below its reference.   python3 scripts/m2final_report.py
Pass/fail counts only."""
import json

E = "out/template_cc/eval/freya"
REF = {"f6-corvid-memo": "m2cg1", "f5-halden-email": "m2cg1", "brief-a": "m2dg1", "f4-brightwell": "m2bg1", "brief-b": "m2bg1", "f3-kestrel": "m2a0g1"}  # the briefs m2final runs


def sc(b, tag):
    try:
        return json.load(open(f"{E}/{b}.{tag}.json"))["passed"]
    except (OSError, KeyError, ValueError):
        return None


tot = ref_tot = n = 0
warn = []
print(f"{'brief':16s}{'m2fg1':>7s}  reference (tag)")
for b, t in REF.items():
    now, ref = sc(b, "m2fg1"), sc(b, t)
    print(f"{b:16s}{('%d/10' % now) if now is not None else '-':>7s}  {('%d/10' % ref) if ref is not None else '-'} ({t})")
    if now is not None:
        tot += now
        n += 1
        ref_tot += ref or 0
        if ref is not None and now <= ref - 2:
            warn.append(f"{b}: {now} vs {ref} ({t})")
print(f"\nm2final total over {n} brief(s) that ran: {tot}/{n * 10}" + (f"  (their references: {ref_tot}/{n * 10})" if n else "") + f"  [{len(REF) - n} of {len(REF)} not finished]")
print("briefs 2+ below their same-policy reference:", "; ".join(warn) or "none")
