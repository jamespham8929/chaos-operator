"""Builds a safe chaos run from a ChaosExperiment spec.

When a ChaosExperiment declares a steadyStateHypothesis and safety guards, the
operator runs it through the ChaosRunner: pre-check, inject, monitor with abort,
roll back, verify recovery. When it does not, the operator falls back to the
legacy fire-and-forget dispatch. This module is the translation layer from CRD
spec to runner objects.
"""

from __future__ import annotations

import os

from .actions import NetworkLatencyAction, PodFailureAction
from .probes import PrometheusProbe
from .runner import ChaosRunner, ExperimentReport
from .safety import BlastRadiusGuard, Comparison, SafetyController
from .steady_state import SteadyStateHypothesis

DEFAULT_PROM_URL = os.getenv("CHAOS_PROMETHEUS_URL", "http://prometheus-operated.monitoring:9090")


def has_safe_config(spec: dict) -> bool:
    return "steadyStateHypothesis" in spec and "safety" in spec


def build_hypothesis(spec: dict) -> tuple[SteadyStateHypothesis, PrometheusProbe]:
    h = spec["steadyStateHypothesis"]
    prom_url = spec.get("prometheus", {}).get("url", DEFAULT_PROM_URL)
    probe = PrometheusProbe(name=h["metric"], query=h["query"], base_url=prom_url)
    hypothesis = SteadyStateHypothesis(
        metric=h["metric"],
        lower=h.get("lower"),
        upper=h.get("upper"),
        sample_size=h.get("sampleSize", 30),
        confidence=h.get("confidence", 0.95),
    )
    return hypothesis, probe


def build_controller(spec: dict) -> SafetyController:
    prom_url = spec.get("prometheus", {}).get("url", DEFAULT_PROM_URL)
    guards = []
    for g in spec["safety"]["guards"]:
        probe = PrometheusProbe(name=g["name"], query=g["query"], base_url=prom_url)
        guards.append(BlastRadiusGuard(
            name=g["name"],
            probe=probe,
            threshold=g["threshold"],
            comparison=Comparison(g.get("comparison", "gt")),
            consecutive_breaches_to_abort=g.get("consecutiveBreachesToAbort", 3),
        ))
    return SafetyController(guards)


def build_action(core_v1, experiment_type: str, pods: list, spec: dict):
    if experiment_type == "network-latency":
        cfg = spec.get("networkLatency", {})
        return NetworkLatencyAction(
            core_v1, pods,
            latency_ms=cfg.get("latencyMs", 100),
            jitter_ms=cfg.get("jitterMs", 10),
        )
    if experiment_type == "pod-failure":
        cfg = spec.get("podFailure", {})
        return PodFailureAction(
            core_v1, pods,
            percentage=cfg.get("percentage", 30),
            grace_period_seconds=cfg.get("gracePeriodSeconds", 0),
        )
    raise ValueError(f"experiment type {experiment_type!r} does not support safe runs yet")


SAFE_RUN_TYPES = ("network-latency", "pod-failure")


def run_safe_experiment(core_v1, experiment_type: str, pods: list, spec: dict) -> dict:
    if experiment_type not in SAFE_RUN_TYPES:
        # cpu-stress and future types still run on the legacy path. Returning a
        # clear status beats raising, which would crash the operator reconcile.
        return {
            "status": "unsupported_for_safe_run",
            "experiment": experiment_type,
            "reason": (
                f"{experiment_type} has no reversible action yet; remove the "
                f"steadyStateHypothesis/safety block to run it on the legacy path"
            ),
        }

    hypothesis, probe = build_hypothesis(spec)
    controller = build_controller(spec)
    action = build_action(core_v1, experiment_type, pods, spec)

    duration = spec.get("safety", {}).get("maxDurationSeconds", 300)
    poll = spec.get("safety", {}).get("pollIntervalSeconds", 5)

    runner = ChaosRunner(hypothesis, probe, poll_interval_seconds=poll)
    report = runner.run(action, controller, duration_seconds=duration)
    return _report_to_status(report)


def _report_to_status(report: ExperimentReport) -> dict:
    return {
        "status": report.outcome.value,
        "experiment": report.experiment,
        "blastRadius": report.blast_radius,
        "abortReason": report.abort_reason,
        "polls": report.polls,
        "baselineVerdict": report.baseline_verdict.value if report.baseline_verdict else None,
        "recoveryVerdict": report.recovery_verdict.value if report.recovery_verdict else None,
        "notes": report.notes,
    }
