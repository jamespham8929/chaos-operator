"""Tests for the five-field cron parser."""

from datetime import datetime, timezone

import pytest

from chaos_operator.cron import CronSchedule


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class TestParse:
    def test_every_field_shape(self):
        s = CronSchedule.parse("*/15 9-17 1,15 JAN-MAR MON-FRI")
        assert s.minutes == {0, 15, 30, 45}
        assert s.hours == set(range(9, 18))
        assert s.days_of_month == {1, 15}
        assert s.months == {1, 2, 3}
        assert s.days_of_week == {1, 2, 3, 4, 5}

    def test_step_from_a_start_value(self):
        assert CronSchedule.parse("5/20 * * * *").minutes == {5, 25, 45}

    def test_range_with_step(self):
        assert CronSchedule.parse("0 0-12/6 * * *").hours == {0, 6, 12}

    def test_seven_is_sunday(self):
        assert CronSchedule.parse("0 0 * * 7").days_of_week == {0}

    def test_names_are_case_insensitive(self):
        assert CronSchedule.parse("0 0 * jan sun").months == {1}

    @pytest.mark.parametrize("macro, expression", [
        ("@hourly", "0 * * * *"),
        ("@daily", "0 0 * * *"),
        ("@weekly", "0 0 * * 0"),
        ("@monthly", "0 0 1 * *"),
        ("@yearly", "0 0 1 1 *"),
    ])
    def test_macros(self, macro, expression):
        a, b = CronSchedule.parse(macro), CronSchedule.parse(expression)
        assert (a.minutes, a.hours, a.days_of_month, a.months, a.days_of_week) == (
            b.minutes, b.hours, b.days_of_month, b.months, b.days_of_week
        )

    @pytest.mark.parametrize("expression", [
        "* * * *",            # four fields
        "60 * * * *",         # minute out of range
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 8",
        "*/0 * * * *",        # zero step
        "10-5 * * * *",       # backwards range
        "1,,2 * * * *",       # empty list item
        "x * * * *",
        "0 0 * FOO *",
        "0 0 30 2 *",         # 30 February never comes
    ])
    def test_rejects(self, expression):
        with pytest.raises(ValueError):
            CronSchedule.parse(expression)


class TestNextAfter:
    def test_is_strictly_after(self):
        s = CronSchedule.parse("0 * * * *")
        assert s.next_after(utc(2026, 11, 23, 10, 0)) == utc(2026, 11, 23, 11, 0)

    def test_drops_seconds(self):
        s = CronSchedule.parse("* * * * *")
        assert s.next_after(utc(2026, 11, 23, 10, 0, 59, 999)) == utc(2026, 11, 23, 10, 1)

    def test_rolls_over_the_year(self):
        s = CronSchedule.parse("30 2 1 1 *")
        assert s.next_after(utc(2026, 6, 1)) == utc(2027, 1, 1, 2, 30)

    def test_weekday_only(self):
        # 2026-11-28 is a Saturday. The next weekday 03:00 is Monday the 30th.
        s = CronSchedule.parse("0 3 * * MON-FRI")
        assert s.next_after(utc(2026, 11, 28, 12, 0)) == utc(2026, 11, 30, 3, 0)

    def test_day_of_month_or_day_of_week_when_both_set(self):
        # Fires on the 1st and on every Monday. 2026-12-01 is a Tuesday.
        s = CronSchedule.parse("0 0 1 * MON")
        assert s.next_after(utc(2026, 11, 24)) == utc(2026, 11, 30)  # Monday
        assert s.next_after(utc(2026, 11, 30)) == utc(2026, 12, 1)   # the 1st

    def test_star_step_in_day_of_month_still_ands(self):
        # "*/2" counts as unrestricted for the OR rule, as in Vixie cron.
        s = CronSchedule.parse("0 0 */2 * MON")
        # Odd days that are also Mondays. 2026-11-30 is an even Monday.
        assert s.next_after(utc(2026, 11, 23)) == utc(2026, 12, 7)

    def test_leap_day(self):
        s = CronSchedule.parse("0 0 29 2 *")
        assert s.next_after(utc(2026, 3, 1)) == utc(2028, 2, 29)

    def test_converts_other_timezones_to_utc(self):
        from datetime import timedelta
        plus_two = timezone(timedelta(hours=2))
        s = CronSchedule.parse("0 * * * *")
        local = datetime(2026, 11, 23, 12, 30, tzinfo=plus_two)
        assert s.next_after(local) == utc(2026, 11, 23, 11)

    def test_needs_a_timezone(self):
        with pytest.raises(ValueError):
            CronSchedule.parse("* * * * *").next_after(datetime(2026, 1, 1))
