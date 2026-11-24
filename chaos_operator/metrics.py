"""Prometheus metrics for experiment runs.

A chaos tool that cannot be watched from the stack it is testing asks people to
trust it. These metrics let the same Prometheus that guards an experiment also
show what the operator did: how many runs, how they ended, how many pods they
touched, and whether any schedule is falling behind.

Label values are bounded. type and outcome come from fixed enums, and
namespace and name come from ChaosExperiment objects, which people create by
hand and in small numbers.
"""

from __future__ import annotations

import os

from prometheus_client import Counter, Gauge, Histogram, start_http_server

RUNS = Counter(
    "chaos_experiment_runs_total",
    "Experiment runs, by type, how they ended, and what started them.",
    ["type", "outcome", "trigger"],
)
ABORTS = Counter(
    "chaos_experiment_aborts_total",
    "Runs the safety controller stopped before they finished.",
    ["type"],
)
DURATION = Histogram(
    "chaos_experiment_duration_seconds",
    "Wall-clock time of a run, from target selection to the last check.",
    ["type"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 1800),
)
PODS_AFFECTED = Counter(
    "chaos_pods_affected_total",
    "Pods a run deleted, delayed, or stressed.",
    ["type"],
)
VERDICTS = Counter(
    "chaos_steady_state_verdicts_total",
    "Steady-state checks, before injection (baseline) and after (recovery).",
    ["phase", "verdict"],
)
SCHEDULE_MISSED = Counter(
    "chaos_schedule_missed_runs_total",
    "Fire times passed over: the operator was down, or the slot was past its deadline.",
    ["namespace", "name"],
)
LAST_RUN = Gauge(
    "chaos_experiment_last_run_timestamp_seconds",
    "Unix time an experiment last ran.",
    ["namespace", "name"],
)
NEXT_RUN = Gauge(
    "chaos_experiment_next_run_timestamp_seconds",
    "Unix time a scheduled experiment is next due.",
    ["namespace", "name"],
)

# Result keys the experiment classes use for the number of pods they touched.
_POD_COUNT_KEYS = ("pods_deleted", "pods_affected")


def pods_affected(result: dict) -> int:
    for key in _POD_COUNT_KEYS:
        if isinstance(result.get(key), int):
            return result[key]
    return 0


def record_run(
    experiment_type: str,
    result: dict,
    duration_seconds: float,
    trigger: str,
    namespace: str,
    name: str,
    finished_at: float,
) -> None:
    outcome = str(result.get("status", "unknown"))
    RUNS.labels(type=experiment_type, outcome=outcome, trigger=trigger).inc()
    DURATION.labels(type=experiment_type).observe(duration_seconds)
    LAST_RUN.labels(namespace=namespace, name=name).set(finished_at)

    count = pods_affected(result)
    if count:
        PODS_AFFECTED.labels(type=experiment_type).inc(count)
    if outcome == "aborted":
        ABORTS.labels(type=experiment_type).inc()
    for phase in ("baseline", "recovery"):
        verdict = result.get(f"{phase}Verdict")
        if verdict:
            VERDICTS.labels(phase=phase, verdict=verdict).inc()


def serve(port: int | None = None) -> int | None:
    """Expose /metrics. A port of 0 turns the endpoint off."""
    if port is None:
        port = int(os.getenv("CHAOS_METRICS_PORT", "8000"))
    if port == 0:
        return None
    start_http_server(port)
    return port
