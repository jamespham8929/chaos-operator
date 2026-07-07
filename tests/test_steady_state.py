"""Tests for steady-state hypothesis verification."""

import random

import pytest

from chaos_operator.probes import NoisyProbe
from chaos_operator.steady_state import (
    SteadyStateHypothesis,
    SteadyStateVerifier,
    Verdict,
    bootstrap_mean_ci,
    permutation_test_difference,
)


class TestBootstrapCI:
    def test_interval_brackets_the_mean(self):
        samples = [0.99, 0.995, 0.991, 0.993, 0.992, 0.994, 0.99, 0.996]
        lo, hi = bootstrap_mean_ci(samples, confidence=0.95, rng=random.Random(1))
        mean = sum(samples) / len(samples)
        assert lo <= mean <= hi

    def test_tighter_data_gives_narrower_interval(self):
        tight = [1.0, 1.0, 1.0, 1.0, 1.0]
        loose = [0.5, 1.5, 0.7, 1.3, 1.0]
        lo_t, hi_t = bootstrap_mean_ci(tight, rng=random.Random(3))
        lo_l, hi_l = bootstrap_mean_ci(loose, rng=random.Random(3))
        assert (hi_t - lo_t) < (hi_l - lo_l)

    def test_empty_sample_raises(self):
        with pytest.raises(ValueError):
            bootstrap_mean_ci([])


class TestVerify:
    def test_holds_when_interval_inside_band(self):
        hyp = SteadyStateHypothesis(metric="availability", lower=0.99, upper=1.0, sample_size=40)
        verifier = SteadyStateVerifier(hyp, rng=random.Random(7))
        probe = NoisyProbe("availability", mean=0.997, stddev=0.001, seed=7)
        measurement = verifier.measure(probe)
        assert verifier.verify(measurement) == Verdict.HOLDS

    def test_violated_when_interval_below_band(self):
        hyp = SteadyStateHypothesis(metric="availability", lower=0.99, upper=1.0, sample_size=40)
        verifier = SteadyStateVerifier(hyp, rng=random.Random(7))
        # Degraded well below the band, with small noise so the CI stays under 0.99.
        probe = NoisyProbe("availability", mean=0.95, stddev=0.001, seed=11)
        measurement = verifier.measure(probe)
        assert verifier.verify(measurement) == Verdict.VIOLATED

    def test_inconclusive_when_straddling_edge(self):
        hyp = SteadyStateHypothesis(metric="latency_ms", upper=250.0, sample_size=30)
        verifier = SteadyStateVerifier(hyp, rng=random.Random(5))
        # Centered right on the upper bound with real spread, so the CI straddles it.
        probe = NoisyProbe("latency_ms", mean=250.0, stddev=30.0, seed=5)
        measurement = verifier.measure(probe)
        assert verifier.verify(measurement) == Verdict.INCONCLUSIVE

    def test_one_sided_band(self):
        hyp = SteadyStateHypothesis(metric="latency_ms", upper=250.0, sample_size=30)
        verifier = SteadyStateVerifier(hyp, rng=random.Random(9))
        probe = NoisyProbe("latency_ms", mean=120.0, stddev=5.0, seed=9)
        measurement = verifier.measure(probe)
        assert verifier.verify(measurement) == Verdict.HOLDS


class TestPermutationTest:
    def test_same_distribution_gives_high_p_value(self):
        rng = random.Random(1)
        baseline = [rng.gauss(100, 5) for _ in range(40)]
        post = [rng.gauss(100, 5) for _ in range(40)]
        p = permutation_test_difference(baseline, post, iterations=2000, rng=random.Random(1))
        assert p > 0.05, "should not detect a difference between identical distributions"

    def test_shifted_distribution_gives_low_p_value(self):
        rng = random.Random(2)
        baseline = [rng.gauss(100, 5) for _ in range(40)]
        post = [rng.gauss(140, 5) for _ in range(40)]
        p = permutation_test_difference(baseline, post, iterations=2000, rng=random.Random(2))
        assert p < 0.05, "should detect a clear 40-unit shift"

    def test_empty_input_raises(self):
        with pytest.raises(ValueError):
            permutation_test_difference([], [1.0])
