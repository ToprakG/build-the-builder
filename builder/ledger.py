"""Source timeline and conflict ledger.

Each input file is dated and typed. Behavioral sentences are grouped. The chosen sentence is the newest
authoritative one: a mail or a meeting note dated on or after a document overrides that document, and a
customer-facing page or a drawing never wins over an authoritative source. When the chosen sentence leaves the
point open, the safest behavior is to hand the call to a human if the documents allow that.
"""

from __future__ import annotations

import email.utils
import json
import re
from pathlib import Path

AUTHORITATIVE = {"mail", "meeting note", "spec", "prompt sheet"}
NOT_AUTHORITATIVE = {"FAQ/web page", "drawing"}
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
STOP = {
    "about", "after", "again", "also", "assistant", "because", "been", "before", "being", "between", "brief",
    "call", "caller", "calls", "close", "could", "customer", "does", "doing", "done", "dont", "each", "else",
    "every", "from", "have", "into", "just", "more", "must", "never", "only", "onto", "other", "over", "please",
    "said", "same", "says", "should", "some", "such", "than", "that", "their", "them", "then", "there", "these",
    "they", "this", "those", "told", "under", "until", "what", "when", "where", "which", "while", "with", "would",
    "your",
}
OPEN_RE = re.compile(
    r"\b(still open|not decided|not yet decided|keep it open|to be decided|\btbd\b|"
    r"we will come back|we'll come back|will come back|not settled|undecided|remains open)\b",
    re.I,
)
HANDOFF_RE = re.compile(
    r"\b(hand over|handover|hand the call|transfer to|a human|an agent|real person|representative|to a person)\b",
    re.I,
)
BEHAVIOR_RE = re.compile(
    r"\b(must|never|always|do not|don't|should|ask|transfer|end the call|only if|shall)\b",
    re.I,
)
DATE_LINE = re.compile(
    r"(?m)^Date:\s*(.+)$"
)
FROM_LINE = re.compile(r"(?m)^From:\s*(.+)$")
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")
TEXT_DATE = re.compile(
    r"\b(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{4})"
    r"(?:(?:.|\n){0,24}?(\d{1,2}):(\d{2}))?",
    re.I,
)
META_RE = re.compile(r"===\s*META:\s*date=([^;]*);\s*type=([^;]*);\s*author=(.*?)\s*===")
FILE_SPLIT = re.compile(r"(?m)^===\s*FILE:\s*(.+?)\s*===\s*$")


def parse_when(text: str) -> tuple[str, str]:
    """Return (YYYY-MM-DD, HH:MM or '') from a header, a filename fragment, or a prose date. Empty if none."""
    text = (text or "").strip()
    if not text:
        return "", ""
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is not None:
        return parsed.date().isoformat(), parsed.strftime("%H:%M")
    m = ISO_DATE.search(text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", (f"{m.group(4)}:{m.group(5)}" if m.group(4) else "")
    m = TEXT_DATE.search(text)
    if m and m.group(2).lower()[:3] in MONTHS:
        month = MONTHS[m.group(2).lower()[:3]]
        hhmm = f"{int(m.group(4)):02d}:{m.group(5)}" if m.group(4) else ""
        return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}", hhmm
    return "", ""


def date_from_name(rel: str) -> tuple[str, str]:
    return parse_when(Path(rel).name)


def author_from_text(text: str) -> str | None:
    m = FROM_LINE.search(text or "")
    if m:
        name = re.sub(r"<[^>]+>", "", m.group(1))
        name = name.strip().strip('"')[:80]
        return name or None
    m = re.search(
        r"(?m)^([A-Z][a-z]+(?:[ \-][A-Z][a-z]+){0,3}),\s+[^\n]{0,60}?\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}",
        text or "")
    if m:
        return m.group(1).strip()
    return None


def classify_source(rel: str, text: str) -> str:
    """mail, meeting note, spec, FAQ/web page, drawing, prompt sheet, or data."""
    name = (rel or "").lower()
    head = (text or "")[:4000]
    low = head.lower()
    ext = Path(rel).suffix.lower()
    if ext in IMAGE or any(w in name for w in ("diagram", "drawing", "figure")):
        return "drawing"
    if re.search(r"(?m)^From:\s*\S", head) and re.search(r"(?m)^(To|Cc|Subject|Date):\s*\S", head):
        return "mail"
    if "prompt sheet" in low or "prompt id" in low:
        return "prompt sheet"
    if any(s in low for s in ("help centre", "help center", "skip to content")) or "faq" in Path(name).name or re.search(r"\bfaq\b", low):
        return "FAQ/web page"
    if (any(part in {"meetings", "meeting"} for part in Path(name).parts)
            or "transcript" in Path(name).name
            or any(s in low for s in ("meeting note", "meeting notes", "meeting minutes", "attendees:"))):
        return "meeting note"
    if ext in {".json", ".xlsx", ".xlsm", ".csv"} and not BEHAVIOR_RE.search(low):
        return "data"
    return "spec"


def describe_source(rel: str, text: str) -> dict:
    named = date_from_name(rel)
    found = date_from_text(text or "")
    when = named if named[0] else found
    return {
        "path": rel,
        "date": when[0] or None,
        "time": when[1] or "",
        "author": author_from_text(text or ""),
        "type": classify_source(rel, text or ""),
    }


def date_from_text(text: str) -> tuple[str, str]:
    """A Date header, else a date in the first lines. A date buried in an example is not the document date."""
    m = DATE_LINE.search(text or "")
    if m:
        when = parse_when(m.group(1))
        if when[0]:
            return when
    head = "\n".join((text or "").splitlines()[:12])
    return parse_when(head)


def _message_when(text: str, fallback: tuple[str, str]) -> tuple[str, str]:
    m = DATE_LINE.search(text or "")
    if m:
        when = parse_when(m.group(1))
        if when[0]:
            return when
    m = re.search(r"(?m)^On\s+(.+?)\s+wrote:", text or "")
    if m:
        when = parse_when(m.group(1))
        if when[0]:
            return when
    return fallback


def _split_messages(text: str, kind: str) -> list[dict]:
    if kind != "mail":
        return [{"text": text, "author": author_from_text(text), "when": date_from_text(text)}]
    chunks = re.split(r"(?m)(?=^From: )", text or "")
    out = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        for part in re.split(r"(?m)(?=^On .+ wrote:)", chunk):
            if part.strip():
                out.append({
                    "text": part,
                    "author": author_from_text(part),
                    "when": ("", ""),
                })
    return out or [{"text": text, "author": author_from_text(text), "when": date_from_text(text)}]


def _blocks(text: str) -> list[str]:
    text = re.sub(r"(?m)^(?:>\s*)+", "", text or "")
    parts = re.split(r"(?m)^(?=\s*(?:\d+\.\s+|Q\d+\b))", text)
    blocks = []
    for part in parts:
        if re.match(r"\s*(?:\d+\.\s+|Q\d+\b)", part):
            blocks.append(part.strip())
            continue
        for para in re.split(r"\n\s*\n", part):
            para = para.strip()
            if para:
                blocks.append(para)
    return blocks


def _header_only(block: str) -> bool:
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if not lines:
        return True
    heads = sum(1 for ln in lines if re.match(r"^(From|To|Cc|Date|Subject|Sent):", ln.strip(), re.I))
    return heads >= max(1, len(lines) - 1)


def _keep(block: str) -> bool:
    if _header_only(block):
        return False
    numbered = bool(re.match(r"\s*(?:\d+\.\s+|Q\d+\b)", block))
    if numbered and len(block) >= 20:
        return True
    if len(block) >= 30 and (BEHAVIOR_RE.search(block) or '"' in block or "“" in block):
        return True
    return len(block) >= 35 and "." in block


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{5,}", text.lower())
    return {w for w in words if w not in STOP}


def _apply_meta(meta: dict, body: str) -> dict:
    m = META_RE.search(body or "")
    if not m:
        return meta
    if m.group(1).strip() not in ("", "unknown"):
        meta["date"] = m.group(1).strip()[:10]
    if m.group(2).strip():
        meta["type"] = m.group(2).strip()
    author = m.group(3).strip()
    if author and author != "unknown":
        meta["author"] = author
    return meta


def explode_text(path: str, text: str) -> list[dict]:
    """One file, or a combined brief with FILE markers, into source records that still hold their text."""
    if "=== FILE:" not in (text or ""):
        meta = describe_source(path, text)
        meta["text"] = text or ""
        return [meta]
    parts = FILE_SPLIT.split(text)
    out = []
    i = 1
    while i + 1 < len(parts):
        name, body = parts[i].strip(), parts[i + 1]
        meta = describe_source(name, body)
        _apply_meta(meta, body)
        meta["text"] = body
        out.append(meta)
        i += 2
    if out:
        return out
    meta = describe_source(path, text)
    meta["text"] = text or ""
    return [meta]


def sources_from_paths(paths) -> list[dict]:
    out = []
    for p in paths:
        path = Path(p)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out.extend(explode_text(path.name, text))
    return out


def _statements(sources: list[dict]) -> list[dict]:
    rows = []
    seq = 0
    for src in sources:
        kind = src.get("type") or "spec"
        if kind not in AUTHORITATIVE | NOT_AUTHORITATIVE:
            continue
        fallback = (src.get("date") or "", src.get("time") or "")
        messages = _split_messages(src.get("text") or "", kind)
        for msg in messages:
            if kind == "mail":
                when = _message_when(msg["text"], fallback)
            else:
                when = fallback if fallback[0] else (msg["when"] if msg["when"][0] else date_from_text(msg["text"]))
            author = msg.get("author") or src.get("author")
            for block in _blocks(msg["text"]):
                if not _keep(block):
                    continue
                rows.append({
                    "path": src.get("path"),
                    "date": when[0] or None,
                    "time": when[1] or "",
                    "author": author,
                    "type": kind,
                    "text": re.sub(r"\s+", " ", block).strip()[:700],
                    "seq": seq,
                })
                seq += 1
    return rows


def _clusters(rows: list[dict]) -> list[list[dict]]:
    """Group sentences that share a rare word and come from different moments. A single document's own list stays apart."""
    if not rows:
        return []
    tokens = [_tokens(r["text"]) for r in rows]
    inv: dict[str, list[int]] = {}
    for i, toks in enumerate(tokens):
        for tok in toks:
            if len(tok) >= 6:
                inv.setdefault(tok, []).append(i)
    rare = {tok: ids for tok, ids in inv.items() if len(ids) in (2, 3, 4)}

    def moment(row: dict) -> tuple:
        return (row.get("path"), row.get("date") or "", row.get("time") or "")

    def label(i: int):
        choices = [tok for tok in tokens[i] if tok in rare]
        if not choices:
            return ("one", i)
        return min(choices, key=lambda tok: (len(rare[tok]), -len(tok), tok))

    buckets: dict[object, list[dict]] = {}
    for i, row in enumerate(rows):
        buckets.setdefault(label(i), []).append(row)
    groups = []
    for group in buckets.values():
        moments = {moment(r) for r in group}
        if len(moments) < 2:
            groups.extend([[r] for r in group])
        else:
            groups.append(group)
    return groups


def _rank(row: dict) -> tuple:
    kind_rank = 1 if row.get("type") in {"mail", "meeting note"} else 0
    return (row.get("date") or "", row.get("time") or "", kind_rank, row.get("seq") or 0)


def _public(row: dict) -> dict:
    return {k: row.get(k) for k in ("path", "date", "author", "type", "text")}


def _why(chosen: dict | None, rows: list[dict]) -> str:
    if chosen is None:
        return "No authoritative source. A customer-facing page or a drawing is not a rule for the assistant."
    others = [r for r in rows if r is not chosen]
    bits = []
    older = [r for r in others if (r.get("date") or "") < (chosen.get("date") or "")]
    if older:
        bits.append(f"{chosen.get('date')} is newer than {', '.join(sorted({r.get('date') or 'undated' for r in older}))}")
    overridden = [r for r in others if r.get("type") in {"spec", "prompt sheet"}
                  and chosen.get("type") in {"mail", "meeting note"}
                  and (r.get("date") or "") <= (chosen.get("date") or "")]
    if overridden:
        bits.append("a mail or meeting note on or after a document overrides that document")
    if any(r.get("type") in NOT_AUTHORITATIVE for r in others):
        bits.append("a customer-facing page or a drawing is not authoritative for the assistant")
    if OPEN_RE.search(chosen.get("text") or ""):
        bits.append("the chosen sentence says this is still open")
    return "; ".join(bits) or "only authoritative sentence"


def _handoff_allowed(rows: list[dict]) -> bool:
    return any(r.get("type") in AUTHORITATIVE and HANDOFF_RE.search(r.get("text") or "") for r in rows)


def build_ledger(sources: list[dict]) -> dict:
    timeline = [{k: s.get(k) for k in ("path", "date", "author", "type")} for s in sources]
    timeline.sort(key=lambda s: (s.get("date") or "", s.get("path") or ""))
    rows = _statements(sources)
    handoff = _handoff_allowed(rows)
    rules = []
    for group in _clusters(rows):
        auth = [r for r in group if r.get("type") in AUTHORITATIVE]
        chosen = max(auth, key=_rank) if auth else None
        rule = {
            "candidates": [_public(r) for r in sorted(group, key=_rank)],
            "chosen": _public(chosen) if chosen else None,
            "why": _why(chosen, group),
            "open": bool(chosen and OPEN_RE.search(chosen.get("text") or "")),
            "safest": None,
        }
        if rule["open"]:
            if handoff:
                rule["safest"] = {
                    "behavior": "hand_over_to_human",
                    "because": "The chosen sentence leaves this open. The documents allow handing the call to a human, so that is the behavior to build.",
                }
            else:
                rule["safest"] = {
                    "behavior": "do_not_invent",
                    "because": "The chosen sentence leaves this open. The documents do not describe handing the call to a human, so no extra rule is invented.",
                }
        rules.append(rule)
    rules.sort(key=lambda r: _rank(r["chosen"]) if r.get("chosen") else ("", "", 0, 0))
    for i, rule in enumerate(rules, 1):
        rule["id"] = f"L{i}"
    return {"sources": timeline, "rules": rules}


def write_ledger(sources: list[dict], path: Path) -> dict:
    ledger = build_ledger(sources)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return ledger
