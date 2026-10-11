import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def features(*extra):
    r = subprocess.run([str(REPO / "scripts/final_build.sh"), *extra, "briefs/brief-a.md", "tools/openapi.json", "/tmp/fb_test_out"],
                       cwd=REPO, capture_output=True, text=True, env={**os.environ, "FINAL_BUILD_DRY": "1", "FEATURES": "f1,f7,f13,f8"})
    line = next(l for l in r.stdout.splitlines() if l.startswith("features: "))
    return set(line[len("features: "):].split(","))


def test_disable_removes_flags_for_this_brief_only():
    assert features() == {"f1_id_complete", "f7_router", "f13_verifier", "f8_fixed_lines"}
    assert features("--disable", "f7_router,f13") == {"f1_id_complete", "f8_fixed_lines"}
    assert features("--disable=f8") == {"f1_id_complete", "f7_router", "f13_verifier"}
