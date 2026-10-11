"""Judge agreement: hand labels vs the judge's answers in patch_log.jsonl.

Labels file (JSONL), one line per labelled patch:
    {"patch_id": "p3", "accept": true, "questions": {"necessary": "yes", "case_specific": "no", ...}}
Every key is optional except patch_id. Output: overall accept agreement and per-question confusion
(the judge's raw yes/no answer against the label).

    python3 -m builder.template_cc.judge_agreement patch_log.jsonl labels.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _jsonl(path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def agreement(log: list[dict], labels: list[dict]) -> dict:
    by_id = {}
    for rec in log:  # the last verdict per patch id wins
        by_id[str(rec.get("patch", {}).get("id"))] = rec
    acc = {"n": 0, "agree": 0}
    per_q: dict[str, dict] = {}
    missing = []
    for lab in labels:
        rec = by_id.get(str(lab["patch_id"]))
        if rec is None:
            missing.append(lab["patch_id"])
            continue
        if "accept" in lab:
            acc["n"] += 1
            acc["agree"] += bool(lab["accept"]) == bool(rec.get("accepted"))
        answers = {c["name"]: c.get("answer") for c in rec.get("checks", []) if c.get("kind") == "decision"}
        for q, want in (lab.get("questions") or {}).items():
            got = answers.get(q)
            if got is None:
                continue
            m = per_q.setdefault(q, {"yes_yes": 0, "yes_no": 0, "no_yes": 0, "no_no": 0})  # label_judge
            m[f"{want}_{got}"] += 1
    out_q = {}
    for q, m in sorted(per_q.items()):
        n = sum(m.values())
        out_q[q] = {**m, "n": n, "agreement": (m["yes_yes"] + m["no_no"]) / n if n else None}
    return {"accept": {**acc, "agreement": acc["agree"] / acc["n"] if acc["n"] else None}, "questions": out_q,
            "missing": missing}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(agreement(_jsonl(argv[0]), _jsonl(argv[1])), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
