"""Patch-zone registry. Every model-facing text of a compiled workflow is assembled from named zones.

zones.json = {"zones": {id: {"locked", "max_chars", "text"}}, "targets": [{"pointer", "kind", "parts"}]}
- kind "sections": the field is the non-empty parts joined by blank lines, each optionally under "## <title>".
- kind "text": the field is the single part's text.
- kind "hook_msgs": the field is hook code; the MSG map between # MSG-BEGIN / # MSG-END is rebuilt from the parts.

`apply(workflow, book, zone_id, new_text)` is the only way a patch touches a workflow.
"""

from __future__ import annotations

import copy

from builder.template_cc.policy_check import get_pointer, set_pointer

MAX_GROWTH = 0.15


class ZoneError(ValueError):
    pass


class ZoneBook:
    def __init__(self):
        self.zones: dict[str, dict] = {}
        self.targets: list[dict] = []

    def add(self, zid: str, text: str, locked: bool, max_chars: int | None = None, purpose: str = "") -> str:
        if zid in self.zones:
            if self.zones[zid]["text"] != text:
                raise ZoneError(f"zone {zid} added twice with different text")
            return zid
        self.zones[zid] = {"locked": locked, "max_chars": max_chars, "text": text, "purpose": purpose}
        return zid

    def target(self, pointer: str, kind: str, parts: list[dict]):
        self.targets.append({"pointer": pointer, "kind": kind, "parts": parts})

    def to_json(self) -> dict:
        return {"zones": self.zones, "targets": self.targets}

    @classmethod
    def from_json(cls, data: dict) -> "ZoneBook":
        b = cls()
        b.zones = copy.deepcopy(data["zones"])
        b.targets = copy.deepcopy(data["targets"])
        return b


def esc(node_or_key: str) -> str:
    return node_or_key.replace("~", "~0").replace("/", "~1")


def render_target(book_zones: dict, t: dict, current=None):
    kind = t["kind"]
    if kind == "text":
        return book_zones[t["parts"][0]["zone"]]["text"]
    if kind == "sections":
        chunks = []
        for p in t["parts"]:
            text = book_zones[p["zone"]]["text"].strip()
            if text:
                chunks.append((f"## {p['title']}\n" if p.get("title") else "") + text)
        return "\n\n".join(chunks)
    if kind == "hook_msgs":
        from builder.template_cc.compiler import snippets  # noqa: PLC0415 - avoids an import cycle
        msgs = {p["key"]: book_zones[p["zone"]]["text"] for p in t["parts"]}
        return snippets.replace_msgs(current, msgs)
    raise ZoneError(f"unknown target kind {kind}")


def render_all(workflow: dict, book: ZoneBook) -> dict:
    for t in book.targets:
        cur = get_pointer(workflow, t["pointer"]) if t["kind"] == "hook_msgs" else None
        set_pointer(workflow, t["pointer"], render_target(book.zones, t, cur))
    return workflow


def model_text_size(book: ZoneBook) -> int:
    return sum(len(z["text"]) for z in book.zones.values())


def check_text(zid: str, zone: dict, new_text: str):
    if zone["locked"]:
        raise ZoneError(f"zone {zid} is locked")
    if zone.get("max_chars") and len(new_text) > zone["max_chars"]:
        raise ZoneError(f"zone {zid}: {len(new_text)} chars > budget {zone['max_chars']}")
    if zid.startswith("hook:") and ("{" in new_text or "}" in new_text):
        raise ZoneError(f"zone {zid}: braces are not allowed in hook messages")


def apply(workflow: dict, book: ZoneBook, zid: str, new_text: str) -> tuple[dict, ZoneBook]:
    """A new (workflow, book) with zone `zid` set to `new_text`. Raises ZoneError for unknown/locked/over budget."""
    if zid not in book.zones:
        raise ZoneError(f"unknown zone {zid}")
    check_text(zid, book.zones[zid], new_text)
    wf, nb = copy.deepcopy(workflow), ZoneBook.from_json(book.to_json())
    nb.zones[zid]["text"] = new_text
    for t in nb.targets:
        if any(p["zone"] == zid for p in t["parts"]):
            cur = get_pointer(wf, t["pointer"]) if t["kind"] == "hook_msgs" else None
            set_pointer(wf, t["pointer"], render_target(nb.zones, t, cur))
    return wf, nb


def listing(book: ZoneBook) -> list[dict]:
    return [{"id": k, "locked": z["locked"], "max_chars": z["max_chars"], "chars": len(z["text"]), "purpose": z.get("purpose", ""),
             "text": z["text"]} for k, z in book.zones.items()]
