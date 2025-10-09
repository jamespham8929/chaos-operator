"""Metric probes.

A probe is anything that can be sampled for a single scalar value: a Prometheus
query, a synthetic generator for tests, or a fixed sequence for replaying a
recorded incident. The steady-state verifier and the safety controller both work
against this interface, so neither one cares where the numbers come from.
"""

from __future__ import annotations

import random
from typing import Protocol, runtime_checkable

import requests


@runtime_checkable
class MetricProbe(Protocol):
    name: str

    def sample(self) -> float:
        """Return a single current value for the metric."""
        ...


class PrometheusProbe:
    """Samples an instant Prometheus query that returns a single scalar."""

    def __init__(self, name: str, query: str, base_url: str, timeout: int = 10):
        self.name = name
        self._query = query
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()

    def sample(self) -> float:
        resp = self._session.get(
            f"{self._base_url}/api/v1/query",
            params={"query": self._query},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {}).get("result", [])
        if not result:
            raise ProbeError(f"query returned no data: {self._query}")
        return float(result[0]["value"][1])


class SequenceProbe:
    """Replays a fixed list of values, one per sample. Useful for replaying a
    recorded metric series in a test or simulation. Raises when exhausted so a
    test cannot silently read past the data it set up."""

    def __init__(self, name: str, values: list[float]):
        self.name = name
        self._values = list(values)
        self._idx = 0

    def sample(self) -> float:
        if self._idx >= len(self._values):
            raise ProbeError(f"sequence probe {self.name!r} exhausted")
        value = self._values[self._idx]
        self._idx += 1
        return value


class NoisyProbe:
    """Draws samples from a normal distribution. Models a metric sitting at a
    steady level with measurement noise. Seeded for reproducible tests."""

    def __init__(self, name: str, mean: float, stddev: float, seed: int | None = None):
        self.name = name
        self._mean = mean
        self._stddev = stddev
        self._rng = random.Random(seed)

    def set_level(self, mean: float) -> None:
        """Shift the underlying level, to simulate the metric degrading during
        a fault and recovering afterward."""
        self._mean = mean

    def sample(self) -> float:
        return self._rng.gauss(self._mean, self._stddev)


class ProbeError(RuntimeError):
    pass
