# Negative-result probe: NPU telemetry sources (2026-06-11)

**Question:** Does this machine expose any NPU busy-time / power signal beyond
the per-context submission-counter deltas `xdna-top` already reads?

**Answer: No.** On kernel 6.17, the `amdxdna` driver exposes **no** per-device
telemetry sysfs node, and `xrt-smi examine --report platform` reports
`Estimated Power : N/A` **even mid-generation**. The two candidate "richer NPU
signal" sources for a future version are both dead on this stack. The only
unprivileged dynamic signal remains the submission-counter delta.

Hardware: AMD Strix Halo, NPU `[0000:c6:00.1] : RyzenAI-npu5`, kernel 6.17,
driver `amdxdna`, `xrt-smi` at `/usr/bin/xrt-smi`. Both transcripts below are
pasted verbatim.

---

## Transcript 1 — sysfs / driver exploration (read-only)

```text
$ ls -la /sys/class/accel/accel0/device/
# → standard PCI attributes only; no busy/usage/util/active/load/npu node:
  ari_enabled  broken_parity_status  class  config  consistent_dma_mask_bits
  current_link_speed  current_link_width  d3cold_allowed  device  device_type
  dma_mask_bits  driver -> ../../../../bus/pci/drivers/amdxdna  driver_override
  enable  firmware_node  fw_version  iommu  iommu_group  irq  link  local_cpulist
  local_cpus  max_link_speed  max_link_width  modalias  msi_bus  msi_irqs
  numa_node  power  power_state  remove  rescan  reset  reset_method  resource
  resource0  resource1  resource2  resource2_wc  resource4  resource5  revision
  subsystem  subsystem_device  subsystem_vendor  uevent  vbnv  vendor

$ find /sys/class/accel/ -maxdepth 6 \( -iname '*busy*' -o -iname '*usage*' \
      -o -iname '*util*' -o -iname '*active*' -o -iname '*load*' -o -iname '*npu*' \)
# → (no output)

$ find /sys -path '*amdxdna*'
/sys/module/amdxdna/...                       # module metadata only
/sys/kernel/tracing/events/amdxdna/xdna_job   # ftrace tracepoints (need tracefs/root)
/sys/kernel/tracing/events/amdxdna/mbox_irq_handle
/sys/kernel/tracing/events/amdxdna/mbox_set_head
/sys/kernel/tracing/events/amdxdna/mbox_set_tail
/sys/kernel/tracing/events/amdxdna/amdxdna_debug_point
# (no per-device telemetry attributes anywhere under the driver)

$ ls /sys/class/accel/accel0/device/accel/accel0/power/
async  autosuspend_delay_ms  control  runtime_active_kids  runtime_active_time
runtime_enabled  runtime_status  runtime_suspended_time  runtime_usage
# → generic runtime-PM residency counters only — NOT an engine busy %.

$ ls /sys/kernel/tracing/events/amdxdna/
amdxdna_debug_point  enable  filter  mbox_irq_handle  mbox_set_head
mbox_set_tail  xdna_job

$ xrt-smi examine --report platform        # baseline, NPU idle
------------------------------
[0000:c6:00.1] : RyzenAI-npu5
------------------------------
Platform
  Name                   : RyzenAI-npu5
  Power Mode             : Default
  Total Columns          : 8

Estimated Power          : N/A
```

**Conclusion (1):** there is no NPU analogue of the iGPU's `gpu_busy_percent`.
The only NPU-specific dynamic kernel signal is the `amdxdna` ftrace tracepoints,
which require elevated tracefs privileges and so are off-limits to a zero-root
tool.

---

## Transcript 2 — `--report platform` sampled during active generation

Load driver: `flm serve llama3.2:1b` (FastFlowLM, local NPU inference server).
The submission counter from `aie-partitions` is shown alongside each platform
sample to prove the NPU was genuinely executing (submissions climbing,
completions trailing by one = in-flight) at the moment each `Estimated Power`
was read.

```text
----- sample 1 (≈1s into gen) -----
  Name             : RyzenAI-npu5
  Power Mode       : Default
  Total Columns    : 8
Estimated Power    : N/A
  |389279 |1 |174 |0 |0 |N/A|
  |N/A    |Active |173 |0 | |N/A|

----- sample 2 (≈2s) -----
Estimated Power    : N/A
  |389279 |1 |302 |0 |0 |N/A|
  |N/A    |Active |301 |0 | |N/A|

----- sample 3 (≈3s) -----
Estimated Power    : N/A
  |389279 |1 |430 |0 |0 |N/A|
  |N/A    |Active |429 |0 | |N/A|

----- sample 4 (≈4s) -----
Estimated Power    : N/A
  |389279 |1 |558 |0 |0 |N/A|
  |N/A    |Active |557 |0 | |N/A|

----- sample 5 (≈5s) -----
Estimated Power    : N/A
  |389279 |1 |684 |0 |0 |N/A|
  |N/A    |Active |683 |0 | |N/A|

----- sample 6 (≈6s) -----
Estimated Power    : N/A
  |389279 |1 |812 |0 |0 |N/A|
  |N/A    |Active |811 |0 | |N/A|
```

**Conclusion (2):** `Estimated Power` is `N/A` across every mid-generation
sample, while submissions climb 174 → 812 (≈128/s) with completions one behind —
unambiguous live NPU work. `Power Mode : Default` is a configuration setting, not
telemetry. So `--report platform` contributes no usable NPU power or activity
signal on this XRT build; the submission-counter delta is the only true source.

The server was started solely for this probe and stopped afterward; the device
returned to `No hardware contexts running on device`.
