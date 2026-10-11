"""Feature flags of the compiler fixes. 'all' (default), 'none', 'f1,f3', 'all,-f2'; short ids f1..f10 work."""

from __future__ import annotations

import os

ALL = ["f1_id_complete", "f2_question_not_refusal", "f3_human_first", "f4_discount", "f5_vague_date_policy",
       "f6_counters", "f8_fixed_lines", "f9_number_input", "f10_extract_vote", "f7_router", "f11_promise_typed",
       "f12_slot_summary", "f13_verifier", "f14_rules_ifthen", "f15_hardship_terms", "f16_intents",
       "f18_side_resume", "f19_refusal_rule", "f20_once_flags", "f21_spoken_answers", "f24_day_once",
       "f25_dispute_wins", "f26_amount_assembly"]
# f18_opening_fallback is an OPT-IN sub-switch of f18_side_resume, OFF by default (f3 GATE: 8/10 without, 4/10 with, same policy): when named,
# a first reply that sets no who1 goes to a fixed second ask (opening_resume); otherwise it stays in `opening` as without f18.
# opt-in: never part of "all"; a flag with no verified policy parameter changes nothing for that brief
# f35_tr_llm_lines (fallback for a Turkish case when the Turkish templates are not ready): with policy.language == "tr" every fixed `tts` line becomes an LLM-spoken
# step ("Say this in natural Turkish, siz form: <text>"). Opt-in, not part of FINAL_FLAGS.
# Full names of opt-in flags (f28_tool_failure) and short ids (f28) are both accepted in a flag list.
OPT_IN = ["f22_forbidden_disclosure", "f18_opening_fallback", "f27_vague_signal", "f28_tool_failure", "f29_scam_open", "f35_tr_llm_lines", "f31_forbidden_products"]
_SHORT: dict = {}
for _n in ALL + OPT_IN:
    _SHORT.setdefault(_n.split("_", 1)[0], _n)  # a short id names the first flag that has it (f16 = f16_intents)


def _one(tok: str) -> str:
    tok = tok.strip()
    if tok in ALL or tok in OPT_IN:
        return tok
    if tok in _SHORT:
        return _SHORT[tok]
    raise ValueError(f"unknown feature {tok!r}; known: {', '.join(ALL + OPT_IN)}")


def parse(spec: str | None = None) -> frozenset:
    """None means the env BUILDER_FEATURES, else 'all'."""
    if spec is None:
        spec = os.environ.get("BUILDER_FEATURES") or "all"
    on: set = set()
    for tok in (t for t in spec.replace(";", ",").split(",") if t.strip()):
        tok = tok.strip()
        if tok == "all":
            on = set(ALL)
        elif tok == "none":
            on = set()
        elif tok.startswith("-"):
            on.discard(_one(tok[1:]))
        else:
            on.add(_one(tok))
    return frozenset(on)


def names(features) -> list[str]:
    return sorted(features)
