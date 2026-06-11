# Snapshot Schema Draft

This document defines the first draft of the `xdna-top snapshot` JSON artifact.
The schema should be explicit, versioned, and stable enough for `env-report`,
`compare`, and `baseline` to remain thin views over the same capture.

## Top-Level Shape

```json
{
  "schema_version": "1.0",
  "kind": "xdna-top.snapshot",
  "captured_at": "2026-06-11T12:34:56.789Z",
  "host": {},
  "commands": {},
  "backends": {},
  "devices": {},
  "telemetry": {},
  "degraded": {},
  "errors": []
}
```

## Fields

### `schema_version`

String. Required.

The snapshot schema version. Compare logic should use this field to decide
whether direct comparison is supported, needs migration, or should fail with a
clear message.

### `kind`

String. Required.

Always `xdna-top.snapshot` for this artifact.

### `captured_at`

String. Required.

UTC ISO-8601 timestamp for the capture.

### `host`

Platform facts that usually affect driver/runtime behavior.

```json
{
  "hostname": "workstation",
  "kernel": {
    "release": "6.14.0-example",
    "version": "#1 SMP PREEMPT_DYNAMIC ..."
  },
  "os": {
    "id": "ubuntu",
    "version_id": "24.04",
    "pretty_name": "Ubuntu 24.04.2 LTS"
  },
  "python": {
    "version": "3.12.3"
  }
}
```

### `commands`

Availability and version/probe status for external commands.

```json
{
  "xrt_smi": {
    "path": "/usr/bin/xrt-smi",
    "available": true,
    "version_output": "XRT build version ...",
    "examine_returncode": 0,
    "aie_partitions_returncode": 0
  }
}
```

Keep raw command output out of the default snapshot unless it is small and
stable. Prefer normalized summaries plus return codes. If raw output is needed
for debugging, add it under an explicit `raw` field later.

### `backends`

Telemetry backend provenance.

```json
{
  "npu": {
    "primary": "amdxdna_ioctl",
    "fallbacks_used": ["xrt_smi"],
    "signals": {
      "device": "amdxdna_ioctl",
      "sensors": "amdxdna_ioctl",
      "contexts": "xrt_smi"
    }
  },
  "igpu": {
    "primary": "sysfs",
    "signals": {
      "busy_pct": "sysfs",
      "power_w": "sysfs"
    }
  }
}
```

The preferred NPU backend is direct AMDXDNA DRM IOCTL probing through
`/dev/accel/*`. `xrt-smi` remains useful for compatibility and for per-context
PID/submission/completion data until equivalent direct attribution is available.

### `devices`

Detected device state.

```json
{
  "accel": {
    "entries": [
      {
        "path": "/dev/accel/accel0",
        "exists": true
      }
    ]
  },
  "npu": {
    "detected": true,
    "bdf": "0000:c6:00.1",
    "name": "RyzenAI-npu5",
    "driver": {
      "drm_version": {
        "major": 0,
        "minor": 7
      },
      "supports_sensors": true
    },
    "sensors": {
      "power_w": {
        "value": 4.2,
        "source": "amdxdna_ioctl"
      },
      "column_utilization_pct": {
        "value": 17.5,
        "source": "amdxdna_ioctl"
      }
    },
    "contexts": [
      {
        "pid": 1234,
        "process_name": "model-server",
        "ctx_id": 1,
        "submissions": 100,
        "completions": 100,
        "status": "Active",
        "source": "xrt_smi"
      }
    ],
    "report_shape": {
      "has_aie_partitions": true,
      "has_hw_contexts": true,
      "context_count": 1
    }
  },
  "igpu": {
    "busy_path": "/sys/class/drm/card1/device/gpu_busy_percent",
    "power_path": "/sys/class/drm/card1/device/hwmon/hwmon4/power1_input",
    "busy_path_exists": true,
    "power_path_exists": true
  }
}
```

The NPU section should preserve per-context PID and counter data because PID
attribution is the strongest available guard against overclaiming. Sensor values
and context counters should carry source/provenance when practical.

### `telemetry`

One fused telemetry reading using the same semantics as `xdna-top --json`.

```json
{
  "gpu_busy_pct": 12,
  "gpu_power_w": 15.027,
  "npu_active": false,
  "state": "ACTIVE",
  "igpu_degraded": false,
  "npu_degraded": false,
  "ts": 1781133202.123
}
```

### `degraded`

Machine-readable degraded status and reasons.

```json
{
  "overall": false,
  "igpu": {
    "degraded": false,
    "reasons": []
  },
  "npu": {
    "degraded": false,
    "reasons": []
  }
}
```

Reason strings should be stable enough for tests and reports, for example:

- `amdxdna_ioctl_unavailable`
- `amdxdna_drm_version_unsupported`
- `amdxdna_sensors_unavailable`
- `xrt_smi_not_found`
- `xrt_smi_examine_failed`
- `aie_partitions_report_failed`
- `igpu_busy_path_missing`
- `igpu_power_path_missing`

### `errors`

List of non-fatal probe errors.

```json
[
  {
    "probe": "amdxdna_ioctl.query_sensors",
    "message": "ioctl failed: invalid argument"
  },
  {
    "probe": "xrt_smi.aie_partitions",
    "message": "command timed out after 1.5s"
  }
]
```

Errors should not prevent snapshot creation unless the output path cannot be
written or the JSON artifact would be invalid.

## Compare Guidance

Initial compare should treat these as high-signal changes:

- `schema_version` changed
- `host.kernel.release` changed
- `backends.npu.primary` changed
- `backends.npu.signals.sensors` changed
- `devices.npu.driver.drm_version` changed
- `devices.npu.driver.supports_sensors` changed
- `commands.xrt_smi.available` changed
- `commands.xrt_smi.aie_partitions_returncode` changed from `0` to non-zero
  when contexts depend on `xrt_smi`
- `/dev/accel/accel0` disappeared
- `devices.npu.bdf` changed
- `devices.npu.report_shape.has_hw_contexts` changed
- `devices.igpu.busy_path` changed
- `devices.igpu.power_path` changed
- `degraded.overall` changed from `false` to `true`

## Assertion Guidance

Assertions should print names, observed values, and thresholds.

Example:

```text
PASS require-npu: observed devices.npu.detected=true
PASS require-npu-sensors: observed devices.npu.driver.supports_sensors=true
PASS require-context-source: observed backends.npu.signals.contexts=xrt_smi
FAIL require-igpu-power: observed devices.igpu.power_path_exists=false, required true
```

Assertions should never convert missing data into guessed values.
