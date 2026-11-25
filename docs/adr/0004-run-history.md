# 4. Keep a run history in status

Date: 2026-11-24

## Status

Accepted

## Context

A chaos run is a deliberate incident. When it pages someone, the first question
is whether the experiment did it. incident-cli ranks candidate causes for a
page, but it could not see chaos runs at all. The portfolio map listed this as
a loose end.

The operator already wrote some of this down. A one-shot experiment stores its
result under `status.handle_experiment`, and a scheduled one stores
`lastRunTime` and `lastResult`. Neither is enough for another tool. There is no
start time for a one-shot run, no history for a schedule, and no target
service.

We looked at three places to keep the record.

- Kubernetes Events. `kubectl describe` shows them, which is nice. But the API
  server deletes Events after one hour by default. incident-cli looks back two
  hours, so a run from 90 minutes ago would be gone.
- Prometheus. The operator already exports run counters and timestamps. But
  labels cannot hold a run ID or a selector without making the series count
  grow with every run.
- The experiment's own status. It lasts as long as the experiment does, and
  one list call returns every run in the cluster.

## Decision

Each run appends a record to `status.runs`, and the list keeps the newest 20
runs. A record has:

- `id` is `<namespace>/<name>/<start time>`, for example
  `payments/nightly/20261124T030005Z`. It is stable, so other tools can cite it.
  The incident gym uses it as the answer key.
- `service` comes from the new `spec.service` field. When that field is empty,
  the operator falls back to the `app.kubernetes.io/name` or `app` label in the
  selector.
- `startedAt` and `finishedAt` are in UTC, to the second.
- `outcome` is the run status, such as `succeeded`, `aborted` or `no_targets`.
- `injected` says whether the run touched a pod. `no_targets`, `skipped`,
  `refused` and `unsupported_for_safe_run` did not. A reader can skip those,
  because a fault that never happened cannot cause a page.
- `trigger`, `type`, `selector` and `podsAffected` add context.

## Consequences

- incident-cli and platform-mcp read chaos runs with one `kubectl get
  chaosexperiments -A -o json` call. They need no access to the operator.
- Deleting an experiment deletes its history. That is acceptable for incident
  work, which looks back hours. A long-term record belongs in the metrics.
- The 20 run limit keeps the object small. A schedule that runs every five
  minutes keeps less than two hours of history, which is about the length of
  one incident-cli window.
- Kubernetes Events are still worth adding later for `kubectl describe`. They
  would be a convenience on top of this record.
