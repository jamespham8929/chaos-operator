"""End-to-end tests for the chaos runner.

These exercise the full lifecycle with a synthetic experiment driving a noisy
probe, a fake clock so nothing actually sleeps, and a fixed RNG so the bootstrap
verdicts are deterministic.
"""

import random

from chaos_operator.experiment import SyntheticExperiment
from chaos_operator.probes import NoisyProbe
from chaos_operator.runner import ChaosRunner, Outcome
from chaos_operator.safety import BlastRadiusGuard, Comparison, SafetyController
from chaos_operator.steady_state import SteadyStateHypothesis


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def build(probe, guard_threshold=0.95, poll_interval=5.0):
    hypothesis = SteadyStateHypothesis(
        metric="availability", lower=0.99, upper=1.0, sample_size=40, confidence=0.95
    )
    clock = FakeClock()
    runner = ChaosRunner(
        hypothesis, probe, poll_interval_seconds=poll_interval,
        sleep=clock.sleep, now=clock.now, rng=random.Random(42),
    )
    guard = BlastRadiusGuard(
        "availability", probe, threshold=guard_threshold,
        comparison=Comparison.LESS_THAN, consecutive_breaches_to_abort=3,
    )
    controller = SafetyController([guard])
    return runner, controller


def test_successful_experiment():
    # Healthy baseline. Mild fault that never trips the guard. Clean recovery.
    probe = NoisyProbe("availability", mean=0.999, stddev=0.0003, seed=1)
    runner, controller = build(probe)
    experiment = SyntheticExperiment(
        "mild-latency",
        on_inject=lambda: probe.set_level(0.97),    # dips, but above 0.95 guard
        on_rollback=lambda: probe.set_level(0.999),
    )
    report = runner.run(experiment, controller, duration_seconds=30)
    assert report.outcome == Outcome.SUCCEEDED
    assert experiment.injected and experiment.rolled_back
    assert report.abort_reason is None


def test_aborts_when_fault_exceeds_blast_radius():
    probe = NoisyProbe("availability", mean=0.999, stddev=0.0003, seed=2)
    runner, controller = build(probe)
    experiment = SyntheticExperiment(
        "severe-outage",
        on_inject=lambda: probe.set_level(0.90),    # well below 0.95 guard
        on_rollback=lambda: probe.set_level(0.999),
    )
    report = runner.run(experiment, controller, duration_seconds=60)
    assert report.outcome == Outcome.ABORTED
    assert report.system_protected_itself
    assert experiment.rolled_back
    assert "availability" in report.abort_reason
    # Aborted on the third consecutive breach, not after the full duration.
    assert report.polls == 3


def test_refuses_to_start_on_unhealthy_baseline():
    # Baseline already below the band. The runner must not inject chaos.
    probe = NoisyProbe("availability", mean=0.95, stddev=0.0003, seed=3)
    runner, controller = build(probe)
    injected = {"value": False}
    experiment = SyntheticExperiment(
        "should-not-run",
        on_inject=lambda: injected.__setitem__("value", True),
        on_rollback=lambda: None,
    )
    report = runner.run(experiment, controller, duration_seconds=30)
    assert report.outcome == Outcome.REFUSED
    assert injected["value"] is False, "must not inject into an unhealthy system"


def test_detects_failure_to_recover():
    # Fault is mild enough not to trip the guard, but rollback does not restore
    # the metric. The post-experiment steady state must fail to verify.
    probe = NoisyProbe("availability", mean=0.999, stddev=0.0003, seed=4)
    runner, controller = build(probe)
    experiment = SyntheticExperiment(
        "leaves-damage",
        on_inject=lambda: probe.set_level(0.97),
        on_rollback=lambda: probe.set_level(0.97),   # never returns to baseline
    )
    report = runner.run(experiment, controller, duration_seconds=30)
    assert report.outcome == Outcome.DID_NOT_RECOVER
    assert report.recovery_verdict is not None
