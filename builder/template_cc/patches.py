"""Patch schema, in-memory application, diff and growth budget.

Prompt patch: {id, bug_ids, zone, op: "append"|"replace", before, after, brief_quote, rationale}
  - replace: `before` is copied exactly from the zone's current text and occurs there exactly once.
  - append: `before` is "".
Policy patch: {id, path (JSON pointer into policy.json), old_value, new_value, brief_quote, rationale}
"""

from __future__ import annotations

import copy
import difflib

from builder.template_cc import zones
from builder.template_cc.policy_check import get_pointer, pointer_exists, set_pointer

PROMPT_KEYS = ("id", "zone", "op", "before", "after", "brief_quote", "rationale")
POLICY_KEYS = ("id", "path", "new_value", "brief_quote", "rationale")


class PatchError(ValueError):
    pass


def shape_errors(p: dict) -> list[str]:
    if not isinstance(p, dict):
        return ["a patch must be an object"]
    errs = [f"missing {k}" for k in PROMPT_KEYS if k not in p]
    if p.get("op") not in ("append", "replace"):
        errs.append("op must be append or replace")
    if p.get("op") == "append" and p.get("before"):
        errs.append("append needs before = ''")
    if p.get("op") == "replace" and not p.get("before"):
        errs.append("replace needs the exact current text in before")
    if not str(p.get("after") or "").strip():
        errs.append("after is empty")
    if not isinstance(p.get("bug_ids", []), list):
        errs.append("bug_ids must be a list")
    return errs


def policy_shape_errors(p: dict) -> list[str]:
    if not isinstance(p, dict):
        return ["a patch must be an object"]
    errs = [f"missing {k}" for k in POLICY_KEYS if k not in p]
    if not str(p.get("path", "")).startswith("/"):
        errs.append("path must be a JSON pointer starting with /")
    return errs


def new_text(book: zones.ZoneBook, p: dict) -> str:
    z = book.zones.get(p["zone"])
    if z is None:
        raise PatchError(f"unknown zone {p['zone']}")
    cur = z["text"]
    if p["op"] == "append":
        return (cur.rstrip() + "\n" + p["after"].strip()).strip() if cur.strip() else p["after"].strip()
    n = cur.count(p["before"])
    if n != 1:
        raise PatchError(f"before occurs {n} times in {p['zone']} (must be exactly once)")
    return cur.replace(p["before"], p["after"])


def apply_one(wf: dict, book: zones.ZoneBook, p: dict) -> tuple[dict, zones.ZoneBook]:
    errs = shape_errors(p)
    if errs:
        raise PatchError("; ".join(errs))
    return zones.apply(wf, book, p["zone"], new_text(book, p))


def apply_all(wf: dict, book: zones.ZoneBook, patches: list[dict]) -> tuple[dict, zones.ZoneBook, list, list]:
    """Apply in order; a patch that fails is skipped. Returns (wf, book, applied, failed[(patch, reason)])."""
    applied, failed = [], []
    for p in patches:
        try:
            wf, book = apply_one(wf, book, p)
            applied.append(p)
        except (PatchError, zones.ZoneError, KeyError) as e:
            failed.append((p, str(e)))
    return wf, book, applied, failed


def growth(base: zones.ZoneBook, new: zones.ZoneBook) -> float:
    a = zones.model_text_size(base)
    return (zones.model_text_size(new) - a) / a if a else 0.0


def within_growth(base: zones.ZoneBook, new: zones.ZoneBook, limit: float = zones.MAX_GROWTH) -> bool:
    return growth(base, new) <= limit


def diff(a: zones.ZoneBook, b: zones.ZoneBook) -> str:
    out = []
    for zid in sorted(set(a.zones) | set(b.zones)):
        x, y = a.zones.get(zid, {}).get("text", ""), b.zones.get(zid, {}).get("text", "")
        if x != y:
            out += list(difflib.unified_diff(x.splitlines(), y.splitlines(), f"{zid} (before)", f"{zid} (after)", lineterm=""))
    return "\n".join(out)


def apply_policy(policy: dict, p: dict) -> dict:
    errs = policy_shape_errors(p)
    if errs:
        raise PatchError("; ".join(errs))
    if not pointer_exists(policy, p["path"]):
        raise PatchError(f"pointer {p['path']} does not exist in policy.json")
    out = copy.deepcopy(policy)
    set_pointer(out, p["path"], copy.deepcopy(p["new_value"]))
    return out


def current_value(policy: dict, path: str):
    return get_pointer(policy, path)
