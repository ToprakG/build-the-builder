"""Check rules.json against the brief text and the workflow. Standard library only.

A rule is {id, kind, statement, source_quote, overridden_by, implemented_in}. Checks:
- source_quote appears in a brief (after normalisation); an `assumption` may quote the tool API instead
- every implemented_in target exists in the workflow
- a rule without a location is "n/a" with a reason
- numbered rule lines of the brief that no quote covers are reported (warning)
"""

from __future__ import annotations

import json
import re

from builder.agentic.tools.lint import _fn

QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                        "“": '"', "”": '"', "„": '"', "″": '"',
                        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
                        "−": "-", " ": " ", "…": "..."})
NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+\S")
MIN_QUOTE = 8


def normalize(text: str) -> str:
    text = str(text).translate(QUOTES)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"^[ \t]*(#+|[-*+>]|\d+[.)])[ \t]+", "", text, flags=re.M)  # headings, bullets, quotes, numbers
    text = text.replace("|", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def _targets(wf: dict) -> dict:
    nodes, gnodes = wf.get("nodes") or {}, wf.get("global_nodes") or {}
    allnodes = {**nodes, **gnodes}
    tools = {}
    for nid, n in allnodes.items():
        tools[nid] = {_fn(t).get("name"): t for t in (n.get("tools") or []) + (n.get("pre_actions") or []) if isinstance(t, dict)}
    tools["workflow"] = {_fn(t).get("name"): t for t in wf.get("tools") or [] if isinstance(t, dict)}
    trans = {(nid, t.get("target_node")) for nid, n in allnodes.items() for t in n.get("transitions") or [] if isinstance(t, dict)}
    return {"nodes": nodes, "gnodes": gnodes, "all": allnodes, "tools": tools, "trans": trans}


def target_exists(loc: str, wf: dict, idx: dict | None = None) -> str | None:
    """None if the location exists, else the reason it does not."""
    idx = idx or _targets(wf)
    loc = str(loc).strip()
    if loc == "system_prompt":
        return None if str(wf.get("system_prompt") or "").strip() else "system_prompt is empty"
    kind, _, rest = loc.partition(":")
    if kind == "node":
        return None if rest in idx["all"] else f"no node {rest!r}"
    if kind == "global":
        return None if rest in idx["gnodes"] else f"no global node {rest!r}"
    if kind == "code":
        n = idx["all"].get(rest)
        return None if n and n.get("node_type") == "code" else f"no code node {rest!r}"
    if kind in ("tool", "hook"):
        owner, _, tool = rest.partition("/")
        t = idx["tools"].get(owner, {}).get(tool)
        if t is None:
            return f"no tool {tool!r} in {owner!r}"
        if kind == "hook" and not (str(t.get("pre_process_code") or "").strip() or str(t.get("post_process_code") or "").strip()):
            return f"tool {tool!r} in {owner!r} has no hook"
        return None
    if kind == "transition":
        src, _, dst = rest.partition("->")
        return None if (src.strip(), dst.strip()) in idx["trans"] else f"no transition {src.strip()} -> {dst.strip()}"
    return f"unknown location kind {loc!r} (use node:, code:, hook:, global:, transition:, tool:, system_prompt)"


def _is_na(impl) -> bool:
    if isinstance(impl, str):
        return impl.strip().lower() == "n/a"
    return isinstance(impl, list) and len(impl) == 1 and str(impl[0]).strip().lower() == "n/a"


def check(rules, wf: dict, brief_texts: list[str], api_text: str = "") -> dict:
    errors, warnings = [], []
    if isinstance(rules, dict) and "rules" in rules:
        rules = rules["rules"]
    if not isinstance(rules, list):
        return {"errors": [{"rule": None, "msg": "rules.json must be a list of rules (or {\"rules\": [...]})"}], "warnings": []}
    briefs = normalize("\n".join(brief_texts))
    api = normalize(api_text)
    idx = _targets(wf) if isinstance(wf, dict) else None
    seen_ids = set()
    for i, rule in enumerate(rules):
        rid = rule.get("id", f"#{i}") if isinstance(rule, dict) else f"#{i}"
        if not isinstance(rule, dict):
            errors.append({"rule": rid, "msg": "rule must be an object"})
            continue
        if rid in seen_ids:
            errors.append({"rule": rid, "msg": "duplicate id"})
        seen_ids.add(rid)
        quote = normalize(rule.get("source_quote") or "")
        if len(quote) < MIN_QUOTE:
            errors.append({"rule": rid, "msg": f"source_quote missing or shorter than {MIN_QUOTE} characters"})
        elif quote not in briefs and not (rule.get("kind") == "assumption" and quote in api):
            errors.append({"rule": rid, "msg": "source_quote not found in the brief: copy the words exactly "
                                               "(assumptions may quote the tool API)"})
        impl = rule.get("implemented_in")
        if _is_na(impl):
            if not str(rule.get("reason") or rule.get("na_reason") or "").strip():
                errors.append({"rule": rid, "msg": "implemented_in is n/a without a reason"})
            continue
        if rule.get("overridden_by") and not impl:
            continue  # an overridden rule lives on through the rule that overrides it
        if not impl:
            errors.append({"rule": rid, "msg": "not implemented: list workflow locations, or n/a with a reason"})
            continue
        if idx is None:
            errors.append({"rule": rid, "msg": "workflow.json missing or invalid"})
            continue
        for loc in impl if isinstance(impl, list) else [impl]:
            why = target_exists(loc, wf, idx)
            if why:
                errors.append({"rule": rid, "msg": f"implemented_in {loc!r}: {why}"})
    # numbered rule lines that no quote covers
    quotes = [normalize(r.get("source_quote") or "") for r in rules if isinstance(r, dict)]
    numbered, uncited = 0, []
    for text in brief_texts:
        for line in text.splitlines():
            if NUMBERED.match(line):
                numbered += 1
                nl = normalize(line)
                if not any(q and (q in nl or nl in q) for q in quotes if len(q) >= MIN_QUOTE):
                    uncited.append(line.strip()[:90])
    if uncited:
        warnings.append({"rule": None, "msg": f"{numbered - len(uncited)}/{numbered} numbered brief lines cited; "
                                              "not cited: " + " | ".join(uncited[:12]) + (" ..." if len(uncited) > 12 else "")})
    return {"errors": errors, "warnings": warnings, "rules": len(rules), "numbered_lines": numbered,
            "numbered_cited": numbered - len(uncited)}


def render(report: dict, limit: int = 60) -> str:
    lines = [f"{len(report['errors'])} error(s), {len(report['warnings'])} warning(s); {report.get('rules', 0)} rules"]
    for kind in ("errors", "warnings"):
        for it in report[kind][:limit]:
            lines.append(f"{kind[:-1].upper()} {it['rule'] or ''}: {it['msg']}")
        if len(report[kind]) > limit:
            lines.append(f"... {len(report[kind]) - limit} more")
    return "\n".join(lines)


def load_rules(path) -> list | dict:
    return json.loads(open(path, encoding="utf-8").read())
