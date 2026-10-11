"""Build Brief A, or Brief A plus Brief B, with the same skills and Sonnet 5.5."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent


def main() -> int:
    which = (sys.argv[1] if len(sys.argv) > 1 else "a").lower()
    if which not in ("a", "b"):
        print("usage: run_collection.py a|b", file=sys.stderr)
        return 2
    spec = importlib.util.spec_from_file_location("run_trial", HERE / "run_trial.py")
    trial = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trial)
    extra_spec = importlib.util.spec_from_file_location("voice_extra", HERE / "tools" / "extra.py")
    extra = importlib.util.module_from_spec(extra_spec)
    extra_spec.loader.exec_module(extra)
    from builder.agentic.run import BuildOptions, run

    briefs = [ROOT / "briefs" / "brief-a.md"]
    if which == "b":
        briefs.append(ROOT / "briefs" / "brief-b.md")
    out = HERE / f"build-{which}"
    report = run(BuildOptions(
        briefs=briefs, api=ROOT / "tools" / "openapi.json", slot=f"skilltrial-brief-{which}",
        out_dir=out, save="freya", max_minutes=18, review=True, review_minutes=5, fix_minutes=6,
        unit=False, localsim=False, keep_workspace=True, model="claude-sonnet-5-5",
        extra={"skill_dirs": [str(p) for p in trial.SKILLS], "prompt_append": trial.PROMPT,
               "bind_tools": extra.Bound, "server_fn": extra.build_server},
    ))
    print(json.dumps({k: report.get(k) for k in ("status", "message", "saved", "trace")},
                     ensure_ascii=False, default=str), flush=True)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
