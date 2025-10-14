"""Adapters that present the real fault experiments as runner Experiments.

The runner needs an inject/rollback contract. The underlying experiment classes
already know how to talk to Kubernetes, so these adapters reuse them and expose
the lifecycle the safety controller depends on.

Reversibility differs by fault type and the adapters are honest about it:

  NetworkLatencyAction  Fully reversible. rollback removes the tc qdisc rule.
  PodFailureAction      Not reversible. A deleted pod cannot be un-deleted.
                        rollback is a no-op, and the safety value here comes from
                        the unhealthy-baseline refusal and the recovery check,
                        not from undoing the deletion.
"""

from __future__ import annotations

import logging

from .experiments.network_chaos import NetworkLatencyExperiment
from .experiments.pod_failure import PodFailureExperiment

logger = logging.getLogger(__name__)


class NetworkLatencyAction:
    def __init__(self, core_v1, pods: list, latency_ms: int, jitter_ms: int):
        self.name = "network-latency"
        self._exp = NetworkLatencyExperiment(core_v1)
        self._pods = pods
        self._latency_ms = latency_ms
        self._jitter_ms = jitter_ms
        self._injected_refs: list[str] = []

    def inject(self) -> None:
        for pod in self._pods:
            ns, name = pod.metadata.namespace, pod.metadata.name
            self._exp._inject_latency(ns, name, self._latency_ms, self._jitter_ms)
            self._injected_refs.append(f"{ns}/{name}")

    def rollback(self) -> None:
        if not self._injected_refs:
            return
        self._exp._cleanup(self._injected_refs)
        self._injected_refs = []

    def blast_radius(self) -> str:
        return (f"{len(self._pods)} pods, +{self._latency_ms}ms"
                f" (+/-{self._jitter_ms}ms) egress latency")


class PodFailureAction:
    def __init__(self, core_v1, pods: list, percentage: int, grace_period_seconds: int = 0):
        self.name = "pod-failure"
        self._exp = PodFailureExperiment(core_v1)
        self._pods = pods
        self._percentage = percentage
        self._grace = grace_period_seconds
        self._done = False

    def inject(self) -> None:
        self._exp.run(self._pods, {
            "percentage": self._percentage,
            "gracePeriodSeconds": self._grace,
        })
        self._done = True

    def rollback(self) -> None:
        # Pod deletion is not reversible. Nothing to undo. The safety controller
        # still guards the blast radius by refusing to start on an unhealthy
        # baseline and by failing the run if the system does not recover.
        if self._done:
            logger.info("pod-failure rollback is a no-op (deletion is irreversible)")

    def blast_radius(self) -> str:
        return f"{self._percentage}% of {len(self._pods)} matching pods deleted"
