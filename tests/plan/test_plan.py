"""The procedure compiler. Product words live in the plan, not in builder/plan."""

from __future__ import annotations

import json
import re
from pathlib import Path

from builder.agentic.tools import lint as alint
from builder.agentic.tools.openapi_summary import summarize
from builder.plan.compile import compile_plan, dump
from builder.plan.extract import extract, from_text, merge
from builder.plan.money import render_money, speak
from builder.plan.schema import PlanError, validate

ROOT = Path(__file__).resolve().parents[2]
PLAN_SRC = ROOT / "builder" / "plan"
BANNED = re.compile(
    r"\b(cards?|statements?|ekstre|debts?|credit|visa|mastercard|freya|lira|sterling|cutoff|customerNumber)\b"
    r"|borç|cut-off|cut_off",
    re.I,
)
CODES = re.compile(r"\b(TRY|USD|EUR|GBP)\b")


def _api(base: str, tools: dict) -> dict:
    paths = {}
    for name, spec in tools.items():
        props, required = {}, []
        for field, meta in spec["request"].items():
            leaf = {"type": meta.get("type", "string")}
            if meta.get("enum"):
                leaf["enum"] = meta["enum"]
            props[field] = leaf
            if meta.get("required"):
                required.append(field)
        resp = {}
        for field, meta in spec["response"].items():
            if meta.get("type") == "array":
                resp[field] = {"type": "array", "items": {"type": "object", "properties": {
                    k: {"type": "string"} for k in meta["items"]}}}
            else:
                resp[field] = {"type": meta.get("type", "string")}
        paths[spec["path"]] = {"post": {
            "operationId": name,
            "summary": spec.get("summary", name),
            "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": props, "required": required}}}},
            "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {
                "type": "object", "properties": resp}}}}},
        }}
    return {"openapi": "3.0.3", "info": {"title": "demo", "version": "1"},
            "servers": [{"url": base}], "paths": paths}


def _lint(plan, wf, spec):
    summary = summarize(spec)
    unused = sorted(set(summary["tools"]) - {s.get("tool") for s in plan["steps"] if s.get("tool")})
    return alint.lint(wf, summary, plan.get("call_variables"), unused)


def _ledger():
    spec = _api("https://mock.example/v1", {
        "list_items": {"path": "/items", "request": {"customerNumber": {"required": True}},
                       "response": {"items": {"type": "array", "items": ["id", "label"]}}},
        "read_item": {"path": "/items/read", "request": {
            "customerNumber": {"required": True}, "itemId": {"required": True}},
            "response": {"amount": {}, "due": {}}},
        "handoff": {"path": "/handoff", "summary": "Hand the call to a person", "request": {
            "customerNumber": {"required": True},
            "reason": {"required": True, "enum": ["person"]}}, "response": {}},
    })
    plan = {
        "language": "en", "clock": "now", "call_variables": ["customerNumber", "now"],
        "system": "You help a caller look up one item.",
        "money": {"unit": "major", "group_sep": ",", "template": "{major} {name} {minor}",
                  "zero": "no {name}", "absent": "balance",
                  "currencies": [{"code": "UNT", "name": "units", "minor_digits": 2}]},
        "lines": {
            "empty": {"text": "There is nothing to report."},
            "read": {"text": "The amount is {{spoken_amount}}. It is due {{due}}."},
            "ask": {"text": "Do you want a person, or are you done?"},
            "bye": {"text": "Goodbye."},
            "connecting": {"text": "I am connecting you now."},
        },
        "steps": [
            {"id": "load", "kind": "tool", "tool": "list_items",
             "inputs": {"customerNumber": {"from": "customerNumber"}},
             "outputs": [{"path": "data.items", "variable": "items", "list": True}],
             "after": [
                 {"op": "count", "list_path": "data.items", "into": "item_count"},
                 {"op": "at", "list_path": "data.items", "index": 0, "field": "id", "into": "only_id"},
             ],
             "branches": [
                 {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
                 {"variable": "item_count", "op": "equals", "value": "0", "goto": "empty"},
                 {"variable": "item_count", "op": "greater_than", "value": "8", "goto": "failed"},
                 {"goto": "read_item"},
             ]},
            {"id": "empty", "kind": "say", "line": "empty", "then": "bye"},
            {"id": "read_item", "kind": "tool", "tool": "read_item",
             "before": [{"op": "copy", "from": "only_id", "into": "chosen_id"}],
             "inputs": {"customerNumber": {"from": "customerNumber"}, "itemId": {"from": "chosen_id"}},
             "outputs": [{"path": "data.amount", "variable": "amount"}, {"path": "data.due", "variable": "due"}],
             "branches": [
                 {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
                 {"goto": "readout"},
             ]},
            {"id": "readout", "kind": "say", "line": "read", "then": "offer",
             "before": [{"op": "money", "from": "amount", "currency": "UNT", "into": "spoken_amount"}]},
            {"id": "offer", "kind": "ask", "line": "ask",
             "task": "Ask whether they want a person or they are done.",
             "extract": [{"name": "choice", "enum": ["person", "done"], "description": "person, or done"}],
             "branches": [
                 {"variable": "choice", "op": "equals", "value": "person", "goto": "to_person"},
                 {"variable": "choice", "op": "equals", "value": "done", "goto": "bye"},
             ],
             "else": "offer", "max_turns": 3, "on_max": "bye"},
            {"id": "to_person", "kind": "handoff", "tool": "handoff",
             "inputs": {"customerNumber": {"from": "customerNumber"}, "reason": {"literal": "person"}},
             "branches": [{"goto": "connecting"}]},
            {"id": "connecting", "kind": "end", "line": "connecting"},
            {"id": "bye", "kind": "end", "line": "bye"},
            {"id": "failed", "kind": "end"},
        ],
        "globals": [{"id": "person", "description": "The caller asks to speak with a person", "goto": "to_person"}],
    }
    return plan, spec


def _procedure():
    """The statement-debt shape. Every product word is in this plan; the compiler only sees the structure."""
    spec = _api("https://bank.example/v1", {
        "listCards": {"path": "/cards", "summary": "List the caller's cards",
                      "request": {"customerNumber": {"required": True}},
                      "response": {"cards": {"type": "array", "items": ["id", "name", "last4"]}}},
        "getStatement": {"path": "/statement", "summary": "Read one statement",
                         "request": {"customerNumber": {"required": True}, "cardId": {"required": True}},
                         "response": {"name": {}, "last4": {}, "cutoff": {}, "due": {}, "tryAmount": {},
                                      "tryStatus": {}, "usdAmount": {}, "eurAmount": {}, "gbpAmount": {}}},
        "getEmail": {"path": "/email", "summary": "Read the address on file",
                     "request": {"customerNumber": {"required": True}}, "response": {"address": {}}},
        "sendEmail": {"path": "/email/send", "summary": "Send the statement",
                      "request": {"customerNumber": {"required": True}, "cardId": {"required": True}},
                      "response": {}},
        "handoff": {"path": "/handoff", "summary": "Hand the call to a person",
                    "request": {"customerNumber": {"required": True},
                                "reason": {"required": True, "enum": ["pay", "person"]}},
                    "response": {}},
    })
    money = {"unit": "major", "in_decimal": "either", "group_sep": ".",
             "template": "{major} {name} {minor}", "zero": "{name} {absent}", "absent": "yok",
             "currencies": [
                 {"code": "TRY", "name": "Türk lirası", "minor_digits": 2},
                 {"code": "USD", "name": "Amerikan doları", "minor_digits": 2},
                 {"code": "EUR", "name": "euro", "minor_digits": 2},
                 {"code": "GBP", "name": "sterlin", "minor_digits": 2},
             ]}
    lines = {
        "none": {"text": "Uygun kart bulunmuyor."},
        "which": {"text": "Kartlarınız: {{card_names}}. Hangisini inceleyeyim?"},
        "no_statement": {"text": "Ekstre borcu bulunmuyor."},
        "readout": {"text": "{{card_name}}, son dört hane {{card_last4}}. Kesim {{cutoff}}, son ödeme {{due}}. "
                            "{{spoken_try}}. {{spoken_usd}}. {{spoken_eur}}. {{spoken_gbp}}."},
        "offer": {"text": "Kesim tarihini değiştirebilirim veya ekstreyi kayıtlı adrese gönderebilirim. Ne yapalım?"},
        "date": {"text": "Son ödeme tarihi {{due}}."},
        "no_email": {"text": "Kayıtlı uygun bir adres yok."},
        "email_read": {"text": "Kayıtlı adres {{email_address}}. Bu adrese göndereyim mi?"},
        "sent": {"text": "Ekstre bu adrese gönderildi."},
        "connecting": {"text": "Sizi bir temsilciye bağlıyorum."},
        "bye": {"text": "İyi günler."},
    }
    statement_outputs = [
        {"path": "data.name", "variable": "card_name"},
        {"path": "data.last4", "variable": "card_last4"},
        {"path": "data.cutoff", "variable": "cutoff"},
        {"path": "data.due", "variable": "due"},
        {"path": "data.tryAmount", "variable": "try_amount"},
        {"path": "data.tryStatus", "variable": "try_status"},
        {"path": "data.usdAmount", "variable": "usd_amount"},
        {"path": "data.eurAmount", "variable": "eur_amount"},
        {"path": "data.gbpAmount", "variable": "gbp_amount"},
    ]
    statement_branches = [
        {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
        {"variable": "try_status", "op": "equals", "value": "none", "goto": "no_statement"},
        {"goto": "readout"},
    ]
    plan = {
        "language": "tr", "clock": "now", "call_variables": ["customerNumber", "now"],
        "system": "Sen bir banka telefon hattının asistanısın. Belgelerdeki cümleleri söyle.",
        "money": money, "lines": lines,
        "steps": [
            {"id": "list_cards", "kind": "tool", "tool": "listCards",
             "inputs": {"customerNumber": {"from": "customerNumber"}},
             "outputs": [{"path": "data.cards", "variable": "cards", "list": True}],
             "after": [
                 {"op": "count", "list_path": "data.cards", "into": "card_count"},
                 {"op": "join", "list_path": "data.cards", "field": "name", "sep": ", ", "into": "card_names"},
                 {"op": "at", "list_path": "data.cards", "index": 0, "field": "id", "into": "only_id"},
             ],
             "branches": [
                 {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
                 {"variable": "card_count", "op": "equals", "value": "0", "goto": "say_none"},
                 {"variable": "card_count", "op": "equals", "value": "1", "goto": "read_one"},
                 {"goto": "ask_which"},
             ]},
            {"id": "say_none", "kind": "say", "line": "none", "then": "bye"},
            {"id": "ask_which", "kind": "ask", "line": "which",
             "task": "Kart adlarını söyle ve hangisi olduğunu sor. Başka bir şey ekleme.",
             "extract": [{"name": "picked_name", "description": "The item name the caller picked"}],
             "branches": [{"variable": "picked_name", "op": "is_set", "goto": "read_named"}],
             "else": "ask_which", "max_turns": 3, "on_max": "bye"},
            {"id": "read_one", "kind": "tool", "tool": "getStatement",
             "before": [{"op": "copy", "from": "only_id", "into": "chosen_id"}],
             "inputs": {"customerNumber": {"from": "customerNumber"}, "cardId": {"from": "chosen_id"}},
             "outputs": statement_outputs, "branches": statement_branches},
            {"id": "read_named", "kind": "tool", "tool": "getStatement",
             "before": [{"op": "select", "tool": "listCards", "list_path": "data.cards",
                         "where_field": "name", "where_var": "picked_name", "into": "chosen",
                         "fields": ["id", "name"]}],
             "inputs": {"customerNumber": {"from": "customerNumber"}, "cardId": {"from": "chosen_id"}},
             "outputs": statement_outputs, "branches": statement_branches},
            {"id": "no_statement", "kind": "say", "line": "no_statement", "then": "offer"},
            {"id": "readout", "kind": "say", "line": "readout", "then": "offer", "before": [
                {"op": "money", "from": "try_amount", "currency": "TRY", "into": "spoken_try"},
                {"op": "money", "from": "usd_amount", "currency": "USD", "into": "spoken_usd"},
                {"op": "money", "from": "eur_amount", "currency": "EUR", "into": "spoken_eur"},
                {"op": "money", "from": "gbp_amount", "currency": "GBP", "into": "spoken_gbp"},
            ]},
            {"id": "offer", "kind": "ask", "line": "offer",
             "task": "Seçenekleri söyle ve cevabı bekle.",
             "extract": [{"name": "choice", "enum": ["pay", "email", "date", "nothing"],
                          "description": "pay, email, date, or nothing"}],
             "branches": [
                 {"variable": "choice", "op": "equals", "value": "pay", "goto": "handoff_pay"},
                 {"variable": "choice", "op": "equals", "value": "email", "goto": "load_email"},
                 {"variable": "choice", "op": "equals", "value": "date", "goto": "say_date"},
                 {"variable": "choice", "op": "equals", "value": "nothing", "goto": "bye"},
             ],
             "else": "offer", "max_turns": 4, "on_max": "bye"},
            {"id": "say_date", "kind": "say", "line": "date", "then": "offer"},
            {"id": "load_email", "kind": "tool", "tool": "getEmail",
             "inputs": {"customerNumber": {"from": "customerNumber"}},
             "outputs": [{"path": "data.address", "variable": "email_address"}],
             "after": [{"op": "flag", "from": "email_address", "into": "has_email", "zero_is_empty": True}],
             "branches": [
                 {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
                 {"variable": "has_email", "op": "equals", "value": "yes", "goto": "ask_send"},
                 {"goto": "say_no_email"},
             ]},
            {"id": "say_no_email", "kind": "say", "line": "no_email", "then": "offer"},
            {"id": "ask_send", "kind": "ask", "line": "email_read",
             "task": "Adresi oku ve göndermek isteyip istemediğini sor.",
             "extract": [{"name": "send", "enum": ["yes", "no"], "description": "yes or no"}],
             "branches": [
                 {"variable": "send", "op": "equals", "value": "yes", "goto": "do_send"},
                 {"variable": "send", "op": "equals", "value": "no", "goto": "offer"},
             ],
             "else": "ask_send", "max_turns": 2, "on_max": "offer"},
            {"id": "do_send", "kind": "tool", "tool": "sendEmail",
             "inputs": {"customerNumber": {"from": "customerNumber"}, "cardId": {"from": "chosen_id"}},
             "branches": [
                 {"variable": "@failure", "op": "greater_than", "value": 0, "goto": "failed"},
                 {"goto": "say_sent"},
             ]},
            {"id": "say_sent", "kind": "say", "line": "sent", "then": "offer"},
            {"id": "handoff_pay", "kind": "handoff", "tool": "handoff",
             "inputs": {"customerNumber": {"from": "customerNumber"}, "reason": {"literal": "pay"}},
             "branches": [{"goto": "connecting"}]},
            {"id": "handoff_person", "kind": "handoff", "tool": "handoff",
             "inputs": {"customerNumber": {"from": "customerNumber"}, "reason": {"literal": "person"}},
             "branches": [{"goto": "connecting"}]},
            {"id": "connecting", "kind": "end", "line": "connecting"},
            {"id": "bye", "kind": "end", "line": "bye"},
            {"id": "failed", "kind": "end"},
        ],
        "globals": [{"id": "person", "description": "The caller asks to speak with a person", "goto": "handoff_person"}],
    }
    return plan, spec


def test_compiler_has_no_product_words():
    hits = []
    for path in PLAN_SRC.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BANNED.search(line) or CODES.search(line):
                hits.append(f"{path.name}:{i}:{line.strip()}")
    assert hits == []


def test_money_follows_the_plan_table():
    money = {"unit": "major", "in_decimal": "either", "group_sep": ".",
             "template": "{major} {name} {minor}", "zero": "{name} {absent}", "absent": "yok",
             "negative": "eksi ",
             "currencies": [{"code": "TRY", "name": "Türk lirası", "minor_digits": 2},
                            {"code": "USD", "name": "Amerikan doları", "minor_digits": 2}]}
    assert speak("1523.40", "TRY", money) == "1.523 Türk lirası 40"
    assert speak("1.523,40", "TRY", money) == "1.523 Türk lirası 40"
    assert speak("0", "USD", money) == "Amerikan doları yok"
    assert speak("", "USD", money) == "Amerikan doları yok"
    assert speak("-2.5", "USD", money) == "eksi 2 Amerikan doları 50"
    ns = {"vars": {}}
    exec(render_money(money) + "\nresult = speak_amount('1523.40', 'TRY')\n", ns)
    assert ns["result"] == speak("1523.40", "TRY", money)
    assert "{{" not in render_money(money) and "}}" not in render_money(money)


def test_ledger_compiles_and_lints():
    plan, spec = _ledger()
    wf = compile_plan(plan, spec)
    assert dump(wf) == dump(compile_plan(plan, spec))
    report = _lint(plan, wf, spec)
    assert report["errors"] == []
    text = dump(wf)
    assert "https://mock.example/v1/items" in text
    assert "item_count_n" in text and "greater_than" in text
    assert "{{now}}" in wf["system_prompt"] and "system clock" in wf["system_prompt"]
    assert wf["initial_node"] == "load_call"
    offer = wf["nodes"]["offer"]
    assert [t["target_node"] for t in offer["transitions"]] == ["to_person_call", "bye", "offer"]


def test_procedure_plan_carries_the_product_words():
    plan, spec = _procedure()
    wf = compile_plan(plan, spec)
    report = _lint(plan, wf, spec)
    assert report["errors"] == [], json.dumps(report["errors"], indent=1)
    text = dump(wf)
    assert "Türk lirası" in text
    assert "listCards" in text and "getStatement" in text and "sendEmail" in text
    after = wf["nodes"]["list_cards_after"]["transitions"]
    assert [t["target_node"] for t in after] == ["failed", "say_none", "read_one_before", "ask_which"]
    offer = wf["nodes"]["offer"]["transitions"]
    assert [t["target_node"] for t in offer] == [
        "handoff_pay_call", "load_email_call", "say_date", "bye", "offer"]
    readout = wf["nodes"]["readout"]
    assert readout["passthrough"] is True
    assert readout["on_enter"]["text"].startswith("{{card_name}}")
    assert "spoken_try" in wf["nodes"]["readout_before"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    # the same compiler, a different plan, does not mention this procedure's tools
    ledger_wf = compile_plan(*_ledger())
    assert "listCards" not in dump(ledger_wf)
    assert "list_items" not in text


def test_unknown_goto_is_rejected():
    plan, spec = _ledger()
    plan["steps"][0]["branches"][0]["goto"] = "missing"
    try:
        compile_plan(plan, spec)
    except PlanError as exc:
        assert any("missing" in e for e in exc.errors)
    else:
        raise AssertionError("expected PlanError")


def test_extract_keeps_a_quoted_value_and_a_ready_plan():
    documents = 'The assistant says exactly: "There is nothing to report."'
    a = {"lines": {"empty": {"text": "hello"}}, "steps": [{"id": "bye", "kind": "end"}]}
    b = {"lines": {"empty": {"text": "There is nothing to report."}},
         "_source": {"/lines/empty/text": "There is nothing to report."}}
    merged, notes = merge(a, b, documents)
    assert merged["lines"]["empty"]["text"] == "There is nothing to report."
    assert notes
    # a quote that is not in the documents does not replace the first reading
    b["_source"]["/lines/empty/text"] = "not in the documents"
    a["lines"]["empty"]["text"] = "hello"
    merged, _ = merge(a, b, documents)
    assert merged["lines"]["empty"]["text"] == "hello"
    ready = from_text(json.dumps(_ledger()[0]))
    assert ready["steps"][0]["tool"] == "list_items"
    assert validate(ready, summarize(_ledger()[1])) == []

    def call(system, user, purpose):
        assert "card" not in system.lower()
        assert "statement" not in system.lower()
        if purpose == "plan check":
            return {"steps": [{"id": "bye", "kind": "end", "text": "Goodbye."}], "language": "en"}
        return {"steps": [{"id": "bye", "kind": "end"}], "language": "en",
                "_source": {"/language": "en"}}

    plan, _notes = extract("Say goodbye.", "tool: none", model="test", call=call)
    assert plan["steps"][-1]["kind"] == "end"
