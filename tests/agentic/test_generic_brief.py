"""Ingest metadata, the conflict ledger, and lint against a tool set that is not the collections kit."""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from builder.agentic.tools.lint import lint
from builder.agentic.tools.openapi_summary import given_call_vars
from builder.agentic.tools.policy_suite import templates
from builder.ingest import ingest
from builder.ledger import build_ledger, classify_source, describe_source


def _src(path, text, **kw):
    meta = describe_source(path, text)
    meta.update(kw)
    meta["text"] = text
    return meta


def test_classify_and_dates():
    mail = "From: Ada Example <ada@example.com>\nDate: Mon, 1 Jun 2026 10:00:00 +0000\nSubject: note\n\nHello\n"
    assert classify_source("notes/2026-06-01-note.txt", mail) == "mail"
    described = describe_source("notes/2026-06-01-note.txt", mail)
    assert described["date"] == "2026-06-01"
    assert described["author"] == "Ada Example"
    assert classify_source("help.md", "Help centre\n\nSkip to content\n") == "FAQ/web page"
    assert classify_source("sheet.md", "Prompt sheet\n\nPrompt id | when\n") == "prompt sheet"
    assert classify_source("meet.md", "Meeting notes\n\nAttendees: Ada\n") == "meeting note"
    assert classify_source("meetings/2026-10-07-session-transcript.txt", "We agreed to ask once.") == "meeting note"
    assert classify_source("pic.png", "") == "drawing"
    assert classify_source("rows.json", '{"rows": [1, 2]}') == "data"
    assert classify_source("BRIEF.md", "The assistant must ask once.") == "spec"
    head = "Prompt sheet\n\nAda Example, operations, 10 Sep 2026\n"
    assert describe_source("ops/sheet.md", head)["date"] == "2026-09-10"
    assert describe_source("ops/sheet.md", head)["author"] == "Ada Example"


def test_ingest_records_meta_and_reads_json(tmp_path, monkeypatch):
    case = tmp_path / "case"
    case.mkdir()
    (case / "BRIEF.md").write_text("The assistant must greet the caller.")
    (case / "2026-04-02-note.txt").write_text(
        "From: Ada Example <ada@example.com>\nTo: Bo Example <bo@example.com>\n"
        "Date: Thu, 2 Apr 2026 09:00:00 +0000\nSubject: hello\n\nPlease ask once.\n")
    (case / "rows.json").write_text('{"rows": [{"id": "a"}]}')
    (case / "api.json").write_text(json.dumps({"openapi": "3.0.0", "paths": {"/a": {}}, "servers": [{"url": "https://tools.example/v1"}]}))
    monkeypatch.setattr("builder.ingest.fetch_openapi", lambda u: (_ for _ in ()).throw(OSError("down")))
    result = ingest(case, "http://mock.example", tmp_path / "out")
    text = Path(result["brief"]).read_text()
    assert "=== FILE: BRIEF.md ===" in text
    assert "type=mail" in text and "date=2026-04-02" in text and "author=Ada Example" in text
    assert '"rows"' in text
    kinds = {s["type"] for s in result["sources"]}
    assert "mail" in kinds and "data" in kinds and "spec" in kinds
    assert "api.json" not in text


def test_ledger_newer_mail_overrides_and_faq_does_not_win():
    spec = _src("2026-03-01-prompts.md", "Prompt sheet\n\nThe yearly limit is three changes.\n")
    mail = _src(
        "2026-04-01-note.txt",
        "From: Ada Example <ada@example.com>\nDate: Wed, 1 Apr 2026 09:00:00 +0000\nSubject: limit\n\n"
        "The yearly limit is still open. We will come back. Until then, transfer to an agent.\n",
    )
    faq = _src(
        "2026-05-01-help.md",
        "Help centre\n\nThe yearly limit is three changes a year and you can do it yourself.\n",
    )
    # filenames carry the dates; force them in case the body date differs
    spec["date"], mail["date"], faq["date"] = "2026-03-01", "2026-04-01", "2026-05-01"
    spec["type"], mail["type"], faq["type"] = "prompt sheet", "mail", "FAQ/web page"
    ledger = build_ledger([spec, mail, faq])
    rule = next(r for r in ledger["rules"] if "yearly" in " ".join(c["text"] for c in r["candidates"]))
    assert rule["chosen"]["type"] == "mail"
    assert "still open" in rule["chosen"]["text"]
    assert any(c["type"] == "FAQ/web page" for c in rule["candidates"])
    assert rule["chosen"]["type"] != "FAQ/web page"
    assert rule["open"] is True
    assert rule["safest"]["behavior"] == "hand_over_to_human"
    assert "not authoritative" in rule["why"]


def test_ledger_later_message_in_one_mail_wins():
    text = (
        "From: Bea Example <bea@example.com>\nDate: Thu, 9 Oct 2026 15:02:00 +0000\nSubject: replies\n\n"
        "Give three calming replies, then transfer to an agent.\n\n"
        "On 9 Oct 2026, at 10:14, Ada Example wrote:\n\n"
        "Give two calming replies, then transfer to an agent.\n"
    )
    ledger = build_ledger([_src("2026-10-09-note.txt", text, type="mail", date="2026-10-09")])
    rule = next(r for r in ledger["rules"] if "calming" in r["chosen"]["text"])
    assert "three calming" in rule["chosen"]["text"]
    assert len(rule["candidates"]) >= 2


def test_ledger_identity_comes_from_the_documents():
    older = _src("2026-01-01-rules.md", "Ask two security questions before giving any account detail.\n", type="spec", date="2026-01-01")
    newer = _src(
        "2026-06-01-note.txt",
        "From: Ada Example <ada@example.com>\nDate: Mon, 1 Jun 2026 10:00:00 +0000\nSubject: check\n\n"
        "Do not ask security questions. The check is already completed before the call is connected.\n",
        type="mail", date="2026-06-01",
    )
    page = _src("2026-07-01-help.md", "Help centre\n\nAsk security questions and a password before we can help.\n",
                type="FAQ/web page", date="2026-07-01")
    ledger = build_ledger([older, newer, page])
    rule = next(r for r in ledger["rules"] if "security" in " ".join(c["text"] for c in r["candidates"]))
    assert "already completed" in rule["chosen"]["text"]
    assert rule["chosen"]["type"] == "mail"


def test_given_call_vars_follow_the_api():
    assert given_call_vars({"tools": {"lookup": {"request": {"accountRef": {}, "when": {}}}}}) == set()
    assert given_call_vars({"tools": {"lookup": {"request": {"customer_id": {}, "now": {}}}}}) == {"customer_id", "now"}
    assert "customerNumber" not in given_call_vars({"tools": {"lookup": {"request": {"ref": {}}}}})


def test_policy_suite_skips_identity_without_a_check_and_keeps_it_when_declared():
    api = {"tools": {"lookup_item": {"request": {}, "response": {}}}}
    names = [t["template"] for t in templates({}, api)]
    assert "identity_last_wrong_answer" not in names
    assert "vague_date" not in names
    assert "below_minimum_amount" not in names
    assert "robot_question_opening" not in names
    checked = {"tools": {"check_party": {"request": {}, "response": {"matched": {"type": "boolean"}}}}}
    policy = {"tools": {"check_party": {"role": "identity"}}, "identity": {"questions": ["code"]}, "sentences": {}}
    again = [t["template"] for t in templates(policy, checked)]
    assert "identity_last_wrong_answer" in again


def test_lint_accepts_a_tool_set_that_is_not_the_kit():
    api = {"base_url": "https://tools.example", "tools": {
        "lookup_item": {"method": "POST", "path": "/lookup_item", "url": "https://tools.example/lookup_item",
                        "summary": "Look up a record", "request": {"ref": {"type": "string", "required": True}},
                        "required": ["ref"], "response": {"label": {"type": "string"}}, "statuses": {}},
    }}
    wf = {
        "initial_node": "start",
        "system_prompt": "Help the caller.",
        "nodes": {
            "start": {"node_type": "conversation", "task_message": "Greet the caller.",
                      "transitions": [{"target_node": "end", "condition": {"operator": "always"}}]},
            "end": {"node_type": "end_call", "transitions": [], "terminal": {"type": "function", "function": {
                "name": "end", "handler": "__function__:end_call",
                "parameters": {"type": "object", "properties": {"spoken_message": {"type": "string", "default": "Goodbye."}}}}}},
        },
    }
    report = lint(wf, api, [], ["lookup_item"])
    assert report["errors"] == []
    bad = json.loads(json.dumps(wf))
    bad["nodes"]["start"]["tools"] = [{"type": "function", "function": {
        "name": "verify_identity", "handler": "__function__:api_call",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "default": "https://tools.example/verify_identity"},
            "method": {"type": "string", "default": "POST"}}}}}]
    bad["nodes"]["start"]["task_message"] = "Call verify_identity when needed."
    errors = lint(bad, api, [], ["lookup_item"])["errors"]
    assert any("verify_identity" in e["msg"] and "not a tool" in e["msg"] for e in errors)
