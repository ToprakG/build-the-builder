"""Generic preprocessing of raw brief material before extraction. No brief values, only structure.

`prepare(text)` returns the text unchanged when it has no email-thread or memo markers. Otherwise:
- an email chain (`-----Original Message-----`, `On ... wrote:`, `>` quote lines) is split into messages, quoted repeats
  are removed, the messages are ordered oldest -> newest (by their Sent/Date header, else reversed from the top-down
  reply order) and each is prefixed `[Message k of n — later messages override earlier ones]`;
- a memo or amendment heading, or a line that starts with Memo / Amendment / Effective / Supersedes / Replaces, is
  prefixed `[Override]`.
The original text stays the reference for quote checks: a quote must match the brief, not this view of it.
"""

from __future__ import annotations

import datetime as dt
import re

SEP = re.compile(r"^\s*(-{3,}\s*Original Message\s*-{3,}|On .{5,200}\bwrote:\s*|_{5,})\s*$", re.I | re.M)
HEADER = re.compile(r"^(From|Sent|Date|To|Subject|Cc):", re.I | re.M)
QUOTE = re.compile(r"^\s*>", re.M)
OVERRIDE_HEADING = re.compile(r"^(#+\s*.*\b(memo|amendments?|supersed\w*|replac\w+)\b.*)$", re.I)
OVERRIDE_START = re.compile(r"^\s*(memo\b|amendments?\b|effective\b|supersedes\b|replaces\b)", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _norm(line: str) -> str:
    return " ".join(re.sub(r"[*_`#|]", " ", line).lower().split())


def _date(chunk: str):
    head = "\n".join(chunk.splitlines()[:8])
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", head)
    if m:
        return dt.date(int(m[1]), int(m[2]), int(m[3]))
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b", head)
    if m and m[2].lower() in MONTHS:
        return dt.date(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
    return None


def is_thread(text: str) -> bool:
    return bool(SEP.search(text) or (QUOTE.search(text) and HEADER.search(text)))


def _split_messages(text: str) -> list[str]:
    parts = [p for p in SEP.split(text)[0::2]] if SEP.search(text) else [text]
    # re.split with one group returns [chunk, sep, chunk, ...]; the "On ... wrote:" separator carries no header, keep chunks
    return [p.strip("\n") for p in parts if p.strip()]


def _thread(text: str) -> str:
    chunks = _split_messages(text)
    if len(chunks) < 2:
        chunks = [text]
    dates = [_date(c) for c in chunks]
    order = list(range(len(chunks)))
    if all(dates):
        order.sort(key=lambda i: (dates[i], -i))  # equal dates: the one lower in the chain is older
    else:
        order.reverse()  # replies are written above the quoted history
    seen: set[str] = set()
    out = []
    for k, i in enumerate(order, 1):
        lines = []
        for line in chunks[i].splitlines():
            line = re.sub(r"^(\s*>)+ ?", "", line)  # a quote line is a repeat of an earlier message or a plain line
            key = _norm(line)
            if len(key) >= 20 and key in seen:
                continue
            if len(key) >= 20:
                seen.add(key)
            lines.append(line)
        body = "\n".join(lines).strip("\n")
        out.append(f"[Message {k} of {len(order)} — later messages override earlier ones]\n{body}")
    return "\n\n".join(out)


def _overrides(text: str) -> str:
    out = []
    for line in text.split("\n"):
        if (OVERRIDE_HEADING.match(line) or OVERRIDE_START.match(line)) and not line.lstrip().startswith("[Override]"):
            line = "[Override] " + line.lstrip() if not line.lstrip().startswith("#") else re.sub(r"^(#+\s*)", r"\1[Override] ", line)
        out.append(line)
    return "\n".join(out)


def prepare(text: str) -> str:
    """The brief as the extractor reads it; byte-identical to `text` when no marker is found."""
    new = _thread(text) if is_thread(text) else text
    new = _overrides(new)
    return new if new != text else text
