"""Kopf-based operator main loop for ChaosExperiment resources.

A ChaosExperiment without a schedule runs once, when it is created. One with a
schedule is left alone at creation, and a timer runs it at each fire time. The
reasoning behind the timer's rules is in docs/adr/0003-scheduled-experiments.md.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import kopf
import kubernetes

from . import history, metrics
from .cron import CronSchedule
from .experiments.cpu_stress import CPUStressExperiment
from .experiments.network_chaos import NetworkLatencyExperiment
from .experiments.pod_failure import PodFailureExperiment
from .orchestration import has_safe_config, run_safe_experiment
from .scheduling import DEFAULT_STARTING_DEADLINE_SECONDS, decide

logger = logging.getLogger(__name__)

GROUP, VERSION, PLURAL = "chaos.jamespham.io", "v1alpha1", "chaosexperiments"
PROTECTED_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease", "chaos-system"}
EXCLUDE_ANNOTATION = "chaos.jamespham.io/exclude"

# How often the timer checks each scheduled experiment. Fire times are
# minute-aligned, so a 30s tick starts a run at most 30s after its slot.
SCHEDULE_TICK_SECONDS = 30

# Kopf stores a handler's return value in status under the handler's name.
RESULT_KEY = "handle_experiment"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # fromisoformat only accepts a trailing Z from Python 3.11.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_k8s_client() -> kubernetes.client.CoreV1Api:
    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    return kubernetes.client.CoreV1Api()


@kopf.on.startup()
def start_metrics_server(logger, **_):
    port = metrics.serve()
    if port:
        logger.info("Serving Prometheus metrics on :%d/metrics", port)


@kopf.on.create(GROUP, VERSION, PLURAL)
@kopf.on.resume(GROUP, VERSION, PLURAL)
def handle_experiment(spec, name, namespace, logger, status=None, **kwargs):
    if namespace in PROTECTED_NAMESPACES:
        logger.warning("Refusing to run experiment in protected namespace %s", namespace)
        return {"status": "skipped", "reason": f"namespace {namespace} is protected"}

    if spec.get("schedule"):
        return _register_schedule(spec, name, namespace, logger)

    # Resume handlers fire for every existing object each time the operator
    # starts. Without this check, a restart would run every one-shot
    # experiment in the cluster again.
    previous = (status or {}).get(RESULT_KEY) or {}
    if previous.get("status") not in (None, "scheduled"):
        logger.info(
            "%s/%s already ran (%s), not running it again",
            namespace, name, previous["status"],
        )
        return None

    return _run(
        spec, name, namespace, trigger="create", logger=logger,
        status=status, patch=kwargs.get("patch"),
    )


def _register_schedule(spec, name, namespace, logger) -> dict:
    try:
        schedule = CronSchedule.parse(spec["schedule"])
    except ValueError as err:
        raise kopf.PermanentError(f"invalid schedule: {err}") from err
    next_run = schedule.next_after(_utcnow())
    metrics.NEXT_RUN.labels(namespace=namespace, name=name).set(next_run.timestamp())
    logger.info("%s/%s scheduled %r, next run %s", namespace, name, schedule.expression, next_run)
    return {"status": "scheduled", "schedule": schedule.expression, "nextRunTime": _iso(next_run)}


@kopf.timer(
    GROUP, VERSION, PLURAL,
    interval=SCHEDULE_TICK_SECONDS,
    when=lambda spec, **_: bool(spec.get("schedule")),
)
def run_scheduled(spec, name, namespace, meta, status, patch, logger, **kwargs):
    return tick(spec, name, namespace, meta, status or {}, patch, logger, now=_utcnow())


def tick(spec, name, namespace, meta, status, patch, logger, now: datetime) -> None:
    """One timer pass for one scheduled experiment. `now` is passed in for tests."""
    if namespace in PROTECTED_NAMESPACES:
        return
    if spec.get("suspend"):
        logger.debug("%s/%s is suspended", namespace, name)
        return
    try:
        schedule = CronSchedule.parse(spec["schedule"])
    except ValueError as err:
        # The create handler already marked the object failed. Do not retry.
        logger.error("%s/%s has an invalid schedule: %s", namespace, name, err)
        return

    previous = dict(status.get("schedule") or {})
    decision = decide(
        schedule,
        now=now,
        handled_through=_parse_time(previous.get("handledThrough")),
        created=_parse_time(meta["creationTimestamp"]),
        starting_deadline_seconds=spec.get(
            "startingDeadlineSeconds", DEFAULT_STARTING_DEADLINE_SECONDS
        ),
    )

    updated = {**previous, "nextRunTime": _iso(decision.next_run)}
    metrics.NEXT_RUN.labels(namespace=namespace, name=name).set(decision.next_run.timestamp())
    updated["handledThrough"] = _iso(decision.handled_through)
    if decision.slot is not None:
        updated["lastScheduleTime"] = _iso(decision.slot)
    if decision.missed:
        updated["missedRuns"] = previous.get("missedRuns", 0) + decision.missed
        metrics.SCHEDULE_MISSED.labels(namespace=namespace, name=name).inc(decision.missed)
        logger.warning(
            "%s/%s missed %d run(s): %s", namespace, name, decision.missed, decision.reason
        )

    if decision.run:
        result = _run(
            spec, name, namespace, trigger="schedule", logger=logger,
            status=status, patch=patch,
        )
        updated["lastRunTime"] = _iso(now)
        updated["lastResult"] = result.get("status")

    patch.status["schedule"] = updated


def _run(spec, name, namespace, trigger: str, logger, status=None, patch=None) -> dict:
    experiment_type = spec.get("type")
    selector = spec.get("selector", {}).get("matchLabels", {})
    logger.info("Running %s experiment %s/%s (%s)", experiment_type, namespace, name, trigger)

    started_at = _utcnow()
    started = time.monotonic()
    core_v1 = _load_k8s_client()
    result = _dispatch_experiment(core_v1, experiment_type, namespace, selector, spec)
    finished_at = _utcnow()
    metrics.record_run(
        experiment_type,
        result,
        duration_seconds=time.monotonic() - started,
        trigger=trigger,
        namespace=namespace,
        name=name,
        finished_at=finished_at.timestamp(),
    )
    if patch is not None:
        record = history.run_record(
            spec, name, namespace, trigger, started_at, finished_at, result
        )
        patch.status["runs"] = history.append((status or {}).get("runs"), record)
    return result


def _dispatch_experiment(
    core_v1: kubernetes.client.CoreV1Api,
    experiment_type: str,
    namespace: str,
    selector: dict,
    spec: dict,
) -> dict:
    pods = _list_eligible_pods(core_v1, namespace, selector)

    if not pods:
        logger.warning("No eligible pods found for experiment in %s", namespace)
        return {"status": "no_targets", "pods_affected": 0}

    # Preferred path: a declared steady-state hypothesis and safety guards turn
    # this into a verified, abortable run instead of fire-and-forget.
    if has_safe_config(spec):
        logger.info("Running %s as a safe experiment (hypothesis + safety guards)", experiment_type)
        return run_safe_experiment(core_v1, experiment_type, pods, spec)

    if experiment_type == "pod-failure":
        cfg = spec.get("podFailure", {})
        exp = PodFailureExperiment(core_v1)
        return exp.run(pods, cfg)

    if experiment_type == "network-latency":
        cfg = spec.get("networkLatency", {})
        exp = NetworkLatencyExperiment(core_v1)
        return exp.run(pods, cfg)

    if experiment_type == "cpu-stress":
        cfg = spec.get("cpuStress", {})
        exp = CPUStressExperiment(core_v1)
        return exp.run(pods, cfg)

    raise kopf.PermanentError(f"Unknown experiment type: {experiment_type}")


def _list_eligible_pods(
    core_v1: kubernetes.client.CoreV1Api, namespace: str, selector: dict
) -> list:
    label_selector = ",".join(f"{k}={v}" for k, v in selector.items())
    pod_list = core_v1.list_namespaced_pod(namespace, label_selector=label_selector)

    return [
        pod for pod in pod_list.items
        if pod.status.phase == "Running"
        # A pod with no annotations at all has None here, not an empty dict.
        and (pod.metadata.annotations or {}).get(EXCLUDE_ANNOTATION) != "true"
    ]
