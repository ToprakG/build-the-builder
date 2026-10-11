import pytest

from builder.template_cc import features as F


def test_parse():
    assert F.parse("all") == frozenset(F.ALL)
    assert F.parse("none") == frozenset()
    assert F.parse("f1,f3") == {"f1_id_complete", "f3_human_first"}
    assert "f2_question_not_refusal" not in F.parse("all,-f2")
    assert F.parse("f1_id_complete") == {"f1_id_complete"}
    with pytest.raises(ValueError):
        F.parse("f99")


def test_env_default(monkeypatch):
    monkeypatch.setenv("BUILDER_FEATURES", "f4")
    assert F.parse(None) == {"f4_discount"}
    monkeypatch.delenv("BUILDER_FEATURES")
    assert F.parse(None) == frozenset(F.ALL)
