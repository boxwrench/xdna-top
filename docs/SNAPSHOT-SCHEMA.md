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
      "contexts": "xrt_smi",
      "power_state": "debugfs"
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

`power_state` is the first direct-AMDXDNA signal: the NPU's active DPM
(Dynamic Power Management) clock/voltage, read from
`/sys/kernel/debug/accel/<bdf>/{dpm_level,powerstate}`. It is an **additive,
optional** field (no `schema_version` bump): present only with a driver that
exports those debugfs nodes (the staging `amdxdna.ko`; the mainline/DKMS driver
does not — see amd/xdna-driver#1447) and only when readable (debugfs is usually
root-only). When unavailable, `devices.npu.power_state.available` is `false`
with a `reason`, and `backends.npu.signals.power_state` is `null`. The value is
the active **clock/voltage power state**, never a utilization percentage.
Consumers should treat unknown keys as forward-compatible and ignore them.

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
    "power_state": {
      "available": true,
      "source": "debugfs",
      "reason": null,
      "powerstate": "SMU power ON",
      "dpm": {
        "active": { "index": 7, "freq_mhz": 847, "volt_mv": 1600 },
        "max": { "index": 7, "freq_mhz": 847, "volt_mv": 1600 },
        "levels": 8
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

`process_name` is a best-effort enrichment of the owning PID, resolved read-only
and unprivileged from `/proc/<pid>/comm` (falling back to the `cmdline` basename)
— not from `xrt-smi`. It is `null` when the process cannot be read (it has
exited, belongs to another user, or `/proc` is unavailable). The context
`source` tag refers to the counter data; the name is derived locally and is a
convenience for attribution, never a measured value.

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

## Record Stream

`xdna-top record` writes a JSONL stream rather than a single object. Each line is
a self-describing event with a `type` and `schema_version`, so consumers (such as
`xdna-top assert`) can select events by type without positional assumptions.

The stream is framed by a `meta` header line and a `summary` footer line, with
zero or more `telemetry` lines in between:

```json
{"type":"meta","schema_version":"1.0","kind":"xdna-top.record","started_at":"2026-06-13T02:16:39Z","params":{"duration_s":60.0,"interval_s":0.2},"host":{}}
{"type":"telemetry","schema_version":"1.0","ts":123.4,"reading":{},"contexts":[]}
{"type":"summary","schema_version":"1.0","kind":"xdna-top.record","started_at":"2026-06-13T02:16:39Z","ended_at":"2026-06-13T02:17:39Z","samples":301}
```

### `meta`

Written first. Carries the record schema version, the same `host` facts block as
a snapshot, and the requested capture window in `params`.

### `telemetry`

One per sample.

- `ts`: capture timestamp for the sample
- `reading`: the fused gauge reading, including `igpu_degraded` / `npu_degraded`
  flags so degraded samples stay visible and machine-readable
- `contexts`: per-context NPU data, each tagged with its `source` backend (for
  example `xrt_smi`). An empty list means no contexts were observed, including
  when the NPU source is unavailable

### `summary`

Written last, including after a `KeyboardInterrupt`, so a partial recording still
ends with a valid summary line. Reports `ended_at` and the number of `samples`
written.

A `duration` of `0` produces exactly one `telemetry` sample. Each line is flushed
as it is written, so the stream is tail-able and a partial artifact remains valid
if a recording is interrupted.

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

`xdna-top compare before.json after.json` reports only these high-signal changes
and, like `git diff --exit-code`, exits `0` when none are found, `1` when any are
found, and `2` on an unreadable snapshot. Each line is tagged `CHANGED` or
`REGRESSION`; capability losses (sensor support lost, accel device disappeared,
NPU BDF lost, `degraded.overall` `false`→`true`, a working `aie-partitions`
report breaking while contexts depend on `xrt-smi`) are tagged `REGRESSION`,
while neutral drift and recoveries are tagged `CHANGED`.

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

`xdna-top assert` accepts either a `snapshot` JSON object or a `record` JSONL
stream and selects the right reading per artifact type. It exits `0` only when
every requested check passes, `1` when any check fails, and `2` on a usage error
(no `--require-*` flags) or an unreadable artifact.

| Check | Snapshot reading | Record reading |
|---|---|---|
| `--require-npu` | `devices.npu.detected` is true | at least one telemetry sample has NPU contexts |
| `--require-npu-sensors` | `devices.npu.driver.supports_sensors` is true | unavailable (snapshot-only field) |
| `--require-context-source` | `backends.npu.signals.contexts` is non-null | telemetry contexts carry a `source` |
| `--require-igpu` | `telemetry.igpu_degraded` is false | no telemetry sample is iGPU-degraded |
| `--require-not-degraded` | `degraded.overall` is false | no telemetry sample is degraded |
| `--require-npu-activity` | `telemetry.npu_active` is true | max context submission delta > 0, or any active sample |

A check that needs a signal the artifact does not carry (for example sensor
support on a record stream) fails and reports the value as unavailable rather
than guessing.
