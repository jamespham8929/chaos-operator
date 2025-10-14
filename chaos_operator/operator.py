"""Kopf-based operator main loop for ChaosExperiment resources."""

import logging

import kopf
import kubernetes

from .experiments.pod_failure import PodFailureExperiment
from .experiments.network_chaos import NetworkLatencyExperiment
from .experiments.cpu_stress import CPUStressExperiment
from .orchestration import has_safe_config, run_safe_experiment

logger = logging.getLogger(__name__)

PROTECTED_NAMESPACES = {"kube-system", "kube-public", "kube-node-lease", "chaos-system"}
EXCLUDE_ANNOTATION = "chaos.jamespham.io/exclude"


def _load_k8s_client() -> kubernetes.client.CoreV1Api:
    try:
        kubernetes.config.load_incluster_config()
    except kubernetes.config.ConfigException:
        kubernetes.config.load_kube_config()
    return kubernetes.client.CoreV1Api()


@kopf.on.create("chaos.jamespham.io", "v1alpha1", "chaosexperiments")
@kopf.on.resume("chaos.jamespham.io", "v1alpha1", "chaosexperiments")
def handle_experiment(spec, name, namespace, logger, **kwargs):
    if namespace in PROTECTED_NAMESPACES:
        logger.warning("Refusing to run experiment in protected namespace %s", namespace)
        return {"status": "skipped", "reason": f"namespace {namespace} is protected"}

    experiment_type = spec.get("type")
    selector = spec.get("selector", {}).get("matchLabels", {})

    logger.info("Running %s experiment in %s/%s", experiment_type, namespace, name)

    core_v1 = _load_k8s_client()
    result = _dispatch_experiment(core_v1, experiment_type, namespace, selector, spec)

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
        and pod.metadata.annotations.get(EXCLUDE_ANNOTATION) != "true"
    ]
