"""Score the built agent on seen and held-out personas. The builder never receives heldout.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import freya  # noqa: E402

HERE = Path(__file__).resolve().parent
NOW = "2026-10-12T10:30:00"


def load(name: str) -> list[dict]:
    return json.loads((HERE / "eval" / name).read_text(encoding="utf-8"))


def main() -> int:
    report = json.loads((HERE / "build" / "build_report.json").read_text(encoding="utf-8"))
    saved = report.get("saved") or {}
    agent_id = saved.get("agent_id")
    if not agent_id:
        print("no agent_id in build_report.json", flush=True)
        return 1
    people = load("seen.json") + load("heldout.json")
    made = freya.create_personas([
        {"name": p["name"], "prompt": p["prompt"], "successCriteria": p["successCriteria"],
         "tags": [p["split"]]} for p in people
    ])["personas"]
    variables = {created["id"]: {"customerNumber": spec["customerNumber"], "now": NOW}
                 for spec, created in zip(people, made)}
    sim_id, _it, status = freya.run_simulation(
        agent_id, "skill trial seen and held-out", variables, max_seconds=240, concurrency=5)
    runs = freya.get_iteration(sim_id, _it).get("runs") or []
    by_name = {r.get("personaName"): r for r in runs}
    summary = []
    for spec in people:
        run = by_name.get(spec["name"]) or {}
        summary.append({"split": spec["split"], "name": spec["name"], "outcome": run.get("outcome"),
                        "reason": (run.get("reason") or "")[:400], "callId": run.get("callId")})
        print(spec["split"], run.get("outcome"), spec["name"], flush=True)
    counts = {}
    for row in summary:
        key = (row["split"], row["outcome"])
        counts[key] = counts.get(key, 0) + 1
    out = {"sim_id": sim_id, "status": status, "agent_id": agent_id, "counts": {f"{a}:{b}": n for (a, b), n in counts.items()},
           "runs": summary}
    (HERE / "build" / "persona-score.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out["counts"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
