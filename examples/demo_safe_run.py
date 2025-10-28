"""Runnable demo of a safe chaos run, no Kubernetes cluster required.

Drives the real runner, safety controller, and steady-state verifier against
synthetic probes. Shows three scenarios end to end:

  1. A mild experiment that holds steady state and completes.
  2. A severe experiment that trips a guard and gets aborted mid-run.
  3. An experiment whose rollback leaves damage, caught by the recovery check.

Run:
    PYTHONPATH=. python examples/demo_safe_run.py
"""

from __future__ import annotations

import random

from chaos_operator.experiment import SyntheticExperiment
from chaos_operator.probes import NoisyProbe
from chaos_operator.runner import ChaosRunner
from chaos_operator.safety import BlastRadiusGuard, Comparison, SafetyController
from chaos_operator.steady_state import SteadyStateHypothesis


class InstantClock:
    """Advances time on each sleep so the demo runs immediately."""

    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def scenario(title: str, inject_level: float, rollback_level: float,
             guard_threshold: float = 0.95) -> None:
    probe = NoisyProbe("availability", mean=0.999, stddev=0.0004, seed=1)
    hypothesis = SteadyStateHypothesis("availability", lower=0.99, upper=1.0, sample_size=40)
    clock = InstantClock()
    runner = ChaosRunner(
        hypothesis, probe, poll_interval_seconds=5,
        sleep=clock.sleep, now=clock.now, rng=random.Random(1),
    )
    guard = BlastRadiusGuard(
        "availability", probe, threshold=guard_threshold,
        comparison=Comparison.LESS_THAN, consecutive_breaches_to_abort=3,
    )
    controller = SafetyController([guard])
    experiment = SyntheticExperiment(
        title,
        on_inject=lambda: probe.set_level(inject_level),
        on_rollback=lambda: probe.set_level(rollback_level),
    )

    report = runner.run(experiment, controller, duration_seconds=60)

    print(f"\n=== {title} ===")
    print(f"outcome:           {report.outcome.value}")
    print(f"blast radius:      {report.blast_radius}")
    print(f"baseline verdict:  {report.baseline_verdict.value if report.baseline_verdict else '-'}")
    print(f"recovery verdict:  {report.recovery_verdict.value if report.recovery_verdict else '-'}")
    print(f"polls before stop: {report.polls}")
    if report.abort_reason:
        print(f"abort reason:      {report.abort_reason}")
    for note in report.notes:
        print(f"  - {note}")


def main() -> None:
    scenario("mild-latency (holds steady state)", inject_level=0.97, rollback_level=0.999)
    scenario("severe-outage (trips guard, aborts)", inject_level=0.88, rollback_level=0.999)
    scenario("leaves-damage (fails recovery check)", inject_level=0.97, rollback_level=0.97)


if __name__ == "__main__":
    main()
