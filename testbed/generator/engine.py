"""Calendar + rule oracle shared by every brief. All expected outcomes are computed here, never hand-written."""
from __future__ import annotations

import datetime as dt

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def parse_now(now: str) -> tuple[dt.date, dt.time]:
    x = dt.datetime.fromisoformat(now)
    return x.date(), x.time()


def words(day: dt.date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day.day} {day.strftime('%B')}"


def short(day: dt.date) -> str:
    return f"{day.day} {day.strftime('%B')}"


class Cal:
    def __init__(self, holidays: list[str]):
        self.hol = {d(h) for h in holidays}

    def is_bd(self, x: dt.date) -> bool:
        return x.weekday() < 5 and x not in self.hol

    def add_bd(self, today: dt.date, n: int) -> dt.date:
        x, k = today, 0
        while k < n:
            x += dt.timedelta(days=1)
            if self.is_bd(x):
                k += 1
        return x

    def next_bd_after(self, x: dt.date) -> dt.date:
        y = x + dt.timedelta(days=1)
        while not self.is_bd(y):
            y += dt.timedelta(days=1)
        return y

    def bd_on_or_after(self, x: dt.date) -> dt.date:
        return x if self.is_bd(x) else self.next_bd_after(x)

    def last_bd_of_week(self, x: dt.date) -> dt.date:
        fri = x + dt.timedelta(days=4 - x.weekday())
        y = fri
        while not self.is_bd(y):
            y -= dt.timedelta(days=1)
        return y

    def attempt1_date(self, today: dt.date) -> dt.date:
        lbw = self.last_bd_of_week(today)
        if lbw <= today:
            lbw = self.last_bd_of_week(today + dt.timedelta(days=7))
        return lbw

    def next_weekday(self, today: dt.date, wd: int) -> dt.date:
        """'next <weekday>': that weekday in the following calendar week."""
        monday_next = today + dt.timedelta(days=7 - today.weekday())
        return monday_next + dt.timedelta(days=wd)

    def this_weekday(self, today: dt.date, wd: int) -> dt.date:
        monday = today - dt.timedelta(days=today.weekday())
        return monday + dt.timedelta(days=wd)


class Policy:
    """Wraps a brief config. Only the rule engine reads it."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cal = Cal(cfg["holidays"])

    def band(self, days_late: int) -> dict:
        for b in self.cfg["bands"]:
            if b["min_days"] <= days_late <= b["max_days"]:
                return b
        raise ValueError(f"days_late {days_late} outside every band of {self.cfg['id']}")

    def deadline(self, today: dt.date, days_late: int) -> dt.date:
        return self.cal.add_bd(today, self.band(days_late)["deadline_business_days"])

    def attempts(self, days_late: int) -> int:
        return self.band(days_late)["attempts"]

    def attempt1(self, today: dt.date, days_late: int) -> dt.date:
        a = self.cal.attempt1_date(today)
        dl = self.deadline(today, days_late)
        return min(a, dl)

    def resolve_named_date(self, today: dt.date, days_late: int, named: dt.date) -> tuple[str, dt.date]:
        """Returns (rule, date the agent should record if the customer accepts)."""
        dl = self.deadline(today, days_late)
        if named < today:
            raise ValueError("named date in the past")
        if named > dl:
            return "after_deadline", dl
        if not self.cal.is_bd(named):
            return "non_business_day", self.cal.next_bd_after(named)
        return "ok", named

    def callback_ok(self, now: str, day: dt.date, hhmm: str) -> bool:
        today, t = parse_now(now)
        cb = self.cfg["callback"]
        allowed_days = []
        if cb.get("same_day", True):
            allowed_days.append(today)
        allowed_days.append(self.cal.next_bd_after(today))
        if day not in allowed_days:
            return False
        if not (cb["start"] <= hhmm <= cb["end"]):
            return False
        when = dt.datetime.combine(day, dt.time.fromisoformat(hhmm))
        return when > dt.datetime.combine(today, t)
