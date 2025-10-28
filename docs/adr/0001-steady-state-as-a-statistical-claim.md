# 1. Treat steady state as a statistical claim

Date: 2025-08-08

## Status

Accepted

## Context

Chaos engineering rests on the steady-state hypothesis: define what healthy looks
like, perturb the system, and verify it returns to healthy. Most homegrown chaos
tooling reduces "verify it returns to healthy" to a single check, one probe of a
health endpoint after the experiment. A single probe is a sample of size one. It
has no error bars. A flaky metric that reads fine on the one request you made
tells you nothing about whether the system actually recovered.

## Decision

Express steady state as a band on a metric's mean, and verify it by sampling.
Take `sampleSize` probes, build a percentile bootstrap confidence interval for
the mean, and compare the interval to the band:

- interval entirely inside the band -> holds
- interval entirely outside -> violated
- interval straddling an edge -> inconclusive (collect more data)

The bootstrap is used rather than a t-interval so there is no distributional
assumption and no scipy dependency. A permutation test is also provided for the
stronger question "is the post-experiment distribution different from the
pre-experiment baseline," which catches a system that recovered to a different
but still in-band level.

## Consequences

- "It recovered" now means "the measured interval sits in the healthy band,"
  which is a claim with a stated confidence level, not a coin flip on one probe.
- The inconclusive verdict is a first-class outcome. The system can ask for more
  samples instead of being forced into a wrong yes or no.
- There is a cost: sampling takes longer than a single probe. For verification
  after an experiment this is fine, the experiment already took minutes.
