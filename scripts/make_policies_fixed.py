"""Apply builder.template_cc.policy_fixups (no LLM) to the cached policies of a set -> out/template_cc/policies_fixed/<brief>/policy.json.

    python3 scripts/make_policies_fixed.py [<source set dir, default out/template_cc/p10>] [<out dir>]
Prints: brief | field | before | after | quote. A field the brief is silent on is not listed (and stays as it was).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from builder.template_cc import policy_fixups  # noqa: E402

src = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "out/template_cc/p10"
dst = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "out/template_cc/policies_fixed"
BRIEFS = {"brief-a": ["briefs/brief-a.md"], "brief-b": ["briefs/brief-a.md", "briefs/brief-b.md"]}
rows = []
for b in ["brief-a", "brief-b"] + sorted(p.name for p in (REPO / "testbed/fake_briefs").iterdir() if p.is_dir()):
    pf = src / b / "policy.json"
    if not pf.exists():
        print(f"{b}: no policy in {src}")
        continue
    files = BRIEFS.get(b) or [f"testbed/fake_briefs/{b}/brief.md"]
    brief = "\n\n".join((REPO / f).read_text(encoding="utf-8") for f in files)
    pol = json.loads(pf.read_text(encoding="utf-8"))
    changes = policy_fixups.apply(pol, brief)
    (dst / b).mkdir(parents=True, exist_ok=True)
    (dst / b / "policy.json").write_text(json.dumps(pol, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for c in changes:
        rows.append((b, c["field"], c["before"], c["after"], c["quote"]))
    if not changes:
        rows.append((b, "(no explicit phrase)", "-", "-", "-"))
print("| brief | field | before | after | quote |\n|---|---|---|---|---|")
for b, f, x, y, q in rows:
    print(f"| {b} | {f} | {x} | {y} | {q[:90]} |")
