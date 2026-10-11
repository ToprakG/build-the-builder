"""Golden baseline: with features=none the compiler output is byte-identical to the pre-flag compiler.

Regenerate (only when a deliberate, flag-independent change is made):  GOLDEN_WRITE=1 pytest tests/template_cc/test_golden_none.py
"""
import json
import os
import shutil
from pathlib import Path

import pytest

from builder.template_cc import compiler
from builder.template_cc.apimodel import ApiModel

REPO = Path(__file__).resolve().parents[2]
FIX = Path(__file__).resolve().parent / "fixtures"
GOLD = Path(__file__).resolve().parent / "golden"
OUT = REPO / "out" / "template_cc"
FAKES = sorted(p.name for p in (REPO / "testbed" / "fake_briefs").iterdir() if p.is_dir())

INPUTS = {"fixture": (FIX / "invented.policy.json", FIX / "invented.openapi.json"),
          "brief-a": (OUT / "policy-a.json", REPO / "tools" / "openapi.json"),
          "brief-b": (OUT / "policy-b.json", REPO / "tools" / "openapi.json")}
INPUTS.update({f: (OUT / "fake" / f / "policy.json", REPO / "testbed" / "fake_briefs" / f / "tools.openapi.json") for f in FAKES})


def policy_path(name):  # the cached policies are copied next to the goldens so the test needs no out/ directory
    return GOLD / f"{name}.policy.json"


def compile_none(name, features=None):
    api = ApiModel.load(INPUTS[name][1])
    policy = json.loads(policy_path(name).read_text(encoding="utf-8"))
    kw = {} if features is None else {"features": features}
    wf, book = compiler.compile(policy, api, **kw)
    return compiler.dump(wf), policy, api


@pytest.mark.parametrize("name", sorted(INPUTS))
def test_features_none_is_byte_identical(name):
    if os.environ.get("GOLDEN_WRITE"):
        shutil.copy(INPUTS[name][0], policy_path(name))
        text, _, _ = compile_none(name, features="none")
        (GOLD / f"{name}.workflow.json").write_text(text, encoding="utf-8")
    gold = GOLD / f"{name}.workflow.json"
    assert gold.exists(), "golden missing; run with GOLDEN_WRITE=1 on the unchanged compiler"
    text, _, _ = compile_none(name, features="none")
    assert text == gold.read_text(encoding="utf-8")
