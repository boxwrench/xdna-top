"""Snapshot artifact builder for xdna-top evidence workflows."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xdna_top.diagnostics import diagnose_memlock
from xdna_top.gauge import HardwareGauge, load_sysfs_paths, parse_xrt_smi
from xdna_top.npu_power import read_npu_power

SCHEMA_VERSION = "1.0"
SNAPSHOT_KIND = "xdna-top.snapshot"
COMMAND_TIMEOUT_S = 1.5


def write_snapshot(snapshot: dict[str, Any], out_path: str | Path | None) -> None:
    """Write a snapshot to disk, or stdout when no output path is supplied."""
    payload = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    if out_path is None:
        print(payload, end="")
        return

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def build_snapshot(
    *,
    bench_dir: str = "/tmp/xdna_top",
    npu_device: str | None = None,
    gpu_idle_busy_pct: int = 10,
    gpu_prefill_power_w: float = 35.0,
    gauge_hysteresis_samples: int = 3,
) -> dict[str, Any]:
    """Capture a point-in-time platform and telemetry snapshot."""
    errors: list[dict[str, str]] = []

    busy_path, power_path = load_sysfs_paths(bench_dir)
    xrt = _probe_xrt_smi(npu_device=npu_device, errors=errors)

    gauge = HardwareGauge(
        gpu_idle_busy_pct=gpu_idle_busy_pct,
        gpu_prefill_power_w=gpu_prefill_power_w,
        gauge_hysteresis_samples=gauge_hysteresis_samples,
        bench_dir=bench_dir,
        pessimistic_fallback=False,
        npu_device=npu_device,
    )
    try:
        reading = gauge.read_direct()
    except Exception as exc:
        errors.append({"probe": "gauge.read_direct", "message": str(exc)})
        reading = None

    contexts = [
        {**ctx, "process_name": ctx.get("process_name"), "source": "xrt_smi"}
        for ctx in xrt["contexts"]
    ]
    npu_detected = xrt["device"]["bdf"] is not None or bool(contexts)
    xrt_contexts_available = xrt["aie_partitions_returncode"] == 0
    # Read unconditionally and independently of xrt-smi: the debugfs power state
    # is a separate subsystem, so it can still confirm the NPU is alive when
    # `examine` fails (e.g. the RLIMIT_MEMLOCK false-negative). The read is fully
    # guarded — root-only/absent debugfs yields available:false, never raises.
    npu_power = read_npu_power(xrt["device"]["bdf"])
    igpu_busy_exists = bool(busy_path and os.path.exists(busy_path))
    igpu_power_exists = bool(power_path and os.path.exists(power_path))

    npu_reasons: list[str] = []
    if not xrt["available"]:
        npu_reasons.append("xrt_smi_not_found")
    elif xrt["examine_returncode"] not in (0, None) and not npu_detected:
        npu_reasons.append("xrt_smi_examine_failed")
    if xrt["available"] and not xrt_contexts_available:
        npu_reasons.append("aie_partitions_report_failed")

    # A low RLIMIT_MEMLOCK makes xrt-smi's MAP_LOCKED firmware mmap fail with
    # EAGAIN, which otherwise reads as "NPU absent". Surface the real cause so a
    # detected-but-degraded NPU is not mistaken for missing hardware.
    memlock_hint = diagnose_memlock(errors)
    if memlock_hint is not None:
        npu_reasons.append("rlimit_memlock_too_low")
        errors.append({"probe": "rlimit_memlock", "message": memlock_hint})

    igpu_reasons: list[str] = []
    if not igpu_busy_exists:
        igpu_reasons.append("igpu_busy_path_missing")
    if not igpu_power_exists:
        igpu_reasons.append("igpu_power_path_missing")

    npu_degraded = bool(npu_reasons)
    igpu_degraded = bool(igpu_reasons)

    telemetry = (
        reading.to_dict()
        if reading is not None
        else {
            "gpu_busy_pct": None,
            "gpu_power_w": None,
            "npu_active": False,
            "state": None,
            "igpu_degraded": True,
            "npu_degraded": True,
            "ts": time.time(),
        }
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": SNAPSHOT_KIND,
        "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "host": host_facts(),
        "commands": {
            "xrt_smi": {
                "path": xrt["path"],
                "available": xrt["available"],
                "version_output": xrt["version_output"],
                "examine_returncode": xrt["examine_returncode"],
                "aie_partitions_returncode": xrt["aie_partitions_returncode"],
            }
        },
        "backends": _backend_provenance(
            xrt_available=xrt["available"],
            xrt_device_available=xrt["device"]["bdf"] is not None,
            xrt_contexts_available=xrt_contexts_available,
            npu_power_available=bool(npu_power.get("available")),
            igpu_busy_available=igpu_busy_exists,
            igpu_power_available=igpu_power_exists,
        ),
        "devices": {
            "accel": {"entries": _accel_entries()},
            "npu": {
                "detected": npu_detected,
                "bdf": xrt["device"]["bdf"],
                "name": xrt["device"]["name"],
                "driver": {
                    "drm_version": None,
                    "supports_sensors": False,
                },
                "sensors": {
                    "power_w": {"value": None, "source": None},
                    "column_utilization_pct": {"value": None, "source": None},
                },
                "power_state": npu_power,
                "contexts": contexts,
                "report_shape": {
                    "has_aie_partitions": xrt["aie_partitions_returncode"] == 0,
                    "has_hw_contexts": bool(contexts),
                    "context_count": len(contexts),
                },
            },
            "igpu": {
                "busy_path": busy_path,
                "power_path": power_path,
                "busy_path_exists": igpu_busy_exists,
                "power_path_exists": igpu_power_exists,
            },
        },
        "telemetry": telemetry,
        "degraded": {
            "overall": npu_degraded or igpu_degraded,
            "igpu": {
                "degraded": igpu_degraded,
                "reasons": igpu_reasons,
            },
            "npu": {
                "degraded": npu_degraded,
                "reasons": npu_reasons,
            },
        },
        "errors": errors,
    }


def host_facts() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "kernel": {
            "release": platform.release(),
            "version": platform.version(),
        },
        "os": _os_release(),
        "python": {
            "version": platform.python_version(),
        },
        "xdna_top": {
            "version": _package_version(),
        },
    }


def _package_version() -> str | None:
    try:
        return importlib.metadata.version("xdna-top")
    except importlib.metadata.PackageNotFoundError:
        return None


def _os_release() -> dict[str, str | None]:
    values: dict[str, str | None] = {
        "id": None,
        "version_id": None,
        "pretty_name": None,
    }
    path = Path("/etc/os-release")
    if not path.exists():
        return values

    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"')
            if key == "ID":
                values["id"] = value
            elif key == "VERSION_ID":
                values["version_id"] = value
            elif key == "PRETTY_NAME":
                values["pretty_name"] = value
    except OSError:
        pass
    return values


def _backend_provenance(
    *,
    xrt_available: bool,
    xrt_device_available: bool,
    xrt_contexts_available: bool,
    igpu_busy_available: bool,
    igpu_power_available: bool,
    npu_power_available: bool = False,
) -> dict[str, Any]:
    npu_signals = {
        "device": "xrt_smi" if xrt_available and xrt_device_available else None,
        "sensors": None,
        "contexts": "xrt_smi" if xrt_available and xrt_contexts_available else None,
        "power_state": "debugfs" if npu_power_available else None,
    }
    igpu_signals = {
        "busy_pct": "sysfs" if igpu_busy_available else None,
        "power_w": "sysfs" if igpu_power_available else None,
    }
    return {
        "npu": {
            "primary": "xrt_smi" if xrt_available else None,
            "fallbacks_used": [],
            "signals": npu_signals,
        },
        "igpu": {
            "primary": "sysfs" if any(igpu_signals.values()) else None,
            "signals": igpu_signals,
        },
    }


def _accel_entries() -> list[dict[str, Any]]:
    accel_dir = Path("/dev/accel")
    paths = sorted(accel_dir.glob("accel*")) if accel_dir.exists() else []
    if not paths:
        return [{"path": "/dev/accel/accel0", "exists": False}]
    return [{"path": str(path), "exists": path.exists()} for path in paths]


def _probe_xrt_smi(
    *,
    npu_device: str | None,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    path = shutil.which("xrt-smi")
    result: dict[str, Any] = {
        "path": path,
        "available": path is not None,
        "version_output": None,
        "examine_returncode": None,
        "aie_partitions_returncode": None,
        "device": {"bdf": None, "name": None},
        "contexts": [],
    }
    if path is None:
        return result

    version = _run_command(["xrt-smi", "--version"], "xrt_smi.version", errors)
    result["version_output"] = _compact_output(version["stdout"] or version["stderr"])

    examine = _run_command(["xrt-smi", "examine"], "xrt_smi.examine", errors)
    result["examine_returncode"] = examine["returncode"]
    if examine["returncode"] == 0:
        result["device"] = _parse_xrt_device(examine["stdout"])
    if npu_device and result["device"]["bdf"] is None:
        result["device"]["bdf"] = npu_device

    target_device = npu_device or result["device"]["bdf"]
    device_args = ["--device", target_device] if target_device else []
    aie = _run_command(
        ["xrt-smi", "examine"] + device_args + ["--report", "aie-partitions"],
        "xrt_smi.aie_partitions",
        errors,
    )
    result["aie_partitions_returncode"] = aie["returncode"]
    if aie["returncode"] == 0:
        result["contexts"] = parse_xrt_smi(aie["stdout"])

    return result


def _run_command(
    cmd: list[str],
    probe: str,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
        )
        if proc.returncode != 0:
            message = _compact_output(proc.stderr or proc.stdout)
            if message:
                errors.append({"probe": probe, "message": message})
        return {
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as exc:
        errors.append({"probe": probe, "message": str(exc)})
        return {"returncode": None, "stdout": "", "stderr": str(exc)}


def _parse_xrt_device(output: str) -> dict[str, str | None]:
    device_re = re.compile(
        r"\|\s*\[(?P<bdf>[0-9a-fA-F:.]+)\]\s*\|\s*(?P<name>[^|]+?)\s*\|"
    )
    for line in output.splitlines():
        match = device_re.search(line)
        if match:
            return {
                "bdf": match.group("bdf"),
                "name": match.group("name").strip(),
            }
    return {"bdf": None, "name": None}


def detect_npu_device(npu_device: str | None = None) -> dict[str, str | None]:
    """One-shot read of NPU device identity (BDF + name) from ``xrt-smi examine``.

    For callers that only need the device label (for example the live TUI header)
    rather than a full snapshot. Returns ``{"bdf": ..., "name": ...}`` with
    ``None`` values when ``xrt-smi`` is absent, the probe fails, or no device is
    reported. Never raises. ``name`` is whatever ``xrt-smi`` reports (e.g.
    ``RyzenAI-npu5``) — a detected fact, not a marketing platform label.
    """
    device: dict[str, str | None] = {"bdf": None, "name": None}
    if shutil.which("xrt-smi") is None:
        if npu_device:
            device["bdf"] = npu_device
        return device
    examine = _run_command(["xrt-smi", "examine"], "xrt_smi.examine", [])
    if examine["returncode"] == 0:
        device = _parse_xrt_device(examine["stdout"])
    if npu_device and device["bdf"] is None:
        device["bdf"] = npu_device
    return device


def _compact_output(output: str | None) -> str | None:
    if not output:
        return None
    text = " ".join(output.split())
    return text[:500] if text else None


def snapshot_main(args: Any) -> int:
    snapshot = build_snapshot(
        bench_dir=args.bench_dir,
        npu_device=args.npu_device,
        gpu_idle_busy_pct=args.idle_busy_pct,
        gpu_prefill_power_w=args.prefill_power_w,
        gauge_hysteresis_samples=args.hysteresis_samples,
    )
    write_snapshot(snapshot, args.out)
    return 0
