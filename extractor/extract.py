"""Extract a brief into a config.

The tool API is read in code. Quoted sentences, tables, holidays and closing rules are read
from the brief in code, with a short quote kept as evidence. The model (temperature 0) reads
the same brief twice. A field the reader did not fill is taken from the model; if the two
readings disagree, one focused question decides it. A failed check is sent back once.
A verifier then maps every rule onto a field or onto other_rules, and unmapped rules get one fix.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from extractor.checks import REQUIRED, check
from extractor.llm import LLMError, complete
from extractor.propose import propose
from extractor.read_brief import read_files
from extractor.tools_api import load, parse

SYSTEM = """You extract a bank collections brief into one JSON config. Temperature is 0. Copy values from the brief. Do not invent a number, a date, a name or an enum.

Return one JSON object with these keys:
- config: the structured config
- evidence: a list of {"path", "quote"}. Every config value has one quote. The quote is copied from the brief, at most 30 words.
- conflicts: a list of {"path", "earlier", "later", "quote"}. A later memo, email or document overrides an earlier rule. List each override. Apply the later value in config. Use [] when nothing conflicts.
- other_rules: a list of strings. Prompt-only rules that are not one of the config fields: how to ask, what counts as yes, read-back, weekday phrases, sympathy, honesty when a tool fails, goodbye. Do not repeat a value that already has its own field.

config fields:
- language: "en" unless the brief names another language.
- address: the brace template in the identification question, such as "{title} {last_name}".
- identity: {"questions": [...], "max_wrong": N, "swap_hint": true|false}. questions are the verify_identity enum, in that order. swap_hint is true only if the brief or the tool response mentions day_month_swapped.
- callback: {"start": "HH:MM", "end": "HH:MM", "same_day": true|false}. same_day is false when a later line forbids same-day callbacks.
- outcome_map: canonical key to [outcome, reason]. reason is null when the outcome has none. In code mode, [code, null].
  Keys, all of them: promise, paid, tr_restructuring, tr_human, tr_support, np_refused, np_hardship, np_dispute, np_service_error, nv_failed, nv_declined, nr_callback, nr_no_callback, nr_wrong_number, nr_deceased, nr_unclear.
- queue_map: {"restructuring", "human", "support"} to the brief's queue values.
- note_map: {"refused", "hardship", "dispute", "paid", "service_error"} to the brief's note values. If the brief lists them in that parenthetical order, zip them. If the names are already those words, map each to itself.
- close_rule: {"kind": "first_non_paid"} or {"kind": "priority", "order": [canonical note keys]}. priority only when the brief ranks reasons. Otherwise first_non_paid, including a one-product brief.
- notes_on_restructuring: a canonical note key, or null. null when no note is written before a transfer.
- product_types: key to {"enum", "label", "floor_field", "last4_field"}. label is how the agent says the product. A card ending in digits becomes "credit card ending {last4}". last4_field is null when that product has none.
  Keys: credit_card→card, personal_loan→loan, car_loan→car, overdraft→overdraft, business_card→bcard, business_loan→bloan, CC→card, PL→loan, AL→car.
- account_fields, tools, product_id_format: copy the tool block below unchanged.
- forbidden_before_verify: the words the opening forbids, in order. Not the word "product".
- multi_product, max_products: true and 3 when a customer can have up to three products. A later document wins. "only one product" inside a note rule is not the cap.
- attempt1: when attempt 1 is the last business day of this week, or next week's if today is that day, and it may not pass the deadline, use exactly: "last business day of this week (next week's if today is that day), never after the deadline".
- id: the id given below. bank: the bank name. kind: "clean" for a specification, "raw" for an email, a memo that amends a policy, or a dialler export.
- holidays: ISO dates YYYY-MM-DD, in the order written. A dotted date DD.MM.YY is that calendar day.
- bands: [{"name", "min_days", "max_days", "deadline_business_days", "attempts"}] in order. They cover the brief's days-late range with no gap and no overlap. A later memo replaces the deadline it names.
- sentences:
  opening: the words after "say you are" or "say it is", then "; ", then the words after "and that". Example: "say you are a digital assistant calling from Example Bank, and that the call is recorded." → "a digital assistant calling from Example Bank; the call is recorded".
  ai: the words after "you are" or "it's" in the robot sentence, with no "and continue".
  failed_id: the words after "tell the customer to" or "tell them to" once security has failed.
  scam: the words the agent says about verifying the call. Start with "verify the call by" when the brief says "verify by". "our website" is written "the website".
  promise_confirmation: the quoted sentence said after a promise is recorded.

Do not add a field that is not listed. Do not drop a canonical outcome key."""


def _raw(paths):
    chunks = []
    for i, path in enumerate(paths, 1):
        chunks.append(f"===== DOCUMENT {i}: {path.name} =====\n" + path.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)


def _facts_block(facts, brief_id):
    skeleton = {
        key: {k: v for k, v in prod.items() if k != "label"}
        for key, prod in facts["products"].items()
    }
    block = {
        "id": brief_id,
        "bank": facts.get("bank"),
        "tools": facts["tools"],
        "account_fields": facts["account_fields"],
        "product_id_format": facts["product_id_format"],
        "identity_questions": facts["questions"],
        "swap_hint_from_api": facts["swap_hint"],
        "product_skeleton": skeleton,
        "note_enum": facts["note_enum"],
        "queue_enum": facts["queue_enum"],
        "close_mode": facts["close_mode"],
        "outcome_enum": facts["outcome_enum"],
        "reason_enum": facts["reason_enum"],
    }
    return json.dumps(block, indent=1)


def _messages(plain, facts, brief_id):
    user = (
        "TOOL BLOCK (copy tools, account_fields and product_id_format; identity questions are this enum):\n"
        + _facts_block(facts, brief_id)
        + "\n\nBRIEF:\n"
        + plain
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def _config_of(payload):
    if not isinstance(payload, dict):
        return {}
    config = payload.get("config")
    return config if isinstance(config, dict) else {}


def _leaf_diff(left, right):
    """Top-level and one-level paths whose JSON values differ."""
    paths = []
    keys = set(left) | set(right)
    for key in sorted(keys):
        a, b = left.get(key), right.get(key)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            for sub in sorted(set(a) | set(b)):
                if a.get(sub) != b.get(sub):
                    paths.append(f"{key}.{sub}")
        else:
            paths.append(key)
    return paths


def _focused(plain, path, reading_a, reading_b, trace):
    message = (
        "Two readings of the same brief disagree. A later passage overrides an earlier one. "
        "Answer with one JSON object {\"value\": ..., \"quote\": \"...\"}. "
        "value is the JSON value of this field after overrides. quote is copied from the brief.\n\n"
        f"Field: {path}\nReading A: {json.dumps(reading_a)}\nReading B: {json.dumps(reading_b)}\n\nBRIEF:\n{plain}"
    )
    payload, row = complete(
        [{"role": "system", "content": "You resolve one field of a collections brief. Return JSON only."},
         {"role": "user", "content": message}],
        purpose=f"resolve {path}",
    )
    trace.append(row)
    return payload.get("value") if isinstance(payload, dict) else None


def _dig(config, path):
    value = config
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _set(config, path, value):
    parts = path.split(".")
    cursor = config
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def _resolve(plain, first, second, draft, trace):
    """Return a model config. Ask one question per disagreed field the reader did not already fill."""
    a, b = _config_of(first), _config_of(second)
    disagreed = []
    for path in _leaf_diff(a, b):
        top = path.split(".", 1)[0]
        if draft.get(top) is not None and top not in ("identity", "callback", "sentences", "close_rule"):
            continue
        if top in ("tools", "account_fields", "product_id_format"):
            continue
        # A field the quote reader filled at the top level is not asked about.
        if "." not in path and draft.get(path) is not None:
            continue
        if "." in path and isinstance(draft.get(top), dict) and path.split(".", 1)[1] in draft[top] and draft[top].get(path.split(".", 1)[1]) is not None:
            continue
        disagreed.append(path)
    resolved = json.loads(json.dumps(a))
    for path in disagreed:
        value = _focused(plain, path, _dig(a, path), _dig(b, path), trace)
        if value is not None:
            _set(resolved, path, value)
    return resolved, disagreed


def publish(draft, model):
    """Quote-backed fields win. The model fills a field the reader left empty."""
    model = model or {}
    out = {}
    keys = list(dict.fromkeys(list(REQUIRED) + list(draft) + list(model)))
    for key in keys:
        if key in draft and draft[key] is not None:
            out[key] = draft[key]
        elif key in model:
            out[key] = model[key]
    out["notes_on_restructuring"] = draft.get("notes_on_restructuring")
    if "memo_changes" in draft:
        out["memo_changes"] = draft["memo_changes"]
    return out


def _repair(plain, config, errors, trace):
    message = (
        "The config failed these checks. Fix only what the errors name. "
        "A later memo overrides an earlier rule. Return {\"config\": ...} with the full config.\n\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\nCONFIG:\n" + json.dumps(config)
        + "\n\nBRIEF:\n" + plain
    )
    payload, row = complete(
        [{"role": "system", "content": SYSTEM}, {"role": "user", "content": message}],
        purpose="repair checks",
    )
    trace.append(row)
    return _config_of(payload) or config


def _verify(plain, config, trace):
    message = (
        "List every numbered rule and every implied rule in this brief (opening, identity, dates, amounts, "
        "promise, hardship, dispute, transfer, closing). For each, maps_to is a config path "
        "(for example holidays, bands, sentences.opening, callback.same_day, outcome_map, other_rules) "
        "or null when nothing in the config or in other_rules covers it.\n"
        "Return {\"rules\": [{\"id\", \"text\", \"maps_to\"}], \"unmapped\": [\"rule text\", ...]}.\n\n"
        "CONFIG:\n" + json.dumps(config) + "\n\nBRIEF:\n" + plain
    )
    payload, row = complete(
        [{"role": "system", "content": "You check that a config covers a brief. Return JSON only."},
         {"role": "user", "content": message}],
        purpose="verify rules",
    )
    trace.append(row)
    return payload if isinstance(payload, dict) else {"rules": [], "unmapped": []}


def _fix_unmapped(plain, config, unmapped, trace):
    message = (
        "These rules are not in the config or in other_rules. Add each one to other_rules, "
        "or set a config field when the rule is one of the structured fields. "
        "Do not change a field that is already correct. "
        "Return {\"other_rules\": [...all of them...], \"config_patch\": {only changed fields}}.\n\n"
        + "\n".join(f"- {item}" for item in unmapped)
        + "\n\nCONFIG:\n" + json.dumps(config)
        + "\n\nBRIEF:\n" + plain
    )
    payload, row = complete(
        [{"role": "system", "content": "You attach uncovered brief rules. Return JSON only."},
         {"role": "user", "content": message}],
        purpose="fix unmapped rules",
    )
    trace.append(row)
    return payload if isinstance(payload, dict) else {}


def _evidence_errors(evidence, plain):
    errors = []
    norm = " ".join(plain.split()).lower()
    if not isinstance(evidence, list) or not evidence:
        return ["evidence: every value needs a quote from the brief"]
    missing = 0
    for item in evidence:
        if not isinstance(item, dict):
            continue
        quote = " ".join(str(item.get("quote") or "").split()).lower()
        if quote and quote not in norm:
            missing += 1
    if missing:
        errors.append(f"evidence: {missing} quote(s) are not in the brief")
    return errors


def extract(paths, tools_path):
    facts = parse(load(tools_path))
    raw = _raw(paths)
    plain = read_files(paths)
    draft, conflicts = propose(raw, facts, paths)
    trace = []
    messages = _messages(plain, facts, draft["id"])
    first, row = complete(messages, purpose="extract")
    trace.append(row)
    try:
        second, row = complete(messages, purpose="extract repeat")
        trace.append(row)
    except LLMError as error:
        second = first
        trace.append({"model": row.get("model"), "purpose": "extract repeat skipped", "error": str(error)[:200]})
    resolved, asked = _resolve(plain, first, second, draft, trace)
    evidence = first.get("evidence") if isinstance(first, dict) else []
    model_conflicts = first.get("conflicts") if isinstance(first, dict) else []
    other = []
    for payload in (first, second):
        if isinstance(payload, dict):
            for rule in payload.get("other_rules") or []:
                if rule not in other:
                    other.append(rule)
    config = publish(draft, resolved)
    errors = check(config, facts, raw)
    repaired = False
    if errors:
        repaired_config = publish(draft, _repair(plain, config, errors, trace))
        repaired_errors = check(repaired_config, facts, raw)
        if len(repaired_errors) < len(errors):
            config = repaired_config
            errors = repaired_errors
        repaired = True
    try:
        verdict = _verify(plain, {**config, "other_rules": other}, trace)
    except LLMError as error:
        verdict = {"rules": [], "unmapped": []}
        trace.append({"purpose": "verify skipped", "error": str(error)[:200]})
    unmapped = [item for item in (verdict.get("unmapped") or []) if item]
    if unmapped:
        fixed = _fix_unmapped(plain, config, unmapped, trace)
        for rule in fixed.get("other_rules") or []:
            if rule not in other:
                other.append(rule)
        # A patch may fill a field the reader left empty. It may not replace a quoted value.
        patch = fixed.get("config_patch") or {}
        if isinstance(patch, dict):
            for key, value in patch.items():
                if key in ("tools", "account_fields", "product_id_format"):
                    continue
                if draft.get(key) is None and value is not None:
                    config[key] = value
            errors = check(config, facts, raw)
    seen = set()
    merged_conflicts = []
    for item in list(conflicts) + list(model_conflicts or []):
        if not isinstance(item, dict):
            continue
        key = item.get("path")
        if key in seen:
            continue
        seen.add(key)
        merged_conflicts.append({k: v for k, v in item.items() if k != "at"})
    return {
        "config": config,
        "model_config": resolved,
        "evidence": evidence,
        "conflicts": merged_conflicts,
        "other_rules": other,
        "unmapped": unmapped,
        "asked": asked,
        "checks": errors,
        "evidence_warnings": _evidence_errors(evidence, plain),
        "repaired": repaired,
        "trace": trace,
        "rules": verdict.get("rules") or [],
    }


def targets():
    rows = [
        ("brief-a", [ROOT / "briefs/brief-a.md"], ROOT / "tools/openapi.json", ROOT / "testbed/configs/brief-a.config.json"),
        ("brief-b", [ROOT / "briefs/brief-a.md", ROOT / "briefs/brief-b.md"], ROOT / "tools/openapi.json", ROOT / "testbed/configs/brief-b.config.json"),
    ]
    for path in sorted((ROOT / "testbed/fake_briefs").iterdir()):
        if (path / "config.json").exists():
            rows.append((path.name, [path / "brief.md"], path / "tools.openapi.json", path / "config.json"))
    return rows
