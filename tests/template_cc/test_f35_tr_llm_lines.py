"""f35_tr_llm_lines (opt-in): with policy.language == "tr" every fixed tts line becomes an LLM step; otherwise nothing changes."""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler, features as FT
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler.graph import Graph

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).parent / "golden"
BASE = "all"


def _build(lang, flags):
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    pol = compiler.policy_check.complete(copy.deepcopy(json.loads((GOLD / "brief-a.policy.json").read_text())), api)
    pol["language"] = lang
    return Graph(pol, api, features=FT.parse(flags)).build()[0]


def _tts(wf):
    nodes = {**wf["nodes"], **wf.get("global_nodes", {})}
    return [k for k, n in nodes.items() if (n.get("on_enter") or {}).get("type") == "tts"]


def test_flag_is_opt_in_and_off_by_default():
    assert "f35_tr_llm_lines" not in FT.parse("all") and "f35_tr_llm_lines" in FT.parse("all,f35")


def test_turkish_turns_every_tts_into_llm_and_other_cases_are_unchanged():
    on = _build("tr", "all,f35")
    assert _tts(on) == []
    nodes = {**on["nodes"], **on.get("global_nodes", {})}
    assert any("Turkish" in (n.get("on_enter") or {}).get("prompt", "") for n in nodes.values())
    assert _tts(_build("tr", "all"))            # flag off: fixed lines stay
    assert _tts(_build("en", "all,f35"))        # flag on, English policy: untouched
