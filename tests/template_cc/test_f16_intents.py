"""F16: the brief's own intent definitions, examples and near misses go into the side extraction description."""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"
BASE = "f1,f2,f3,f4,f9,f11"
INTENTS = {
    "hardship": {"definition": "Zorbling or a lost zibble.", "examples": ["my zibble is gone", "zorbling again"],
                 "near_misses": ["the bill is too big"], "_source": "x"},
    "discount": {"definition": "Asks for a lower amount.", "examples": ["can you lower it"], "near_misses": [], "_source": "x"},
    "refusal": {"definition": "never mapped to a side value", "examples": [], "near_misses": []},
}


def build(features, intents=INTENTS, name="brief-a"):
    pol = copy.deepcopy(json.loads((GOLD / f"{name}.policy.json").read_text()))
    if intents is not None:
        pol["intents"] = intents
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    wf, book = compiler.compile(pol, api, features=features)
    return wf, book, pol, api


def side(wf, nid="negotiate"):
    return next(e for e in wf["nodes"][nid]["extraction_schema"] if e["name"] == "side")["description"]


def test_definitions_examples_and_near_misses_are_in_the_side_description():
    wf, book, pol, api = build(BASE + ",f16")
    d = side(wf)
    assert "hardship (the brief's definition): Zorbling or a lost zibble." in d
    assert 'Examples: "my zibble is gone"; "zorbling again".' in d
    assert 'NOT hardship: "the bill is too big".' in d
    assert "discount (the brief's definition): Asks for a lower amount." in d
    assert "never mapped" not in d  # `refusal` is not a side value
    assert side(wf, "negotiate_resume") == d and side(wf, "promise_confirm") == d
    assert compiler.lint(wf, pol, api)["errors"] == []
    zone = book.zones["extract:negotiate/side/description"]
    assert zone["max_chars"] >= len(zone["text"])  # the longer text is inside its own budget


def test_without_the_flag_or_without_intents_nothing_changes():
    off, _, _, _ = build(BASE)
    assert "zibble" not in side(off)
    none, _, _, _ = build(BASE + ",f16", intents=None)
    assert side(none) == side(off)
    empty, _, _, _ = build(BASE + ",f16", intents={})
    assert side(empty) == side(off)


def test_only_values_the_node_extracts_are_described():
    wf, _, _, _ = build(BASE + ",f16", intents={"restructuring": {"definition": "a new plan", "examples": [], "near_misses": []}})
    assert "restructuring (the brief's definition): a new plan." in side(wf)
    wf2, _, _, _ = build("f1,f2,f3,f9,f11,f16", intents={"discount": {"definition": "lower amount"}})  # no f4: no discount value
    assert "lower amount" not in side(wf2)
