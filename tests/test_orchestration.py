"""Tests for the spec-to-runner orchestration layer."""

from chaos_operator.orchestration import has_safe_config, run_safe_experiment


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
