"""The run's wall clock, as the builder agent sees it through `time_left`. Standard library only."""

from __future__ import annotations

import time

# The skill's phase plan, as shares of the agent's budget (22 minutes by default: 4 / 6 / 2 / 8 / rest).
PHASES = (("read", 4 / 22), ("extract", 6 / 22), ("design", 2 / 22), ("build", 8 / 22), ("check_and_fix", 2 / 22))


class Clock:
    def __init__(self, max_minutes: float, start: float | None = None, now=time.monotonic):
        self.max_s = max_minutes * 60
        self._now = now
        self.start = self._now() if start is None else start

    def elapsed_min(self) -> float:
        return (self._now() - self.start) / 60

    def left_min(self) -> float:
        return max(0.0, self.max_s / 60 - self.elapsed_min())

    def plan(self) -> list[dict]:
        total, at, out = self.max_s / 60, 0.0, []
        for name, share in PHASES:
            out.append({"phase": name, "by_minute": round(at + share * total, 1)})
            at += share * total
        out[-1]["by_minute"] = round(total, 1)
        return out

    def time_left(self) -> dict:
        left = self.left_min()
        elapsed = self.elapsed_min()
        due = next((p["phase"] for p in self.plan() if elapsed < p["by_minute"]), "check_and_fix")
        advice = ("finish now: make sure out/workflow.json lints with 0 errors" if left < 3 else
                  "short on time: prefer a lint-clean simpler workflow" if left < 6 else "on budget")
        return {"minutes_left": round(left, 1), "minutes_elapsed": round(elapsed, 1), "phase_now": due,
                "plan": self.plan(), "advice": advice}
