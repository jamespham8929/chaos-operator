"""Tests for the spec-to-runner orchestration layer."""

from unittest.mock import patch

import pytest

from chaos_operator import orchestration
from chaos_operator.actions import NetworkLatencyAction, PodFailureAction
from chaos_operator.orchestration import (
    build_action,
    build_controller,
    build_hypothesis,
    has_safe_config,
    run_safe_experiment,
)
from chaos_operator.probes import PrometheusProbe
from chaos_operator.runner import ExperimentReport, Outcome
from chaos_operator.safety import Comparison


class TestSafeConfigGating:
    def test_requires_both_hypothesis_and_safety(self):
        assert has_safe_config({"steadyStateHypothesis": {}, "safety": {}}) is True
        assert has_safe_config({"steadyStateHypothesis": {}}) is False
        assert has_safe_config({"safety": {}}) is False
        assert has_safe_config({"type": "pod-failure"}) is False


class TestUnsupportedType:
    def test_cpu_stress_safe_run_returns_clear_status_not_crash(self):
        # cpu-stress has no reversible action yet. The safe path must return a
        # status the operator can record, not raise and crash the reconcile.
        result = run_safe_experiment(
            core_v1=None,  # never touched, the type check short-circuits first
            experiment_type="cpu-stress",
            pods=[object()],
            spec={"steadyStateHypothesis": {}, "safety": {}},
        )
        assert result["status"] == "unsupported_for_safe_run"
        assert result["experiment"] == "cpu-stress"
        assert "legacy path" in result["reason"]


class TestBuildHypothesis:
    def test_maps_spec_fields_onto_hypothesis_and_probe(self):
        spec = {
            "steadyStateHypothesis": {
                "metric": "success_rate",
                "query": "sum(rate(ok[1m]))",
                "lower": 0.99,
                "upper": 1.0,
                "sampleSize": 40,
                "confidence": 0.9,
            },
            "prometheus": {"url": "http://prom:9090"},
        }

        hypothesis, probe = build_hypothesis(spec)

        assert hypothesis.metric == "success_rate"
        assert hypothesis.lower == 0.99
        assert hypothesis.upper == 1.0
        assert hypothesis.sample_size == 40
        assert hypothesis.confidence == 0.9
        assert isinstance(probe, PrometheusProbe)
        assert probe.name == "success_rate"

    def test_defaults_sample_size_and_confidence(self):
        spec = {"steadyStateHypothesis": {"metric": "m", "query": "q"}}

        hypothesis, _ = build_hypothesis(spec)

        assert hypothesis.sample_size == 30
        assert hypothesis.confidence == 0.95


class TestBuildController:
    def test_builds_guards_from_spec(self):
        spec = {
            "safety": {
                "guards": [
                    {
                        "name": "error-rate",
                        "query": "q",
                        "threshold": 0.05,
                        "comparison": "gt",
                        "consecutiveBreachesToAbort": 4,
                    }
                ]
            }
        }

        controller = build_controller(spec)

        (guard,) = controller._guards
        assert guard.name == "error-rate"
        assert guard.threshold == 0.05
        assert guard.comparison == Comparison.GREATER_THAN
        assert guard.consecutive_breaches_to_abort == 4

    def test_comparison_defaults_to_greater_than(self):
        spec = {"safety": {"guards": [{"name": "g", "query": "q", "threshold": 1.0}]}}

        controller = build_controller(spec)

        assert controller._guards[0].comparison == Comparison.GREATER_THAN


class TestBuildAction:
    def test_network_latency_action(self):
        spec = {"networkLatency": {"latencyMs": 200, "jitterMs": 50}}

        action = build_action(None, "network-latency", [object(), object()], spec)

        assert isinstance(action, NetworkLatencyAction)
        assert action.name == "network-latency"
        assert "200ms" in action.blast_radius()

    def test_pod_failure_action(self):
        spec = {"podFailure": {"percentage": 40}}

        action = build_action(None, "pod-failure", [object()], spec)

        assert isinstance(action, PodFailureAction)
        assert action.name == "pod-failure"
        assert "40%" in action.blast_radius()

    def test_unsupported_type_raises(self):
        with pytest.raises(ValueError):
            build_action(None, "cpu-stress", [object()], {})


class TestRunSafeExperimentMapping:
    def test_maps_runner_report_to_status_dict(self):
        spec = {
            "steadyStateHypothesis": {"metric": "m", "query": "q"},
            "safety": {"guards": [{"name": "g", "query": "q", "threshold": 1.0}]},
            "networkLatency": {"latencyMs": 100},
        }
        report = ExperimentReport(
            experiment="network-latency",
            outcome=Outcome.SUCCEEDED,
            blast_radius="2 pods",
            polls=4,
            notes=["done"],
        )

        with patch.object(orchestration, "ChaosRunner") as runner_cls:
            runner_cls.return_value.run.return_value = report
            result = run_safe_experiment(None, "network-latency", [object(), object()], spec)

        assert result["status"] == "succeeded"
        assert result["experiment"] == "network-latency"
        assert result["polls"] == 4
        assert result["baselineVerdict"] is None
        assert result["notes"] == ["done"]
