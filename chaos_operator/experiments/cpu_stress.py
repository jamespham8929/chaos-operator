"""CPU stress experiment using stress-ng inside target containers."""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .podexec import exec_in_pod

if TYPE_CHECKING:
    import kubernetes

logger = logging.getLogger(__name__)


class CPUStressExperiment:
    def __init__(self, core_v1: kubernetes.client.CoreV1Api, container: str | None = None):
        self._core_v1 = core_v1
        # The container that has stress-ng. None lets the API pick.
        self._container = container
        self._lock = threading.Lock()
        self._exec_failed: list[str] = []

    def run(self, pods: list, config: dict) -> dict:
        workers = config.get("workers", 1)
        duration_seconds = config.get("durationSeconds", 60)

        affected = []
        failed = []
        threads = []

        for pod in pods:
            name = pod.metadata.name
            namespace = pod.metadata.namespace
            try:
                t = threading.Thread(
                    target=self._stress_pod,
                    args=(namespace, name, workers, duration_seconds),
                    daemon=True,
                )
                t.start()
                threads.append(t)
                affected.append(f"{namespace}/{name}")
                logger.info(
                    "Started CPU stress on %s/%s (%d workers, %ds)",
                    namespace, name, workers, duration_seconds,
                )
            except Exception as e:
                logger.error("Failed to start stress on %s/%s: %s", namespace, name, e)
                failed.append(f"{namespace}/{name}")

        for t in threads:
            t.join(timeout=duration_seconds + 30)

        # A thread that started but whose stress-ng failed did not stress
        # anything, so it moves from affected to failed.
        with self._lock:
            exec_failed = list(self._exec_failed)
        affected = [ref for ref in affected if ref not in exec_failed]
        failed.extend(exec_failed)

        return {
            "status": "completed" if affected else "failed",
            "experiment": "cpu-stress",
            "workers": workers,
            "duration_seconds": duration_seconds,
            "pods_affected": len(affected),
            "pods_failed": len(failed),
        }

    def _stress_pod(self, namespace: str, pod_name: str, workers: int, duration: int) -> None:
        try:
            cmd = ["stress-ng", "--cpu", str(workers), "--timeout", str(duration)]
            self._exec(namespace, pod_name, cmd, timeout_seconds=duration + 30)
        except Exception as e:
            logger.error("stress-ng exec failed on %s/%s: %s", namespace, pod_name, e)
            with self._lock:
                self._exec_failed.append(f"{namespace}/{pod_name}")

    def _exec(
        self, namespace: str, pod_name: str, command: list[str], timeout_seconds: int = 60
    ) -> str:
        return exec_in_pod(
            self._core_v1, namespace, pod_name, command, self._container, timeout_seconds
        )
