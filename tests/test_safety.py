"""Tests for the safety controller and blast-radius guards."""

import pytest

from chaos_operator.probes import SequenceProbe
from chaos_operator.safety import (
    BlastRadiusGuard,
    Comparison,
    SafetyController,
)


class TestBlastRadiusGuard:
    def test_no_trip_below_threshold(self):
        probe = SequenceProbe("error_rate", [0.001, 0.002, 0.001, 0.0])
        guard = BlastRadiusGuard("errors", probe, threshold=0.05)
        for _ in range(4):
            reading = guard.observe()
            assert not reading.tripped

    def test_trips_after_consecutive_breaches(self):
        probe = SequenceProbe("error_rate", [0.10, 0.11, 0.12])
        guard = BlastRadiusGuard("errors", probe, threshold=0.05,
                                 consecutive_breaches_to_abort=3)
        assert not guard.observe().tripped   # streak 1
        assert not guard.observe().tripped   # streak 2
        assert guard.observe().tripped       # streak 3 -> trip

    def test_debounce_resets_on_recovery(self):
        # A single spike then recovery must not trip a 3-breach guard.
        probe = SequenceProbe("error_rate", [0.10, 0.10, 0.001, 0.10, 0.10])
        guard = BlastRadiusGuard("errors", probe, threshold=0.05,
                                 consecutive_breaches_to_abort=3)
        results = [guard.observe().tripped for _ in range(5)]
        assert results == [False, False, False, False, False]

    def test_less_than_comparison_for_availability(self):
        probe = SequenceProbe("availability", [0.999, 0.95, 0.94, 0.93])
        guard = BlastRadiusGuard("avail", probe, threshold=0.99,
                                 comparison=Comparison.LESS_THAN,
                                 consecutive_breaches_to_abort=3)
        assert not guard.observe().tripped   # 0.999 ok
        assert not guard.observe().tripped   # 0.95 breach streak 1
        assert not guard.observe().tripped   # 0.94 breach streak 2
        assert guard.observe().tripped       # 0.93 breach streak 3


class TestSafetyController:
    def test_requires_at_least_one_guard(self):
        with pytest.raises(ValueError):
            SafetyController([])

    def test_aborts_when_any_guard_trips(self):
        ok = BlastRadiusGuard("latency", SequenceProbe("l", [10, 10, 10]), threshold=500)
        bad = BlastRadiusGuard("errors", SequenceProbe("e", [0.2, 0.2, 0.2]),
                               threshold=0.05, consecutive_breaches_to_abort=3)
        controller = SafetyController([ok, bad])
        controller.evaluate()  # streak 1
        controller.evaluate()  # streak 2
        decision = controller.evaluate()  # streak 3 -> abort
        assert decision.abort
        assert "errors" in decision.reason

    def test_no_abort_when_all_guards_healthy(self):
        g1 = BlastRadiusGuard("latency", SequenceProbe("l", [10, 20, 15]), threshold=500)
        g2 = BlastRadiusGuard("errors", SequenceProbe("e", [0.0, 0.01, 0.0]), threshold=0.05)
        controller = SafetyController([g1, g2])
        for _ in range(3):
            assert not controller.evaluate().abort

    def test_reset_clears_streaks(self):
        guard = BlastRadiusGuard("errors", SequenceProbe("e", [0.2, 0.2, 0.2, 0.2]),
                                 threshold=0.05, consecutive_breaches_to_abort=3)
        controller = SafetyController([guard])
        controller.evaluate()
        controller.evaluate()
        controller.reset()
        # After reset the streak restarts, so one more breach is not yet a trip.
        assert not controller.evaluate().abort
