"""Deterministic validation, completion and conservative repair of policy.json.

- `validate_schema`: a small JSON-Schema subset validator (type, required, properties, additionalProperties, items,
  enum, pattern, minimum, minItems, maxItems, minLength). Standard library only.
- `check(policy, api, brief_text)`: schema + every reference against the tool API + calendar sanity + quotes.
- `complete(policy, api)`: fills the T-CC extension keys with generic defaults (never a brief value).
- `repair(policy, violations, api)`: sets anything still invalid to the conservative default from DESIGN.md §3.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path

SCHEMA = json.loads((Path(__file__).resolve().parent / "policy.schema.json").read_text(encoding="utf-8"))

CANONICAL_OUTCOMES = ("promise", "paid", "tr_restructuring", "tr_human", "tr_support", "np_refused", "np_hardship",
                      "np_dispute", "np_service_error", "nv_failed", "nv_declined", "nr_callback", "nr_no_callback",
                      "nr_wrong_number", "nr_deceased", "nr_unclear")
CANONICAL_QUEUES = ("restructuring", "human", "support")
CANONICAL_NOTES = ("refused", "hardship", "dispute", "paid", "service_error")
LINE_KEYS = ("opening", "right_party_question", "ask_again", "condolence", "debt_intro", "first_ask", "first_ask_multi",
             "confirm_question", "promise_confirmation", "ai", "scam", "failed_id", "paid_reply", "change_promise",
             "cannot_record", "no_info", "id_reason", "hardship_sympathy", "hint_retry", "goodbye")
ATTEMPT_DATES = ("end_of_week_roll", "end_of_week", "deadline", "today", "next_business_day")
ATTEMPT_AMOUNTS = ("full_or_floor", "full", "floor")


# --- schema ---------------------------------------------------------------------------------------------------------

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
          "null": type(None)}


def _is_type(v, t: str) -> bool:
    if t in ("integer", "number") and isinstance(v, bool):
        return False
    return isinstance(v, _TYPES[t])


def validate_schema(obj, schema: dict = SCHEMA, path: str = "") -> list[dict]:
    errs: list[dict] = []
    p = path or "/"
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(obj, x) for x in types):
            return [{"path": p, "msg": f"expected {'/'.join(types)}, got {type(obj).__name__}"}]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append({"path": p, "msg": f"{obj!r} not one of {schema['enum']}"})
    if isinstance(obj, str):
        if "pattern" in schema and not re.search(schema["pattern"], obj):
            errs.append({"path": p, "msg": f"{obj!r} does not match {schema['pattern']}"})
        if len(obj) < schema.get("minLength", 0):
            errs.append({"path": p, "msg": "too short"})
    if isinstance(obj, (int, float)) and not isinstance(obj, bool) and "minimum" in schema and obj < schema["minimum"]:
        errs.append({"path": p, "msg": f"{obj} < minimum {schema['minimum']}"})
    if isinstance(obj, list):
        if len(obj) < schema.get("minItems", 0):
            errs.append({"path": p, "msg": f"needs at least {schema['minItems']} item(s)"})
        if "maxItems" in schema and len(obj) > schema["maxItems"]:
            errs.append({"path": p, "msg": f"at most {schema['maxItems']} item(s)"})
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(obj):
                errs += validate_schema(v, schema["items"], f"{path}/{i}")
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append({"path": f"{path}/{k}", "msg": "missing"})
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in obj.items():
            if k in props:
                errs += validate_schema(v, props[k], f"{path}/{k}")
            elif isinstance(extra, dict):
                errs += validate_schema(v, extra, f"{path}/{k}")
            elif extra is False:
                errs.append({"path": f"{path}/{k}", "msg": "unknown key"})
    return errs


# --- text helpers -----------------------------------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " "})


def norm_text(s: str) -> str:
    s = str(s or "").translate(_QUOTES).lower()
    s = re.sub(r"[*_`#>|\\]", " ", s)
    s = s.replace('"', " ").replace("'", " ")
    s = re.sub(r"[\s]+", " ", s)
    return s.strip(" .,:;!?")


def quote_in(quote: str, brief: str) -> bool:
    q = norm_text(quote)
    if not q:
        return False
    nb = norm_text(brief)
    if q in nb:
        return True
    # Placeholders like {title} or {{title}} in a line stand for any short text in the brief.
    if "{" in quote:
        parts = [re.escape(norm_text(x)) for x in re.split(r"\{+[^{}]*\}+", quote)]
        pattern = r".{0,40}?".join(x for x in parts if x is not None)
        return bool(pattern and re.search(pattern, nb))
    return False


def get_pointer(obj, pointer: str):
    cur = obj
    for part in [p for p in pointer.split("/") if p != ""]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(pointer)
    return cur


def set_pointer(obj, pointer: str, value):
    parts = [p.replace("~1", "/").replace("~0", "~") for p in pointer.split("/") if p != ""]
    if not parts:
        raise KeyError("empty pointer")
    cur = obj
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        if last not in cur:
            raise KeyError(pointer)
        cur[last] = value


def pointer_exists(obj, pointer: str) -> bool:
    try:
        get_pointer(obj, pointer)
        return True
    except (KeyError, IndexError, ValueError, TypeError):
        return False


# --- brief-text rules (generic, no brief values) -------------------------------------------------------------------------

_NUMS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}
_N = r"(\d+|" + "|".join(w for w in _NUMS if w not in ("zero", "one")) + r")"
_THING = r"(?:products?|accounts?|loans?|cards?|credit lines?|facilities)"
_MULTI = [
    re.compile(r"\b(?:up to|at most|maximum of|max(?:imum)?|no more than|limit(?:ed)? to)\s+" + _N + r"\s+(?:\w+\s+){0,2}" + _THING, re.I),
    re.compile(r"\b(?:one|1) to " + _N + r"\s+(?:\w+\s+){0,2}" + _THING, re.I),
    re.compile(r"\b" + _N + r"\s+(?:\w+\s+){0,2}" + _THING.replace("?", "") + r"\s+(?:per customer|at a time|at once|each|in total)", re.I),
    re.compile(r"\b(?:more than one|multiple|several|two or more|a few)\s+(?:\w+\s+){0,2}" + _THING, re.I),
]


def brief_multi_product(brief: str) -> int:
    """Largest product count the brief states a customer can have (>= 2), or 0 when it states none. Generic phrasing only."""
    best = 0
    for rx in _MULTI:
        for m in rx.finditer(brief or ""):
            g = m.group(1) if m.groups() and m.group(1) else None
            n = 2 if g is None else (int(g) if g.isdigit() else _NUMS.get(g.lower(), 0))
            if n >= 2:
                best = max(best, n)
    return best


def apply_multi_product_rule(policy: dict, brief: str) -> str | None:
    """An LLM multi_product claim needs a statement in the brief. Returns a note when the policy was changed."""
    n = brief_multi_product(brief)
    if n:
        return None
    if policy.get("multi_product") or (policy.get("max_products") or 1) > 1 or policy.get("max_products") is None:
        policy["multi_product"], policy["max_products"] = False, 1
        return "multi_product forced to false / max_products 1: the brief states no customer with more than one product"
    return None


# --- completion -------------------------------------------------------------------------------------------------------


def _line(text, verbatim=False):
    return {"text": text, "verbatim": verbatim}


def _sentence(s: str) -> str:
    s = str(s or "").strip()
    if not s:
        return s
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".?!" else s + "."


def default_lines(policy: dict) -> dict:
    """Speakable fallbacks built from the canonical `sentences` and the address. Generic wording only."""
    from builder.template_cc.compiler import i18n as I18N  # lazy: the compiler package imports this module
    s = policy.get("sentences") or {}
    addr = policy.get("address") or ""
    out = {
        "opening": _line(_sentence("Hello, this is " + s["opening"]) if s.get("opening") else "Hello."),
        "right_party_question": _line(f"Am I speaking with {addr}?" if addr else "Am I speaking with the account holder?"),
        "condolence": _line("I am sorry for your loss."),
        "debt_intro": _line("I am calling about a late payment."),
        "first_ask": _line("Can you pay this amount today?"),
        "first_ask_multi": _line("Can you pay all of these today?"),
        "confirm_question": _line("Do you confirm?"),
        "ai": _line(_sentence("I am " + s["ai"]) if s.get("ai") else "I am a digital assistant."),
        "scam": _line(_sentence("You can " + s["scam"]) if s.get("scam") else "You can call the bank to check this call."),
        "failed_id": _line(_sentence("Please " + s["failed_id"]) if s.get("failed_id") else "I cannot continue without verification."),
        "paid_reply": _line("Thank you. A payment can take a little time to show on the account."),
        "change_promise": _line("A recorded promise cannot be changed on this call."),
        "cannot_record": _line("I am sorry, I could not record that."),
        "no_info": _line("I do not have that information."),
        "id_reason": _line("These questions protect your account."),
        "hardship_sympathy": _line("I am sorry to hear that."),
        "hint_retry": _line("Could you repeat that, please, one part at a time?"),
        "goodbye": _line("Thank you for your time. Goodbye."),
    }
    if str(policy.get("language") or "en").lower()[:2] == "tr":
        T = I18N.TR_LINES_ADDR
        out.update({k: _line(v) for k, v in I18N.TR_LINES.items() if k != "promise_confirmation"})
        out["opening"] = _line(T["opening"].format(opening=_sentence(s["opening"])) if s.get("opening") else T["opening_none"])
        out["right_party_question"] = _line(T["right_party_question"].format(addr=addr) if addr else T["right_party_question_none"])
        for k in ("ai", "scam", "failed_id"):
            if s.get(k):
                out[k] = _line(_sentence(s[k]))
    out["ask_again"] = copy.deepcopy(out["right_party_question"])
    if s.get("promise_confirmation"):
        out["promise_confirmation"] = _line(s["promise_confirmation"], True)
    else:
        out["promise_confirmation"] = _line(I18N.TR_LINES["promise_confirmation"] if str(policy.get("language") or "en").lower()[:2] == "tr"
                                            else "Your promise is recorded.")
    return out


def complete(policy: dict, api=None) -> dict:
    """A copy with every T-CC extension key present. Existing values are kept."""
    p = copy.deepcopy(policy)
    p.setdefault("call_variables", [])
    cv = list(p["call_variables"])
    for v in re.findall(r"\{\{?\s*([A-Za-z_]\w*)\s*\}?\}", p.get("address") or ""):
        if v not in cv:
            cv.append(v)
    for v in ("customer_id", "now"):
        if v not in cv:
            cv.append(v)
    p["call_variables"] = cv
    lines = default_lines(p)
    lines.update({k: v for k, v in (p.get("lines") or {}).items() if isinstance(v, dict) and v.get("text")})
    p["lines"] = lines
    p.setdefault("attempt_plan", [{"date": "end_of_week_roll", "amount": "full_or_floor", "cap_at_deadline": True},
                                  {"date": "deadline", "amount": "floor", "cap_at_deadline": True}])
    ww = p.get("weekday_words") or {}
    p["weekday_words"] = {"next": ww.get("next", "following_week"), "this": ww.get("this", "current_week")}
    retries = {"verify_identity": 1, "get_account": 1, "create_promise": 1, "schedule_callback": 1}
    retries.update(p.get("retries") or {})
    p["retries"] = retries
    limits = {"right_party": 2, "id_decline": 1, "callback_reask": 1, "vague_date": 1, "stop_talking": 2,
              "human_offer": 1}
    limits.update(p.get("ask_limits") or {})
    p["ask_limits"] = limits
    p.setdefault("extra_rules", [])
    p.setdefault("_source", {})
    p.setdefault("_overrides", [])
    ident = p.setdefault("identity", {})
    ident.setdefault("prompts", {})
    ident.setdefault("kinds", {})
    for q in ident.get("questions") or []:
        ident["kinds"].setdefault(q, infer_kind(q, api))
        ident["prompts"].setdefault(q, "What is your " + q.replace("_", " ") + "?")
    p.setdefault("unused_tools", [])
    for key in ("outcome_map", "queue_map", "note_map"):
        p.setdefault(key, {})
    return p


def infer_kind(field: str, api=None) -> str:
    """date / year / digits / code / text from the field's generic name parts and the API description."""
    desc = ""
    if api is not None:
        desc = " ".join(str((api.request("verify_identity").get(f) or {}).get("description", "")) for f in ("answer", "field"))
    low = field.lower()
    if "year" in low:
        return "year"
    if "date" in low or "dob" in low:
        return "date"
    if re.search(r"last\d|digits|pin|number", low):
        return "digits"
    if "code" in low or "ref" in low:
        return "code"
    if field.lower() in desc.lower() and "digit" in desc.lower():
        return "digits"
    return "text"


# --- semantic checks ------------------------------------------------------------------------------------------------


def _hhmm(s) -> dt.time | None:
    try:
        return dt.time.fromisoformat(str(s))
    except ValueError:
        return None


def check(policy: dict, api, brief_text: str) -> list[dict]:
    """Every violation as {path, msg}. `api` is an ApiModel."""
    errs = validate_schema(policy)
    if any(e["msg"] == "missing" and e["path"].count("/") == 1 for e in errs):
        return errs  # top-level keys missing: the rest would only repeat it
    tools = policy.get("tools") or {}

    def need_field(tool, key, ptr):
        name = (tools.get(tool) or {}).get(key)
        if name and api.has(tool) and name not in api.request(tool):
            errs.append({"path": ptr, "msg": f"{tool} has no request field {name!r} (fields: {', '.join(api.request(tool))})"})

    for tool, keys in (("verify_identity", ("field", "answer")), ("create_promise", ("product_id", "amount", "date")),
                       ("transfer_to_agent", ("queue",)), ("add_note", ("product_id", "reason"))):
        for k in keys:
            need_field(tool, k, f"/tools/{tool}/{k}")
    sc = tools.get("schedule_callback") or {}
    for k in (("datetime",) if sc.get("mode") == "datetime" else ("date", "time")):
        if not sc.get(k):
            errs.append({"path": f"/tools/schedule_callback/{k}", "msg": "missing field name for this mode"})
        else:
            need_field("schedule_callback", k, f"/tools/schedule_callback/{k}")
    cc = tools.get("close_call") or {}
    for k in (("code",) if cc.get("mode") == "code" else ("outcome", "reason")):
        if not cc.get(k):
            errs.append({"path": f"/tools/close_call/{k}", "msg": "missing field name for this mode"})
        else:
            need_field("close_call", k, f"/tools/close_call/{k}")

    # identity questions are the API's field values
    fenum = api.enum("verify_identity", (tools.get("verify_identity") or {}).get("field", "")) if api.has("verify_identity") else None
    for i, q in enumerate((policy.get("identity") or {}).get("questions") or []):
        if fenum is not None and q not in fenum:
            errs.append({"path": f"/identity/questions/{i}", "msg": f"{q!r} is not in the verify_identity enum {fenum}"})

    # outcome / queue / note values exist in the API enums
    if cc.get("mode") == "code":
        cenum = api.enum("close_call", cc.get("code", ""))
        renum = None
        oenum = cenum
    else:
        oenum = api.enum("close_call", cc.get("outcome", ""))
        renum = api.enum("close_call", cc.get("reason", ""))
    for k, v in (policy.get("outcome_map") or {}).items():
        if k not in CANONICAL_OUTCOMES:
            errs.append({"path": f"/outcome_map/{k}", "msg": f"unknown canonical key (use {', '.join(CANONICAL_OUTCOMES)})"})
        if not v:
            continue
        if oenum is not None and v[0] not in oenum:
            errs.append({"path": f"/outcome_map/{k}", "msg": f"{v[0]!r} not in the close_call enum {oenum}"})
        if v[1] is not None and renum is not None and v[1] not in renum:
            errs.append({"path": f"/outcome_map/{k}", "msg": f"reason {v[1]!r} not in the close_call reason enum {renum}"})
        if cc.get("mode") == "code" and v[1] is not None:
            errs.append({"path": f"/outcome_map/{k}", "msg": "close_call takes one code: the second item must be null"})
    qenum = api.enum("transfer_to_agent", (tools.get("transfer_to_agent") or {}).get("queue", ""))
    for k, v in (policy.get("queue_map") or {}).items():
        if k not in CANONICAL_QUEUES:
            errs.append({"path": f"/queue_map/{k}", "msg": f"unknown canonical key (use {', '.join(CANONICAL_QUEUES)})"})
        if v is not None and qenum is not None and v not in qenum:
            errs.append({"path": f"/queue_map/{k}", "msg": f"{v!r} not in the transfer_to_agent enum {qenum}"})
    nenum = api.enum("add_note", (tools.get("add_note") or {}).get("reason", ""))
    for k, v in (policy.get("note_map") or {}).items():
        if k not in CANONICAL_NOTES:
            errs.append({"path": f"/note_map/{k}", "msg": f"unknown canonical key (use {', '.join(CANONICAL_NOTES)})"})
        if v is not None and nenum is not None and v not in nenum:
            errs.append({"path": f"/note_map/{k}", "msg": f"{v!r} not in the add_note reason enum {nenum}"})
    cr = policy.get("close_rule") or {}
    if cr.get("kind") == "priority":
        bad = [x for x in cr.get("order") or [] if x not in CANONICAL_NOTES]
        if not cr.get("order") or bad:
            errs.append({"path": "/close_rule/order", "msg": f"priority needs an order of canonical note keys; bad: {bad}"})
    nor = policy.get("notes_on_restructuring")
    if nor is not None and nor not in CANONICAL_NOTES:
        errs.append({"path": "/notes_on_restructuring", "msg": f"must be a canonical note key or null, got {nor!r}"})

    # product types: enum values exist in the API item enum when it declares one
    items = api.account_item_fields()
    af = policy.get("account_fields") or {}
    tenum = (items.get(af.get("type", "")) or {}).get("enum")
    for k, t in (policy.get("product_types") or {}).items():
        if tenum and t.get("enum") not in tenum:
            errs.append({"path": f"/product_types/{k}/enum", "msg": f"{t.get('enum')!r} not in the account type enum {tenum}"})
        if items and t.get("floor_field") and t["floor_field"] not in items:
            errs.append({"path": f"/product_types/{k}/floor_field", "msg": f"{t['floor_field']!r} is not an account field"})
    if items:
        for k, v in af.items():
            if v not in items:
                errs.append({"path": f"/account_fields/{k}", "msg": f"{v!r} is not an account field ({', '.join(items)})"})

    # calendar
    for i, h in enumerate(policy.get("holidays") or []):
        try:
            dt.date.fromisoformat(str(h))
        except ValueError:
            errs.append({"path": f"/holidays/{i}", "msg": f"{h!r} is not a valid date"})
    bands = sorted([b for b in policy.get("bands") or [] if isinstance(b, dict)], key=lambda b: b.get("min_days", 0))
    for i, b in enumerate(bands):
        if isinstance(b.get("min_days"), int) and isinstance(b.get("max_days"), int) and b["min_days"] > b["max_days"]:
            errs.append({"path": f"/bands/{i}", "msg": "min_days > max_days"})
        if i and isinstance(b.get("min_days"), int) and isinstance(bands[i - 1].get("max_days"), int):
            if b["min_days"] <= bands[i - 1]["max_days"]:
                errs.append({"path": f"/bands/{i}", "msg": "bands overlap"})
            elif b["min_days"] != bands[i - 1]["max_days"] + 1:
                errs.append({"path": f"/bands/{i}", "msg": "bands are not contiguous"})
    cb = policy.get("callback") or {}
    a, b = _hhmm(cb.get("start")), _hhmm(cb.get("end"))
    if a and b and not a < b:
        errs.append({"path": "/callback", "msg": "callback start must be before end"})

    # quotes
    for ptr, quote in (policy.get("_source") or {}).items():
        if not quote_in(quote, brief_text):
            errs.append({"path": f"/_source/{ptr}", "msg": "quote not found in the brief", "quote": quote})
    for k, line in (policy.get("lines") or {}).items():
        if isinstance(line, dict) and line.get("verbatim") and line.get("text") and not quote_in(line["text"], brief_text):
            errs.append({"path": f"/lines/{k}/text", "msg": "a verbatim line must appear in the brief"})
        if k not in LINE_KEYS:
            errs.append({"path": f"/lines/{k}", "msg": f"unknown line key (use {', '.join(LINE_KEYS)})"})
    for i, r in enumerate(policy.get("extra_rules") or []):
        if r.get("brief_quote") and not quote_in(r["brief_quote"], brief_text):
            errs.append({"path": f"/extra_rules/{i}/brief_quote", "msg": "quote not found in the brief"})
    # double braces would break generated code
    for s in _strings(policy):
        if "{{" in s or "}}" in s:
            errs.append({"path": "/", "msg": f"no double braces in policy values: {s[:60]!r}"})
            break
    return errs


def _strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from _strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from _strings(v)


# --- conservative repair ----------------------------------------------------------------------------------------------

DEFAULT_BAND = {"name": "default", "min_days": 1, "max_days": 9999, "deadline_business_days": 5, "attempts": 1}


def repair(policy: dict, violations: list[dict], api) -> tuple[dict, list[str]]:
    """Apply the documented conservative default for every remaining violation. Returns (policy, log lines)."""
    p = copy.deepcopy(policy)
    log: list[str] = []
    paths = [v["path"] for v in violations]

    def note(msg):
        log.append(msg)

    for v in violations:
        path = v["path"]
        if path.startswith("/holidays/"):
            p["holidays"] = [h for h in p.get("holidays") or [] if _valid_date(h)]
            note(f"dropped invalid holiday(s): {v['msg']}")
        elif path.startswith("/bands") or path == "/bands":
            p["bands"] = [dict(DEFAULT_BAND)]
            note(f"bands invalid ({v['msg']}): one default band, deadline 5 business days, 1 attempt")
        elif path == "/callback":
            cb = p.get("callback") or {}
            cb["start"], cb["end"] = sorted([cb.get("start", "09:00"), cb.get("end", "17:00")])
            cb["same_day"] = False
            p["callback"] = cb
            note("callback window invalid: start/end sorted, same_day false")
        elif path.startswith("/outcome_map/"):
            key = path.split("/")[2]
            p["outcome_map"][key] = None
            note(f"outcome_map.{key} invalid ({v['msg']}): route disabled")
        elif path.startswith("/queue_map/"):
            key = path.split("/")[2]
            p["queue_map"][key] = None
            note(f"queue_map.{key} invalid: transfer route disabled")
        elif path.startswith("/note_map/"):
            key = path.split("/")[2]
            p["note_map"][key] = None
            note(f"note_map.{key} invalid: note value dropped")
        elif path.startswith("/identity/questions/"):
            fenum = api.enum("verify_identity", p["tools"]["verify_identity"]["field"]) or []
            p["identity"]["questions"] = [q for q in p["identity"]["questions"] if q in fenum] or fenum[:2]
            note("identity questions outside the API enum dropped")
        elif path == "/identity/max_wrong":
            p["identity"]["max_wrong"] = 2
            note("identity.max_wrong invalid: 2")
        elif path.startswith("/_source/"):
            p["_source"][path[len("/_source/"):]] = "UNVERIFIED: " + str(v.get("quote", ""))[:200]
            note(f"quote not found, kept and flagged: {path}")
        elif path.startswith("/lines/") and path.endswith("/text"):
            key = path.split("/")[2]
            p["lines"][key]["verbatim"] = False
            note(f"line {key} not found verbatim in the brief: spoken as the model's paraphrase source")
        elif path.startswith("/lines/"):
            p["lines"].pop(path.split("/")[2], None)
            note(f"unknown line key dropped: {path}")
        elif path.startswith("/extra_rules/"):
            note(f"extra rule quote not found (kept): {path}")
        elif path == "/close_rule/order":
            p["close_rule"] = {"kind": "first_non_paid"}
            note("close_rule priority order invalid: first_non_paid")
        elif path == "/notes_on_restructuring":
            p["notes_on_restructuring"] = None
            note("notes_on_restructuring invalid: null")
        else:
            note(f"left as is (no default): {path}: {v['msg']}")
    if "/bands" in paths and not p.get("bands"):
        p["bands"] = [dict(DEFAULT_BAND)]
    return p, log


def _valid_date(h) -> bool:
    try:
        dt.date.fromisoformat(str(h))
        return True
    except ValueError:
        return False
