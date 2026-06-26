"""Tests for the ``hermes devload`` command backend."""

from __future__ import annotations

import argparse
import json
import subprocess
from unittest.mock import patch

from hermes_cli.devload import (
    DevloadConfigError,
    parse_dev_machines,
    run_devload_command,
    select_machines,
)


def _args(*machines: str, all_: bool = False, json_output: bool = True, timeout=None):
    return argparse.Namespace(
        machines=list(machines),
        all=all_,
        json_output=json_output,
        timeout=timeout,
    )


def _completed(stdout: str, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["ssh"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class TestDevloadConfig:
    def test_parse_supported_machine_shapes(self):
        machines = parse_dev_machines(
            {
                "hedge-a": "hedge-a",
                "hedge-b": {
                    "target": "deploy@hedge-b",
                    "label": "Hedge B",
                    "timeout": 8,
                },
                "hedge-c": {
                    "host": "hedge-c.internal",
                    "user": "deploy",
                    "port": 2222,
                },
            }
        )

        assert [machine.name for machine in machines] == ["hedge-a", "hedge-b", "hedge-c"]
        assert machines[0].target == "hedge-a"
        assert machines[1].label == "Hedge B"
        assert machines[1].timeout == 8
        assert machines[2].target == "deploy@hedge-c.internal"
        assert machines[2].port == 2222

    def test_select_unknown_machine_reports_known_names(self):
        machines = parse_dev_machines({"hedge-a": "hedge-a"})

        try:
            select_machines(machines, names=["missing"], all_machines=False)
        except DevloadConfigError as exc:
            assert "missing" in str(exc)
            assert "hedge-a" in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("expected DevloadConfigError")

    def test_names_and_all_are_mutually_exclusive(self):
        machines = parse_dev_machines({"hedge-a": "hedge-a"})

        try:
            select_machines(machines, names=["hedge-a"], all_machines=True)
        except DevloadConfigError as exc:
            assert "--all" in str(exc)
        else:  # pragma: no cover - assertion clarity
            raise AssertionError("expected DevloadConfigError")


class TestDevloadCommand:
    def test_json_output_reports_load_cpu_and_memory(self, capsys):
        config = {"dev_machines": {"hedge-a": "hedge-a"}}
        stdout = (
            "loadavg=2.00 1.00 0.50 1/100 123\n"
            "cpus=4\n"
            "mem_total_kb=1048576\n"
            "mem_available_kb=524288\n"
        )

        with patch("hermes_cli.config.load_config", return_value=config), patch(
            "subprocess.run",
            return_value=_completed(stdout),
        ) as run_mock:
            rc = run_devload_command(_args())

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        machine = payload["machines"][0]
        assert machine["name"] == "hedge-a"
        assert machine["target"] == "hedge-a"
        assert machine["ok"] is True
        assert machine["status"] == "ok"
        assert machine["load1"] == 2.0
        assert machine["cpus"] == 4
        assert machine["load1_per_cpu"] == 0.5
        assert machine["mem_total_mb"] == 1024
        assert machine["mem_used_mb"] == 512
        assert machine["mem_used_pct"] == 50.0
        ssh_cmd = run_mock.call_args.args[0]
        assert ssh_cmd[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        assert ssh_cmd[-2] == "hedge-a"

    def test_table_output_is_default(self, capsys):
        config = {"dev_machines": {"hedge-a": "hedge-a"}}
        stdout = "loadavg=0.10 0.20 0.30 1/100 123\ncpus=2\n"

        with patch("hermes_cli.config.load_config", return_value=config), patch(
            "subprocess.run",
            return_value=_completed(stdout),
        ):
            rc = run_devload_command(_args(json_output=False))

        assert rc == 0
        out = capsys.readouterr().out
        assert "Development machine load" in out
        assert "hedge-a" in out
        assert "0.10/0.20/0.30" in out

    def test_ssh_failure_returns_partial_failure(self, capsys):
        config = {"dev_machines": {"hedge-a": "hedge-a"}}

        with patch("hermes_cli.config.load_config", return_value=config), patch(
            "subprocess.run",
            return_value=_completed("", stderr="Permission denied\n", returncode=255),
        ):
            rc = run_devload_command(_args())

        assert rc == 1
        machine = json.loads(capsys.readouterr().out)["machines"][0]
        assert machine["ok"] is False
        assert machine["status"] == "error"
        assert machine["error"] == "Permission denied"

    def test_timeout_override_applies_to_ssh_and_subprocess(self, capsys):
        config = {"dev_machines": {"hedge-a": {"target": "deploy@hedge-a", "timeout": 30}}}

        with patch("hermes_cli.config.load_config", return_value=config), patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ssh"], timeout=5),
        ) as run_mock:
            rc = run_devload_command(_args("hedge-a", timeout=5))

        assert rc == 1
        ssh_cmd = run_mock.call_args.args[0]
        assert "ConnectTimeout=5" in ssh_cmd
        assert run_mock.call_args.kwargs["timeout"] == 5
        machine = json.loads(capsys.readouterr().out)["machines"][0]
        assert machine["error"] == "timed out after 5s"

    def test_missing_config_returns_usage_error(self, capsys):
        with patch("hermes_cli.config.load_config", return_value={}):
            rc = run_devload_command(_args(json_output=False))

        assert rc == 2
        assert "No dev machines configured" in capsys.readouterr().err
