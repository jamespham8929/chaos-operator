# chaos-operator

A lightweight Kubernetes operator that runs configurable chaos experiments. Inject pod failures, network latency, or CPU stress into target workloads via a `ChaosExperiment` custom resource. Useful for validating service resilience and disaster recovery runbooks before incidents happen.

## Experiments

| Type | What it does |
|------|-------------|
| `pod-failure` | Deletes a random subset of pods matching a label selector |
| `network-latency` | Injects configurable egress latency using `tc netem` via a privileged sidecar |
| `cpu-stress` | Runs CPU-bound work inside target containers using `stress-ng` |

## Installation

```bash
# Install the CRD
kubectl apply -f k8s/crds/chaosexperiment.yaml

# Create the operator RBAC
kubectl apply -f k8s/rbac.yaml

# Deploy the operator
kubectl apply -f k8s/operator-deployment.yaml
```

Python 3.10+ and `kubernetes` client library are required if running outside the cluster:

```bash
pip install -r requirements.txt
```

## Usage

Define a `ChaosExperiment` resource:

```yaml
apiVersion: chaos.jamespham.io/v1alpha1
kind: ChaosExperiment
metadata:
  name: checkout-pod-failure
  namespace: payments
spec:
  type: pod-failure
  selector:
    matchLabels:
      app: checkout-api
  schedule: "0 */4 * * *"   # every 4 hours
  podFailure:
    percentage: 30            # kill 30% of matching pods
    gracePeriodSeconds: 0
```

```yaml
apiVersion: chaos.jamespham.io/v1alpha1
kind: ChaosExperiment
metadata:
  name: latency-injection
  namespace: payments
spec:
  type: network-latency
  selector:
    matchLabels:
      app: checkout-api
  networkLatency:
    latencyMs: 200
    jitterMs: 50
    durationSeconds: 300
```

## Running tests

```bash
pip install pytest pytest-mock
pytest tests/ -v
```

## Safety

The operator will not run experiments on pods with the annotation `chaos.jamespham.io/exclude: "true"`. It also refuses to target the `kube-system` namespace.

## License

MIT
