# chaos-operator

A Kubernetes operator for chaos experiments that treats the two things most
homegrown chaos tooling skips as the whole point: **verifying the system returned
to steady state, and aborting the experiment when it does more damage than
intended.**

## The problem

Injecting a fault is the easy part. `kubectl delete pod` is a chaos experiment.
The hard parts are the ones that make it safe to run on something that matters:

1. **Did the system actually recover?** The common answer is to probe a health
   endpoint once after the experiment. That is a sample of size one. A flaky
   metric that happens to read green on that one probe tells you nothing.
2. **What stops the experiment when it goes wrong?** Most "inject and sleep"
   scripts run for a fixed duration and only inspect the damage afterward. By
   then the experiment may have burned a month of error budget. The blast radius
   was never bounded.

Mature platforms like Gremlin and LitmusChaos handle these. A lot of internal
tooling does not, because the fault injection is what gets built first and the
safety machinery is what gets deferred. This operator is built the other way
around: the safety machinery is the core, and the fault injection plugs into it.

## What it does

A `ChaosExperiment` declares a steady-state hypothesis and a set of safety
guards. The operator then runs this lifecycle:

```
measure steady state ─▶ healthy?  ──no──▶ REFUSE (do not inject into a sick system)
        │ yes
        ▼
    inject fault
        │
        ▼
  poll guards every Ns ──guard trips──▶ roll back ─▶ ABORTED
        │ duration elapses
        ▼
     roll back
        │
        ▼
 measure steady state ──not in band──▶ DID_NOT_RECOVER
        │ in band
        ▼
     SUCCEEDED
```

### Steady state is a statistical claim

Steady state is a band on a metric's mean, for example "success rate is at least
99%." The verifier samples the metric, builds a percentile bootstrap confidence
interval for the mean, and checks whether that interval sits inside the band. The
outcome is one of holds, violated, or inconclusive. A permutation test is also
available to check whether the post-experiment distribution is measurably
different from the pre-experiment baseline. See [ADR 0001](docs/adr/0001-steady-state-as-a-statistical-claim.md).

### Safety guards abort on blast radius

Each guard watches a metric with a threshold and a direction. Guards are polled
throughout the run. To avoid aborting on a single noisy scrape, a guard must
breach for N consecutive polls before it trips (default 3). When any guard trips,
the fault is rolled back immediately. See [ADR 0002](docs/adr/0002-abort-on-blast-radius.md).

## Try it without a cluster

The runner, safety controller, and verifier work against any metric probe, so the
whole thing runs locally against synthetic probes:

```bash
pip install -r requirements.txt
PYTHONPATH=. python examples/demo_safe_run.py
```

Output (abridged):

```
=== severe-outage (trips guard, aborts) ===
outcome:           aborted
baseline verdict:  holds
polls before stop: 3
abort reason:      availability: 0.8803 < 0.95 for 3 consecutive polls

=== leaves-damage (fails recovery check) ===
outcome:           did_not_recover
recovery verdict:  violated
```

## Running on a cluster

```bash
kubectl apply -f k8s/crds/chaosexperiment.yaml
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/operator-deployment.yaml
kubectl apply -f examples/safe-experiment.yaml
```

The example in [`examples/safe-experiment.yaml`](examples/safe-experiment.yaml)
injects 200ms of latency into `checkout-api`, holds the run to a 99% success-rate
hypothesis, and aborts if the error rate crosses 5% or p99 latency crosses 1.5s.

An experiment with no `steadyStateHypothesis` and `safety` block falls back to
the legacy fire-and-forget dispatch, so the safety machinery is opt-in per
experiment.

## Layout

```
chaos_operator/
  steady_state.py   bootstrap CI, permutation test, hypothesis verdicts
  safety.py         blast-radius guards with debounce, safety controller
  runner.py         the lifecycle orchestrator
  probes.py         Prometheus, sequence, and synthetic metric probes
  experiment.py     the inject/rollback protocol
  actions.py        adapters making the real experiments reversible
  orchestration.py  builds a run from a ChaosExperiment spec
  experiments/      the fault injectors (pod failure, network latency, cpu stress)
docs/adr/           why steady state is statistical, why we abort on blast radius
examples/           a runnable local demo and a cluster manifest
```

## Tests

```bash
pip install pytest
PYTHONPATH=. pytest tests/ -v
```

The runner tests in [`tests/test_runner.py`](tests/test_runner.py) cover the four
outcomes (succeeded, aborted, refused, did-not-recover) end to end with a fake
clock, so they run instantly and deterministically.

## Limitations

- Guards are only as timely as their metrics. A guard on a lagging metric aborts
  late. The tool cannot guarantee a metric reflects real user pain.
- Pod deletion is not reversible, so for that fault the safety value is the
  unhealthy-baseline refusal and the recovery check, not rollback.
- Scheduling (the `schedule` field) defines recurrence but cron execution is not
  wired into the operator loop yet. It is parsed and stored, not fired.
- Safe runs cover `network-latency` and `pod-failure`. `cpu-stress` has no
  reversible action yet, so with a safety block it reports
  `unsupported_for_safe_run` and should be run on the legacy path instead.

## License

MIT
