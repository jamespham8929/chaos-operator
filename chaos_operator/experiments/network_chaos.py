"""Network latency injection using tc netem via kubectl exec."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import kubernetes

logger = logging.getLogger(__name__)


class NetworkLatencyExperiment:
    def __init__(self, core_v1: "kubernetes.client.CoreV1Api"):
        self._core_v1 = core_v1

    def run(self, pods: list, config: dict) -> dict:
        latency_ms = config.get("latencyMs", 100)
        jitter_ms = config.get("jitterMs", 10)
        duration_seconds = config.get("durationSeconds", 60)

        affected = []
        failed = []

        for pod in pods:
            name = pod.metadata.name
            namespace = pod.metadata.namespace
            try:
                self._inject_latency(namespace, name, latency_ms, jitter_ms)
                affected.append(f"{namespace}/{name}")
                logger.info(
                    "Injected %dms latency (+%dms jitter) on %s/%s",
                    latency_ms, jitter_ms, namespace, name
                )
            except Exception as e:
                logger.error("Latency injection failed on %s/%s: %s", namespace, name, e)
                failed.append(f"{namespace}/{name}")

        if affected:
            logger.info("Holding latency for %d seconds...", duration_seconds)
            time.sleep(duration_seconds)
            self._cleanup(affected)

        return {
            "status": "completed",
            "experiment": "network-latency",
            "latency_ms": latency_ms,
            "jitter_ms": jitter_ms,
            "duration_seconds": duration_seconds,
            "pods_affected": len(affected),
            "pods_failed": len(failed),
        }

    def _inject_latency(
        self, namespace: str, pod_name: str, latency_ms: int, jitter_ms: int
    ) -> None:
        # tc qdisc add dev eth0 root netem delay <N>ms <J>ms
        cmd = [
            "tc", "qdisc", "add", "dev", "eth0", "root", "netem",
            "delay", f"{latency_ms}ms", f"{jitter_ms}ms",
        ]
        self._exec(namespace, pod_name, cmd)

    def _cleanup(self, pod_refs: list[str]) -> None:
        for ref in pod_refs:
            namespace, pod_name = ref.split("/", 1)
            try:
                self._exec(namespace, pod_name, ["tc", "qdisc", "del", "dev", "eth0", "root"])
                logger.info("Removed tc rules from %s/%s", namespace, pod_name)
            except Exception as e:
                logger.warning("Cleanup failed on %s/%s: %s", namespace, pod_name, e)

    def _exec(self, namespace: str, pod_name: str, command: list[str]) -> str:
        from kubernetes import stream
        return stream.stream(
            self._core_v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
