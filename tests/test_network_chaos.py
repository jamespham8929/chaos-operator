"""Tests for the network latency experiment."""

from unittest.mock import MagicMock, patch

from chaos_operator.experiments.network_chaos import NetworkLatencyExperiment


def make_pod(name, namespace="default"):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    return pod


class TestNetworkLatencyExperiment:
    def test_calls_exec_with_tc_command(self):
        core_v1 = MagicMock()
        exp = NetworkLatencyExperiment(core_v1)

        with patch.object(exp, "_exec"), \
             patch("time.sleep"):
            pods = [make_pod("pod-0")]
            result = exp.run(pods, {"latencyMs": 100, "jitterMs": 10, "durationSeconds": 1})

        assert result["status"] == "completed"
        assert result["pods_affected"] == 1

    def test_cleans_up_after_duration(self):
        core_v1 = MagicMock()
        exp = NetworkLatencyExperiment(core_v1)
        cleanup_calls = []

        def fake_exec(namespace, pod_name, cmd):
            cleanup_calls.append(cmd)

        with patch.object(exp, "_exec", side_effect=fake_exec), \
             patch("time.sleep"):
            pods = [make_pod("pod-0")]
            exp.run(pods, {"latencyMs": 50, "jitterMs": 5, "durationSeconds": 1})

        # First call is injection, second is cleanup
        assert any("del" in cmd for cmd in cleanup_calls[-1])

    def test_handles_injection_failure(self):
        core_v1 = MagicMock()
        exp = NetworkLatencyExperiment(core_v1)

        with patch.object(exp, "_exec", side_effect=Exception("exec failed")):
            pods = [make_pod("pod-fail")]
            result = exp.run(pods, {"latencyMs": 100, "durationSeconds": 1})

        assert result["pods_failed"] == 1
        assert result["pods_affected"] == 0

    def test_config_values_reflected_in_result(self):
        core_v1 = MagicMock()
        exp = NetworkLatencyExperiment(core_v1)

        with patch.object(exp, "_exec"), patch("time.sleep"):
            result = exp.run(
                [make_pod("pod-0")],
                {"latencyMs": 200, "jitterMs": 25, "durationSeconds": 120}
            )

        assert result["latency_ms"] == 200
        assert result["jitter_ms"] == 25
        assert result["duration_seconds"] == 120
