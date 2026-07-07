"""Tests for the kopf handler wiring in operator.py.

The experiment classes and the safe-run path have their own tests. These cover
the operator's own logic: refusing protected namespaces, filtering pods down to
eligible targets, and routing a spec to the right experiment.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import kopf
import pytest

from chaos_operator import operator


def make_pod(name="pod-0", namespace="default", phase="Running", annotations=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, namespace=namespace, annotations=annotations or {}
        ),
        status=SimpleNamespace(phase=phase),
    )


def core_returning(pods):
    core_v1 = MagicMock()
    core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=pods)
    return core_v1


class TestListEligiblePods:
    def test_keeps_only_running_unexcluded_pods(self):
        pods = [
            make_pod("running"),
            make_pod("pending", phase="Pending"),
            make_pod("excluded", annotations={operator.EXCLUDE_ANNOTATION: "true"}),
        ]
        core_v1 = core_returning(pods)

        eligible = operator._list_eligible_pods(core_v1, "default", {"app": "web"})

        assert [p.metadata.name for p in eligible] == ["running"]

    def test_builds_label_selector_from_match_labels(self):
        core_v1 = core_returning([])

        operator._list_eligible_pods(core_v1, "default", {"app": "web", "tier": "api"})

        _, kwargs = core_v1.list_namespaced_pod.call_args
        assert kwargs["label_selector"] == "app=web,tier=api"


class TestDispatch:
    def test_no_eligible_pods_returns_no_targets(self):
        core_v1 = core_returning([])

        result = operator._dispatch_experiment(
            core_v1, "pod-failure", "default", {"app": "web"}, {}
        )

        assert result == {"status": "no_targets", "pods_affected": 0}

    def test_safe_config_routes_to_safe_experiment(self):
        core_v1 = core_returning([make_pod()])
        spec = {"steadyStateHypothesis": {}, "safety": {}, "type": "network-latency"}

        with patch.object(
            operator, "run_safe_experiment", return_value={"status": "succeeded"}
        ) as safe:
            result = operator._dispatch_experiment(
                core_v1, "network-latency", "default", {"app": "web"}, spec
            )

        safe.assert_called_once()
        assert result == {"status": "succeeded"}

    @pytest.mark.parametrize(
        "experiment_type,class_name",
        [
            ("pod-failure", "PodFailureExperiment"),
            ("network-latency", "NetworkLatencyExperiment"),
            ("cpu-stress", "CPUStressExperiment"),
        ],
    )
    def test_routes_each_type_to_its_experiment(self, experiment_type, class_name):
        core_v1 = core_returning([make_pod()])

        with patch.object(operator, class_name) as exp_cls:
            exp_cls.return_value.run.return_value = {"status": "completed"}
            result = operator._dispatch_experiment(
                core_v1, experiment_type, "default", {"app": "web"}, {}
            )

        exp_cls.return_value.run.assert_called_once()
        assert result == {"status": "completed"}

    def test_unknown_type_raises_permanent_error(self):
        core_v1 = core_returning([make_pod()])

        with pytest.raises(kopf.PermanentError):
            operator._dispatch_experiment(
                core_v1, "black-hole", "default", {"app": "web"}, {}
            )


class TestHandleExperiment:
    def test_refuses_protected_namespace(self):
        result = operator.handle_experiment(
            spec={"type": "pod-failure"},
            name="exp",
            namespace="kube-system",
            logger=MagicMock(),
        )

        assert result["status"] == "skipped"
        assert "protected" in result["reason"]

    def test_dispatches_for_a_normal_namespace(self):
        spec = {"type": "pod-failure", "selector": {"matchLabels": {"app": "web"}}}

        with patch.object(operator, "_load_k8s_client") as load, \
             patch.object(
                 operator, "_dispatch_experiment", return_value={"status": "completed"}
             ) as dispatch:
            result = operator.handle_experiment(
                spec=spec, name="exp", namespace="payments", logger=MagicMock()
            )

        load.assert_called_once()
        dispatch.assert_called_once()
        assert result == {"status": "completed"}
