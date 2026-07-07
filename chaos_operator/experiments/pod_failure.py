"""Pod failure experiment: deletes a random percentage of target pods."""

from __future__ import annotations

import logging
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import kubernetes

logger = logging.getLogger(__name__)


class PodFailureExperiment:
    def __init__(self, core_v1: kubernetes.client.CoreV1Api):
        self._core_v1 = core_v1

    def run(self, pods: list, config: dict) -> dict:
        percentage = config.get("percentage", 30)
        grace_period = config.get("gracePeriodSeconds", 0)

        count = max(1, math.ceil(len(pods) * percentage / 100))
        targets = random.sample(pods, min(count, len(pods)))

        deleted = []
        failed = []

        for pod in targets:
            name = pod.metadata.name
            namespace = pod.metadata.namespace
            try:
                self._core_v1.delete_namespaced_pod(
                    name=name,
                    namespace=namespace,
                    grace_period_seconds=grace_period,
                )
                logger.info("Deleted pod %s/%s", namespace, name)
                deleted.append(f"{namespace}/{name}")
            except Exception as e:
                logger.error("Failed to delete pod %s/%s: %s", namespace, name, e)
                failed.append(f"{namespace}/{name}")

        return {
            "status": "completed",
            "experiment": "pod-failure",
            "pods_targeted": len(targets),
            "pods_deleted": len(deleted),
            "pods_failed": len(failed),
            "deleted": deleted,
        }
