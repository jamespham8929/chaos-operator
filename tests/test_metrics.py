"""Tests for the Prometheus metrics the operator exports."""

from unittest.mock import patch

from prometheus_client import REGISTRY

from chaos_operator import metrics


def value(metric, **labels):
    return REGISTRY.get_sample_value(metric, labels) or 0.0


class TestRecordRun:
    def test_counts_a_run_and_the_pods_it_touched(self):
        before_runs = value("chaos_experiment_runs_total",
                            type="pod-failure", outcome="completed", trigger="schedule")
        before_pods = value("chaos_pods_affected_total", type="pod-failure")

        metrics.record_run(
            "pod-failure", {"status": "completed", "pods_deleted": 3},
            duration_seconds=4.2, trigger="schedule",
            namespace="payments", name="nightly", finished_at=1_800_000_000,
        )

        assert value("chaos_experiment_runs_total",
                     type="pod-failure", outcome="completed", trigger="schedule") == before_runs + 1
        assert value("chaos_pods_affected_total", type="pod-failure") == before_pods + 3
        assert value("chaos_experiment_last_run_timestamp_seconds",
                     namespace="payments", name="nightly") == 1_800_000_000

    def test_an_aborted_safe_run_counts_as_an_abort_with_verdicts(self):
        before_aborts = value("chaos_experiment_aborts_total", type="network-latency")
        before_baseline = value("chaos_steady_state_verdicts_total",
                                phase="baseline", verdict="holds")

        metrics.record_run(
            "network-latency",
            {"status": "aborted", "baselineVerdict": "holds", "recoveryVerdict": None},
            duration_seconds=31.0, trigger="create",
            namespace="payments", name="latency", finished_at=1_800_000_100,
        )

        assert value("chaos_experiment_aborts_total", type="network-latency") == before_aborts + 1
        assert value("chaos_steady_state_verdicts_total",
                     phase="baseline", verdict="holds") == before_baseline + 1

    def test_pods_affected_reads_either_key(self):
        assert metrics.pods_affected({"pods_deleted": 2}) == 2
        assert metrics.pods_affected({"pods_affected": 5}) == 5
        assert metrics.pods_affected({"status": "no_targets"}) == 0


class TestServe:
    def test_port_zero_turns_the_endpoint_off(self):
        with patch.object(metrics, "start_http_server") as start:
            assert metrics.serve(0) is None
        start.assert_not_called()

    def test_reads_the_port_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("CHAOS_METRICS_PORT", "9123")
        with patch.object(metrics, "start_http_server") as start:
            assert metrics.serve() == 9123
        start.assert_called_once_with(9123)
