"""Markdown rendering for xdna-top snapshot artifacts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from xdna_top.assertions import load_artifact
from xdna_top.snapshot import SNAPSHOT_KIND


def load_snapshot(path: str | Path) -> dict[str, Any]:
    """Load a snapshot JSON artifact from disk."""
    with Path(path).open("r", encoding="utf-8") as f:
        snapshot = json.load(f)
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot root must be a JSON object")
    if snapshot.get("kind") != SNAPSHOT_KIND:
        raise ValueError(f"expected kind={SNAPSHOT_KIND!r}")
    return snapshot


def render_markdown(snapshot: dict[str, Any]) -> str:
    """Render a concise Markdown report from a captured snapshot."""
    host = snapshot.get("host", {})
    os_info = host.get("os", {})
    kernel = host.get("kernel", {})
    python = host.get("python", {})
    xdna_top = host.get("xdna_top", {})
    commands = snapshot.get("commands", {})
    xrt_smi = commands.get("xrt_smi", {})
    backends = snapshot.get("backends", {})
    devices = snapshot.get("devices", {})
    telemetry = snapshot.get("telemetry", {})
    degraded = snapshot.get("degraded", {})
    errors = snapshot.get("errors", [])

    npu = devices.get("npu", {})
    igpu = devices.get("igpu", {})
    accel = devices.get("accel", {})

    lines = [
        "# xdna-top Environment Report",
        "",
        "## Snapshot",
        "",
        f"- Captured at: {_value(snapshot.get('captured_at'))}",
        f"- Schema version: {_value(snapshot.get('schema_version'))}",
        f"- Kind: {_value(snapshot.get('kind'))}",
        "",
        "## Host",
        "",
        f"- Hostname: {_value(host.get('hostname'))}",
        f"- OS: {_value(os_info.get('pretty_name') or os_info.get('id'))}",
        f"- Kernel: {_value(kernel.get('release'))}",
        f"- Python: {_value(python.get('version'))}",
        f"- xdna-top: {_value(xdna_top.get('version'))}",
        "",
        "## Commands",
        "",
        f"- xrt-smi available: {_bool(xrt_smi.get('available'))}",
        f"- xrt-smi path: {_value(xrt_smi.get('path'))}",
        f"- xrt-smi examine return code: {_value(xrt_smi.get('examine_returncode'))}",
        f"- xrt-smi AIE partitions return code: {_value(xrt_smi.get('aie_partitions_returncode'))}",
        f"- xrt-smi version: {_value(xrt_smi.get('version_output'))}",
        "",
        "## Backend Provenance",
        "",
        f"- NPU primary backend: {_value(_get(backends, 'npu', 'primary'))}",
        f"- NPU device signal: {_value(_get(backends, 'npu', 'signals', 'device'))}",
        f"- NPU sensor signal: {_value(_get(backends, 'npu', 'signals', 'sensors'))}",
        f"- NPU context signal: {_value(_get(backends, 'npu', 'signals', 'contexts'))}",
        f"- iGPU primary backend: {_value(_get(backends, 'igpu', 'primary'))}",
        f"- iGPU busy signal: {_value(_get(backends, 'igpu', 'signals', 'busy_pct'))}",
        f"- iGPU power signal: {_value(_get(backends, 'igpu', 'signals', 'power_w'))}",
        "",
        "## Devices",
        "",
        f"- accel entries: {_accel_summary(accel.get('entries', []))}",
        f"- NPU detected: {_bool(npu.get('detected'))}",
        f"- NPU BDF: {_value(npu.get('bdf'))}",
        f"- NPU name: {_value(npu.get('name'))}",
        f"- NPU context count: {_value(_get(npu, 'report_shape', 'context_count'))}",
        f"- NPU sensor support: {_bool(_get(npu, 'driver', 'supports_sensors'))}",
        f"- iGPU busy path: {_path_status(igpu.get('busy_path'), igpu.get('busy_path_exists'))}",
        f"- iGPU power path: {_path_status(igpu.get('power_path'), igpu.get('power_path_exists'))}",
        "",
        "## Captured Telemetry",
        "",
        f"- iGPU busy: {_metric(telemetry.get('gpu_busy_pct'), '%')}",
        f"- iGPU power: {_metric(telemetry.get('gpu_power_w'), 'W')}",
        f"- iGPU state: {_value(telemetry.get('state'))}",
        f"- NPU active: {_bool(telemetry.get('npu_active'))}",
        f"- Telemetry timestamp: {_value(telemetry.get('ts'))}",
        "",
        "## Degraded Status",
        "",
        f"- Overall degraded: {_bool(degraded.get('overall'))}",
        f"- iGPU degraded: {_bool(_get(degraded, 'igpu', 'degraded'))}",
        f"- iGPU reasons: {_list(_get(degraded, 'igpu', 'reasons'))}",
        f"- NPU degraded: {_bool(_get(degraded, 'npu', 'degraded'))}",
        f"- NPU reasons: {_list(_get(degraded, 'npu', 'reasons'))}",
    ]

    if errors:
        lines.extend(["", "## Probe Errors", ""])
        for error in errors:
            if not isinstance(error, dict):
                continue
            lines.append(
                f"- {_value(error.get('probe'))}: {_value(error.get('message'))}"
            )

    return "\n".join(lines) + "\n"


def write_report(report: str, out_path: str | Path | None) -> None:
    """Write a Markdown report to disk, or stdout when no output path is supplied."""
    if out_path is None:
        print(report, end="")
        return

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


def render_record_markdown(events: list[Any]) -> str:
    """Render a concise Markdown report from a captured record JSONL stream."""
    meta = _first_event(events, "meta")
    summary = _first_event(events, "summary")
    telemetry = [
        e for e in events if isinstance(e, dict) and e.get("type") == "telemetry"
    ]
    marks = [e for e in events if isinstance(e, dict) and e.get("type") == "mark"]

    host = meta.get("host", {})
    params = meta.get("params", {})
    first = (telemetry[0].get("reading") or {}) if telemetry else {}
    last = (telemetry[-1].get("reading") or {}) if telemetry else {}

    active_samples = sum(
        1 for e in telemetry if (e.get("reading") or {}).get("npu_active") is True
    )
    degraded_samples = sum(
        1
        for e in telemetry
        if (e.get("reading") or {}).get("igpu_degraded") is True
        or (e.get("reading") or {}).get("npu_degraded") is True
    )
    sources = sorted(
        {
            ctx.get("source")
            for e in telemetry
            for ctx in (e.get("contexts") or [])
            if isinstance(ctx, dict) and ctx.get("source")
        }
    )
    observed_contexts = sorted(
        {
            _format_context_id(ctx)
            for e in telemetry
            for ctx in (e.get("contexts") or [])
            if isinstance(ctx, dict)
        }
    )

    lines = [
        "# xdna-top Telemetry Report",
        "",
        "## Recording",
        "",
        f"- Schema version: {_value(meta.get('schema_version'))}",
        f"- Started at: {_value(meta.get('started_at'))}",
        f"- Ended at: {_value(summary.get('ended_at'))}",
        f"- Requested duration: {_metric(params.get('duration_s'), 's')}",
        f"- Requested interval: {_metric(params.get('interval_s'), 's')}",
        f"- Telemetry samples: {len(telemetry)}",
        f"- Observed window: {_window_seconds(telemetry)}",
        "",
        "## Host",
        "",
        *_host_lines(host),
        "",
        "## Observed Activity",
        "",
        f"- NPU active samples: {active_samples}/{len(telemetry)}",
        f"- Max context submission delta: {_max_submission_delta(telemetry)}",
        f"- Degraded samples: {degraded_samples}/{len(telemetry)}",
        f"- Context sources: {_list(sources)}",
        f"- Observed contexts (PID/ctx): {_list(observed_contexts)}",
        "",
        "## First Reading",
        "",
        *_reading_lines(first),
        "",
        "## Last Reading",
        "",
        *_reading_lines(last),
    ]

    if marks:
        lines.extend(["", "## Marks", ""])
        for mark in marks:
            lines.append(
                f"- {_value(mark.get('ts'))}: {_value(mark.get('label'))}"
            )

    return "\n".join(lines) + "\n"


def env_report_main(args: Any) -> int:
    try:
        artifact = load_artifact(args.snapshot)
        if artifact.kind == "snapshot":
            report = render_markdown(artifact.snapshot)
        else:
            report = render_record_markdown(artifact.events)
        write_report(report, args.out)
    except Exception as exc:
        print(f"env-report failed: {exc}", file=sys.stderr)
        return 2
    return 0


def _first_event(events: list[Any], event_type: str) -> dict[str, Any]:
    for event in events:
        if isinstance(event, dict) and event.get("type") == event_type:
            return event
    return {}


def _host_lines(host: dict[str, Any]) -> list[str]:
    os_info = host.get("os", {})
    kernel = host.get("kernel", {})
    python = host.get("python", {})
    xdna_top = host.get("xdna_top", {})
    return [
        f"- Hostname: {_value(host.get('hostname'))}",
        f"- OS: {_value(os_info.get('pretty_name') or os_info.get('id'))}",
        f"- Kernel: {_value(kernel.get('release'))}",
        f"- Python: {_value(python.get('version'))}",
        f"- xdna-top: {_value(xdna_top.get('version'))}",
    ]


def _reading_lines(reading: dict[str, Any]) -> list[str]:
    return [
        f"- iGPU busy: {_metric(reading.get('gpu_busy_pct'), '%')}",
        f"- iGPU power: {_metric(reading.get('gpu_power_w'), 'W')}",
        f"- iGPU state: {_value(reading.get('state'))}",
        f"- NPU active: {_bool(reading.get('npu_active'))}",
        f"- Timestamp: {_value(reading.get('ts'))}",
    ]


def _window_seconds(telemetry: list[dict[str, Any]]) -> str:
    stamps = [e.get("ts") for e in telemetry if isinstance(e.get("ts"), (int, float))]
    if len(stamps) < 2:
        return "unavailable"
    return f"{round(max(stamps) - min(stamps), 3)} s"


def _max_submission_delta(telemetry: list[dict[str, Any]]) -> int:
    per_key: dict[tuple[Any, Any], list[float]] = {}
    for event in telemetry:
        for ctx in event.get("contexts") or []:
            if not isinstance(ctx, dict):
                continue
            submissions = ctx.get("submissions")
            if not isinstance(submissions, (int, float)) or isinstance(
                submissions, bool
            ):
                continue
            key = (ctx.get("pid"), ctx.get("ctx_id"))
            if key not in per_key:
                per_key[key] = [submissions, submissions]
            else:
                per_key[key][0] = min(per_key[key][0], submissions)
                per_key[key][1] = max(per_key[key][1], submissions)
    return int(max((hi - lo for lo, hi in per_key.values()), default=0))


def _get(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _format_context_id(ctx: dict[str, Any]) -> str:
    """Render a context as ``PID/ctx`` plus the process name when resolved.

    The process name is best-effort (``/proc``-derived attribution, not a
    measured value); contexts captured before name resolution simply omit it.
    """
    base = f"{ctx.get('pid')}/{ctx.get('ctx_id')}"
    name = ctx.get("process_name")
    return f"{base} ({name})" if name else base


def _value(value: Any) -> str:
    if value is None or value == "":
        return "unavailable"
    return str(value)


def _bool(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "unavailable"


def _list(values: Any) -> str:
    if not values:
        return "none"
    if not isinstance(values, list):
        return _value(values)
    return ", ".join(str(value) for value in values)


def _metric(value: Any, unit: str) -> str:
    if value is None:
        return "unavailable"
    return f"{value} {unit}"


def _path_status(path: Any, exists: Any) -> str:
    return f"{_value(path)} (exists: {_bool(exists)})"


def _accel_summary(entries: Any) -> str:
    if not entries:
        return "none"
    if not isinstance(entries, list):
        return _value(entries)
    parts = []
    for entry in entries:
        if isinstance(entry, dict):
            parts.append(f"{_value(entry.get('path'))} exists={_bool(entry.get('exists'))}")
        else:
            parts.append(_value(entry))
    return ", ".join(parts)
