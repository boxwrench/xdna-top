# Platform capture: XDNA1 (Phoenix / Hawk Point)

A Ryzen AI capture profile for the first-generation NPU, the part that
FastFlowLM / Lemonade skip. Short version: **`xdna-top` works on gen-1
unmodified.** NPU detection, per-context attribution against a live hardware
context, and iGPU sysfs auto-discovery all succeed, and `degraded.overall` is
`false`.

Raw artifacts for this profile live in [`captures/`](captures/).

## Test platform

| | |
|---|---|
| Machine | HP Victus, Ryzen 7 8845HS w/ Radeon 780M |
| NPU | `RyzenAI-npu1`, arch `aie2`, topology `6x5`, BDF `0000:06:00.1` |
| NPU firmware | `1.5.5.391` |
| iGPU | Radeon 780M (gfx1103) |
| OS / kernel | Ubuntu 25.10 / 6.17.0-6-generic |
| Driver | `amdxdna` 2.25.0_20260623 |
| Stack | XRT 2.25.0, xdna-top 0.2.0, Python 3.13.7 |

## What works out of the box

### NPU detected; device row parses
`xrt-smi examine` reports `RyzenAI-npu1 | aie2 | 6x5`. Snapshot `devices.npu`:
`detected: true`, `name: RyzenAI-npu1`, `bdf: 0000:06:00.1`,
`report_shape.has_aie_partitions: true`.

### Per-context attribution against a LIVE context
A sustained 512x512x512 int16 matmul loop was run on the NPU to hold a hardware
context open. While busy, `xdna-top snapshot` captured it (see
[`captures/snapshot_xdna1_busy.json`](captures/snapshot_xdna1_busy.json)):

```json
{ "pid": 3429337, "ctx_id": 1, "submissions": 1865, "completions": 1864,
  "status": "Active", "process_name": "python3", "source": "xrt_smi" }
```

`report_shape.has_hw_contexts: true`, `context_count: 1`, and `--json` reported
`npu_active: true` (the submissions counter incrementing and leading completions
by one in-flight job). Activity is reported as submission-counter deltas /
active hardware contexts, never as a utilization percentage.

**The XDNA1 `aie-partitions` HW-context table uses the same column schema the
parser already expects** (PID / Ctx ID / Submissions row, then Process Name /
Status / Completions row). So submission-delta logic and `assert --between`
carry over to gen-1 with no parser change. Idle vs busy raw reports:
[`captures/aie-partitions_idle.txt`](captures/aie-partitions_idle.txt),
[`captures/aie-partitions_busy.txt`](captures/aie-partitions_busy.txt).

### iGPU telemetry generalizes off Strix Halo
`discover_sysfs` found the Phoenix 780M endpoints with no hint:
`busy = /sys/class/drm/card2/device/gpu_busy_percent`,
`power = /sys/class/hwmon/hwmon9/power1_input`. These are the driver's own
sysfs counters (no estimation). Idle reading: 2% busy, 12.1 W.

## Gen-1 specifics / honest gaps

- **NPU sensors absent:** `driver.supports_sensors: false`; `power_w` and
  `column_utilization_pct` are `null`. This matches the Strix Halo situation and
  is gated on the direct AMDXDNA telemetry IOCTLs
  ([amd/xdna-driver#1447](https://github.com/amd/xdna-driver/issues/1447)) — not
  a gen-1 regression, a shared gap.
- **N/A columns on firmware 1.5.5.391:** the HW-context table emits `GOPS`,
  `FPS`, `Latency`, and `Total Memory Usage` as `N/A`. If those are surfaced for
  Strix Halo later, gen-1 will need an `N/A`-tolerant path. `Memory Usage` and
  `Instr BO` (`32 KB` observed) are present in the report but not parsed today.

## One environment gotcha worth a graceful-degradation hint

Not gen-1 specific, but it makes `xdna-top` report a false **"NPU absent"** on a
fresh box. An unprivileged `xrt-smi` hits:

```
xrt-smi ERROR: mmap(... len=67108864 ... flags=0x2011) failed (err=-11): Resource temporarily unavailable
```

The NPU firmware region is mmap'd with `MAP_LOCKED` (flags
`0x2011 = MAP_SHARED|MAP_FIXED|MAP_LOCKED`), checked against `RLIMIT_MEMLOCK`.
The default soft **and hard** memlock here is 8 MB (`ulimit -l` = 8192), below
the 64 MB requested, so `mmap` returns `EAGAIN`. The user cannot self-raise
(hard cap = 8 MB).

Isolation test (confirms it is memlock, not root): running as the same UID but
with `prlimit --memlock=unlimited`, `xrt-smi examine` succeeds and reports
`RyzenAI-npu1 aie2 6x5`.

When this happens, `examine` fails and the snapshot records
`npu_reasons: ["xrt_smi_examine_failed"]` — i.e. the NPU looks absent. A small,
claims-precise improvement: when `examine` fails with an mmap/`EAGAIN` signature
and `ulimit -l` is low, print a hint to raise `RLIMIT_MEMLOCK` rather than
implying the hardware is missing.

User fix:

```
# /etc/security/limits.d/95-xdna.conf
@render   -   memlock   unlimited
```

(or run as root). Membership in the `render` group is also required for
`/dev/accel/accel0` (mode `crw-rw---- root render`).

## Reproduce

```bash
# NPU pane needs xrt-smi on PATH and memlock raised (see above)
xdna-top snapshot --out snapshot_idle.json          # NPU idle
# ...start an NPU workload, then:
xdna-top snapshot --out snapshot_busy.json          # live context
xdna-top --json                                     # fused reading
xrt-smi examine --report aie-partitions             # raw context table
```
