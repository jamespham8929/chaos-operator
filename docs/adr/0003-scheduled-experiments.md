# 3. Run scheduled experiments once, and never late

Date: 2026-11-23

## Status

Accepted

## Context

The `schedule` field was parsed and stored, but nothing fired it. A recurring
experiment is the main way chaos becomes a habit instead of a one-off game day,
so the field needed a real loop behind it.

Running a cron schedule sounds simple. The hard cases come from the operator
itself going down:

- The operator restarts or is rescheduled. Several fire times may pass while it
  is gone. Should it run all of them when it comes back?
- A run was planned for 03:00, the quiet hour. The operator comes back at 09:00.
  Should it run now?

There was also a bug in the same area. The create handler was also registered
for `on.resume`, and kopf calls resume handlers for every object each time the
operator starts. The CRD had no status schema, so the API server pruned the
result the operator wrote. Nothing recorded that a run had happened. So every
restart ran every one-shot experiment in the cluster again.

## Decision

A kopf timer checks each scheduled experiment every 30 seconds. The decision
for each tick is a pure function, `scheduling.decide`, of the schedule, the
clock, and the last fire time the operator dealt with. It follows two rules.

1. Never backfill. If several fire times passed during an outage, run once for
   the latest one and count the rest in `status.schedule.missedRuns` and in the
   `chaos_schedule_missed_runs_total` metric. Running five pod-failure
   experiments back to back is a different and much larger experiment than
   the one someone scheduled.
2. Never run late. A fire time that is older than `startingDeadlineSeconds`
   (300 by default) is skipped. People schedule chaos for a quiet window on
   purpose. A run that starts hours late can land in peak traffic, which is the
   one thing the schedule was meant to avoid.

The operator stores `handledThrough` in status after each tick. It is the latest
fire time it ran or skipped. The next tick only looks at fire times after it, so
a slot is never counted or run twice.

A scheduled experiment does not run at creation. It waits for its first fire
time, like a Kubernetes CronJob. `suspend: true` stops the timer without
deleting the object.

For the resume bug, the CRD now has a status schema that keeps what the
operator writes, and the resume path skips a one-shot experiment that already
has a result.

Cron parsing is a small in-repo module rather than a dependency. It covers five
fields, ranges, steps, lists, names, and the `@daily` style macros, in UTC.
Day-of-month and day-of-week follow Vixie cron, so when both are set, either
one matching is enough.

## Consequences

- A run can start up to one tick (30 seconds) after its fire time.
- A schedule tighter than the tick, such as every minute with a 30 second
  deadline, can miss slots under load. The CRD sets a 30 second minimum on the
  deadline for this reason.
- Missed runs are visible. The `ChaosScheduleMissedRuns` alert in
  `k8s/monitoring.yaml` opens a ticket when a schedule skips runs, because a
  resilience test that silently stops running gives false confidence.
- Only one replica should run. Kopf's peering stops two operators from handling
  the same object at once, but the design assumes one active operator.
