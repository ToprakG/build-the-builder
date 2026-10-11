"""No brief value in the builder: bank names, holiday dates (ISO and spoken forms), enum / queue / note / code values,
identity field names and sentences over 6 words from every testbed config and Brief A/B must not appear anywhere in
builder/template_cc/** (tests excluded). Tokens the kit itself uses (tools/openapi.json, docs/*.md) and the canonical
schema keys are removed from the blacklist."""

import datetime
import json
import re

import pytest

from builder.template_cc import policy_check
from tests.template_cc.conftest import REPO

PKG = REPO / "builder" / "template_cc"
SCANNED = sorted(p for p in PKG.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".json", ".md"))
CONFIGS = sorted((REPO / "testbed" / "configs").glob("*.json")) + sorted((REPO / "testbed" / "fake_briefs").glob("*/config.json"))


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _dates(iso):
    out = set()
    for h in iso:
        d = datetime.date.fromisoformat(h)
        out |= {h, f"{d.day} {d:%B}", f"{d:%B} {d.day}", f"{d:%d.%m.%y}", f"{d:%d.%m.%Y}"}
    return out


def _sentences(text):
    return {s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.split()) > 6}


def blacklist() -> set:
    terms = set()
    for path in CONFIGS:
        c = json.loads(path.read_text())
        terms.add(c["bank"])
        terms |= _dates(c["holidays"])
        for key in ("outcome_map", "queue_map", "note_map", "tools", "account_fields"):
            terms |= set(_strings(c[key]))
        terms |= set(c["identity"]["questions"])
        terms |= {pt["enum"] for pt in c["product_types"].values()}
        terms |= {s for s in _strings(c["sentences"]) if len(s.split()) > 6}
    for brief in ("brief-a.md", "brief-b.md"):
        terms |= _sentences((REPO / "briefs" / brief).read_text())
    kit = (REPO / "tools" / "openapi.json").read_text().lower() + "".join(p.read_text().lower() for p in (REPO / "docs").glob("*.md"))
    schema = (PKG / "policy.schema.json").read_text()
    canonical = set(re.findall(r'"([a-z_][a-z0-9_]*)"', schema)) | set(policy_check.CANONICAL_OUTCOMES) | \
        set(policy_check.CANONICAL_QUEUES) | set(policy_check.CANONICAL_NOTES)
    generic = {t for t in terms if t and t.lower() not in kit and t not in canonical}
    for c in sorted((REPO / "testbed" / "configs").glob("brief-*.config.json")):  # always blacklisted
        cfg = json.loads(c.read_text())
        generic |= {cfg["bank"]} | _dates(cfg["holidays"])
    return generic


def test_blacklist_has_the_brief_specific_values():
    black = blacklist()
    for must in ("Freya Bank", "Halden Savings", "2026-10-26", "26 October", "postcode", "amount_cents", "10-PTP", "welfare",
                 "You will receive a text message confirming this promise."):
        assert must in black, must
    assert "promise" not in black and "verify_identity" not in black


ALLOW = json.loads((REPO / "tests" / "template_cc" / "no_leak_allowlist.json").read_text())["allow"]


def test_allowlist_entries_are_justified_and_narrow():
    for fname, entries in ALLOW.items():
        assert (PKG / fname).exists() or any(p.name == fname for p in SCANNED), fname
        for token, why in entries.items():
            assert len(why) > 40 and not re.search(r"\d", token) and " " not in token


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(REPO)))
def test_no_brief_value_in_the_builder(path):
    text = path.read_text(encoding="utf-8")
    allowed = set(ALLOW.get(path.name, {}))
    hits = [t for t in sorted(blacklist() - allowed)
            if re.search(r"(?<![A-Za-z0-9_])" + re.escape(t) + r"(?![A-Za-z0-9_])", text, re.I)]
    assert hits == [], f"{path.name} contains brief-specific values: {hits}"
