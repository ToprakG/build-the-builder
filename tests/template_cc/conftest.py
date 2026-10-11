"""Offline tests for T-CC. No network, no real model, SDK or decision-model call."""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
FIX = Path(__file__).resolve().parent / "fixtures"

# Test-only: the testbed configs (ground truth) with their tool API. The builder never reads these.
TESTBED = [(REPO / "testbed" / "configs" / "brief-a.config.json", REPO / "tools" / "openapi.json"),
           (REPO / "testbed" / "configs" / "brief-b.config.json", REPO / "tools" / "openapi.json")] + [
    (d / "config.json", d / "tools.openapi.json") for d in sorted((REPO / "testbed" / "fake_briefs").iterdir()) if d.is_dir()]


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture
def fx_api():
    from builder.template_cc.apimodel import ApiModel

    return ApiModel.load(FIX / "invented.openapi.json")


@pytest.fixture
def fx_policy(fx_api):
    from builder.template_cc import policy_check

    return policy_check.complete(load(FIX / "invented.policy.json"), fx_api)


@pytest.fixture
def fx_brief():
    return (FIX / "invented.brief.md").read_text(encoding="utf-8")


def tb_policies():
    """(id, policy, ApiModel) for every testbed config, completed with T-CC defaults. Test-only use."""
    from builder.template_cc import policy_check
    from builder.template_cc.apimodel import ApiModel

    out = []
    for cfg_path, api_path in TESTBED:
        api = ApiModel.load(api_path)
        cfg = load(cfg_path)
        out.append((cfg.get("id", cfg_path.parent.name), policy_check.complete(copy.deepcopy(cfg), api), api))
    return out
