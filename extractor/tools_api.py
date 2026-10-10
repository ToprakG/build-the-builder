"""Parse tools.openapi.json. No model. The seven tools, their fields, and get_account's products."""
import json
import re


SEVEN = [
    "verify_identity", "get_account", "create_promise", "add_note",
    "schedule_callback", "transfer_to_agent", "close_call",
]


def load(path):
    with open(path) as f:
        return json.load(f)


def _resolve(node, root, seen=None):
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not ref:
        return node
    seen = seen or set()
    if ref in seen:
        return {}
    seen.add(ref)
    cur = root
    for part in ref.lstrip("#/").split("/"):
        if not isinstance(cur, dict) or part not in cur:
            return node
        cur = cur[part]
    return _resolve(cur, root, seen) if isinstance(cur, dict) else cur


def _schema(node, root):
    node = _resolve(node if isinstance(node, dict) else {}, root)
    if not isinstance(node, dict):
        return {}
    return node


def _body(api, name):
    path = api.get("paths", {}).get("/" + name, {})
    post = path.get("post") or path.get("get") or {}
    content = (post.get("requestBody") or {}).get("content") or {}
    json_body = content.get("application/json") or next(iter(content.values()), {})
    return _schema(json_body.get("schema") or {}, api), (post.get("summary") or "")


def _response(api, name):
    path = api.get("paths", {}).get("/" + name, {})
    post = path.get("post") or path.get("get") or {}
    responses = post.get("responses") or {}
    ok = responses.get("200") or responses.get("201") or {}
    content = ok.get("content") or {}
    json_body = content.get("application/json") or next(iter(content.values()), {})
    return _schema(json_body.get("schema") or {}, api)


def _fields(schema, api):
    schema = _schema(schema, api)
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    out = {}
    for name, spec in props.items():
        spec = _schema(spec, api)
        item = {"type": spec.get("type") or "string"}
        if spec.get("enum") is not None:
            item["enum"] = list(spec["enum"])
        if spec.get("format"):
            item["format"] = spec["format"]
        if spec.get("description"):
            item["description"] = spec["description"]
        item["required"] = name in required
        out[name] = item
    return out


def _product_fields(response, api):
    props = _fields(response, api)
    products = (response.get("properties") or {}).get("products") or {}
    products = _schema(products, api)
    items = _schema(products.get("items") or {}, api)
    return _fields(items, api)


def _enums_in(description, enums):
    if not description:
        return []
    text = description.lower().replace("_", " ")
    raw = description.lower()
    hits = []
    for enum in enums:
        token = enum.lower().replace("_", " ")
        if token and (token in text or enum.lower() in raw):
            hits.append(enum)
    if hits:
        return hits
    if re.search(r"\bloans?\b", text):
        return [e for e in enums if "loan" in e.lower() or e.lower() in ("pl", "al")]
    if re.search(r"\bcards?\b", text):
        return [e for e in enums if "card" in e.lower() or e.lower() == "cc"]
    return []


def _role_tools(specs, products):
    """The config.json tools section: which API field plays which role."""
    vi = specs["verify_identity"]["request"]
    enum_name = next(n for n, f in vi.items() if n != "customer_id" and f.get("enum"))
    answer_name = next(n for n, f in vi.items() if n not in ("customer_id", enum_name))
    cp = specs["create_promise"]["request"]
    amount_name, date_name, product_name = None, None, None
    for name, field in cp.items():
        if name == "customer_id":
            continue
        desc = (field.get("description") or "").lower()
        if field.get("format") == "date" or "date" in name:
            date_name = name
        elif field.get("type") in ("number", "integer") or "amount" in name or "cent" in desc:
            amount_name = name
        else:
            product_name = name
    amount = cp.get(amount_name) or {}
    unit = "cents" if amount.get("type") == "integer" or "cent" in (amount.get("description") or "").lower() else "currency"
    sc = specs["schedule_callback"]["request"]
    sc_fields = [n for n in sc if n != "customer_id"]
    if len(sc_fields) == 1:
        schedule = {"mode": "datetime", "datetime": sc_fields[0]}
    else:
        date_f = next((n for n in sc_fields if "date" in n and "time" not in n), sc_fields[0])
        time_f = next((n for n in sc_fields if n != date_f), sc_fields[-1])
        schedule = {"mode": "date_time", "date": date_f, "time": time_f}
    cc = specs["close_call"]["request"]
    if "outcome" in cc and "reason" in cc:
        close = {"mode": "outcome_reason", "outcome": "outcome", "reason": "reason"}
    else:
        code_name = next(n for n in cc if n != "customer_id")
        close = {"mode": "code", "code": code_name}
    tf = specs["transfer_to_agent"]["request"]
    queue_name = next((n for n, f in tf.items() if f.get("enum")), "queue")
    an = specs["add_note"]["request"]
    reason_name = next(n for n, f in an.items() if f.get("enum"))
    pid = products and next((n for n in products if n in an), None)
    if not pid:
        pid = next(n for n in an if n not in ("customer_id", reason_name, "text"))
    return {
        "verify_identity": {"field": enum_name, "answer": answer_name},
        "create_promise": {"product_id": product_name, "amount": amount_name, "date": date_name, "amount_unit": unit},
        "schedule_callback": schedule,
        "close_call": close,
        "transfer_to_agent": {"queue": queue_name},
        "add_note": {"product_id": pid, "reason": reason_name},
    }


_KEYS = {
    "credit_card": "card", "personal_loan": "loan", "car_loan": "car",
    "business_card": "bcard", "business_loan": "bloan", "overdraft": "overdraft",
    "cc": "card", "pl": "loan", "al": "car",
}


def _account(products):
    type_name = next(n for n, f in products.items() if f.get("enum"))
    enums = list(products[type_name]["enum"])
    days_name = next(n for n, f in products.items() if f.get("type") == "integer")
    numbers = [n for n, f in products.items() if f.get("type") == "number"]
    floors = [n for n in numbers if "only" in (products[n].get("description") or "").lower()]
    past = [n for n in numbers if n not in floors]
    if not past:
        past = [n for n in numbers if not re.search(r"min|floor|instal", n)]
    strings = [n for n, f in products.items() if f.get("type") == "string" and n != type_name and not f.get("enum")]
    pid = next((n for n in strings if re.search(r"id|ref", n)), strings[0])
    last4s = [n for n in strings if n != pid]
    return {
        "product_id": pid,
        "type": type_name,
        "past_due": past[0],
        "days_late": days_name,
    }, enums, floors, last4s


def _product_skeleton(products):
    account, enums, floors, last4s = _account(products)
    by_enum = {e: {"enum": e, "floor_field": None, "last4_field": None} for e in enums}
    for floor in floors:
        hits = _enums_in(products[floor].get("description") or "", enums)
        for enum in hits:
            by_enum[enum]["floor_field"] = floor
    for last4 in last4s:
        hits = _enums_in(products[last4].get("description") or "", enums)
        targets = hits or ([e for e in enums if "card" in e.lower() or e.lower() == "cc"] if len(last4s) == 1 else [])
        for enum in targets:
            by_enum[enum]["last4_field"] = last4
    ordered = {}
    for enum in enums:
        key = _KEYS.get(enum.lower(), re.sub(r"[^a-z0-9]+", "_", enum.lower()).strip("_"))
        ordered[key] = by_enum[enum]
    return account, ordered


def _id_format(field_name):
    """The brief rarely prints an id. product_id is p1; an account_ref is acc-1."""
    if field_name == "account_ref":
        return "acc-{n}"
    return "p{n}"


def parse(api):
    specs = {}
    for name in SEVEN:
        request, summary = _body(api, name)
        specs[name] = {"summary": summary, "request": _fields(request, api), "response": _fields(_response(api, name), api)}
    products = _product_fields(_response(api, "get_account"), api)
    specs["get_account"]["products"] = products
    account, skeleton = _product_skeleton(products)
    tools = _role_tools(specs, products)
    vi = specs["verify_identity"]
    hint = vi["response"].get("hint") or {}
    swap = "hint" in vi["response"] and (not hint.get("enum") or "day_month_swapped" in hint.get("enum"))
    wrong = None
    m = re.search(r"(\d+)\s+wrong answers", vi["summary"], re.I)
    if m:
        wrong = int(m.group(1))
    title = (api.get("info") or {}).get("title") or ""
    bank = re.sub(r"\s+collections tools$", "", title).strip() or None
    close = specs["close_call"]["request"]
    return {
        "specs": specs,
        "tools": tools,
        "account_fields": account,
        "products": skeleton,
        "questions": list((vi["request"].get(tools["verify_identity"]["field"]) or {}).get("enum") or []),
        "swap_hint": swap,
        "max_wrong_hint": wrong,
        "product_id_format": _id_format(account["product_id"]),
        "bank": bank,
        "note_enum": list((specs["add_note"]["request"].get(tools["add_note"]["reason"]) or {}).get("enum") or []),
        "queue_enum": list((specs["transfer_to_agent"]["request"].get(tools["transfer_to_agent"]["queue"]) or {}).get("enum") or []),
        "outcome_enum": list((close.get("outcome") or close.get(tools["close_call"].get("code", "code")) or {}).get("enum") or []),
        "reason_enum": list((close.get("reason") or {}).get("enum") or []),
        "close_mode": tools["close_call"]["mode"],
    }
