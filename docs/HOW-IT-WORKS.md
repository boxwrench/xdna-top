# How it works

This is the guided tour. It assumes you know what a terminal is and that your
machine has an AMD Strix Halo APU in it - nothing else. By the end you'll know
exactly where every number on the screen comes from, and just as importantly,
what those numbers can and cannot tell you.

## The two engines

A Strix Halo APU carries two compute engines that matter here:

**The iGPU** (Radeon 8060S, architecture `gfx1151`) is a conventional GPU. It
has occupancy, clocks, power draw — the familiar dials — and the `amdgpu`
kernel driver dutifully tracks them. A GPU executes work *temporally*: shader
cores grind through a queue, and "busy %" honestly summarizes how saturated
they were over a sampling window.

**The NPU** (AMD XDNA, enumerated as `RyzenAI-npu5`) is a different engine: a
*spatial dataflow* fabric. Instead of cores marching through instructions, an
array of AI-engine tiles is configured into a layout that data streams
*through*. Work isn't scheduled onto it moment-to-moment; a workload claims a
**hardware context** (a partition of tiles), and then jobs are *submitted*
through that context. This architectural difference has a consequence that
shapes this whole tool:

> **Be precise about NPU percentages.** A driver may expose a direct sensor
> such as column utilization, and `xdna-top` should report that when the kernel
> supports it. But a generic "NPU utilization %" is not the same thing as proof
> that a specific request used the NPU. For that, per-context ownership and
> submission/completion deltas are the stronger evidence.

## Reading AMD GPUs: per-device sysfs

Discovery enumerates numeric `cardN` entries under `/sys/class/drm`, verifies
AMD vendor `0x1002` and an `amdgpu` driver binding, and binds each device to its
own `hwmon` directory named `amdgpu`. It ignores connectors, other drivers,
HIP visibility masks, and NPU availability. Cards are not aggregated, and
busy/power never come from different devices. Names prefer `product_name`, then
`/usr/share/libdrm/amdgpu.ids` (device + revision), then PCI identity.

| Reading | Device-relative source | Conversion / limit |
|---|---|---|
| Busy | `gpu_busy_percent` | Driver percentage; no process attribution |
| VRAM | `mem_info_vram_used`, `mem_info_vram_total` | Bytes |
| GTT | `mem_info_gtt_used`, `mem_info_gtt_total` | Bytes; host-memory budget, not VRAM |
| Edge temperature | `hwmon/.../temp1_input` | Millidegrees → °C; not hotspot |
| Power | `power1_average`, falling back to `power1_input` | Microwatts → W |
| Cap | `power1_cap` | Microwatts → W |
| Shader clock | `freq1_input` | Hz → MHz; current **sclk sample**, not a maximum |
| PCIe | `current_link_speed`, `max_link_speed`, `current_link_width`, `max_link_width` | Recognized GT/s values map to Gen1–6; lane counts |

These mappings follow the kernel's [hwmon documentation](https://docs.kernel.org/gpu/amdgpu/thermal.html)
and [memory sysfs documentation](https://docs.kernel.org/gpu/amdgpu/driver-misc.html).
**APU power includes CPU/SoC**: it is not isolated GPU watts. PCIe links are not
meaningful for an integrated GPU. Conservative PCI identity hints currently
recognize Strix Halo (`1586`), the verified desktop iGPU (`13c0`),
Navi 31 (`744c`) and Navi 48
(`7551`); other devices have `is_apu: null`, with the general APU caveat still
shown. Unknown type does not block any sensor. No memory-size heuristic guesses
a device's type. Recognized APU PCIe numbers remain raw in JSON with
`pcie_meaningful: false`; the TUI labels them inapplicable and the exporter
omits those PCIe samples.

On Strix Halo, VRAM can be a small firmware carve-out while a model lives in
GTT. Keep them separate. Each card's GTT total is a budget backed by the same
host RAM; adding budgets across cards would count that RAM repeatedly. These
are device allocation counters, not proof that a particular model owns memory.

Every read is guarded: absent, unreadable, removed, malformed or unknown-speed
files yield `null` (`N/A` in the TUI). Each card carries an `unavailable` field
list; `degraded` means busy or power is missing. Optional missing sensors do not
hide readable fields. No percentage or power value is fabricated, including
with the legacy private `pessimistic_fallback` argument.

The gauge discovers devices once for its lifetime and polls read-only at nominal
5 Hz. Each card keeps 300 samples for nominal 60-second sparklines; a slow NPU
probe can lengthen that wall-clock window. Missing samples use visible gaps,
display buckets retain observed peaks to fit the pane,
and power history uses an explicitly marked automatic scale, not a claimed
hardware maximum. Multi-card panes need a taller terminal to show every row.

`--json`, snapshot telemetry and record readings share a `gpus` list.
Snapshot/record schema **1.1** adds per-card telemetry; older 1.0 captures remain
readable. Legacy scalar `gpu_busy_pct`, `gpu_power_w`, `state`, `igpu_degraded`,
and `xdna_igpu_*` metrics describe the **first numeric amdgpu card**, which may
be discrete. `state` is a threshold heuristic, not proof of an inference phase.
An explicit `--bench-dir/e0_sysfs.json` still overrides only those legacy
scalars; the per-card list always reports actual per-device sysfs readings.

## Reading the NPU: IOCTLs first, XRT where it helps

The preferred NPU path is to talk to the kernel driver directly through the
DRM/AMDXDNA IOCTL interface exposed by `/dev/accel/*`.

If you are new to this layer, think of an IOCTL as a structured question a
userspace program asks a device driver. Reading a normal file says "give me
bytes." An IOCTL says "run this driver-specific operation with this C-shaped
data structure." For `xdna-top`, that direct path can answer questions such as:

- Which accel device is present?
- What AMDXDNA DRM driver version is this?
- Does this kernel expose NPU sensors?
- Is there a direct power or column-utilization sensor?

That is better than shelling out to a command-line tool when the kernel already
has the answer.

There is still one signal `xdna-top` cares about that the direct sensor path may
not replace yet: **per-context attribution**. We need to know which PID owns a
context and whether that context's counters moved. Today, AMD's XRT tooling
exposes that table:

The `amdxdna` driver exposes the NPU through AMD's XRT runtime, and XRT ships
a CLI: `xrt-smi`. One report turns out to be gold:

```
xrt-smi examine --report aie-partitions
```

It dumps the NPU's hardware-context table: which **PID** owns each context, and
cumulative **submission** and **completion** counters per context. Those two
counters are the request-attribution story:

| Observation across two samples | Meaning |
|---|---|
| Counters static | Context exists but is **idle** |
| Submissions incrementing | Context is **actively executing** work |
| Submissions > completions | Jobs are **in flight at this instant** |

`xdna-top` samples the context report, diffs counters against the previous
sample, and derives each context's state from the delta. That's the "ACTIVE"
badge - not a guess, a measurement. As the direct IOCTL backend grows, `xrt-smi`
becomes a compatibility and context-attribution source rather than the preferred
low-level telemetry path.

## A real capture, annotated

These are excerpts from the actual calibration run that validated the signal
(an LLM running on the NPU via a local inference server; full raw capture in
the repo's bench data).

**Idle** — the model is loaded, a context is held, nothing is executing:

```
PID      CTX   SUBMISSIONS   COMPLETIONS   STATUS
93941     1        15 268        15 268    Active
```

Note the trap: the *status column says "Active"* because the context is
allocated — but submissions sat frozen at 15 268 across samples. By counter
delta: **idle**. (This is exactly why naive parsing misleads, and why the
delta logic exists.)

**Under load** — the same context while the model generates tokens:

```
PID      CTX   SUBMISSIONS   COMPLETIONS   STATUS
93941     1        15 461        15 460    Active    ◆ in-flight
```

Submissions climbed 15 268 → 15 461 across the generation, and at this instant
submissions lead completions by one: a job is physically executing as the
sample was taken. PID 93941 is the inference server process — attribution for
free.

## Fusing it

Each poll tick, the gauge layer takes the best available readings from each
backend, stamps them with a shared timestamp, and emits a single fused record.
Today that means iGPU values from sysfs and NPU context deltas from XRT. The
roadmap adds direct AMDXDNA IOCTL probing for NPU device, driver, power, and
column-utilization sensors.

The TUI renders the fused record, and `--json` mode prints it raw so you can
pipe it into anything.

If a source is missing - no sensor support, no `xrt-smi` on PATH, unreadable
sysfs node, a parse failure after an XRT update - that pane **degrades and says
so** rather than crashing or, worse, silently showing stale numbers. A
monitoring tool's first duty is to not lie about whether it's monitoring.

## From live view to evidence

The live TUI answers "what is happening right now?" The evidence commands answer
"can I prove what happened, later, to someone else?" They are all thin views over
the *same* fused reading, so nothing new is measured - it is just captured in a
versioned, machine-readable shape. Every artifact carries a `schema_version` and
the same degraded flags as the live view.

- **`xdna-top snapshot`** writes one JSON object: host, devices, backends, one
  fused reading, and degraded reasons. It is the point-in-time record everything
  else compares against. On stacks that export the `amdxdna` debugfs nodes it
  also records the NPU's active DPM clock state (npuclk/hclk MHz) and SMU
  powerstate, read directly from debugfs and independent of `xrt-smi` — a
  clock-state power *level*, never a utilization %, and `unavailable` (with a
  reason) when the nodes can't be read.
- **`xdna-top env-report <file>`** renders a paste-ready Markdown summary from a
  captured snapshot *or* a recording. It never re-probes the machine, so the
  report stays reproducible.
- **`xdna-top record`** streams typed JSONL events (`meta`, then `telemetry` per
  sample, then `summary`) over a window. Because it captures counter movement
  *over time*, it is the strongest evidence that the NPU actually did work.
  **`xdna-top mark "<label>"`** appends a marker event so a script can tag exactly
  when a trial or request happened.
- **`xdna-top assert <file> --require-...`** turns an artifact into a CI gate: each
  named check prints its observed value next to its requirement and the process
  exits non-zero if any fails. A missing signal fails honestly; it is never
  converted into a guessed pass.
- **`xdna-top compare a.json b.json`** diffs two snapshots and surfaces only
  high-signal platform drift (kernel, accel device, backend/sensor availability,
  NPU BDF, sysfs paths, degraded regressions). **`xdna-top baseline save/check`**
  wraps that into a named known-good canary you can re-check after a kernel, BIOS,
  distro, or XRT update.
- **`xdna-top exporter`** serves the same fused reading as Prometheus metrics at
  `/metrics`, read fresh on each scrape, so a workload's NPU+iGPU telemetry can
  be graphed over time in Prometheus/Grafana. A failed read is reported as
  `up=0` rather than a vanished target, and it binds to loopback by default (the
  `/metrics` endpoint is unauthenticated). Needs the optional `[exporter]`
  extra; see [EXPORTER.md](EXPORTER.md).
- **`xdna-top workload-check --chat-url <url> --model <name>`** is the supervised
  evidence command: it reads the NPU contexts, sends one short request to an
  OpenAI-compatible endpoint, reads the contexts again, and reports the
  per-context submission/completion deltas across that window — e.g. "observed
  PID 1234 context 1 submission_delta=42 during request window". It deliberately
  stops at the measurement: a concurrent workload can move the same counters, so
  the output ships that caveat and never claims the request *caused* the NPU
  work. Endpoint availability, the model response summary, and the deltas are all
  distinct fields, and the exit code reflects only whether the endpoint
  responded — not whether the NPU was active. A real capture against an
  NPU-backed FastFlowLM endpoint is committed at
  [../docs/captures/workload_check_flm_npu.json](captures/workload_check_flm_npu.json)
  (7 contexts moved, `max_submission_delta=205`).

The discipline throughout is *measured language*. A recording lets you say
"observed PID 1234 context 1 submission_delta=42 during the request window" - which
is true and checkable - rather than "the request ran on the NPU", which a
concurrent workload could have faked. The evidence model exists precisely so the
claim never outruns the measurement.

## Honest limits

- **Direct sensors depend on kernel support.** AMDXDNA DRM IOCTLs can expose
  richer NPU signals on newer kernels, but older stacks may not support the
  sensor query. That is a degraded result, not a reason to invent data.
- **The xrt-smi output format is still a dependency for context attribution.**
  Until equivalent per-context PID/counter data is available through direct
  probing, an XRT release that reshapes the report can break the context pane.
  It will fail visibly, and pinned-format test fixtures guard the parser.
- **5 Hz sampling bounds what you can see.** A burst shorter than ~200 ms can
  fall between samples; cumulative counters still record that it happened
  (the next delta jumps), but its precise timing is smeared.
- **Sensor values are not request attribution.** A column-utilization or power
  sensor can say the NPU was doing something, but a concurrent workload could be
  responsible. PID-owned context deltas remain the better proof for a supervised
  request.
- **If AMD fixes `amd-smi` on gfx1151**, the iGPU half of this tool becomes
  redundant - happily. The NPU half and the unified view remain the point.

(The kernel also emits `amdxdna` ftrace tracepoints per job, which could
corroborate the delta method — but tracefs needs elevated privileges, and
xdna-top keeps its zero-root promise.)
