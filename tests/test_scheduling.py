"""Tests for the per-tick scheduling decision."""

from datetime import datetime, timedelta, timezone

from chaos_operator.cron import CronSchedule
from chaos_operator.scheduling import MAX_SLOTS_COUNTED, decide

HOURLY = CronSchedule.parse("0 * * * *")
CREATED = datetime(2026, 11, 23, 9, 30, tzinfo=timezone.utc)


def at(hour, minute=0, second=0):
    return datetime(2026, 11, 23, hour, minute, second, tzinfo=timezone.utc)


class TestDecide:
    def test_not_due_before_the_first_slot(self):
        d = decide(HOURLY, now=at(9, 59), handled_through=None, created=CREATED)
        assert not d.run
        assert d.slot is None
        assert d.next_run == at(10)
        assert d.missed == 0

    def test_runs_on_the_first_tick_after_a_slot(self):
        d = decide(HOURLY, now=at(10, 0, 20), handled_through=None, created=CREATED)
        assert d.run
        assert d.slot == at(10)
        assert d.next_run == at(11)
        assert d.missed == 0

    def test_does_not_run_the_same_slot_twice(self):
        d = decide(HOURLY, now=at(10, 0, 50), handled_through=at(10), created=CREATED)
        assert not d.run
        assert d.next_run == at(11)

    def test_runs_once_after_downtime_and_counts_the_rest(self):
        # Down from 09:45 to 13:01. Slots at 10, 11, 12, and 13 came due.
        d = decide(HOURLY, now=at(13, 1), handled_through=at(9), created=CREATED)
        assert d.run
        assert d.slot == at(13)
        assert d.missed == 3
        assert d.next_run == at(14)

    def test_skips_a_slot_past_the_starting_deadline(self):
        # A nightly 03:00 run discovered at 09:00 must not start at peak time.
        nightly = CronSchedule.parse("0 3 * * *")
        created = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        d = decide(nightly, now=at(9), handled_through=None, created=created,
                   starting_deadline_seconds=300)
        assert not d.run
        assert d.slot is None
        assert d.handled_through == at(9)
        assert d.missed == 1
        assert d.next_run == datetime(2026, 11, 24, 3, tzinfo=timezone.utc)
        assert "starting deadline" in d.reason

    def test_a_slot_exactly_at_the_deadline_still_runs(self):
        d = decide(HOURLY, now=at(10, 5), handled_through=None, created=CREATED,
                   starting_deadline_seconds=300)
        assert d.run
        assert d.slot == at(10)

    def test_a_slot_inside_the_deadline_still_runs(self):
        d = decide(HOURLY, now=at(10, 4, 59), handled_through=None, created=CREATED,
                   starting_deadline_seconds=300)
        assert d.run

    def test_the_skipped_slot_is_recorded_so_it_is_not_counted_again(self):
        nightly = CronSchedule.parse("0 3 * * *")
        created = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
        first = decide(nightly, now=at(9), handled_through=None, created=created)
        second = decide(nightly, now=at(9, 0, 30), handled_through=first.handled_through,
                        created=created)
        assert not second.run
        assert second.missed == 0

    def test_long_downtime_on_a_busy_schedule_stays_bounded(self):
        every_minute = CronSchedule.parse("* * * * *")
        now = at(12) + timedelta(days=30)
        d = decide(every_minute, now=now, handled_through=None, created=CREATED)
        assert d.run
        assert d.slot == now
        assert d.next_run == now + timedelta(minutes=1)
        assert d.missed == MAX_SLOTS_COUNTED - 1
