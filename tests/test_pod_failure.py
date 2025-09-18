"""Tests for the pod failure experiment."""

from unittest.mock import MagicMock, call
import pytest

from chaos_operator.experiments.pod_failure import PodFailureExperiment


def make_pod(name, namespace="default", phase="Running", exclude=False):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.annotations = {"chaos.jamespham.io/exclude": "true"} if exclude else {}
    pod.status.phase = phase
    return pod


class TestPodFailureExperiment:
    def test_deletes_percentage_of_pods(self):
        core_v1 = MagicMock()
        exp = PodFailureExperiment(core_v1)

        pods = [make_pod(f"pod-{i}") for i in range(10)]
        result = exp.run(pods, {"percentage": 30, "gracePeriodSeconds": 0})

        assert result["status"] == "completed"
        assert result["pods_deleted"] == 3
        assert result["pods_targeted"] == 3

    def test_always_deletes_at_least_one_pod(self):
        core_v1 = MagicMock()
        exp = PodFailureExperiment(core_v1)

        pods = [make_pod("pod-0")]
        result = exp.run(pods, {"percentage": 10})
        assert result["pods_deleted"] >= 1

    def test_handles_delete_failure_gracefully(self):
        core_v1 = MagicMock()
        core_v1.delete_namespaced_pod.side_effect = Exception("API error")
        exp = PodFailureExperiment(core_v1)

        pods = [make_pod("pod-fail")]
        result = exp.run(pods, {"percentage": 100})

        assert result["status"] == "completed"
        assert result["pods_failed"] == 1
        assert result["pods_deleted"] == 0

    def test_grace_period_passed_to_api(self):
        core_v1 = MagicMock()
        exp = PodFailureExperiment(core_v1)

        pods = [make_pod("pod-0")]
        exp.run(pods, {"percentage": 100, "gracePeriodSeconds": 5})

        core_v1.delete_namespaced_pod.assert_called_once_with(
            name="pod-0",
            namespace="default",
            grace_period_seconds=5,
        )

    def test_does_not_exceed_pod_count(self):
        core_v1 = MagicMock()
        exp = PodFailureExperiment(core_v1)

        pods = [make_pod(f"pod-{i}") for i in range(3)]
        result = exp.run(pods, {"percentage": 200})
        assert result["pods_targeted"] <= 3

    def test_empty_pod_list(self):
        core_v1 = MagicMock()
        exp = PodFailureExperiment(core_v1)

        result = exp.run([], {"percentage": 50})
        core_v1.delete_namespaced_pod.assert_not_called()
        assert result["pods_deleted"] == 0
