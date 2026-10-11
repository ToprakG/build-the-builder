"""Deterministic post-extraction fixups for the two fields that flip between extractions or are read wrongly.

DEFINITION (single source; policy.schema.json repeats it): ask_limits.id_decline = the number of times the agent re-asks after
the FIRST refusal of an identity question (the explanation is said once, on the first refusal). The call closes on
refusal number id_decline + 1. "Explain once, close after the second refusal" -> id_decline = 1.

Each rule needs an explicit phrase in the brief and returns (value, quote); with no phrase the policy value is left alone.
The quote is written to _source. No brief value in code: only phrasing patterns.

    python3 -m builder.template_cc.policy_fixups <policy.json> --brief <brief file> [--brief ...] [-o <out.json>]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from builder.template_cc import policy_check

ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
COUNTS = {"once": 1, "twice": 2, "thrice": 3, "one time": 1, "two times": 2, "three times": 3}
_REFUSE = r"(?:refus\w*|declin\w*|(?:does not|doesn't|do not|don't|won't|will not) (?:want to )?answer|non-?answers?)"
_CLOSE = re.compile(r"\b(clos\w+|end\w*|stop\w*|hang\w*|terminat\w+|not[_ ]verified|=)\b", re.I)
_CTX = re.compile(r"\b(identity|verif\w*|security|questions?|answer\w*|protect\w*)\b", re.I)
_ORDINAL_REFUSAL = re.compile(
    r"\b(first|second|third|fourth|fifth|(\d+)(?:st|nd|rd|th))\s+(?:such\s+|more\s+)?(?:refusal|declin\w*|question|no\b)", re.I)
_AFTER_REFUSAL = re.compile(r"\bafter\s+(?:the\s+)?(first|second|third|fourth|fifth|(\d+)(?:st|nd|rd|th))\s+" + _REFUSE, re.I)
_TWICE = re.compile(r"\b" + _REFUSE + r"\s+(?:it\s+|them\s+)?(twice|three times|thrice|(\d+) times)\b", re.I)
_REASK = re.compile(r"\b(?:re-?ask|ask(?: (?:it|them|the question|again))*)\s+(once|twice|thrice|(\d+) times|one time|two times|three times)\s+more\b|"
                    r"\bask\s+once\s+more\b", re.I)
_EXPLAIN = re.compile(r"\bexplain\w*\s+(once|twice|one time|two times)\b", re.I)
_STILL = re.compile(r"\bstill\s+" + _REFUSE, re.I)


def sentences(text: str) -> list[str]:
    flat = re.sub(r"[*`|]", " ", text or "")
    out = []
    for part in re.split(r"\n+", flat):
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", part) if s.strip()]
    return out


def _n(word: str, digits) -> int:
    return int(digits) if digits else ORD.get(word.lower(), 0)


def id_decline(brief: str):
    """(value, quote) from the refusal count at which the brief closes the call, or from an explicit "ask once more"; else None."""
    found: dict[int, str] = {}
    sents = sentences(brief)
    for s in sents:
        if not (_CTX.search(s) or _REFUSE_RE.search(s)):
            continue
        m = _ORDINAL_REFUSAL.search(s) or _AFTER_REFUSAL.search(s)
        if m and _CLOSE.search(s) and _REFUSE_RE.search(s + " refusal" if "question" in m.group(0).lower() else s):
            n = _n(m.group(1), m.group(2))
            if n >= 1:
                found.setdefault(n - 1, s)
                continue
        m = _TWICE.search(s)
        if m and _CLOSE.search(s) and _CTX.search(s):
            n = COUNTS.get(m.group(1).lower()) or int(m.group(3))
            found.setdefault(n - 1, s)
            continue
        m = _REASK.search(s)
        if m and _CTX.search(s) and _REFUSE_RE.search(s):
            word = (m.group(1) or "once").lower()
            found.setdefault(COUNTS.get(word) or int(m.group(2)), s)
    if len(found) == 1:
        v, q = next(iter(found.items()))
        return v, q
    if found:
        return None  # the brief's sentences disagree
    # supporting evidence only: "explain once ... if they still decline, close" -> the closing refusal is number explanations + 1
    for i, s in enumerate(sents):
        m = _EXPLAIN.search(s)
        if m and _CTX.search(s):
            for t in sents[i:i + 3]:
                if _STILL.search(t) and _CLOSE.search(t):
                    return COUNTS[m.group(1).lower()], t
    return None


_REFUSE_RE = re.compile(_REFUSE, re.I)

# ask_limits.right_party = the TOTAL number of times the agent asks "am I speaking with X?" (the first ask plus the repeats):
# "ask once more" -> 2, "if it is still unclear after the second ask, close" -> 2. (Not a count of repeats, unlike id_decline.)
_RP_CTX = re.compile(r"\b(speaking with|confirm\w*|right person|is that you|who is (speaking|this)|the customer\b.*\bidentity)\b", re.I)
_RP_MORE = re.compile(r"\bask\w*\s+(?:(?:it|them|the question)\s+)?(once|twice|thrice|one time|two times)\s+more\b|\bask\s+again\b|\bre-?ask\b", re.I)
_RP_NTH = re.compile(r"\b(second|third|fourth)\s+ask\b", re.I)


def right_party(brief: str):
    """(total asks, quote) from an explicit repeat of the right-party question in the opening; None when the brief is silent / disagrees."""
    found: dict[int, str] = {}
    for line in (brief or "").splitlines():
        text = " ".join(re.sub(r"[*`|]", " ", line).split())
        if not _RP_CTX.search(text):
            continue
        sents = [x for x in re.split(r"(?<=[.!?])\s+", text) if x]
        for x in sents:
            m = _RP_MORE.search(x)
            if m and not _REFUSE_RE.search(x):
                n = 1 + (COUNTS.get((m.group(1) or "once").lower(), 1) if m.group(1) else 1)
                found.setdefault(n, x)
            m = _RP_NTH.search(x)
            if m:
                found.setdefault(ORD[m.group(1).lower()], x)
    if len(found) == 1:
        v, q = next(iter(found.items()))
        return v, q
    return None

_CAP_TRUE = re.compile(r"\b(no later than|not later than|not after|never after|never later than|capped at|must not be after|may not be after)\b[^.]{0,30}\bdeadline\b|"
                       r"\bafter the deadline[,;]? (then )?(propose|offer|use|take|name)\w* the deadline\b|"
                       r"\b(is|falls|would be|lands) after the deadline[,;]? (then )?(propose|offer|use|take|name)\w* the deadline\b", re.I)
_CAP_FALSE = re.compile(r"\b(even if|regardless of|ignoring|without regard to|whether or not)\b[^.]{0,30}\bdeadline\b", re.I)


def cap_at_deadline(brief: str, k: int):
    """(value, quote) for attempt k (1-based): the attempt's own line first, then a general sentence about the promise date."""
    for line in (brief or "").splitlines():
        if re.search(rf"\battempt\s*{k}\b", line, re.I) and not re.search(rf"\battempt\s*{k}\d", line, re.I):
            body = " ".join(re.sub(r"[*`|]", " ", line).split())
            if _CAP_FALSE.search(body):
                return False, body
            if _CAP_TRUE.search(body):
                return True, body
    general = [s for s in sentences(brief) if re.search(r"\bdates?\b", s, re.I) and not re.search(r"\battempt\s*\d", s, re.I)]
    for s in general:
        if _CAP_FALSE.search(s):
            return False, s
    for s in general:
        if _CAP_TRUE.search(s):
            return True, s
    return None


def apply(policy: dict, brief: str) -> list[dict]:
    """Set the fields whose rule matched (quote must be found in the brief). Returns [{field, before, after, quote}]."""
    changes = []
    src = policy.setdefault("_source", {}) if isinstance(policy.get("_source", {}), dict) else {}

    def put(field, ptr, setter, before, rule):
        if rule is None or not policy_check.quote_in(rule[1], brief):
            return
        setter(rule[0])
        src[ptr] = rule[1]
        changes.append({"field": field, "before": before, "after": rule[0], "quote": rule[1]})

    before = (policy.get("ask_limits") or {}).get("id_decline")
    put("ask_limits.id_decline", "/ask_limits/id_decline", lambda v: policy.setdefault("ask_limits", {}).__setitem__("id_decline", v),
        before, id_decline(brief))
    rp = (policy.get("ask_limits") or {}).get("right_party")
    put("ask_limits.right_party", "/ask_limits/right_party", lambda v: policy.setdefault("ask_limits", {}).__setitem__("right_party", v),
        rp, right_party(brief))
    for i, step in enumerate(policy.get("attempt_plan") or []):
        put(f"attempt_plan[{i}].cap_at_deadline", f"/attempt_plan/{i}/cap_at_deadline",
            lambda v, st=step: st.__setitem__("cap_at_deadline", v), step.get("cap_at_deadline"), cap_at_deadline(brief, i + 1))
    return changes


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("policy")
    ap.add_argument("--brief", action="append", required=True)
    ap.add_argument("-o", "--out")
    a = ap.parse_args(argv)
    brief = "\n\n".join(Path(b).read_text(encoding="utf-8") for b in a.brief)
    p = json.loads(Path(a.policy).read_text(encoding="utf-8"))
    for c in apply(p, brief):
        print(f"fixup {c['field']}: {c['before']!r} -> {c['after']!r}  | {c['quote'][:110]}")
    Path(a.out or a.policy).write_text(json.dumps(p, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
