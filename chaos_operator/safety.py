"""Safety controller: abort an experiment when it exceeds its blast radius.

A chaos experiment is a deliberate fault. The whole point is that it might hurt,
so the one thing the tooling must get right is stopping when it hurts too much.
Most homegrown chaos scripts run for a fixed duration and only check the damage
afterward. By then the experiment may have already burned a month of error
budget.

The safety controller polls one or more guard metrics during the experiment. Each
guard has a threshold and a direction. To avoid aborting on a single noisy scrape
it debounces: a guard must breach for N consecutive polls before it trips. When
any guard trips, the controller signals abort with the guard's name and readings,
and the runner rolls the fault back immediately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .probes import MetricProbe


class Comparison(str, Enum):
    GREATER_THAN = "gt"   # breach when value > threshold (error rate, latency)
    LESS_THAN = "lt"      # breach when value < threshold (availability, throughput)


@dataclass
class BlastRadiusGuard:
    name: str
    probe: MetricProbe
    threshold: float
    comparison: Comparison = Comparison.GREATER_THAN
    consecutive_breaches_to_abort: int = 3

    _streak: int = field(default=0, repr=False)

    def reset(self) -> None:
        self._streak = 0

    def observe(self) -> "GuardReading":
        value = self.probe.sample()
        breached = self._is_breach(value)
        self._streak = self._streak + 1 if breached else 0
        tripped = self._streak >= self.consecutive_breaches_to_abort
        return GuardReading(
            guard=self.name,
            value=value,
            threshold=self.threshold,
            comparison=self.comparison,
            breached=breached,
            streak=self._streak,
            tripped=tripped,
        )

    def _is_breach(self, value: float) -> bool:
        if self.comparison == Comparison.GREATER_THAN:
            return value > self.threshold
        return value < self.threshold


@dataclass(frozen=True)
class GuardReading:
    guard: str
    value: float
    threshold: float
    comparison: Comparison
    breached: bool
    streak: int
    tripped: bool

    def describe(self) -> str:
        op = ">" if self.comparison == Comparison.GREATER_THAN else "<"
        return (f"{self.guard}: {self.value:.4g} {op} {self.threshold:.4g} "
                f"for {self.streak} consecutive polls")


@dataclass(frozen=True)
class AbortDecision:
    abort: bool
    reason: str = ""
    readings: tuple[GuardReading, ...] = ()


class SafetyController:
    def __init__(self, guards: list[BlastRadiusGuard]):
        if not guards:
            raise ValueError("a safety controller needs at least one guard")
        self._guards = guards

    def reset(self) -> None:
        for g in self._guards:
            g.reset()

    def evaluate(self) -> AbortDecision:
        """Poll every guard once. Abort if any guard has tripped."""
        readings = tuple(g.observe() for g in self._guards)
        tripped = [r for r in readings if r.tripped]
        if tripped:
            reason = "; ".join(r.describe() for r in tripped)
            return AbortDecision(abort=True, reason=reason, readings=readings)
        return AbortDecision(abort=False, readings=readings)
