# The Strix Halo platform: NPU, iGPU, and CPU

This is the hardware `xdna-top` was built to watch — a single AMD Strix Halo APU
with three compute engines (NPU, iGPU, CPU) sharing one pool of memory. This doc
is the *spec and capability reference*: what each engine is, what it's good at,
and — in the spirit of the rest of this repo — exactly which of its signals you
can actually read on this silicon today.

If you want the **measurement mechanics** (how the numbers on the TUI are
derived), read [HOW-IT-WORKS.md](HOW-IT-WORKS.md). If you want a worked
**use-case** built on this platform, see [REM](https://github.com/boxwrench/rem)
— an agent-memory co-processor that runs its background work on the NPU while the
iGPU stays free for the foreground model. This page is the substrate both of
those sit on.

> **Numbers vs. spec sheets.** Vendor TOPS and peak-bandwidth figures below are
> published specifications, cited to source. The *measured* figures (achieved
> bandwidth, contention, what sensors exist) are facts about **this box**
> (AMD RYZEN AI MAX+ 395, kernel 6.17.0-35-generic, XRT 2.21.75) and may differ
> on yours. `xdna-top` exists precisely so you can re-measure rather than trust.

---

## The chip in one paragraph

The **AMD Ryzen AI MAX+ 395** ("Strix Halo") is a *monolithic APU*: CPU, GPU,
NPU, and the memory controller all live on one die and share one physical pool of
RAM — there is no discrete VRAM partition.[^amd-blog] That single fact drives
everything `xdna-top` and REM measure: the three engines are **compute-isolated
but bandwidth-shared**. Each has its own execution resources, but they all pull
from the same memory bus, so the interesting question is never "is engine X fast"
in isolation — it's "what happens to engine Y when X is also running."

| Block | This chip | Architecture |
| --- | --- | --- |
| **CPU** | 16 cores | Zen 5 |
| **iGPU** | Radeon 8060S, 40 compute units, `gfx1151` | RDNA 3.5 |
| **NPU** | XDNA 2, enumerated `RyzenAI-npu5` | spatial dataflow, 50 TOPS |
| **Memory** | up to 128 GB LPDDR5x-8000, 256-bit bus | unified, shared |

Sources: AMD product page and architecture deep-dives.[^amd-product][^toms][^anand]

---

## The NPU (XDNA 2) — the engine nobody else monitors

The NPU is the reason this tool exists, so it gets the most detail.

**Architecture.** XDNA 2 is a **spatial dataflow** fabric, not a temporal
processor. Where a CPU or GPU marches execution units through an instruction
stream, the NPU configures a 2D array of **AI Engine tiles** into a pipeline that
data streams *through*.[^toms] On Strix Halo that array is **32 compute tiles,
organized 8 columns × 4 rows**.[^toms] Each tile is a VLIW, SIMD vector processor
paired with a scalar processor and local program/data memory. Relative to the
first-gen XDNA (20 tiles), XDNA 2 roughly doubled the MACs per tile and added
~1.6× on-chip memory to reach its performance target.[^toms]

**Performance.** AMD rates the XDNA 2 NPU at **50 peak TOPS**, and — notably — at
that figure in **both INT8 and Block FP16**, where most NPUs only hit their
headline number at INT8.[^amd-product][^anand] AMD cites a combined
platform figure of up to ~126 TOPS across NPU + CPU + GPU; the NPU's own share is
the 50 TOPS.[^amd-blog]

**Why it matters as a placement target.** The NPU's value on this platform is not
raw peak throughput — the iGPU has far more FLOPs. It is **perf/watt** and
**staying off the iGPU's lane**. For a latency-tolerant background job running
next to an interactive iGPU model, this box measures the NPU doing ~3× the
throughput of spare CPU cores at ~3× better total-board perf/watt, while
contending *slightly less* with the main lane — see
[experiments/npu-igpu-contention.md](experiments/npu-igpu-contention.md). That is
the opening REM builds on.

**What you can actually read about it (the honest part).** The NPU is the most
opaque engine on this chip. On this stack:

| Signal | Readable? | How |
| --- | --- | --- |
| Per-context activity (PID, submissions, completions) | ✅ yes | `xrt-smi examine --report aie-partitions`, diffed over time |
| NPU power (isolated) | ❌ no | `xrt-smi` reports Estimated Power: N/A |
| NPU temperature | ❌ no | no hwmon temp node under the NPU BDF |
| NPU memory bandwidth | ❌ no | no report or sysfs counter exposes it |
| Tile/column utilization | ⚠️ stack-dependent | newer AMDXDNA kernels may expose it; label precisely when present |

This is why `xdna-top` reports NPU work as **per-context submission/completion
deltas** (attributable, PID-owned) rather than a made-up "utilization %." The
full, machine-generated inventory for this box is in
[sensor-probe-6.17.0-35-generic.md](sensor-probe-6.17.0-35-generic.md);
regenerate it for your machine with `tools/probe_sensors.py`.

---

## The iGPU (Radeon 8060S, RDNA 3.5)

A conventional GPU: 40 RDNA 3.5 compute units, `gfx1151`, with the familiar dials
— occupancy, clocks, power. It executes work *temporally* (shader cores grind a
queue), so a "busy %" honestly summarizes saturation over a window. In the
local-AI context this is the **interactive "main lane"**: the engine you run the
foreground model on and want to protect.

**What you can read:** `gpu_busy_percent` and instantaneous power, straight from
the kernel's `amdgpu` sysfs nodes, at 5 Hz with 60-second sparklines — no
estimation. Note that `amd-smi` returns `N/A` for essentially everything on
gfx1151 ([ROCm #6035](https://github.com/ROCm/ROCm/issues/6035)), which is *why*
`xdna-top` reads sysfs directly. iGPU edge temperature is also exposed via the
amdgpu hwmon node.

---

## The CPU (16× Zen 5)

The general-purpose engine and, on this platform, the **baseline alternative** for
any offload decision: "should this background job go on the NPU, or just on spare
CPU cores?" The contention experiment treats a 4-thread CPU generation job as the
control arm against the NPU. CPU/package temperature (`k10temp`, Tctl) is readable
and is the useful signal for sustained-load / throttle checks.

---

## Memory — the one bus they all share

This is the crux of the whole platform. One unified pool of **LPDDR5x-8000 on a
256-bit bus**:

- **Theoretical peak:** **256 GB/s** (256-bit × 8000 MT/s).[^localai]
- **Measured in practice:** **~212–215 GB/s**.[^localai]

> **Reconciling the two numbers you'll see in these repos.** Earlier docs cited
> "~256 GB/s" (the theoretical peak) and "~212 GB/s" (a practical/achieved
> figure) as if they competed. They don't — they're the ceiling and the realistic
> achieved rate. Quote **256 GB/s theoretical / ~212–215 GB/s achieved** and say
> which you mean.

Because all three engines draw from this one bus, every concurrency result on this
chip is fundamentally a **memory-bandwidth contention** story. Whole-SoC package
power (PPT) is readable from the amdgpu `power1` rail, but it is the *combined*
CPU+iGPU+NPU rail — **per-engine power is not separable** on this stack, which is
why total-board perf/watt is the honest denominator and marginal-watt claims are
not made.

---

## The three engines at a glance

| | CPU (Zen 5) | iGPU (RDNA 3.5) | NPU (XDNA 2) |
| --- | --- | --- | --- |
| Execution model | temporal, general | temporal, massively parallel | spatial dataflow |
| Peak AI throughput | lowest | highest (FLOPs) | 50 TOPS (INT8 / BF16) |
| Best role here | general / control arm | interactive "main lane" | latency-tolerant background, perf/watt |
| Isolated power readable? | via package only | ✅ (gfx rail) | ❌ |
| Temperature readable? | ✅ (Tctl) | ✅ (edge) | ❌ |
| Activity attribution | OS-level | busy % | per-context submission deltas |

All three share one ~256 GB/s unified memory bus — the source of all contention.

---

## Where to go next

- **Measure your own box:** `xdna-top snapshot` + `tools/probe_sensors.py` →
  your machine's real capability inventory.
- **The concurrency numbers:** [experiments/npu-igpu-contention.md](experiments/npu-igpu-contention.md).
- **A use-case to build on:** [REM](https://github.com/boxwrench/rem) puts agent
  memory-maintenance on the NPU using exactly this telemetry as its evidence layer.

[^amd-product]: [AMD Ryzen AI Max+ 395 product page](https://www.amd.com/en/products/processors/laptop/ryzen/ai-300-series/amd-ryzen-ai-max-plus-395.html) — "50+ peak AI TOPS XDNA 2 NPU," up to 128 GB unified memory, LPDDR5x-8000.
[^amd-blog]: [AMD Ryzen AI Max+ 395: Breakthrough AI Performance](https://www.amd.com/en/blogs/2025/amd-ryzen-ai-max-395-processor-breakthrough-ai-.html) — monolithic APU, combined platform TOPS.
[^toms]: [Tom's Hardware — AMD deep-dives Zen 5 / RDNA 3.5 / XDNA 2](https://www.tomshardware.com/pc-components/cpus/amd-deep-dives-zen-5-ryzen-9000-and-strix-point-cpu-rdna-35-gpu-and-xdna-2-architectures/5) — XDNA 2 spatial dataflow, 8×4 = 32 tiles, 20→32 tile expansion, 2× MACs/tile, ~1.6× on-chip memory.
[^anand]: [AMD details Ryzen AI 300 series (XDNA 2 NPU)](https://www.anandtech.com/show/21469/amd-details-ryzen-ai-300-series-for-mobile-strix-point-with-rdna-35-igpu-xdna-2-npu/2) — 50 TOPS at INT8 and Block FP16.
[^localai]: [Ryzen AI Max+ 395 (Strix Halo) for Local AI](https://localaimaster.com/blog/strix-halo-ai-max-395-guide) — 256-bit bus, 256 GB/s theoretical, ~215 GB/s measured.
