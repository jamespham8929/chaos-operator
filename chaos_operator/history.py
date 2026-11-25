"""Keep a short record of each run in the experiment's status.

Other tools need to know what chaos did and when. incident-cli reads these
records as candidate causes, so a page during a game day points at the
experiment first. The record lives in status instead of Kubernetes Events,
because Events expire after one hour by default. That is shorter than the two
hour window incident-cli looks back over. See docs/adr/0004-run-history.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .metrics import pods_affected

# Enough for a daily schedule to keep three weeks of runs, and small enough
# that the object stays far below the etcd size limit.
HISTORY_LIMIT = 20

# Outcomes where no fault reached any pod. Either the operator stopped first,
# or the injection failed on every pod. A run like this cannot be the cause of
# an incident, and readers can skip it.
NOT_INJECTED = frozenset(
    {"no_targets", "skipped", "refused", "unsupported_for_safe_run", "failed"}
)

# Labels that usually name the service a pod belongs to, most specific first.
SERVICE_LABELS = ("app.kubernetes.io/name", "app")


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def service_for(spec: dict) -> str:
    """The service an experiment targets.

    `spec.service` wins when it is set. Otherwise the usual app labels in the
    selector are a good guess. An empty string means the operator cannot tell.
    """
    if spec.get("service"):
        return spec["service"]
    labels = (spec.get("selector") or {}).get("matchLabels") or {}
    for key in SERVICE_LABELS:
        if labels.get(key):
            return labels[key]
    return ""


def run_record(
    spec: dict,
    name: str,
    namespace: str,
    trigger: str,
    started: datetime,
    finished: datetime,
    result: dict,
) -> dict:
    outcome = str(result.get("status", "unknown"))
    return {
        # Stable and readable. A name only runs once per second at most,
        # because schedules fire on whole minutes.
        "id": f"{namespace}/{name}/{started.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
        "trigger": trigger,
        "type": spec.get("type", ""),
        "service": service_for(spec),
        "selector": dict((spec.get("selector") or {}).get("matchLabels") or {}),
        "startedAt": iso(started),
        "finishedAt": iso(finished),
        "outcome": outcome,
        "injected": outcome not in NOT_INJECTED,
        "podsAffected": pods_affected(result),
    }


def append(history: list | None, record: dict, limit: int = HISTORY_LIMIT) -> list:
    """Add a record and keep only the newest `limit`, oldest first."""
    runs = list(history or [])
    runs.append(record)
    return runs[-limit:]
