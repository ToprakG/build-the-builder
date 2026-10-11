"""Spoken amounts. Currency names, the zero phrase and the template come from the plan.

`speak` is the implementation the tests call. `render_money` is the same implementation pasted into a
workflow code node, with the plan's table inlined. Neither function names a currency.
"""

from __future__ import annotations

import json


def _cfg(money: dict | None) -> dict:
    money = money or {}
    return {
        "unit": money.get("unit") or "major",
        "in_decimal": money.get("in_decimal") or "either",
        "group_sep": money.get("group_sep") or "",
        "template": money.get("template") or "{major} {name} {minor}",
        "zero": money.get("zero") or "{name} {absent}",
        "absent": money.get("absent") if money.get("absent") is not None else "0",
        "negative": money.get("negative") if money.get("negative") is not None else "-",
        "currencies": list(money.get("currencies") or []),
    }


def _spec(code, currencies):
    want = str(code or "")
    for row in currencies:
        if str(row.get("code")) == want:
            return row
    return {"code": want, "name": want, "minor_digits": 2}


def _group(major: str, sep: str) -> str:
    if not sep or len(major) <= 3:
        return major
    parts = []
    rest = major
    while rest:
        parts.append(rest[-3:])
        rest = rest[:-3]
    return sep.join(reversed(parts))


def _to_major_text(text: str, in_decimal: str) -> str:
    if in_decimal == ",":
        return text.replace(".", "").replace(",", ".")
    if in_decimal == ".":
        return text.replace(",", "")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            return text.replace(".", "").replace(",", ".")
        return text.replace(",", "")
    if text.count(",") == 1 and text.count(".") == 0:
        return text.replace(",", ".")
    return text


def _parts(raw, code, unit, in_decimal, currencies):
    """(negative, major, minor, spec) or None when raw is empty."""
    spec = _spec(code, currencies)
    digits = int(spec.get("minor_digits") or 0)
    text = "" if raw is None else str(raw).strip()
    if text == "" or text.lower() in ("none", "null"):
        return None
    neg = text[0] == "-"
    if neg:
        text = text[1:].strip()
    text = text.replace(" ", "")
    if not any(ch.isdigit() for ch in text):
        return None
    if unit == "minor":
        only = "".join(ch for ch in text if ch.isdigit())
        scale = 10 ** digits if digits else 1
        major_i, minor_i = divmod(int(only), scale)
        minor = str(minor_i).zfill(digits) if digits else ""
        return neg, str(major_i), minor, spec
    text = _to_major_text(text, in_decimal)
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail
    if "." in text:
        whole, frac = text.split(".", 1)
    else:
        whole, frac = text, ""
    whole = "".join(ch for ch in whole if ch.isdigit()) or "0"
    frac_digits = "".join(ch for ch in frac if ch.isdigit())
    if digits <= 0:
        return neg, str(int(whole)), "", spec
    padded = (frac_digits + ("0" * (digits + 1)))[: digits + 1]
    minor = int(padded[:digits] or "0")
    extra = padded[digits:digits + 1]
    if extra >= "5":
        minor += 1
    if minor >= 10 ** digits:
        minor = 0
        whole = str(int(whole) + 1)
    return neg, str(int(whole)), str(minor).zfill(digits), spec


def _fill(template: str, **keys) -> str:
    try:
        return template.format(**keys)
    except (KeyError, IndexError, ValueError):
        return template


def speak(raw, code, money: dict | None = None) -> str:
    """The spoken form of one amount. An empty or zero amount uses the plan's zero template."""
    cfg = _cfg(money)
    spec = _spec(code, cfg["currencies"])
    name = str(spec.get("name") or spec.get("code") or "")
    code_s = str(spec.get("code") or "")
    keys = {"name": name, "code": code_s, "absent": str(cfg["absent"]), "major": "0", "minor": "0"}
    parsed = _parts(raw, code, cfg["unit"], cfg["in_decimal"], cfg["currencies"])
    if parsed is None:
        return _fill(cfg["zero"], **keys)
    neg, major, minor, spec = parsed
    if int(major) == 0 and int(minor or "0") == 0:
        return _fill(cfg["zero"], **keys)
    keys["major"] = _group(major, str(cfg["group_sep"]))
    keys["minor"] = minor
    spoken = _fill(cfg["template"], **keys).strip()
    if neg and cfg["negative"]:
        return str(cfg["negative"]) + spoken
    return spoken


def render_money(money: dict | None) -> str:
    """Python source defining speak_amount(raw, code). No doubled braces, so a code node can hold it."""
    cfg = _cfg(money)
    currencies = [{"code": str(c.get("code") or ""), "name": str(c.get("name") or c.get("code") or ""),
                   "minor_digits": int(c.get("minor_digits") or 0)} for c in cfg["currencies"]]
    lines = [
        "UNIT = " + json.dumps(cfg["unit"], ensure_ascii=False),
        "IN_DECIMAL = " + json.dumps(cfg["in_decimal"], ensure_ascii=False),
        "GROUP_SEP = " + json.dumps(cfg["group_sep"], ensure_ascii=False),
        "TEMPLATE = " + json.dumps(cfg["template"], ensure_ascii=False),
        "ZERO = " + json.dumps(cfg["zero"], ensure_ascii=False),
        "ABSENT = " + json.dumps(cfg["absent"], ensure_ascii=False),
        "NEGATIVE = " + json.dumps(cfg["negative"], ensure_ascii=False),
        "CURRENCIES = " + json.dumps(currencies, ensure_ascii=False),
        "def _spec(code):",
        "    want = str(code or '')",
        "    for row in CURRENCIES:",
        "        if str(row.get('code')) == want:",
        "            return row",
        "    return {'code': want, 'name': want, 'minor_digits': 2}",
        "def _group(major, sep):",
        "    if not sep or len(major) <= 3:",
        "        return major",
        "    parts = []",
        "    rest = major",
        "    while rest:",
        "        parts.append(rest[-3:])",
        "        rest = rest[:-3]",
        "    return sep.join(reversed(parts))",
        "def _to_major_text(text, in_decimal):",
        "    if in_decimal == ',':",
        "        return text.replace('.', '').replace(',', '.')",
        "    if in_decimal == '.':",
        "        return text.replace(',', '')",
        "    if ',' in text and '.' in text:",
        "        if text.rfind(',') > text.rfind('.'):",
        "            return text.replace('.', '').replace(',', '.')",
        "        return text.replace(',', '')",
        "    if text.count(',') == 1 and text.count('.') == 0:",
        "        return text.replace(',', '.')",
        "    return text",
        "def speak_amount(raw, code):",
        "    spec = _spec(code)",
        "    name = str(spec.get('name') or '')",
        "    code_s = str(spec.get('code') or '')",
        "    keys = {'name': name, 'code': code_s, 'absent': str(ABSENT), 'major': '0', 'minor': '0'}",
        "    text = '' if raw is None else str(raw).strip()",
        "    if text == '' or text.lower() in ('none', 'null'):",
        "        return ZERO.format(**keys)",
        "    neg = text[0] == '-'",
        "    if neg:",
        "        text = text[1:].strip()",
        "    text = text.replace(' ', '')",
        "    if not any(ch.isdigit() for ch in text):",
        "        return ZERO.format(**keys)",
        "    digits = int(spec.get('minor_digits') or 0)",
        "    if UNIT == 'minor':",
        "        only = ''.join(ch for ch in text if ch.isdigit())",
        "        scale = 10 ** digits if digits else 1",
        "        major_i, minor_i = divmod(int(only), scale)",
        "        major, minor = str(major_i), (str(minor_i).zfill(digits) if digits else '')",
        "    else:",
        "        text = _to_major_text(text, IN_DECIMAL)",
        "        if text.count('.') > 1:",
        "            head, _, tail = text.rpartition('.')",
        "            text = head.replace('.', '') + '.' + tail",
        "        if '.' in text:",
        "            whole, frac = text.split('.', 1)",
        "        else:",
        "            whole, frac = text, ''",
        "        whole = ''.join(ch for ch in whole if ch.isdigit()) or '0'",
        "        frac_digits = ''.join(ch for ch in frac if ch.isdigit())",
        "        if digits <= 0:",
        "            major, minor = str(int(whole)), ''",
        "        else:",
        "            padded = (frac_digits + ('0' * (digits + 1)))[: digits + 1]",
        "            minor_i = int(padded[:digits] or '0')",
        "            extra = padded[digits:digits + 1]",
        "            if extra >= '5':",
        "                minor_i += 1",
        "            if minor_i >= 10 ** digits:",
        "                minor_i = 0",
        "                whole = str(int(whole) + 1)",
        "            major, minor = str(int(whole)), str(minor_i).zfill(digits)",
        "    if int(major) == 0 and int(minor or '0') == 0:",
        "        return ZERO.format(**keys)",
        "    keys['major'] = _group(major, str(GROUP_SEP))",
        "    keys['minor'] = minor",
        "    spoken = TEMPLATE.format(**keys).strip()",
        "    if neg and NEGATIVE:",
        "        return str(NEGATIVE) + spoken",
        "    return spoken",
    ]
    return "\n".join(lines) + "\n"


ROWS_SOURCE = """\
def _cell(row, field):
    if field in row:
        return row[field]
    suffix = '.' + field
    for key, val in row.items():
        if key == field or str(key).endswith(suffix):
            return val
    return ''

def _rows(prefix):
    found = {}
    needle = prefix + '.'
    for key in list(vars):
        text = str(key)
        if not text.startswith(needle):
            continue
        rest = text[len(needle):]
        parts = rest.split('.')
        if not parts or not parts[0].isdigit():
            continue
        bucket = found.get(parts[0])
        if bucket is None:
            bucket = {}
            found[parts[0]] = bucket
        bucket['.'.join(parts[1:])] = vars.get(key)
    ordered = []
    for index in sorted(found, key=lambda s: int(s)):
        ordered.append(found[index])
    return ordered
"""
