"""COMPILE (pure code): policy.json + tool API -> workflow.json + zones.json. Deterministic.

    wf, book = compile(policy, api)            # api: builder.template_cc.apimodel.ApiModel
    dump(wf)                                   # canonical JSON text (byte-identical for the same input)
"""

from __future__ import annotations

import copy
import json

from builder.template_cc import features as feat
from builder.template_cc import policy_check
from builder.template_cc.compiler.graph import Graph
from builder.template_cc.zones import ZoneBook, render_all


def compile(policy: dict, api, close_form: str = "lifecycle", features=None) -> tuple[dict, ZoneBook]:  # noqa: A001 - the spec's name
    # the library default is no fixes (the run CLI passes the active set, default all); a string is parsed like --features
    f = frozenset() if features is None else (feat.parse(features) if isinstance(features, str) else frozenset(features))
    p = policy_check.complete(copy.deepcopy(policy), api)
    wf, book = Graph(p, api, close_form, features=f).build()
    render_all(wf, book)
    return wf, book


def dump(obj) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False) + "\n"


def unused_tools(policy: dict, wf: dict) -> list[str]:
    used = set()
    for group in ("nodes", "global_nodes"):
        for n in (wf.get(group) or {}).values():
            for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
                used.add(t.get("function", {}).get("name"))
    for t in wf.get("tools") or []:
        used.add(t.get("function", {}).get("name"))
    kit = ("verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call")
    return sorted(set(policy.get("unused_tools") or []) | {t for t in kit if t not in used})


def lint(wf: dict, policy: dict, api) -> dict:
    from builder.agentic.tools import lint as alint

    p = policy_check.complete(policy, api)
    return alint.lint(wf, api.summary, p.get("call_variables"), unused_tools(p, wf))
