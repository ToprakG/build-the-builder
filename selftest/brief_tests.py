"""Scenarios from the brief text alone. A disagreement with the config is an extraction error."""
import json

ALLOW = (
    "identity.max_wrong",
    "identity.swap_hint",
    "callback.start",
    "callback.end",
    "callback.same_day",
    "holidays",
)


def _dig(config, path):
    current = config
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set(config, path, value):
    parts = path.split(".")
    current = config
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def _norm(text):
    return " ".join(str(text or "").split()).lower()


def _quoted(plain, quote):
    return bool(quote) and _norm(quote) in _norm(plain)


def _same_type(current, value):
    if isinstance(current, bool):
        return isinstance(value, bool)
    if isinstance(current, int) and not isinstance(current, bool):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(current, str):
        return isinstance(value, str)
    if isinstance(current, list):
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return False


def expectations(plain, trace):
    """8 to 10 brief scenarios plus literal field values the brief states."""
    from extractor.llm import complete, model_name
    message = (
        "Read only the brief. Return JSON: {\"scenarios\": [{\"name\", \"tool\", \"expectation\"}], "
        "\"expectations\": [{\"path\", \"value\", \"quote\"}]}.\n"
        "Write 8 to 10 scenarios. Each scenario names the next tool the agent should call "
        "(verify_identity, get_account, create_promise, schedule_callback, add_note, transfer_to_agent, close_call) "
        "and one sentence for what should happen.\n"
        "expectations may use only these paths: identity.max_wrong, identity.swap_hint, callback.start, "
        "callback.end, callback.same_day, holidays. value is a number, true or false, HH:MM, or a list of "
        "YYYY-MM-DD dates. quote is the brief words that state that value, copied exactly.\n\n"
        "BRIEF:\n" + plain
    )
    payload, row = complete(
        [{"role": "system", "content": "You read a brief and return JSON only."},
         {"role": "user", "content": message}],
        purpose="brief scenarios",
    )
    if trace:
        trace.model(row.get("model") or model_name(), f"brief scenarios ({row.get('seconds')}s)")
    if not isinstance(payload, dict):
        return [], []
    scenarios = [item for item in payload.get("scenarios") or [] if isinstance(item, dict)]
    found = [item for item in payload.get("expectations") or [] if isinstance(item, dict)]
    return scenarios[:10], found


def disagreements(plain, config, found):
    """A quoted brief value that differs from the config."""
    rows = []
    for item in found:
        path = item.get("path")
        if path not in ALLOW:
            continue
        if not _quoted(plain, item.get("quote")):
            continue
        value = item.get("value")
        current = _dig(config, path)
        if current is None or not _same_type(current, value) or current == value:
            continue
        rows.append({"path": path, "config": current, "brief": value, "quote": item.get("quote")})
    return rows


def reextract(plain, item, trace):
    """One focused question. Returns a new value, or None."""
    from extractor.llm import complete, model_name
    message = (
        f"The config has {item['path']} = {json.dumps(item['config'])}. "
        f"A quote from the brief says {json.dumps(item['quote'])}. "
        "If the quote is the rule for this field, return {\"value\": <the corrected value>}. "
        "If the quote is not the rule for this field, return {\"value\": null}.\n\n"
        "BRIEF:\n" + plain
    )
    payload, row = complete(
        [{"role": "system", "content": "You answer one field. Return JSON only."},
         {"role": "user", "content": message}],
        purpose=f"re-extract {item['path']}",
    )
    if trace:
        trace.model(row.get("model") or model_name(), f"re-extract {item['path']} ({row.get('seconds')}s)")
    if not isinstance(payload, dict):
        return None
    value = payload.get("value")
    if value is None or not _same_type(item["config"], value) or value == item["config"]:
        return None
    return value
