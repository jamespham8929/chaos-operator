"""Run a command in a pod, and fail when the command fails.

kubernetes.stream returns whatever the command printed, even when the command
exits non-zero or does not exist. A fault that never ran then counted as
injected, and the run record said so. Incident tools read those records as
the answer key, so a silent failure there is a wrong answer key.

This helper reads the exit status from the exec error channel and raises
ExecError unless the command succeeded.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# The channel where the API server reports the exit status of an exec.
ERROR_CHANNEL = 3


class ExecError(RuntimeError):
    """The command did not run, or it exited with an error."""


def exec_in_pod(
    core_v1,
    namespace: str,
    pod_name: str,
    command: list[str],
    container: str | None = None,
    timeout_seconds: int = 60,
) -> str:
    """Run command in the pod and return its output.

    container picks the container in a pod that has more than one. A fault
    can then run in a sidecar that has tc or stress-ng, while the app
    container stays minimal. Pods share one network namespace, so tc in a
    sidecar changes the traffic of the whole pod.
    """
    from kubernetes import stream

    kwargs = {
        "command": command,
        "stderr": True,
        "stdin": False,
        "stdout": True,
        "tty": False,
        "_preload_content": False,
    }
    if container:
        kwargs["container"] = container
    where = f"{namespace}/{pod_name}" + (f" ({container})" if container else "")

    resp = stream.stream(core_v1.connect_get_namespaced_pod_exec, pod_name, namespace, **kwargs)
    try:
        resp.run_forever(timeout=timeout_seconds)
        if resp.is_open():
            raise ExecError(f"{command[0]} on {where} did not finish in {timeout_seconds}s")
        out = resp.read_stdout() or ""
        err = resp.read_stderr() or ""
        status = _status(resp.read_channel(ERROR_CHANNEL))
    finally:
        resp.close()

    if status.get("status") != "Success":
        detail = status.get("message") or err.strip() or "no exit status"
        raise ExecError(f"{' '.join(command)} failed on {where}: {detail}")
    return out


def _status(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {"message": raw}
    return parsed if isinstance(parsed, dict) else {"message": raw}
