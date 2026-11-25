"""Tests for the checked pod exec helper.

The fake stands in for kubernetes.stream's WSClient. It mirrors the calls the
helper makes: run_forever, is_open, read_stdout, read_stderr, read_channel and
close. The status JSON on the error channel is the same shape the API server
sends.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from chaos_operator.experiments.cpu_stress import CPUStressExperiment
from chaos_operator.experiments.network_chaos import NetworkLatencyExperiment
from chaos_operator.experiments.podexec import ERROR_CHANNEL, ExecError, exec_in_pod
from chaos_operator.history import run_record

SUCCESS = json.dumps({"metadata": {}, "status": "Success"})
EXIT_1 = json.dumps({
    "metadata": {}, "status": "Failure", "message": "command terminated with non-zero exit code: 1",
    "reason": "NonZeroExitCode", "details": {"causes": [{"reason": "ExitCode", "message": "1"}]},
})
NOT_FOUND = json.dumps({
    "metadata": {}, "status": "Failure",
    "message": 'exec: "tc": executable file not found in $PATH',
})


class FakeWS:
    def __init__(self, status=SUCCESS, stdout="", stderr="", still_open=False):
        self._status = status
        self._stdout = stdout
        self._stderr = stderr
        self._open = still_open
        self.closed = False
        self.timeout = None

    def run_forever(self, timeout=None):
        self.timeout = timeout

    def is_open(self):
        return self._open

    def read_stdout(self):
        return self._stdout

    def read_stderr(self):
        return self._stderr

    def read_channel(self, channel):
        assert channel == ERROR_CHANNEL
        return self._status

    def close(self):
        self.closed = True


def run_with(ws, **kwargs):
    with patch("kubernetes.stream.stream", return_value=ws) as stream:
        out = exec_in_pod(MagicMock(), "shop", "inventory-0", ["tc", "qdisc", "show"], **kwargs)
    return out, stream


def test_success_returns_stdout_and_closes():
    ws = FakeWS(stdout="qdisc netem 8001: root")
    out, stream = run_with(ws)
    assert out == "qdisc netem 8001: root"
    assert ws.closed
    assert "container" not in stream.call_args.kwargs
    assert stream.call_args.kwargs["_preload_content"] is False


def test_container_is_passed_to_the_api():
    _, stream = run_with(FakeWS(), container="chaos-agent", timeout_seconds=90)
    assert stream.call_args.kwargs["container"] == "chaos-agent"


def test_timeout_is_passed_to_the_websocket():
    ws = FakeWS()
    run_with(ws, timeout_seconds=90)
    assert ws.timeout == 90


def test_non_zero_exit_raises():
    ws = FakeWS(status=EXIT_1, stderr="RTNETLINK answers: Operation not permitted")
    with pytest.raises(ExecError, match="non-zero exit code"):
        run_with(ws)
    assert ws.closed


def test_missing_binary_raises_with_the_api_message():
    with pytest.raises(ExecError, match="executable file not found"):
        run_with(FakeWS(status=NOT_FOUND))


def test_no_exit_status_is_a_failure():
    # Without a status there is no proof the command ran.
    with pytest.raises(ExecError, match="no exit status"):
        run_with(FakeWS(status=""))


def test_plain_text_status_is_reported():
    with pytest.raises(ExecError, match="something odd"):
        run_with(FakeWS(status="something odd"))


def test_command_still_running_after_timeout_raises():
    ws = FakeWS(still_open=True)
    with pytest.raises(ExecError, match="did not finish in 5s"):
        run_with(ws, timeout_seconds=5)
    assert ws.closed


def make_pod(name, namespace="shop"):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    return pod


class TestFaultsUseTheContainer:
    def test_network_latency_execs_in_the_named_container(self):
        calls = []

        def fake(core_v1, namespace, pod, command, container=None, timeout_seconds=60):
            calls.append((pod, command[0], container))
            return ""

        exp = NetworkLatencyExperiment(MagicMock(), container="chaos-agent")
        with patch("chaos_operator.experiments.network_chaos.exec_in_pod", side_effect=fake), \
             patch("time.sleep"):
            result = exp.run([make_pod("inventory-0")], {"latencyMs": 300, "durationSeconds": 1})
        assert result["status"] == "completed"
        assert calls == [
            ("inventory-0", "tc", "chaos-agent"),  # inject
            ("inventory-0", "tc", "chaos-agent"),  # clean up
        ]

    def test_network_latency_that_reaches_no_pod_is_failed(self):
        exp = NetworkLatencyExperiment(MagicMock(), container="chaos-agent")
        with patch("chaos_operator.experiments.network_chaos.exec_in_pod",
                   side_effect=ExecError("tc: not found")):
            result = exp.run([make_pod("a"), make_pod("b")], {"durationSeconds": 1})
        assert result["status"] == "failed"
        assert result["pods_failed"] == 2
        # The run record must not call this injected, or incident tools would
        # read a fault that never happened as a cause.
        assert run_record({}, "x", "shop", "manual", _now(), _now(), result)["injected"] is False

    def test_cpu_stress_counts_exec_failures_as_failed(self):
        def fake(core_v1, namespace, pod, command, container=None, timeout_seconds=60):
            assert container == "chaos-agent"
            assert timeout_seconds == 31
            if pod == "b":
                raise ExecError("stress-ng: not found")
            return ""

        exp = CPUStressExperiment(MagicMock(), container="chaos-agent")
        with patch("chaos_operator.experiments.cpu_stress.exec_in_pod", side_effect=fake):
            result = exp.run([make_pod("a"), make_pod("b")], {"durationSeconds": 1})
        assert result["status"] == "completed"
        assert result["pods_affected"] == 1
        assert result["pods_failed"] == 1

    def test_cpu_stress_that_reaches_no_pod_is_failed(self):
        exp = CPUStressExperiment(MagicMock())
        with patch("chaos_operator.experiments.cpu_stress.exec_in_pod",
                   side_effect=ExecError("stress-ng: not found")):
            result = exp.run([make_pod("a")], {"durationSeconds": 1})
        assert result["status"] == "failed"
        assert result["pods_affected"] == 0


def _now():
    from datetime import datetime, timezone
    return datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
