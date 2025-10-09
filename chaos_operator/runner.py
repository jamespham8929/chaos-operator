"""Orchestrates a safe chaos experiment.

Lifecycle:

  1. Measure the steady state and verify the system is healthy BEFORE injecting.
     Running chaos on an already-degraded system teaches you nothing and risks an
     outage, so a failed pre-check refuses to start.
  2. Inject the fault.
  3. Poll the safety controller for the experiment duration. If any guard trips,
     roll back immediately and record the abort.
  4. Roll back (if not already aborted).
  5. Measure the steady state again and verify the system returned to it.
  6. Emit a report with a single overall outcome.

The clock and sleep function are injected so tests run instantly and
deterministically. In production they default to the real monotonic clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .experiment import Experiment
from .probes import MetricProbe
from .safety import AbortDecision, SafetyController
from .steady_state import (
    Measurement,
    SteadyStateHypothesis,
    SteadyStateVerifier,
    Verdict,
)


class Outcome(str, Enum):
    # Steady state held, hypothesis verified before and after, no abort.
    SUCCEEDED = "succeeded"
    # Safety controller tripped, fault rolled back. The system protected itself.
    ABORTED = "aborted"
    # Fault completed but the system did not return to steady state.
    DID_NOT_RECOVER = "did_not_recover"
    # Refused to start because the pre-check did not show a healthy baseline.
    REFUSED = "refused"


@dataclass
class ExperimentReport:
    experiment: str
    outcome: Outcome
    blast_radius: str
    baseline: Measurement | None = None
    recovery: Measurement | None = None
    baseline_verdict: Verdict | None = None
    recovery_verdict: Verdict | None = None
    abort_reason: str | None = None
    polls: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def system_protected_itself(self) -> bool:
        return self.outcome == Outcome.ABORTED


class ChaosRunner:
    def __init__(
        self,
        hypothesis: SteadyStateHypothesis,
        probe: MetricProbe,
        poll_interval_seconds: float = 5.0,
        sleep=time.sleep,
        now=time.monotonic,
        rng=None,
    ):
        self._verifier = SteadyStateVerifier(hypothesis, rng=rng)
        self._probe = probe
        self._poll_interval = poll_interval_seconds
        self._sleep = sleep
        self._now = now

    def run(
        self,
        experiment: Experiment,
        controller: SafetyController,
        duration_seconds: float,
    ) -> ExperimentReport:
        report = ExperimentReport(
            experiment=experiment.name,
            outcome=Outcome.SUCCEEDED,
            blast_radius=experiment.blast_radius(),
        )

        # 1. Pre-check. Do not inject chaos into an unhealthy system.
        baseline = self._verifier.measure(self._probe)
        baseline_verdict = self._verifier.verify(baseline)
        report.baseline = baseline
        report.baseline_verdict = baseline_verdict
        if baseline_verdict != Verdict.HOLDS:
            report.outcome = Outcome.REFUSED
            report.notes.append(
                f"refused to start: baseline steady state {baseline_verdict.value} "
                f"(mean {baseline.mean:.4g}, CI [{baseline.ci_lower:.4g}, {baseline.ci_upper:.4g}])"
            )
            return report

        # 2. Inject.
        controller.reset()
        experiment.inject()
        report.notes.append(f"injected fault: {experiment.name}")

        # 3. Monitor.
        aborted = self._monitor(experiment, controller, duration_seconds, report)

        # 4. Roll back if the monitor did not already.
        if not aborted:
            experiment.rollback()
            report.notes.append("fault duration elapsed, rolled back")

        # 5. Verify recovery.
        recovery = self._verifier.measure(self._probe)
        recovery_verdict = self._verifier.verify(recovery)
        report.recovery = recovery
        report.recovery_verdict = recovery_verdict

        # 6. Final outcome.
        if aborted:
            report.outcome = Outcome.ABORTED
        elif recovery_verdict == Verdict.HOLDS:
            report.outcome = Outcome.SUCCEEDED
        else:
            report.outcome = Outcome.DID_NOT_RECOVER
            report.notes.append(
                f"did not return to steady state: recovery {recovery_verdict.value} "
                f"(mean {recovery.mean:.4g}, CI [{recovery.ci_lower:.4g}, {recovery.ci_upper:.4g}])"
            )
        return report

    def _monitor(
        self,
        experiment: Experiment,
        controller: SafetyController,
        duration_seconds: float,
        report: ExperimentReport,
    ) -> bool:
        deadline = self._now() + duration_seconds
        while self._now() < deadline:
            self._sleep(self._poll_interval)
            report.polls += 1
            decision: AbortDecision = controller.evaluate()
            if decision.abort:
                experiment.rollback()
                report.abort_reason = decision.reason
                report.notes.append(f"ABORT after {report.polls} polls: {decision.reason}")
                return True
        return False
