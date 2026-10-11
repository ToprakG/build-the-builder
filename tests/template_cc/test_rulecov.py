"""rule coverage: splitter, capability checks on the p6 brief-a build (offline: regex pre-pass, no LLM), unknown-rule injection."""
import json
from pathlib import Path

from builder.template_cc import rulecov as rc
from builder.template_cc import unknown_rules as ur

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "out" / "template_cc" / "p6" / "brief-a"


def test_split_brief_keeps_normative_sentences_and_table_rows():
    text = "# T\n## 9. Getting\n30. A flat refusal uses one attempt. Freya Bank is fictional.\n| a | b |\n|---|---|\n| `paid` | none |\n"
    s = rc.split_brief(text, "x")
    assert [x["text"] for x in s][0].startswith("A flat refusal") and len(s) == 2 and s[1]["table"]


def test_check_marks_prompt_only_rules():
    if not BUILD.exists():
        return
    pol, wf = json.loads((BUILD / "policy.json").read_text()), json.loads((BUILD / "workflow.json").read_text())
    sents = rc.split_brief((REPO / "briefs" / "brief-a.md").read_text(), "brief-a")
    for s in sents:
        s["normative"], s["types"], s["stage"] = True, s["regex_types"] or ["verbatim-line"], s["stage_guess"]
    by = {r["text"]: r for r in rc.check(sents, pol, wf)}
    assert by["Say that the rest stays overdue."]["enforced_by"] == "prompt"
    assert by['Never ask "how much can you pay?".']["enforced_by"] in ("prompt", "missing")
    assert by["Brief A never uses `add_note`."]["enforced_by"] == "code"


def test_unknown_rules_injected_once():
    wf = {"system_prompt": "base", "nodes": {"identity": {"task_message": "job"}}, "global_nodes": {}}
    u = [{"brief": "b", "line": 1, "stage": "identity", "text": "Send four digits."},
         {"brief": "b", "line": 2, "stage": "global", "text": "Mind the gap."}]
    ur.apply(wf, u)
    ur.apply(wf, u)
    assert wf["nodes"]["identity"]["task_message"].count("Send four digits.") == 1
    assert wf["system_prompt"].count("Mind the gap.") == 1
