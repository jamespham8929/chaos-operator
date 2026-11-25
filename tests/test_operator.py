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

    @pytest.mark.parametrize(
        "experiment_type,class_name",
        [("network-latency", "NetworkLatencyExperiment"), ("cpu-stress", "CPUStressExperiment")],
    )
    def test_exec_faults_get_the_container(self, experiment_type, class_name):
        core_v1 = core_returning([make_pod()])
        with patch.object(operator, class_name) as exp_cls:
            exp_cls.return_value.run.return_value = {"status": "completed"}
            operator._dispatch_experiment(
                core_v1, experiment_type, "default", {"app": "web"}, {"container": "chaos-agent"}
            )
        assert exp_cls.call_args.kwargs["container"] == "chaos-agent"

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


class TestHandleExperimentLifecycle:
    def test_scheduled_experiment_does_not_run_at_creation(self):
        spec = {"type": "pod-failure", "schedule": "0 3 * * *"}
        with patch.object(operator, "_dispatch_experiment") as dispatch:
            result = operator.handle_experiment(
                spec=spec, name="nightly", namespace="payments", logger=MagicMock()
            )
        dispatch.assert_not_called()
        assert result["status"] == "scheduled"
        assert result["nextRunTime"].endswith("T03:00:00Z")

    def test_invalid_schedule_is_a_permanent_error(self):
        spec = {"type": "pod-failure", "schedule": "61 * * * *"}
        with pytest.raises(kopf.PermanentError):
            operator.handle_experiment(
                spec=spec, name="bad", namespace="payments", logger=MagicMock()
            )

    def test_resume_does_not_rerun_a_finished_one_shot(self):
        # kopf calls resume handlers for every object on each operator start.
        spec = {"type": "pod-failure", "selector": {"matchLabels": {"app": "web"}}}
        status = {operator.RESULT_KEY: {"status": "completed"}}
        with patch.object(operator, "_load_k8s_client"), \
             patch.object(operator, "_dispatch_experiment") as dispatch:
            result = operator.handle_experiment(
                spec=spec, name="exp", namespace="payments", logger=MagicMock(),
                status=status,
            )
        dispatch.assert_not_called()
        assert result is None

    def test_resume_runs_a_one_shot_that_never_finished(self):
        spec = {"type": "pod-failure", "selector": {"matchLabels": {"app": "web"}}}
        with patch.object(operator, "_load_k8s_client"), \
             patch.object(
                 operator, "_dispatch_experiment", return_value={"status": "completed"}
             ) as dispatch:
            operator.handle_experiment(
                spec=spec, name="exp", namespace="payments", logger=MagicMock(), status={},
            )
        dispatch.assert_called_once()


class TestTick:
    CREATED = "2026-11-22T12:00:00Z"

    def _tick(self, spec, status, now, dispatch_result=None):
        patch_obj = SimpleNamespace(status={})
        with patch.object(operator, "_load_k8s_client"), \
             patch.object(
                 operator, "_dispatch_experiment",
                 return_value=dispatch_result or {"status": "completed", "pods_deleted": 2},
             ) as dispatch:
            operator.tick(
                spec, "nightly", "payments", {"creationTimestamp": self.CREATED},
                status, patch_obj, MagicMock(), now=now,
            )
        return patch_obj.status.get("schedule"), dispatch

    @staticmethod
    def _at(hour, minute=0, day=23):
        from datetime import datetime, timezone
        return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)

    def test_runs_when_due_and_records_the_slot(self):
        spec = {"type": "pod-failure", "schedule": "0 3 * * *"}
        written, dispatch = self._tick(spec, {}, self._at(3, 0))

        dispatch.assert_called_once()
        assert written["lastScheduleTime"] == "2026-11-23T03:00:00Z"
        assert written["handledThrough"] == "2026-11-23T03:00:00Z"
        assert written["lastRunTime"] == "2026-11-23T03:00:00Z"
        assert written["lastResult"] == "completed"
        assert written["nextRunTime"] == "2026-11-24T03:00:00Z"

    def test_waits_when_not_due(self):
        spec = {"type": "pod-failure", "schedule": "0 3 * * *"}
        status = {"schedule": {"handledThrough": "2026-11-23T03:00:00Z"}}
        written, dispatch = self._tick(spec, status, self._at(12))

        dispatch.assert_not_called()
        assert written["nextRunTime"] == "2026-11-24T03:00:00Z"
        assert "lastRunTime" not in written

    def test_skips_a_late_slot_and_counts_it(self):
        spec = {"type": "pod-failure", "schedule": "0 3 * * *", "startingDeadlineSeconds": 300}
        status = {"schedule": {"missedRuns": 2}}
        written, dispatch = self._tick(spec, status, self._at(9))

        dispatch.assert_not_called()
        assert written["missedRuns"] == 3
        assert written["handledThrough"] == "2026-11-23T09:00:00Z"

    def test_suspended_experiment_does_nothing(self):
        spec = {"type": "pod-failure", "schedule": "* * * * *", "suspend": True}
        written, dispatch = self._tick(spec, {}, self._at(3))
        dispatch.assert_not_called()
        assert written is None

    def test_protected_namespace_never_runs(self):
        spec = {"type": "pod-failure", "schedule": "* * * * *"}
        patch_obj = SimpleNamespace(status={})
        with patch.object(operator, "_dispatch_experiment") as dispatch:
            operator.tick(
                spec, "x", "kube-system", {"creationTimestamp": self.CREATED},
                {}, patch_obj, MagicMock(), now=self._at(3),
            )
        dispatch.assert_not_called()

    def test_invalid_schedule_is_logged_not_raised(self):
        spec = {"type": "pod-failure", "schedule": "nope"}
        written, dispatch = self._tick(spec, {}, self._at(3))
        dispatch.assert_not_called()
        assert written is None


class TestPodWithoutAnnotations:
    def test_is_eligible(self):
        pod = make_pod("bare")
        pod.metadata.annotations = None
        eligible = operator._list_eligible_pods(core_returning([pod]), "default", {})
        assert [p.metadata.name for p in eligible] == ["bare"]


class TestRunHistory:
    """Each run leaves a record in status.runs for incident tools to read."""

    SPEC = {"type": "pod-failure", "selector": {"matchLabels": {"app": "checkout"}}}

    def _run_once(self, status, trigger_create=True):
        from datetime import datetime, timezone
        patch_obj = SimpleNamespace(status={})
        clock = iter([
            datetime(2026, 11, 24, 3, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 11, 24, 3, 2, 0, tzinfo=timezone.utc),
        ])
        with patch.object(operator, "_load_k8s_client"), \
             patch.object(operator, "_utcnow", side_effect=lambda: next(clock)), \
             patch.object(
                 operator, "_dispatch_experiment",
                 return_value={"status": "completed", "pods_deleted": 1},
             ):
            operator.handle_experiment(
                spec=self.SPEC, name="exp", namespace="payments", logger=MagicMock(),
                status=status, patch=patch_obj,
            )
        return patch_obj.status.get("runs")

    def test_a_one_shot_run_is_recorded(self):
        runs = self._run_once(status={})
        assert len(runs) == 1
        assert runs[0]["id"] == "payments/exp/20261124T030001Z"
        assert runs[0]["service"] == "checkout"
        assert runs[0]["trigger"] == "create"
        assert runs[0]["startedAt"] == "2026-11-24T03:00:01Z"
        assert runs[0]["finishedAt"] == "2026-11-24T03:02:00Z"
        assert runs[0]["podsAffected"] == 1

    def test_earlier_runs_are_kept(self):
        runs = self._run_once(status={"runs": [{"id": "older"}]})
        assert [r["id"] for r in runs] == ["older", "payments/exp/20261124T030001Z"]

    def test_a_scheduled_run_is_recorded(self):
        spec = {**self.SPEC, "schedule": "0 3 * * *"}
        patch_obj = SimpleNamespace(status={})
        from datetime import datetime, timezone
        now = datetime(2026, 11, 23, 3, 0, tzinfo=timezone.utc)
        with patch.object(operator, "_load_k8s_client"), \
             patch.object(
                 operator, "_dispatch_experiment", return_value={"status": "succeeded"},
             ):
            operator.tick(
                spec, "nightly", "payments", {"creationTimestamp": "2026-11-22T12:00:00Z"},
                {}, patch_obj, MagicMock(), now=now,
            )
        runs = patch_obj.status["runs"]
        assert len(runs) == 1
        assert runs[0]["trigger"] == "schedule"
        assert runs[0]["outcome"] == "succeeded"
        assert patch_obj.status["schedule"]["lastResult"] == "succeeded"

    def test_a_skipped_protected_namespace_writes_nothing(self):
        patch_obj = SimpleNamespace(status={})
        operator.handle_experiment(
            spec=self.SPEC, name="exp", namespace="kube-system", logger=MagicMock(),
            patch=patch_obj,
        )
        assert "runs" not in patch_obj.status
