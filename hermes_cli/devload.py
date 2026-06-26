"""Development machine load reporting for ``hermes devload``."""

from __future__ import annotations

import datetime as _dt
import json
import math
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_WORKERS = 8


_PROBE_SCRIPT = """\
set -u
printf 'loadavg='
cat /proc/loadavg 2>/dev/null || printf '\\n'
cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || printf '1')
printf 'cpus=%s\\n' "$cpus"
awk '
  /^MemTotal:/ {print "mem_total_kb="$2}
  /^MemAvailable:/ {print "mem_available_kb="$2}
' /proc/meminfo 2>/dev/null || true
"""


class DevloadConfigError(ValueError):
    """Raised when ``dev_machines`` cannot be interpreted."""


@dataclass(frozen=True)
class DevMachine:
    name: str
    label: str
    target: str
    port: int | None = None
    timeout: float | None = None


@dataclass
class DevloadResult:
    name: str
    label: str
    target: str
    ok: bool
    status: str
    load1: float | None = None
    load5: float | None = None
    load15: float | None = None
    cpus: int | None = None
    load1_per_cpu: float | None = None
    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    mem_used_pct: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "target": self.target,
            "ok": self.ok,
            "status": self.status,
            "load1": self.load1,
            "load5": self.load5,
            "load15": self.load15,
            "cpus": self.cpus,
            "load1_per_cpu": self.load1_per_cpu,
            "mem_total_mb": self.mem_total_mb,
            "mem_used_mb": self.mem_used_mb,
            "mem_used_pct": self.mem_used_pct,
            "error": self.error,
        }


def run_devload_command(args: Any) -> int:
    """Run the devload CLI command and return a process exit code."""

    try:
        machines = load_dev_machines()
        selected = select_machines(
            machines,
            names=getattr(args, "machines", None) or [],
            all_machines=bool(getattr(args, "all", False)),
        )
        timeout_override = _coerce_optional_timeout(
            getattr(args, "timeout", None),
            "--timeout",
        )
    except DevloadConfigError as exc:
        _print_config_error(str(exc), json_output=bool(getattr(args, "json_output", False)))
        return 2

    results = query_machines(selected, timeout_override=timeout_override)
    json_output = bool(getattr(args, "json_output", False))
    if json_output:
        print(json.dumps(build_payload(results), sort_keys=True))
    else:
        print(format_results_table(results))
    return 0 if all(result.ok for result in results) else 1


def load_dev_machines() -> list[DevMachine]:
    from hermes_cli.config import load_config

    config = load_config()
    return parse_dev_machines(config.get("dev_machines"))


def parse_dev_machines(raw: Any) -> list[DevMachine]:
    if raw in (None, {}):
        raise DevloadConfigError(
            "No dev machines configured. Add a dev_machines mapping to config.yaml."
        )
    if not isinstance(raw, dict):
        raise DevloadConfigError("dev_machines must be a YAML mapping of name to SSH target.")

    machines: list[DevMachine] = []
    for raw_name, entry in raw.items():
        name = str(raw_name).strip()
        if not name:
            raise DevloadConfigError("dev_machines contains an empty machine name.")
        machines.append(_parse_machine_entry(name, entry))
    if not machines:
        raise DevloadConfigError(
            "No dev machines configured. Add at least one entry under dev_machines."
        )
    return machines


def select_machines(
    machines: list[DevMachine],
    *,
    names: list[str],
    all_machines: bool,
) -> list[DevMachine]:
    if names and all_machines:
        raise DevloadConfigError("Use either machine names or --all, not both.")
    if not names:
        return machines

    by_name = {machine.name: machine for machine in machines}
    selected: list[DevMachine] = []
    missing: list[str] = []
    for name in names:
        machine = by_name.get(name)
        if machine is None:
            missing.append(name)
        else:
            selected.append(machine)
    if missing:
        known = ", ".join(sorted(by_name))
        raise DevloadConfigError(
            f"Unknown dev machine(s): {', '.join(missing)}. Known machines: {known}"
        )
    return selected


def query_machines(
    machines: list[DevMachine],
    *,
    timeout_override: float | None = None,
) -> list[DevloadResult]:
    if len(machines) <= 1:
        return [_query_machine(machine, timeout_override=timeout_override) for machine in machines]

    results: list[DevloadResult | None] = [None] * len(machines)
    max_workers = min(MAX_WORKERS, len(machines))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(_query_machine, machine, timeout_override=timeout_override): idx
            for idx, machine in enumerate(machines)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as exc:  # pragma: no cover - defensive guard
                machine = machines[idx]
                results[idx] = _error_result(machine, f"probe failed: {exc}")
    return [result for result in results if result is not None]


def build_payload(results: list[DevloadResult]) -> dict[str, Any]:
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "machines": [result.to_dict() for result in results],
    }


def format_results_table(results: list[DevloadResult]) -> str:
    headers = ["Machine", "Target", "Status", "Load 1/5/15", "CPU", "Load/CPU", "Mem", "Error"]
    rows = [
        [
            result.label if result.label != result.name else result.name,
            result.target,
            result.status,
            _format_loads(result),
            str(result.cpus) if result.cpus is not None else "-",
            f"{result.load1_per_cpu:.2f}" if result.load1_per_cpu is not None else "-",
            _format_memory(result),
            result.error or "",
        ]
        for result in results
    ]
    widths = [
        max(len(headers[idx]), *(len(row[idx]) for row in rows))
        for idx in range(len(headers))
    ]
    lines = ["Development machine load", ""]
    lines.append("  ".join(headers[idx].ljust(widths[idx]) for idx in range(len(headers))))
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  ".join(row[idx].ljust(widths[idx]) for idx in range(len(headers))))
    return "\n".join(lines)


def _parse_machine_entry(name: str, entry: Any) -> DevMachine:
    if isinstance(entry, str):
        target = entry.strip()
        if not target:
            raise DevloadConfigError(f"dev_machines.{name} must not be empty.")
        return DevMachine(name=name, label=name, target=target)

    if not isinstance(entry, dict):
        raise DevloadConfigError(
            f"dev_machines.{name} must be a string target or mapping."
        )

    label = str(entry.get("label") or name).strip() or name
    target_raw = entry.get("target")
    host_raw = entry.get("host")
    user_raw = entry.get("user")
    port = _coerce_optional_port(entry.get("port"), f"dev_machines.{name}.port")
    timeout = _coerce_optional_timeout(entry.get("timeout"), f"dev_machines.{name}.timeout")

    if target_raw:
        target = str(target_raw).strip()
    elif host_raw:
        host = str(host_raw).strip()
        user = str(user_raw).strip() if user_raw else ""
        if user and "@" in host:
            raise DevloadConfigError(
                f"dev_machines.{name} has both user and a user-qualified host."
            )
        target = f"{user}@{host}" if user else host
    else:
        raise DevloadConfigError(
            f"dev_machines.{name} needs either target or host."
        )

    if not target:
        raise DevloadConfigError(f"dev_machines.{name} resolved to an empty SSH target.")
    return DevMachine(name=name, label=label, target=target, port=port, timeout=timeout)


def _query_machine(machine: DevMachine, *, timeout_override: float | None) -> DevloadResult:
    timeout = timeout_override if timeout_override is not None else machine.timeout
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS
    command = _ssh_command(machine, timeout)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _error_result(machine, f"timed out after {timeout:g}s")

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        if not message:
            message = f"ssh exited with status {completed.returncode}"
        return _error_result(machine, _single_line(message))

    try:
        return _parse_probe_output(machine, completed.stdout)
    except DevloadConfigError as exc:
        return _error_result(machine, str(exc))


def _ssh_command(machine: DevMachine, timeout: float) -> list[str]:
    ssh_timeout = max(1, int(math.ceil(timeout)))
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={ssh_timeout}",
    ]
    if machine.port is not None:
        command.extend(["-p", str(machine.port)])
    command.extend([machine.target, "LC_ALL=C sh -lc " + shlex.quote(_PROBE_SCRIPT)])
    return command


def _parse_probe_output(machine: DevMachine, output: str) -> DevloadResult:
    values: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()

    loads = _parse_loadavg(values.get("loadavg", ""), machine.name)
    cpus = _parse_positive_int(values.get("cpus"), f"{machine.name} cpus")
    load1_per_cpu = round(loads[0] / cpus, 3)
    total_kb = _parse_optional_int(values.get("mem_total_kb"))
    available_kb = _parse_optional_int(values.get("mem_available_kb"))

    mem_total_mb: int | None = None
    mem_used_mb: int | None = None
    mem_used_pct: float | None = None
    if total_kb and total_kb > 0 and available_kb is not None:
        used_kb = max(0, total_kb - available_kb)
        mem_total_mb = round(total_kb / 1024)
        mem_used_mb = round(used_kb / 1024)
        mem_used_pct = round((used_kb / total_kb) * 100, 1)

    status = _status(load1_per_cpu=load1_per_cpu, mem_used_pct=mem_used_pct)
    return DevloadResult(
        name=machine.name,
        label=machine.label,
        target=machine.target,
        ok=True,
        status=status,
        load1=loads[0],
        load5=loads[1],
        load15=loads[2],
        cpus=cpus,
        load1_per_cpu=load1_per_cpu,
        mem_total_mb=mem_total_mb,
        mem_used_mb=mem_used_mb,
        mem_used_pct=mem_used_pct,
    )


def _parse_loadavg(raw: str, machine_name: str) -> tuple[float, float, float]:
    parts = raw.split()
    if len(parts) < 3:
        raise DevloadConfigError(f"{machine_name}: probe did not return load averages")
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise DevloadConfigError(f"{machine_name}: invalid load average output") from exc


def _parse_positive_int(raw: str | None, label: str) -> int:
    try:
        value = int(str(raw or "").strip())
    except ValueError as exc:
        raise DevloadConfigError(f"{label} is not an integer") from exc
    if value <= 0:
        raise DevloadConfigError(f"{label} must be positive")
    return value


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _coerce_optional_port(raw: Any, label: str) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise DevloadConfigError(f"{label} must be an integer port.") from exc
    if value < 1 or value > 65535:
        raise DevloadConfigError(f"{label} must be between 1 and 65535.")
    return value


def _coerce_optional_timeout(raw: Any, label: str) -> float | None:
    if raw in (None, ""):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise DevloadConfigError(f"{label} must be a number of seconds.") from exc
    if value <= 0:
        raise DevloadConfigError(f"{label} must be greater than zero.")
    return value


def _status(*, load1_per_cpu: float, mem_used_pct: float | None) -> str:
    if load1_per_cpu >= 1.0 or (mem_used_pct is not None and mem_used_pct >= 90.0):
        return "busy"
    if load1_per_cpu >= 0.7 or (mem_used_pct is not None and mem_used_pct >= 80.0):
        return "warn"
    return "ok"


def _format_loads(result: DevloadResult) -> str:
    if result.load1 is None or result.load5 is None or result.load15 is None:
        return "-"
    return f"{result.load1:.2f}/{result.load5:.2f}/{result.load15:.2f}"


def _format_memory(result: DevloadResult) -> str:
    if (
        result.mem_used_mb is None
        or result.mem_total_mb is None
        or result.mem_used_pct is None
    ):
        return "-"
    return f"{result.mem_used_mb}/{result.mem_total_mb}MB {result.mem_used_pct:.1f}%"


def _error_result(machine: DevMachine, error: str) -> DevloadResult:
    return DevloadResult(
        name=machine.name,
        label=machine.label,
        target=machine.target,
        ok=False,
        status="error",
        error=error,
    )


def _single_line(value: str) -> str:
    return " ".join(value.split())


def _print_config_error(message: str, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps({"error": message, "machines": []}, sort_keys=True))
    else:
        print(f"devload: {message}", file=sys.stderr)
