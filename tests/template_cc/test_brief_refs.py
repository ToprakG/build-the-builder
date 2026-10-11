"""Brief references (scripts/brief_refs.py) and the policy sanity check (scripts/policy_sanity.py). Synthetic briefs and policies only."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import brief_refs as B  # noqa: E402
import policy_sanity as S  # noqa: E402

BASE = "# Brief X: Some Bank, one product\n\nEvery rule below applies.\n"
CHILD = "# Brief Y: Some Bank, many products\n\nBrief Y is Brief X with a customer who can be late on three products. Every rule of Brief X applies unless replaced.\n"
UNREL = "# Brief Q: Other Bank\n\nIn the Brief Q world the customer pays on time. Nothing else is assumed.\n"


def w(d, name, text):
    p = Path(d) / name
    p.write_text(text)
    return p


def test_detect_refers_and_does_not_refer():
    assert B.detect(CHILD) == ["X"]
    assert B.detect(BASE) == [] and B.detect(UNREL) == []                      # no other brief mentioned; its own id is never a reference
    assert B.detect("Same as Brief K, except the callback window.", "") == ["K"]
    assert B.detect("Brief K is not supplied.", "") == ["K"]
    assert B.detect("Brief B: appendix B lists the holidays.", "") == []        # a bare mention without a reference context is not one


def test_reference_resolved_in_the_call_first(tmp_path):
    a, b = w(tmp_path, "one.md", BASE), w(tmp_path, "two.md", CHILD)
    repo = tmp_path / "repo"; repo.mkdir()
    w(repo, "brief-x.md", "# Brief X: the repo's other X\n")                    # also in the repo: the call wins
    order, log = B.resolve([b], pool=[a, b], repo=str(repo))
    assert order == [str(a), str(b)] and log[0]["found_in"] == "call"


def test_reference_resolved_from_the_repo_by_title_or_file_name(tmp_path):
    b = w(tmp_path, "hidden.md", CHILD)
    repo = tmp_path / "repo"; repo.mkdir()
    base = w(repo, "brief-x.md", BASE)
    order, log = B.resolve([b], pool=[b], repo=str(repo))
    assert order == [str(base), str(b)] and log[0]["found_in"] == "repo"
    base.unlink(); w(repo, "other.md", BASE)                                    # found by title letter too
    assert B.resolve([b], pool=[b], repo=str(repo))[0][0].endswith("other.md")


def test_unresolvable_reference_is_a_warning_and_builds_alone(tmp_path):
    b = w(tmp_path, "hidden.md", CHILD)
    order, log = B.resolve([b], pool=[b], repo=str(tmp_path / "nope"))
    assert order == [str(b)] and log[0]["status"] == "WARN"


def test_no_reference_changes_nothing_and_cycles_stop(tmp_path):
    a = w(tmp_path, "a.md", BASE.replace("Every rule", "Brief Y is Brief X with changes. Every rule"))
    b = w(tmp_path, "b.md", CHILD)
    order, _ = B.resolve([a, b], pool=[a, b], repo=str(tmp_path / "nope"))
    assert sorted(order) == sorted([str(a), str(b)]) and len(order) == 2      # each file once, no endless loop
    q = w(tmp_path, "q.md", UNREL)
    assert B.resolve([q], pool=[q], repo=str(tmp_path))[0] == [str(q)]


def sane_policy():
    return {"bands": [{"name": "early", "min_days": 1, "max_days": 7, "deadline_business_days": 12, "attempts": 1}],
            "attempt_plan": [{"date": "deadline", "amount": "floor"}],
            "lines": {"opening": {"text": "Hello, I am a digital assistant calling from Some Bank."}},
            "outcome_map": {"tr_human": ["transferred", "human"], "tr_support": ["transferred", "support"]},
            "attempt1": "Propose the floor.", "extra_rules": [{"statement": "Pass customer_id to every call."}]}


def test_sanity_accepts_a_real_policy():
    assert S.check(sane_policy()) == []


def test_sanity_flags_each_degenerate_signal():
    p = sane_policy(); p["bands"] = [{"name": "unknown", "min_days": 0, "max_days": 0, "deadline_business_days": 0, "attempts": 0}]
    assert any("placeholder bands" in r for r in S.check(p))
    p = sane_policy(); p["attempt_plan"] = []
    assert any("attempt_plan" in r for r in S.check(p))
    p = sane_policy(); p["lines"]["opening"]["text"] = "Hello."
    assert any("opening" in r for r in S.check(p))
    p = sane_policy(); p["outcome_map"]["tr_human"] = None
    assert any("tr_human" in r for r in S.check(p))
    p = sane_policy(); p["extra_rules"] = [{"statement": "Brief X is not supplied. Bands are unknown."}]
    assert any("not supplied" in r for r in S.check(p))
    p = sane_policy(); p["bands"] = []
    assert S.check(p) == ["no bands"]


def test_sanity_cli_exit_codes(tmp_path):
    import subprocess
    good, bad = tmp_path / "g.json", tmp_path / "b.json"
    good.write_text(json.dumps(sane_policy())); bad.write_text(json.dumps({**sane_policy(), "attempt_plan": []}))
    run = lambda f: subprocess.run([sys.executable, str(REPO / "scripts" / "policy_sanity.py"), str(f), "--out", str(tmp_path / "o.json")], capture_output=True, text=True)
    assert run(good).returncode == 0 and run(bad).returncode == 1
    assert json.loads((tmp_path / "o.json").read_text())["degenerate"] is True


def test_hidden_brief_placed_in_briefs_wins_over_a_dev_brief_with_the_same_title(tmp_path):
    """RUNBOOK_FINAL: the 20:00 files go to briefs/00-<name>.md, which sorts before the dev brief-a.md that has the same title letter."""
    repo = tmp_path / "briefs"; repo.mkdir()
    dev = w(repo, "brief-x.md", BASE)
    hidden = w(repo, "00-hidden1.md", BASE.replace("one product", "hidden bank"))
    child = w(tmp_path, "h2.md", CHILD)
    order, log = B.resolve([child], pool=[child], repo=str(repo))
    assert order == [str(hidden), str(child)] and log[0]["found_in"] == "repo" and str(dev) not in order
