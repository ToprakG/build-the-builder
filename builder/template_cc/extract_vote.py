"""F10: merge several extractions of the same brief field by field (self-consistency), and check quotes against values.

Pure functions, no LLM, no brief values. The other agent wires this into extract.py.

    merged, votes = merge([p1, p2, p3], brief_text)
    votes["/identity/max_wrong"]  -> "3/3" | "2/3" | "split"
    quote_mismatches(policy)      -> ["/bands/0", ...]   # a number or ISO date in the quote differs from the value

Merge rule: where every policy has an object (or a list of objects of the same length) the merge recurses; anywhere
else the whole value is one vote (a missing key votes "absent"). The majority wins. On a tie, the value whose
`_source` quote (the nearest pointer at or above it) appears in the brief wins; then the earliest policy's value.
"""

from __future__ import annotations

import json
import re

ABSENT = object()
META = ("_source", "_overrides")


def norm(s) -> str:
    """Lowercase, strip markdown * ` |, collapse whitespace."""
    s = re.sub(r"[*`|]", " ", str(s or "").lower())
    return " ".join(s.split())


def _esc(key) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def _key(v) -> str:
    return "<absent>" if v is ABSENT else json.dumps(v, sort_keys=True, ensure_ascii=False)


def flatten(policy: dict) -> dict:
    """JSON pointer -> leaf value (lists of scalars and empty containers are leaves). Meta keys are skipped."""
    out: dict = {}

    def walk(ptr, v):
        if isinstance(v, dict) and v:
            for k, x in v.items():
                if ptr == "" and k in META:
                    continue
                walk(ptr + "/" + _esc(k), x)
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for i, x in enumerate(v):
                walk(ptr + "/" + str(i), x)
        else:
            out[ptr] = v

    walk("", policy or {})
    return out


def _quote_for(policy: dict, ptr: str):
    src = (policy or {}).get("_source") or {}
    p = ptr
    while True:
        if p in src and isinstance(src[p], str):
            return p, src[p]
        if not p:
            return None, None
        p = p.rsplit("/", 1)[0]


def merge(policies: list[dict], brief_text: str) -> tuple[dict, dict]:
    policies = [p for p in policies if isinstance(p, dict)]
    if not policies:
        return {}, {}
    n = len(policies)
    brief_n = norm(brief_text)
    votes: dict = {}
    winners_by_ptr: dict = {}  # pointer -> indexes of policies that hold the winning value

    def vote(ptr, values):
        groups: dict = {}
        order = []
        for i, v in enumerate(values):
            k = _key(v)
            if k not in groups:
                groups[k] = []
                order.append(k)
            groups[k].append(i)
        best = max(len(g) for g in groups.values())
        tied = [k for k in order if len(groups[k]) == best]
        if len(tied) > 1:
            def quoted(k):
                for i in groups[k]:
                    _, q = _quote_for(policies[i], ptr)
                    if q and norm(q) and norm(q) in brief_n:
                        return True
                return False
            tied = [k for k in tied if quoted(k)] or tied
        win = tied[0]
        votes[ptr] = "%d/%d" % (best, n) if best * 2 > n else "split"
        winners_by_ptr[ptr] = groups[win]
        return values[groups[win][0]]

    def rec(ptr, values):
        present = [v for v in values if v is not ABSENT]
        if present and all(isinstance(v, dict) for v in values) and any(v for v in values):
            out = {}
            keys = []
            for v in values:
                for k in v:
                    if ptr == "" and k in META:
                        continue
                    if k not in keys:
                        keys.append(k)
            for k in keys:
                r = rec(ptr + "/" + _esc(k), [v.get(k, ABSENT) for v in values])
                if r is not ABSENT:
                    out[k] = r
            return out
        if (present and all(isinstance(v, list) for v in values) and len({len(v) for v in values}) == 1
                and values[0] and all(isinstance(x, dict) for v in values for x in v)):
            return [rec(ptr + "/" + str(i), [v[i] for v in values]) for i in range(len(values[0]))]
        return vote(ptr, values)

    merged = rec("", policies)
    # carry the quotes of the winning values
    src: dict = {}
    for ptr, idxs in winners_by_ptr.items():
        best = None
        for i in idxs:
            sp, q = _quote_for(policies[i], ptr)
            if q is None:
                continue
            if best is None or (norm(q) in brief_n and not norm(best[1]) in brief_n):
                best = (sp, q)
        if best and best[0] not in src:
            src[best[0]] = best[1]
    for p in policies:  # quotes of pointers that are not leaves (e.g. a whole section): keep the first one found
        for k, q in ((p.get("_source") or {}).items()):
            if k not in src and _get(merged, k) is not ABSENT:
                src[k] = q
    if src:
        merged["_source"] = src
    ov = [p.get("_overrides") for p in policies if p.get("_overrides") is not None]
    if ov:
        merged["_overrides"] = ov[0]
    return merged, votes


def _get(doc, ptr):
    cur = doc
    if not ptr:
        return cur
    for part in ptr.lstrip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return ABSENT
    return cur


ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
HHMM = re.compile(r"\b\d{1,2}:\d{2}\b")
NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def _num_str(x) -> str:
    f = float(x)
    return str(int(f)) if f == int(f) else repr(f)


def _tokens_text(s: str) -> set:
    s = str(s)
    out = set(ISO.findall(s))
    s = ISO.sub(" ", s)
    out |= {t.zfill(5) for t in HHMM.findall(s)}
    s = HHMM.sub(" ", s)
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    out |= {_num_str(t) for t in NUM.findall(s)}
    return out


def _tokens_value(v) -> set:
    out: set = set()
    if isinstance(v, bool) or v is None:
        return out
    if isinstance(v, (int, float)):
        return {_num_str(v)}
    if isinstance(v, str):
        return _tokens_text(v)
    if isinstance(v, dict):
        for x in v.values():
            out |= _tokens_value(x)
    elif isinstance(v, list):
        for x in v:
            out |= _tokens_value(x)
    return out


_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
_WORDS.update({w: str(i) for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth".split(), 1)})
_STRICT_PREFIXES = ("/bands", "/callback")  # every number of the value must be in the quote, not only the reverse


def _word_tokens(s: str) -> set:
    return {_WORDS[w] for w in re.findall(r"[a-z]+", str(s).lower()) if w in _WORDS}


def quote_mismatches(policy: dict) -> list[str]:
    """Pointers whose `_source` quote and value disagree on a number, ISO date or time.

    A number in the quote that is not in the value (or, for a leaf, in its enclosing object) is a mismatch. For bands and the callback window the reverse
    holds too: every number of the value must be in the quote (digits or number words). Only fields whose value holds
    at least one number, date or time are checked (a text field is not numeric)."""
    bad = []
    for ptr, quote in ((policy or {}).get("_source") or {}).items():
        if not isinstance(quote, str):
            continue
        v = _get(policy, ptr)
        if v is ABSENT:
            continue
        have = _tokens_value(v)
        if not have:
            continue
        said = _tokens_text(quote)
        if ptr.count("/") >= 2:  # a leaf's quote is often the whole sentence: numbers of the enclosing object count too
            parent = _get(policy, ptr.rsplit("/", 1)[0])
            ctx = have | (_tokens_value(parent) if parent is not ABSENT else set())
        else:
            ctx = have
        if said - ctx or (ptr.startswith(_STRICT_PREFIXES) and have - said - _word_tokens(quote)):
            bad.append(ptr)
    return sorted(bad)
