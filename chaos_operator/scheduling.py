"""Decide whether a scheduled experiment should run on this tick.

The operator wakes up on a fixed interval and asks one question per experiment:
is a fire time due, and is it still safe to act on it? The answer is a pure
function of the schedule, the clock, and the last slot handled, so it is tested
without kopf, a cluster, or real time.

Two rules shape the answer. Both are explained in
docs/adr/0003-scheduled-experiments.md.

1. Never backfill. If the operator was down across several fire times, it runs
   once for the latest one and counts the rest as missed.
2. Never run late. A slot older than the starting deadline is skipped, not run.
   Chaos is often scheduled for a quiet window, and a run that starts hours
   late can land in peak traffic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .cron import CronSchedule

DEFAULT_STARTING_DEADLINE_SECONDS = 300

# An every-minute schedule with the operator down for a week is about 10,000
# slots. Past this many, stop counting and report the cap.
MAX_SLOTS_COUNTED = 20_000


@dataclass(frozen=True)
class Decision:
    run: bool
    # The fire time being run, or None when nothing runs this tick.
    slot: datetime | None
    # Every fire time at or before this one is dealt with. The caller stores it
    # so the next tick starts counting after it.
    handled_through: datetime
    next_run: datetime
    # Fire times passed over without a run.
    missed: int
    reason: str


def _count_due(schedule: CronSchedule, first_due: datetime, now: datetime) -> int:
    count, slot = 0, first_due
    while slot <= now and count < MAX_SLOTS_COUNTED:
        count += 1
        slot = schedule.next_after(slot)
    return count


def decide(
    schedule: CronSchedule,
    now: datetime,
    handled_through: datetime | None,
    created: datetime,
    starting_deadline_seconds: int = DEFAULT_STARTING_DEADLINE_SECONDS,
) -> Decision:
    anchor = handled_through or created
    first_due = schedule.next_after(anchor)
    if now < first_due:
        return Decision(
            run=False, slot=None, handled_through=anchor,
            next_run=first_due, missed=0, reason="not due",
        )

    due = _count_due(schedule, first_due, now)

    # Only a slot inside the deadline window may run. Look there directly, so
    # a long outage costs a short walk, not one step per missed slot.
    window_start = now - timedelta(seconds=starting_deadline_seconds)
    probe = max(anchor, window_start - timedelta(microseconds=1))
    latest = None
    candidate = schedule.next_after(probe)
    while candidate <= now:
        latest = candidate
        candidate = schedule.next_after(candidate)
    # candidate is now the first fire time after `now`.

    if latest is None:
        return Decision(
            run=False, slot=None, handled_through=now, next_run=candidate, missed=due,
            reason=(
                f"{due} slot(s) came due, none inside the "
                f"{starting_deadline_seconds}s starting deadline"
            ),
        )

    return Decision(
        run=True, slot=latest, handled_through=latest, next_run=candidate,
        missed=due - 1, reason="due",
    )
