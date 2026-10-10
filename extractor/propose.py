"""Read structured fields out of the brief text. A later memo overrides an earlier rule."""
import re

from extractor.checks import CANON_NOTES, OUTCOME_KEYS

ATTEMPT1 = "last business day of this week (next week's if today is that day), never after the deadline"

_MONTH = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}
_PAIR = {
    ("promise", None): "promise", ("paid", None): "paid",
    ("transferred", "restructuring"): "tr_restructuring", ("transferred", "human"): "tr_human", ("transferred", "support"): "tr_support",
    ("no_promise", "refused"): "np_refused", ("no_promise", "hardship"): "np_hardship",
    ("no_promise", "dispute"): "np_dispute", ("no_promise", "service_error"): "np_service_error",
    ("not_verified", "failed"): "nv_failed", ("not_verified", "declined"): "nv_declined",
    ("not_reached", "callback"): "nr_callback", ("not_reached", "no_callback"): "nr_no_callback",
    ("not_reached", "wrong_number"): "nr_wrong_number", ("not_reached", "deceased"): "nr_deceased",
    ("not_reached", "unclear"): "nr_unclear",
}
_ALIASES = [
    ("business card", ["business_card"]),
    ("credit card", ["credit_card", "cc"]),
    ("business overdraft", ["overdraft"]),
    ("business loan", ["business_loan"]),
    ("personal loan", ["personal_loan", "pl"]),
    ("car loan", ["car_loan", "al"]),
    ("auto loan", ["car_loan", "al"]),
    ("overdraft", ["overdraft"]),
]
_PAREN = {
    "refused": "refused", "hardship": "hardship", "dispute": "dispute",
    "already paid": "paid", "paid": "paid", "service error": "service_error", "service_error": "service_error",
}
_CODE_RULES = [
    (r"promise to pay", "promise"),
    (r"already paid", "paid"),
    (r"restructuring", "tr_restructuring"),
    (r"human requested", "tr_human"),
    (r"hardship, support declined", "np_hardship"),
    (r"transferred - support", "tr_support"),
    (r"refused", "np_refused"),
    (r"disputed", "np_dispute"),
    (r"service failed", "np_service_error"),
    (r"failed security", "nv_failed"),
    (r"declined security", "nv_declined"),
    (r"callback booked", "nr_callback"),
    (r"no valid callback", "nr_no_callback"),
    (r"wrong number", "nr_wrong_number"),
    (r"deceased", "nr_deceased"),
    (r"unclear", "nr_unclear"),
]


def _ordinal(n):
    n = int(n)
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _last(pattern, text, flags=re.I):
    found = list(re.finditer(pattern, text, flags))
    return found[-1] if found else None


def _years(line):
    span = re.search(r"\b(20\d{2})/(\d{2})\b", line)
    if span:
        first = int(span.group(1))
        return [first, first // 100 * 100 + int(span.group(2))]
    return [int(y) for y in re.findall(r"20\d{2}", line)]


def _year_for(month, years):
    if not years:
        return None
    if len(years) == 1:
        return years[0]
    return min(years) if month >= 7 else max(years)


def _dates_on_line(line):
    import datetime as dt
    found = []
    for m in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", line):
        try:
            found.append(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat())
        except ValueError:
            pass
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", line):
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if day > 12 and month <= 12:
            pass
        elif month > 12 and day <= 12:
            day, month = month, day
        try:
            found.append(dt.date(year, month, day).isoformat())
        except ValueError:
            pass
    if found:
        return found
    years = _years(line)
    month_re = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    for m in re.finditer(rf"\b(\d{{1,2}})\s+({month_re})\b", line, re.I):
        month = _MONTH[m.group(2)[:3].lower()]
        year = _year_for(month, years)
        if not year:
            continue
        try:
            found.append(dt.date(year, month, int(m.group(1))).isoformat())
        except ValueError:
            pass
    return found


def holidays(text):
    found = []
    for line in text.splitlines():
        if re.search(r"holiday|we.?re closed|bank closed", line, re.I):
            found.extend(_dates_on_line(line))
    out = []
    for day in found:
        if day not in out:
            out.append(day)
    return out


def _band_from_table(text):
    rows = []
    pat = re.compile(
        r"\|\s*(\d+)\s*(?:to|-)\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*(?:the\s+)?(\d+)\s*(?:st|nd|rd|th)\s+business day[^|]*\|\s*(\d+)\s*\|",
        re.I,
    )
    for m in pat.finditer(text):
        rows.append({
            "name": m.group(3).strip().strip("`"),
            "min_days": int(m.group(1)),
            "max_days": int(m.group(2)),
            "deadline_business_days": int(m.group(4)),
            "attempts": int(m.group(5)),
        })
    return rows


def _band_from_prose(text):
    rows = []
    pat = re.compile(
        r"(Early|Later|Late)\s+arrears\s*\((\d+)\s*(?:-|to)\s*(\d+)\s+days[^)]*\)\s*:[^.]*?(\d+)\s*(?:st|nd|rd|th)\s+business day[^.]*?\b(ONE|TWO|THREE|\d+)\s+go",
        re.I,
    )
    names = {"early": "early", "later": "late", "late": "late"}
    for m in pat.finditer(text):
        word = m.group(5).lower()
        rows.append({
            "name": names[m.group(1).lower()],
            "min_days": int(m.group(2)),
            "max_days": int(m.group(3)),
            "deadline_business_days": int(m.group(4)),
            "attempts": _WORDS.get(word, int(word) if word.isdigit() else 1),
        })
    return rows


def _band_from_codes(text):
    rows = []
    pat = re.compile(r"^\s*(B\d+)\s+(\d+)\s*-\s*(\d+)\b.*?N\s*=\s*(\d+).*?(\d+)\s*$", re.I | re.M)
    for m in pat.finditer(text):
        rows.append({
            "name": m.group(1).upper(),
            "min_days": int(m.group(2)),
            "max_days": int(m.group(3)),
            "deadline_business_days": int(m.group(4)),
            "attempts": int(m.group(5)),
        })
    return rows


def _apply_memo_bands(text, rows, conflicts):
    m = _last(r"(\d+)\s+to\s+(\d+)\s+days late[^.]*?(?:now|is)\s+the\s+\**(\d+)\s*(?:st|nd|rd|th)\**\s+business day", text, re.I | re.S)
    if not m or not rows:
        return rows
    lo, hi, deadline = int(m.group(1)), int(m.group(2)), int(m.group(3))
    previous = re.search(r"previously the\s+\**(\d+)", m.group(0), re.I)
    for band in rows:
        if band["min_days"] == lo and band["max_days"] == hi and band["deadline_business_days"] != deadline:
            old = band["deadline_business_days"]
            band["deadline_business_days"] = deadline
            was = int(previous.group(1)) if previous else old
            conflicts.append({
                "path": f"bands.{band['name']}.deadline_business_days",
                "earlier": str(was),
                "later": str(deadline),
                "quote": " ".join(m.group(0).split())[:240],
                "at": m.start(),
            })
    return rows


def bands(text, conflicts):
    rows = _band_from_table(text) or _band_from_prose(text) or _band_from_codes(text)
    return _apply_memo_bands(text, rows, conflicts)


def callback(text, conflicts):
    times = [(m.start(), f"{int(m.group(1)):02d}:{m.group(2)}", f"{int(m.group(3)):02d}:{m.group(4)}")
             for m in re.finditer(r"(?:from|between)\s+(\d{1,2}):(\d{2})\s+(?:to|and)\s+(\d{1,2}):(\d{2})", text, re.I)]
    times += [(m.start(), m.group(1), m.group(2))
              for m in re.finditer(r"\b(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})\b", text)]
    start = end = None
    if times:
        times.sort()
        _, start, end = times[-1]
    flags = []
    for m in re.finditer(r"today or (?:the )?next business day", text, re.I):
        flags.append((m.start(), True, m.group(0)))
    for m in re.finditer(r"next business day only|no longer book same-day|no same-day|must be on the \**next business day", text, re.I):
        flags.append((m.start(), False, m.group(0)))
    same = None
    if flags:
        flags.sort()
        if any(item[1] for item in flags) and any(not item[1] for item in flags):
            conflicts.append({
                "path": "callback.same_day",
                "earlier": "today or the next business day",
                "later": "the next business day only",
                "quote": flags[-1][2],
                "at": flags[-1][0],
            })
        same = flags[-1][1]
    return {"start": start, "end": end, "same_day": same}


def _word_list(chunk):
    words = []
    for part in re.split(r",|\bor\b", chunk):
        word = part.strip(" .`\"'")
        if word and "product" not in word.lower() and len(word.split()) == 1:
            words.append(word)
    return words


def _forbidden(text):
    found = []
    for m in re.finditer(r"Do not say\s+(.+?)(?:\s+or the product|\s+before)", text, re.I):
        found.append((m.start(), re.findall(r'"([^"]+)"', m.group(1))))
    for m in re.finditer(r"Nothing about\s+(.+?)\s+the product", text, re.I):
        found.append((m.start(), _word_list(m.group(1))))
    for m in re.finditer(r"No mention of\s+(.+?)(?:\s+or the product|\s+before)", text, re.I):
        found.append((m.start(), _word_list(m.group(1))))
    if not found:
        return []
    found.sort()
    return found[-1][1]


def _address(text):
    m = _last(r'Am I speaking with\s+([^"?\n]+)\?', text)
    if not m:
        return None
    raw = m.group(1).strip().rstrip(".")
    raw = raw.replace("<title>", "{title}").replace("<last_name>", "{last_name}").replace("<surname>", "{last_name}")
    raw = re.sub(r"\bMr/Ms\b", "{title}", raw)
    return raw


def _opening(text):
    m = _last(r"say you are\s+(.+?),\s+and that\s+(.+?)\.", text)
    if not m:
        m = _last(r"say it is\s+(.+?)\s+and that\s+(.+?)\.", text)
    if m:
        return f"{m.group(1).strip()}; {m.group(2).strip()}"
    m = _last(r'OPEN:\s*"([^"]+)"', text)
    if not m:
        return None
    line = m.group(1).strip()
    line = line.replace("this call is recorded", "the call is recorded")
    if not re.match(r"^(a|an)\s", line, re.I):
        line = ("an " if line[:1].lower() in "aeiou" else "a ") + line
    return line


def _ai(text):
    m = _last(r"say that you are\s+(.+?)\s+and continue", text)
    if m:
        return m.group(1).strip()
    m = _last(r"say it's\s+(.+?)\s+and carry on", text)
    if m:
        return m.group(1).strip()
    m = _last(r'Robot/AI question\s*->\s*"([^"]+)"', text)
    return m.group(1).strip() if m else None


def _failed_id(text):
    m = _last(r"(?:wrong answers|check is over|failed security).{0,220}?[Tt]ell (?:the customer|them) to\s+(.+?)(?:\.|,)\s*[Cc]lose", text, re.I | re.S)
    if m:
        return m.group(1).strip().strip('"')
    m = _last(r'say\s+"([^"]+)"\s*;\s*nothing about the account', text)
    return m.group(1).strip() if m else None


def _scam(text):
    m = _last(r"(?:say they can|tell them they can)\s+(.+?),\s+and continue", text)
    if not m:
        m = _last(r"tell them to\s+(verify\s+.+?),\s+then continue", text)
    if not m:
        m = _last(r"SCAM\?\s*->\s*(verify[^.;\n]+)", text)
    if not m:
        return None
    line = m.group(1).strip().rstrip(".")
    line = line.replace("our website", "the website")
    if line.startswith("verify by "):
        line = "verify the call by " + line[len("verify by "):]
    return line


def _promise(text):
    m = _last(r'(?:say this sentence|must say|After success say):\s*"([^"]+)"', text)
    return m.group(1).strip() if m else None


def _max_wrong(text):
    m = _last(r"\b(one|two|three|four|\d+)\s+wrong answers", text)
    if not m:
        return None
    word = m.group(1).lower()
    if word in _WORDS:
        return _WORDS[word]
    return int(word)


def _multi(text):
    """'only one product' means the note rule still applies to a single product. It is not the cap."""
    marks = []
    for m in re.finditer(r"up to three products|one to three products", text, re.I):
        marks.append((m.start(), True, 3))
    for m in re.finditer(r"(?:late on|per customer[^\n]{0,20}|pilot is)\s+one product\b|^one product per customer", text, re.I | re.M):
        marks.append((m.start(), False, 1))
    if not marks:
        return None
    marks.sort()
    return marks[-1][1], marks[-1][2]


def _note_map(text, note_enum):
    m = _last(r"a reason:\s*((?:`[^`]+`(?:,\s*)?)+)\s*\(([^)]+)\)", text)
    if m:
        values = re.findall(r"`([^`]+)`", m.group(1))
        keys = [_PAREN.get(part.strip().lower()) for part in m.group(2).split(",")]
        if len(values) == len(keys) and all(keys) and len(keys) == len(CANON_NOTES):
            return {key: value for key, value in zip(keys, values)}
    if set(note_enum) == set(CANON_NOTES):
        return {key: key for key in CANON_NOTES}
    return None


def _queue_role(name, before):
    lowered = name.lower()
    if "restructur" in lowered:
        return "restructuring"
    if lowered in ("general", "agent", "human", "relationship_manager"):
        return "human"
    if lowered in ("support", "welfare", "hardship"):
        return "support"
    positions = []
    for role, words in (
        ("restructuring", ["restructur"]),
        ("human", ["human"]),
        ("support", ["support", "welfare", "hardship"]),
    ):
        for word in words:
            idx = before.rfind(word)
            if idx >= 0:
                positions.append((idx, role))
    return max(positions)[1] if positions else None


def _queues(text):
    found = []
    pat = re.compile(r"queue\s+`([^`]+)`|transfer_to_agent\(\s*`?([A-Za-z0-9_]+)`?\s*\)", re.I)
    for m in pat.finditer(text):
        name = m.group(1) or m.group(2)
        role = _queue_role(name, text[max(0, m.start() - 180):m.start()].lower())
        if role:
            found.append((m.start(), role, name))
    out = {}
    for _, role, name in found:
        out[role] = name
    if set(out) == {"restructuring", "human", "support"}:
        return out
    return None


def _outcomes_table(text):
    found = {}
    for m in re.finditer(r"\|\s*`([^`]+)`\s*\|\s*([^|\n]+)\|", text):
        outcome = m.group(1).strip()
        reasons = re.findall(r"`([^`]+)`", m.group(2))
        if not reasons or m.group(2).strip().lower() == "none":
            pair = (outcome, None)
            if pair in _PAIR:
                found[_PAIR[pair]] = [outcome, None]
            continue
        for reason in reasons:
            pair = (outcome, reason)
            if pair in _PAIR:
                found[_PAIR[pair]] = [outcome, reason]
    if set(found) == set(OUTCOME_KEYS):
        return {key: found[key] for key in OUTCOME_KEYS}
    return None


def _outcomes_prose(text):
    found = {}
    patterns = (
        r"`([a-z_]+)`\s*/\s*`([a-z_]+)`",
        r"outcome\s+`([a-z_]+)`,\s*reason\s+`([a-z_]+)`",
    )
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            pair = (m.group(1), m.group(2))
            if pair in _PAIR:
                found[_PAIR[pair]] = [m.group(1), m.group(2)]
    if re.search(r"`promise` and `paid` take no reason", text):
        found["promise"] = ["promise", None]
        found["paid"] = ["paid", None]
    elif re.search(r"close(?:\s+as)?\s+`paid`", text):
        found["paid"] = ["paid", None]
    if set(found) == set(OUTCOME_KEYS):
        return {key: found[key] for key in OUTCOME_KEYS}
    return None


def _outcomes_codes(text):
    found = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d{2}-[A-Z0-9-]+)\s{2,}(.+)$", line)
        if not m:
            continue
        meaning = m.group(2).lower()
        for pattern, key in _CODE_RULES:
            if re.search(pattern, meaning):
                found[key] = [m.group(1), None]
                break
    if set(found) == set(OUTCOME_KEYS):
        return {key: found[key] for key in OUTCOME_KEYS}
    return None


def _labels(text, products):
    enums = {prod["enum"].lower(): prod["enum"] for prod in products.values()}
    phrases = re.findall(r'"([^"]{2,80})"', text)
    chosen = {}
    for phrase in phrases:
        low = phrase.lower()
        for alias, names in _ALIASES:
            if alias in low:
                for name in names:
                    actual = enums.get(name.lower())
                    if actual and actual not in chosen:
                        label = re.sub(r"\b\d{4}\b", "{last4}", phrase)
                        label = label.replace("<last4>", "{last4}")
                        chosen[actual] = label
                break
    if set(chosen) != set(enums.values()):
        return None
    out = {}
    for key, prod in products.items():
        out[key] = {**prod, "label": chosen[prod["enum"]]}
    return out


def _close_rule(text):
    m = _last(r"most serious reason[^.]*?in this order:\s*([^\n.]+)", text, re.I | re.S)
    if not m:
        return {"kind": "first_non_paid"}
    tokens = re.findall(r"`([^`]+)`", m.group(1))
    order = []
    for token in tokens:
        key = token.strip().lower().replace(" ", "_")
        if key == "already_paid":
            key = "paid"
        if key in CANON_NOTES and key not in order:
            order.append(key)
    if order:
        return {"kind": "priority", "order": order}
    return {"kind": "first_non_paid"}


def _notes_on_restructuring(text, note_map):
    m = _last(r"Before the transfer, call `add_note` with reason `([^`]+)`", text)
    if not m or not note_map:
        return None
    value = m.group(1)
    inverse = {v: k for k, v in note_map.items()}
    return inverse.get(value, value if value in CANON_NOTES else None)


def _memo_changes(conflicts):
    changes = []
    for item in sorted(conflicts, key=lambda item: item.get("at", 0)):
        if item["path"].endswith("deadline_business_days"):
            name = item["path"].split(".")[1]
            changes.append(f"{name}-band deadline is the {_ordinal(item['later'])} business day (was the {_ordinal(item['earlier'])})")
        elif item["path"] == "callback.same_day" and item["later"] == "the next business day only":
            changes.append("callbacks only on the next business day (no same-day callbacks)")
    return changes


def _brief_id(paths):
    last = paths[-1]
    if last.stem == "brief":
        return last.parent.name
    return last.stem


def propose(text, facts, paths):
    """Config fields read from the brief, plus conflicts a later passage introduced."""
    conflicts = []
    notes = _note_map(text, facts["note_enum"])
    multi = _multi(text)
    cb = callback(text, conflicts)
    band_rows = bands(text, conflicts)
    products = _labels(text, facts["products"])
    config = {
        "language": "en",
        "address": _address(text),
        "identity": {
            "questions": list(facts["questions"]),
            "max_wrong": _max_wrong(text) or facts.get("max_wrong_hint"),
            "swap_hint": facts["swap_hint"],
        },
        "callback": cb,
        "outcome_map": _outcomes_table(text) or _outcomes_prose(text) or _outcomes_codes(text),
        "queue_map": _queues(text),
        "note_map": notes,
        "close_rule": _close_rule(text),
        "notes_on_restructuring": _notes_on_restructuring(text, notes),
        "product_types": products,
        "account_fields": facts["account_fields"],
        "product_id_format": facts["product_id_format"],
        "tools": facts["tools"],
        "forbidden_before_verify": _forbidden(text),
        "multi_product": multi[0] if multi else False,
        "max_products": multi[1] if multi else 1,
        "attempt1": ATTEMPT1 if "last business day of this week" in text else None,
        "id": _brief_id(paths),
        "bank": facts.get("bank"),
        "kind": "raw" if any(mark in text for mark in ("-----Original Message-----", "DIALLER CONFIGURATION", "INTERNAL MEMO")) else "clean",
        "holidays": holidays(text),
        "bands": band_rows,
        "sentences": {
            "opening": _opening(text),
            "promise_confirmation": _promise(text),
            "ai": _ai(text),
            "scam": _scam(text),
            "failed_id": _failed_id(text),
        },
    }
    changes = _memo_changes(conflicts)
    if changes:
        config["memo_changes"] = changes
    return config, conflicts
