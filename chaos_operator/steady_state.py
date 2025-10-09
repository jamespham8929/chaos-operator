"""Steady-state hypothesis verification.

The Principles of Chaos Engineering say an experiment should start from a
measured steady state and verify the system returns to it afterward. Most
homegrown chaos tooling skips the verification, or does it with a single
point-in-time metric check that is at the mercy of noise. A health endpoint that
happens to return 200 on the one request you made after the experiment tells you
very little.

This module treats steady state as a statistical claim. It takes a sample of the
metric, builds a bootstrap confidence interval for its mean, and decides whether
that interval sits inside the acceptable band. It can also test whether the
post-experiment distribution is meaningfully different from the pre-experiment
baseline using a permutation test, so "it recovered" means "we could not detect a
difference from how it started," not "one probe came back green."
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum

from .probes import MetricProbe


class Verdict(str, Enum):
    HOLDS = "holds"            # interval sits inside the acceptable band
    VIOLATED = "violated"      # interval sits outside the band
    INCONCLUSIVE = "inconclusive"  # interval straddles a band edge, need more data


@dataclass(frozen=True)
class Measurement:
    metric: str
    samples: list[float]
    mean: float
    ci_lower: float
    ci_upper: float

    @property
    def n(self) -> int:
        return len(self.samples)


@dataclass(frozen=True)
class SteadyStateHypothesis:
    """A claim that a metric's mean sits within [lower, upper].

    Bounds are absolute. For an availability metric a band might be
    [0.99, 1.0]. For p99 latency in milliseconds it might be [0, 250]. Leave a
    bound as None to make the band one-sided.
    """

    metric: str
    lower: float | None = None
    upper: float | None = None
    sample_size: int = 30
    confidence: float = 0.95

    def contains(self, value: float) -> bool:
        if self.lower is not None and value < self.lower:
            return False
        if self.upper is not None and value > self.upper:
            return False
        return True


def bootstrap_mean_ci(
    samples: list[float], confidence: float = 0.95, iterations: int = 2000,
    rng: random.Random | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean.

    Resamples the data with replacement `iterations` times, takes the mean of
    each resample, and reads the empirical percentiles. No distributional
    assumption and no scipy. For n below ~5 the interval is wide and honest about
    it, which is the correct behavior rather than a false precision.
    """
    if not samples:
        raise ValueError("cannot bootstrap an empty sample")
    rng = rng or random.Random()
    n = len(samples)
    means = []
    for _ in range(iterations):
        resample = [samples[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resample) / n)
    means.sort()
    tail = (1.0 - confidence) / 2.0
    lo = means[int(tail * iterations)]
    hi = means[int((1.0 - tail) * iterations) - 1]
    return lo, hi


class SteadyStateVerifier:
    def __init__(self, hypothesis: SteadyStateHypothesis, rng: random.Random | None = None):
        self.hypothesis = hypothesis
        self._rng = rng or random.Random()

    def measure(self, probe: MetricProbe) -> Measurement:
        samples = [probe.sample() for _ in range(self.hypothesis.sample_size)]
        mean = sum(samples) / len(samples)
        lo, hi = bootstrap_mean_ci(samples, self.hypothesis.confidence, rng=self._rng)
        return Measurement(self.hypothesis.metric, samples, mean, lo, hi)

    def verify(self, measurement: Measurement) -> Verdict:
        """Decide whether the measured interval sits inside the band."""
        h = self.hypothesis
        ci_inside = h.contains(measurement.ci_lower) and h.contains(measurement.ci_upper)
        if ci_inside:
            return Verdict.HOLDS

        # Fully outside on either side is a clear violation.
        if h.upper is not None and measurement.ci_lower > h.upper:
            return Verdict.VIOLATED
        if h.lower is not None and measurement.ci_upper < h.lower:
            return Verdict.VIOLATED

        # Interval straddles a band edge. Not enough evidence either way.
        return Verdict.INCONCLUSIVE


def permutation_test_difference(
    baseline: list[float], post: list[float], iterations: int = 5000,
    rng: random.Random | None = None,
) -> float:
    """Two-sided permutation test on the difference in means.

    Returns a p-value for the null hypothesis that baseline and post are drawn
    from the same distribution. A high p-value means we could not detect that the
    system changed, which is the evidence we want for "it recovered to baseline."
    A low p-value means the post-experiment state is measurably different.
    """
    if not baseline or not post:
        raise ValueError("both samples must be non-empty")
    rng = rng or random.Random()

    observed = abs(_mean(baseline) - _mean(post))
    pool = baseline + post
    n_baseline = len(baseline)
    count_as_extreme = 0

    for _ in range(iterations):
        rng.shuffle(pool)
        perm_baseline = pool[:n_baseline]
        perm_post = pool[n_baseline:]
        diff = abs(_mean(perm_baseline) - _mean(perm_post))
        if diff >= observed:
            count_as_extreme += 1

    return (count_as_extreme + 1) / (iterations + 1)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)
