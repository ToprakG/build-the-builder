"""Build the statement-debt agent with local Claude Code, then score seen and held-out personas.

The held-out file is never copied into the builder workspace. The six examples in the
test-environment pack are the seen set; eval/heldout.json is not part of that pack.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACK = Path("/Users/toprakgundogdu/Downloads/test-environment")
HERE = Path(__file__).resolve().parent
OUT = HERE / "build"
MOCK_PUBLIC = "https://thus-soccer-step-asks.trycloudflare.com"
SKILLS = [
    HERE / "skills" / "pack-inventory",
    HERE / "skills" / "speech-library",
    HERE / "skills" / "workflow-graph",
    HERE / "skills" / "side-exits",
    HERE / "skills" / "adversarial-checklist",
]
PROMPT = """Also follow these skills, in this order, before you design the graph. If a skill does not load, read its SKILL.md under .claude/skills/:
pack-inventory, speech-library, workflow-graph, side-exits, adversarial-checklist.

Call mcp__fb__ledger_digest before extracting rules. Call mcp__fb__speech_guard on every spoken line a code node produces. Call mcp__fb__side_exit_check after the graph is written and remove every self-edge.

The tool server URL and the paths come from input/tools.openapi.json. Send the request envelope that file declares. Do not invent a flatter body.

The examples in the brief are not the full test. Other callers will be scored after you finish, and you do not have their scripts. Do not hardcode a customer number, an amount, or a card name from an example.
"""


def prepare() -> tuple[Path, Path]:
    from builder.ingest import ingest  # noqa: PLC0415

    heldout = (HERE / "eval" / "heldout.json").read_text(encoding="utf-8")
    ing = ingest(PACK, "http://127.0.0.1:8799", OUT / "ingest")
    brief = Path(ing["brief"])
    text = brief.read_text(encoding="utf-8")
    if "Held out" in text or "saldirgan@gmail.com" in text:
        raise SystemExit("held-out material leaked into the brief")
    api_path = Path(ing["api"])
    api = json.loads(api_path.read_text(encoding="utf-8"))
    api["servers"] = [{"url": MOCK_PUBLIC}]
    api_path.write_text(json.dumps(api, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ingest files={ing['files']} chars={ing['chars']} server={MOCK_PUBLIC}", flush=True)
    return brief, api_path


if __name__ == "__main__":
    brief, api = prepare()
    import importlib.util
    spec = importlib.util.spec_from_file_location("voice_extra", HERE / "tools" / "extra.py")
    extra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extra)
    from builder.agentic.run import BuildOptions, run
    report = run(BuildOptions(
        briefs=[brief], api=api, slot="skilltrial-statement", out_dir=OUT, save="freya",
        max_minutes=18, review=True, review_minutes=5, fix_minutes=6, unit=False, localsim=False,
        keep_workspace=True, model="claude-sonnet-5-5",
        extra={"skill_dirs": [str(p) for p in SKILLS], "prompt_append": PROMPT,
               "bind_tools": extra.Bound, "server_fn": extra.build_server},
    ))
    print(json.dumps({k: report.get(k) for k in ("status", "message", "saved", "trace", "workspace")},
                     ensure_ascii=False, default=str), flush=True)
    sys.exit(0 if report.get("status") == "ok" else 1)
