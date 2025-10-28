# 2. Abort experiments on blast-radius guards

Date: 2025-08-22

## Status

Accepted

## Context

A chaos experiment is a deliberate fault, so the tooling's most important job is
stopping when the fault does more damage than intended. A fixed-duration
experiment that only checks the damage afterward can burn a month of error budget
before anyone looks. The failure mode is not theoretical, it is the default for
any "inject and sleep" script.

## Decision

Run every experiment under a safety controller that polls one or more guard
metrics during the run. A guard has a metric, a threshold, and a direction
(greater-than for error rate or latency, less-than for availability or
throughput). When a guard trips, the runner rolls the fault back immediately.

Two design points matter:

1. Debounce. A guard must breach for N consecutive polls before it trips
   (default 3). A single noisy scrape will not abort a healthy experiment.
2. Pre-check. The runner measures steady state and refuses to start if the system
   is not already healthy. Injecting chaos into a degraded system is how a small
   incident becomes a large one.

Rollback semantics depend on the fault. Network latency is fully reversible, so
rollback removes the rule. Pod deletion is not, so rollback is a no-op and the
guard's value there is the pre-check and the recovery verification, not undo.

## Consequences

- An experiment that exceeds its blast radius stops in seconds, not at the end of
  its scheduled duration. The demo shows an abort on the third poll.
- The system can protect itself without a human watching the dashboard, which is
  the only way chaos experiments can run unattended or on a schedule.
- Guards are only as good as their metrics. A guard on a metric that lags reality
  will abort late. This is documented as an operational responsibility, the tool
  cannot guarantee a metric reflects user pain.
