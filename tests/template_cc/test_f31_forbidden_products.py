"""F31: the product names of product_types are always words forbidden before verification."""
import copy
import json
from pathlib import Path

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel
from builder.template_cc.compiler import prompts as P

REPO = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent / "golden"


def pol(edit=None):
    p = json.loads((GOLD / "brief-a.policy.json").read_text())
    if edit:
        edit(p)
    return p


def test_names_missing_from_the_extracted_list_are_added_without_placeholders():
    def drop(p):
        p["forbidden_before_verify"] = ["debt"]
    p = pol(drop)
    words = P.forbidden_words(p, True)
    assert words[0] == "debt" and {"credit card", "personal loan", "car loan"} <= set(words)
    assert all("{" not in w for w in words)
    assert P.forbidden_words(p, False) == ["debt"]  # flag off: as extracted


def test_no_duplicates_and_an_empty_or_missing_list_still_works():
    p = pol()
    p["forbidden_before_verify"] = ["Personal Loan"]
    assert [w.lower() for w in P.forbidden_words(p, True)].count("personal loan") == 1
    for missing in ({"forbidden_before_verify": None}, {"forbidden_before_verify": []}):
        q = pol(lambda x: x.update(missing))
        assert "car loan" in P.forbidden_words(q, True)


def test_missing_product_types_changes_nothing():
    for bad in (None, [], {"x": "not a dict"}, {"x": {"label": None}}):
        p = pol(lambda x: x.update({"product_types": bad, "forbidden_before_verify": ["debt"]}))
        assert P.forbidden_words(p, True) == ["debt"]
    assert "Never say these words" not in P.system_forbidden({"forbidden_before_verify": [], "product_types": {}}, True)


def test_the_compiled_system_zone_carries_the_names_only_with_the_flag():
    api = ApiModel.load(REPO / "tools" / "openapi.json")
    p = pol(lambda x: x.update({"forbidden_before_verify": ["debt"]}))
    for flags, expect in (("f8,f31", True), ("f8", False)):
        wf, _ = compiler.compile(copy.deepcopy(p), api, features=flags)
        blob = json.dumps(wf)
        assert ('\\"debt\\", \\"credit card\\"' in blob) is expect
