"""Static pre-check (no simulation): every policy field whose value disagrees with its own _source quote is routed straight to R1
(the extract verifier for that field, accepted only with an exact brief quote).

    .venv310/bin/python scripts/policy_precheck.py <brief file>... <policy.json> --out policy.fixed.json
Prints one JSON line {"mismatches": [...], "changed": N}; exit 0. final_run.sh rebuilds with POLICY=<fixed> when changed > 0.
"""
import argparse, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import selftest_repair as R  # noqa: E402
from builder.template_cc import io as tio  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("inputs", nargs="+")
ap.add_argument("--out", required=True)
ap.add_argument("--build-dir", help="the build of this policy: its trace shows lines the compiler defaulted")
a = ap.parse_args()
*bf, pol = a.inputs
brief = tio.brief_text(tio.read_briefs([Path(b) for b in bf]))
policy = json.loads(Path(pol).read_text())
new, log = R.precheck_fix(policy, brief, a.build_dir)
Path(a.out).write_text(json.dumps(new, indent=1, ensure_ascii=False))
print(json.dumps({"mismatches": log, "changed": sum(1 for x in log if "new" in x)}, ensure_ascii=False))
