"""A small five-field cron parser.

It supports what a chaos schedule needs: numbers, `*`, ranges, steps, lists,
three-letter month and weekday names, and the @hourly style macros. All times
are UTC. A dependency would do the same, but this is short enough to own and
test, and it keeps the operator image small.

Day-of-month and day-of-week follow Vixie cron: when both are restricted, a day
matches if either one matches. "0 3 1 * MON" fires on the 1st and on every
Monday, not only on Mondays that fall on the 1st.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

MONTH_NAMES = {name: i for i, name in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
    start=1,
)}
DAY_NAMES = {name: i for i, name in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}

# (low, high, names) for minute, hour, day of month, month, day of week.
FIELDS = [
    (0, 59, {}),
    (0, 23, {}),
    (1, 31, {}),
    (1, 12, MONTH_NAMES),
    (0, 7, DAY_NAMES),  # 7 is Sunday too, folded to 0 below
]

# A schedule like "0 0 30 2 *" (30 February) never fires. Searching this far
# ahead finds every real schedule, including 29 February in a leap year.
SEARCH_LIMIT = timedelta(days=366 * 8)


def _parse_value(token: str, names: dict[str, int]) -> int:
    upper = token.upper()
    if upper in names:
        return names[upper]
    if not token.isdigit():
        raise ValueError(f"{token!r} is not a number or a known name")
    return int(token)


def _parse_field(text: str, low: int, high: int, names: dict[str, int]) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise ValueError(f"empty list item in {text!r}")
        base, _, step_text = part.partition("/")
        step = 1
        if step_text:
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"step in {part!r} must be a positive number")
            step = int(step_text)

        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _parse_value(first, names), _parse_value(last, names)
        else:
            start = _parse_value(base, names)
            # "5/15" means every 15 starting at 5, as in Vixie cron.
            end = high if step_text else start

        if not (low <= start <= high and low <= end <= high):
            raise ValueError(f"{part!r} is outside {low}-{high}")
        if start > end:
            raise ValueError(f"range {part!r} runs backwards")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_restricted: bool
    dow_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> CronSchedule:
        text = MACROS.get(expression.strip().lower(), expression.strip())
        parts = text.split()
        if len(parts) != 5:
            raise ValueError(
                f"cron expression {expression!r} needs 5 fields "
                f"(minute hour day-of-month month day-of-week), got {len(parts)}"
            )
        parsed = [
            _parse_field(part, low, high, names)
            for part, (low, high, names) in zip(parts, FIELDS, strict=True)
        ]
        days_of_week = frozenset(0 if d == 7 else d for d in parsed[4])
        schedule = cls(
            expression=expression,
            minutes=parsed[0],
            hours=parsed[1],
            days_of_month=parsed[2],
            months=parsed[3],
            days_of_week=days_of_week,
            # Vixie cron treats a field that starts with "*" as unrestricted,
            # so "*/2" in day-of-month still ANDs with day-of-week.
            dom_restricted=not parts[2].startswith("*"),
            dow_restricted=not parts[4].startswith("*"),
        )
        # Fail at parse time, not on the first tick, for a schedule that can
        # never fire.
        schedule.next_after(datetime(2000, 1, 1, tzinfo=timezone.utc))
        return schedule

    def _day_matches(self, moment: datetime) -> bool:
        dom = moment.day in self.days_of_month
        # Python counts Monday as 0. Cron counts Sunday as 0.
        dow = (moment.weekday() + 1) % 7 in self.days_of_week
        if self.dom_restricted and self.dow_restricted:
            return dom or dow
        return dom and dow

    def next_after(self, moment: datetime) -> datetime:
        """The first fire time strictly after `moment`, in UTC."""
        if moment.tzinfo is None:
            raise ValueError("next_after needs a timezone-aware datetime")
        start = moment.astimezone(timezone.utc)
        t = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = start + SEARCH_LIMIT

        while t <= limit:
            if t.month not in self.months:
                year, month = (t.year + 1, 1) if t.month == 12 else (t.year, t.month + 1)
                t = t.replace(year=year, month=month, day=1, hour=0, minute=0)
                continue
            if not self._day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if t.hour not in self.hours:
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if t.minute not in self.minutes:
                t += timedelta(minutes=1)
                continue
            return t
        raise ValueError(f"cron expression {self.expression!r} never fires")
