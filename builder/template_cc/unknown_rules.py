"""UNKNOWN RULES: normative brief sentences that fit no rule type are never dropped.

    python3 -m builder.template_cc.unknown_rules <brief.md>... <policy.json> <workflow.json> [--llm on|off] [--out DIR]

Compiler-independent post-processing of a compiled workflow.json (it never imports or edits compiler/graph.py):
  (a) every UNKNOWN sentence is appended, verbatim, to the task_message of the node(s) of its stage under
      "## Unclassified brief rules (verbatim)" (the system_prompt for stage `global`, or when the stage has no node);
      a sentence already literally present in that prompt is not added twice;
  (b) the list goes to <out>/unknown_rules.json and <out>/UNKNOWN.md for human review.
Idempotent: the section is rebuilt from scratch on every run. Called by scripts/final_build.sh after the compile.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder.template_cc import rulecov as rc
from builder.template_cc.io import REPO

TITLE = "## Unclassified brief rules (verbatim)"
INTRO = "These sentences of the brief were not recognised as a known rule type. Follow each one exactly as written."


def _strip_old(text: str) -> str:
    i = (text or "").find(TITLE)
    return (text[:i].rstrip() + "\n") if i >= 0 else (text or "")


def _present(sentence: str, prompt: str) -> bool:
    return rc.squash(sentence) in rc.squash(prompt)


def apply(wf: dict, unknown: list[dict]) -> list[dict]:
    """Inject the sentences into wf (in place). Returns one record per sentence: where it went, or why it was not added."""
    nodes = {**(wf.get("nodes") or {}), **(wf.get("global_nodes") or {})}
    for n in nodes.values():
        if n.get("task_message") is not None:
            n["task_message"] = _strip_old(n["task_message"])
    wf["system_prompt"] = _strip_old(wf.get("system_prompt") or "")
    per_target: dict[str, list[str]] = {}
    records = []
    for u in unknown:
        stage = u.get("stage") or "global"
        targets = [] if stage == "global" else rc.scan_stage_nodes(wf, stage)
        if not targets:
            targets = ["<system_prompt>"]
        added, skipped = [], []
        for t in targets:
            prompt = wf["system_prompt"] if t == "<system_prompt>" else nodes[t]["task_message"]
            (skipped if _present(u["text"], prompt) else added).append(t)
            if t in added:
                per_target.setdefault(t, []).append(u["text"])
        records.append({"brief": u["brief"], "line": u["line"], "stage": stage, "text": u["text"],
                        "injected_into": added, "already_in_prompt": skipped})
    for t, sents in per_target.items():
        block = TITLE + "\n" + INTRO + "\n" + "\n".join("- " + s for s in dict.fromkeys(sents))
        if t == "<system_prompt>":
            wf["system_prompt"] = wf["system_prompt"].rstrip() + "\n\n" + block + "\n"
        else:
            nodes[t]["task_message"] = nodes[t]["task_message"].rstrip() + "\n\n" + block
    return records


def unknown_sentences(briefs: list[Path], name: str, model: str, cache: Path, use_llm: bool) -> list[dict]:
    sents: list[dict] = []
    for bp in briefs:
        part = rc.split_brief(bp.read_text(encoding="utf-8"), name)
        for s in part:
            s["id"] += len(sents)
        sents += part
    rc.classify(sents, name, model, cache, use_llm=use_llm)
    return [s for s in sents if s.get("normative", True) and not s.get("types")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="<brief.md>... <policy.json> <workflow.json>")
    ap.add_argument("--llm", choices=["on", "off"], default="on")
    ap.add_argument("--model", default="claude_cli:sonnet")
    ap.add_argument("--cache", default=str(REPO / "out" / "rule_coverage" / "cache"))
    ap.add_argument("--out", help="directory for unknown_rules.json / UNKNOWN.md (default: the workflow's directory)")
    a = ap.parse_args(argv)
    if len(a.files) < 3:
        ap.print_usage(sys.stderr)
        return 2
    *briefs, _policy, wfp = a.files
    wf_path = Path(wfp)
    out = Path(a.out) if a.out else wf_path.parent
    name = rc.brief_label([Path(b) for b in briefs])
    unknown = unknown_sentences([Path(b) for b in briefs], name, a.model, Path(a.cache), a.llm == "on")
    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    records = apply(wf, unknown)
    wf_path.write_text(json.dumps(wf, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    out.mkdir(parents=True, exist_ok=True)
    (out / "unknown_rules.json").write_text(json.dumps(records, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = [f"# UNKNOWN brief rules ({len(records)})", "",
          "Normative sentences that fit no rule type. Each was injected verbatim into the prompt of its stage's node(s); "
          "review them and decide whether one needs code.", ""]
    for r in records:
        where = ", ".join(r["injected_into"]) or "(already literally in the prompt)"
        md.append(f"- **{r['brief']} line {r['line']}**, stage `{r['stage']}`, injected into {where}: {r['text']}")
    (out / "UNKNOWN.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"unknown_rules: {len(records)} sentence(s); injected {sum(bool(r['injected_into']) for r in records)}, "
          f"already present {sum(not r['injected_into'] for r in records)} -> {out / 'UNKNOWN.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
