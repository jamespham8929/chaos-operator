"""Experiment protocol used by the runner.

An experiment injects a fault and can roll it back. Rollback matters: when the
safety controller trips mid-run, the runner calls rollback to stop the bleeding.
For inherently reversible faults (network latency via tc, traffic blackholes)
rollback removes the rule. For pod deletion, rollback means "stop deleting more
pods," so the experiment is written to delete incrementally rather than all at
once, and rollback halts the schedule.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Experiment(Protocol):
    name: str

    def inject(self) -> None:
        """Begin injecting the fault."""
        ...

    def rollback(self) -> None:
        """Undo or halt the fault. Must be idempotent and safe to call even if
        inject was never called or already rolled back."""
        ...

    def blast_radius(self) -> str:
        """Human-readable description of what this experiment can affect."""
        ...


class SyntheticExperiment:
    """An in-memory experiment used by tests and the simulation.

    Driving a NoisyProbe, it shifts the metric to a degraded level on inject and
    back to baseline on rollback, so the runner, safety controller, and
    steady-state verifier can be exercised end to end without a cluster.
    """

    def __init__(self, name: str, on_inject, on_rollback, blast: str = "synthetic target"):
        self.name = name
        self._on_inject = on_inject
        self._on_rollback = on_rollback
        self._blast = blast
        self.injected = False
        self.rolled_back = False

    def inject(self) -> None:
        self.injected = True
        self._on_inject()

    def rollback(self) -> None:
        # Idempotent: safe to call from both the abort path and the normal path.
        if self.rolled_back:
            return
        self.rolled_back = True
        self._on_rollback()

    def blast_radius(self) -> str:
        return self._blast
