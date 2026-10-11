"""TestRunner protocol for the self-test loop. The local tester (built in another session) implements it.

    run(workflow, cases, tool_api, policy, reps) -> list[CaseResult]

`NullRunner` raises (never falls back to Freya simulations). `FakeRunner` returns scripted results for tests.
`score_calls` scores a call's tool log against a case with OUR policy as the config (testbed/scoring/score_local.py,
imported as code; no testbed data is read).
"""

from __future__ import annotations

import importlib.util
from dataclasses import asdict, dataclass, field
from typing import Callable, Protocol

from builder.template_cc.io import REPO


@dataclass
class CaseResult:
    case_id: str
    rep: int
    passed: bool
    failed_reasons: list = field(default_factory=list)
    transcript: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    infra: bool = False  # the test infrastructure failed (e.g. chat API 5xx): unscored, not the agent's fault

    def as_dict(self):
        return asdict(self)


class TestRunner(Protocol):
    def run(self, workflow: dict, cases: list[dict], tool_api: dict, policy: dict, reps: int) -> list[CaseResult]:
        ...


class RunnerNotConnected(RuntimeError):
    pass


class NullRunner:
    def run(self, workflow, cases, tool_api, policy, reps):
        raise RunnerNotConnected("local tester not connected (--self-test local needs a TestRunner; "
                                 "this builder never falls back to Freya simulations)")


class FakeRunner:
    """Scripted results: fn(workflow, case, rep) -> bool | CaseResult."""

    def __init__(self, fn: Callable):
        self.fn, self.calls = fn, []

    def run(self, workflow, cases, tool_api, policy, reps):
        self.calls.append((workflow, [c["id"] for c in cases], reps))
        out = []
        for c in cases:
            for r in range(1, reps + 1):
                res = self.fn(workflow, c, r)
                if isinstance(res, CaseResult):
                    out.append(res)
                else:
                    out.append(CaseResult(c["id"], r, bool(res), [] if res else ["scripted failure"],
                                          [{"role": "agent", "text": "Hello."}, {"role": "user", "text": "Yes."}], []))
        return out


def _score_local():
    spec = importlib.util.spec_from_file_location("_tcc_score_local", REPO / "testbed" / "scoring" / "score_local.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score_calls(case: dict, calls: list[dict], policy: dict) -> dict:
    """Outcome part of a case: records from the tool log vs the case's expected records, with our policy as cfg."""
    return _score_local().score(case, calls, policy)


def pass_counts(results: list[CaseResult]) -> dict:
    out: dict[str, int] = {}
    for r in results:
        out[r.case_id] = out.get(r.case_id, 0) + int(r.passed)
    return out


def infra_ids(results: list[CaseResult]) -> set:
    """Case ids with at least one unscored (infrastructure-failed) run."""
    return {r.case_id for r in results if getattr(r, "infra", False)}
