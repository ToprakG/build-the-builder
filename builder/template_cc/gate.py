"""GATE: commit a candidate only if heldout improves by the paired sign test and no fully-passing case breaks.

Ported from agentforge/experiments/sign_test.py (two-sided exact sign test on per-case pass-count differences).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

WIN_P = 0.1


def _two_sided(positive: int, n: int) -> float:
    if n == 0:
        return 1.0
    probs = [math.comb(n, i) / 2 ** n for i in range(n + 1)]
    cutoff = probs[positive]
    return min(1.0, sum(p for p in probs if p <= cutoff + 1e-12))


def sign_p(diffs: list[int]) -> tuple[float, int]:
    nonzero = [d for d in diffs if d != 0]
    n = len(nonzero)
    positive = sum(1 for d in nonzero if d > 0)
    return _two_sided(positive, n), positive - (n - positive)


@dataclass
class GateDecision:
    commit: bool
    p_value: float
    direction: int
    wins: int
    losses: int
    ties: int
    broken: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    best: dict = field(default_factory=dict)
    candidate: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


def decide(best: dict, candidate: dict, reps: int, p_threshold: float = WIN_P) -> GateDecision:
    """best/candidate: case_id -> passes (0..reps) on the heldout set."""
    ids = sorted(best)
    missing = [i for i in ids if i not in candidate]
    if missing:
        raise ValueError(f"candidate misses heldout cases: {missing}")
    diffs = [candidate[i] - best[i] for i in ids]
    p, direction = sign_p(diffs)
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    broken = [i for i in ids if best[i] == reps and candidate[i] == 0]
    reasons = [f"heldout: {wins} better, {losses} worse, {len(ids) - wins - losses} equal; sign test p={p:.3f} "
               f"(threshold {p_threshold}), direction {direction:+d}",
               f"pass total best {sum(best.values())} vs candidate {sum(candidate.values())} of {len(ids) * reps}"]
    commit = p < p_threshold and direction > 0
    if not commit:
        reasons.append("no significant improvement")
    if broken:
        commit = False
        reasons.append(f"cases that passed every rep now fail every rep: {broken}")
    return GateDecision(commit, p, direction, wins, losses, len(ids) - wins - losses, broken, reasons, dict(best), dict(candidate))
