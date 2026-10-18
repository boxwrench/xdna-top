---
title: The Strix Halo XDNA2 NPU Envelope for LLM Work
date: 2026-10-17
kernel: 7.3.0-rc6-perfopt+
hardware: AMD RYZEN AI MAX+ 395 (Strix Halo, gfx1151)
xrt_version: 2.25.00
headline: On one Strix Halo box the XDNA2 NPU runs 3.2-26.5 TFLOPS of bfp8 GEMM depending on rows, is 1.55-5.63x slower than the iGPU on matched dense GEMMs, and helps LLM work only when the result can be consumed late.
---

# The Strix Halo XDNA2 NPU Envelope for LLM Work

**What this is.** A measured envelope for the XDNA2 NPU on Strix Halo, written for
people reading `xdna-top` telemetry and deciding what to place on the NPU. It is a
synthesis of measurements on one box, not a new benchmark: every number carries
its shape, data format, and safety state. The full tables, method, and the
`npu-hold` script are in REM's
[measured NPU map](https://github.com/boxwrench/REM/blob/main/docs/strix-halo-npu-measured-map.md).
The separate question of a background job beside an interactive iGPU model already
has its own experiment — [NPU vs CPU background-job contention](npu-igpu-contention.md)
— and is not repeated here.

## The box behind these numbers

| | |
|---|---|
| SoC | AMD Ryzen AI MAX+ 395 (Strix Halo), 128 GB LPDDR5x |
| iGPU | Radeon 8060S, `gfx1151`, 40 CUs |
| NPU | XDNA 2 / NPU5 `RyzenAI-npu5`, aie2p 6x8, firmware 1.1.2.65 |
| Software | XRT 2.25.00, `amdxdna` 7.0.0-rc1, ROCm 7.2.2, kernel `7.3.0-rc6-perfopt+`, `iommu=pt` |
| Format | bfp8 (8-bit block floating point), 128x64x128 tiles |
| Safety | FCLK 2000 MHz, NPU pmode `performance`, NPU `power/control=on` |

The installed fixtures have no native BF16 or INT8 NPU kernel; bfp8 is the
working format and it is approximate. GPU rates are BF16. NPU times are host wall
time (launch plus completion wait), so they include dispatch; per-call copy costs
are called out separately. Where a conclusion is arithmetic rather than a
measurement, it is marked **[reasoning]**.

## Rate is a function of rows, not a single number

"27 TFLOPS" is true only at the shape the array was built for. At K=2560, N=1024,
bfp8:

| M | NPU ms | NPU TFLOPS | GPU BF16 ms | GPU TFLOPS |
|---:|---:|---:|---:|---:|
| 128 | 0.211 | 3.2 | 0.063 | 10.7 |
| 256 | 0.216 | 6.2 | 0.062 | 21.5 |
| 512 | 0.225 | 11.9 | 0.121 | 22.2 |
| 4096 | 0.811 | 26.5 | 0.524 | 41.0 |

Rows 128–512 use the 8-core small-M kernel; M=4096 uses the 32-core shape. On that 8-core kernel, a
request for fewer than 128 rows still executes as 128, so a drafter's 8-row block
gets about **0.2 useful TFLOPS** while the GPU gets 0.64. Across matched dense
GEMMs the NPU is **1.55–5.63× slower than this iGPU**, and **3.2–3.5× slower at a
block shape**.

At the dense 27B FFN shapes, an open-toolchain kernel reaches 17.3–19.3 TFLOPS
(M=512/2048/4096, K=5120 or 17408). Mike Alani's shipped kernel reaches
26.5–27.7 TFLOPS, but only at M=4096, K=2560 (it can K-chunk for larger K). The
same session rebuilt with mlir-aie 1.4.4 + Peano 22 lands at 17.3–19.6 TFLOPS; the
~35% deficit is in the core program, and the open toolchain we tested did not
close it. This is the installed-toolchain envelope, not a hardware ceiling.

Two other costs are part of the envelope:

- **Per-call copies:** ~0.86 ms to encode A and decode C per call at the GLM
  q/k/v shapes. Per-GEMM calls lose; calls must be batched per layer chunk.
- **Numerical error:** bfp8 with BF16 accumulation is 1.0% / 1.2% / 2.6% max
  relative error at K=2560 / 4096 / 8192. Any integration needs a quality gate.

## Contention goes both ways

The NPU and iGPU share one LPDDR5x bus, so "NPU rate alone" is not the rate you
get under load. Measured at 0/50/100% NPU duty: the iGPU loses **1.0–3.6%** during
dense 27B prefill and **3.7% / 10.1%** during a 32K MoE prefill at 50% / 100% duty;
the NPU loses **13–28%** under the same prefill. Ling-3.0 decode loses 4.8% under
a small-M NPU load and 16.4% under a large-M load. The
[contention experiment](npu-igpu-contention.md) covers the background-job case;
the loaded columns above are what an offload design has to use.

## What `xdna-top` can and cannot see

This matters when reading the numbers above: most of the envelope is not visible
from telemetry.

| Signal | Observable? | How / why |
|---|---|---|
| NPU per-context activity, submissions, completions | ✅ yes | `xrt-smi examine --report aie-partitions`, diffed over time; `snapshot` / `record` / `assert` |
| Which process owns an NPU context | ✅ yes, within the driver's reporting | context → PID mapping in a snapshot |
| Overlap between NPU and iGPU work | ✅ yes | activity windows plus GPU busy state |
| A per-call NPU busy counter | ❌ no | submission/completion cadence is a proxy, not an array-busy signal |
| NPU-isolated power | ❌ no | `xrt-smi` reports Estimated Power N/A; no NPU hwmon; `amdgpu` `power1` is package PPT |
| NPU temperature | ❌ no | no hwmon node under the NPU BDF |
| Per-tile / array utilization | ❌ no | only existence and timing of activity |
| Achieved DRAM bandwidth accounting | ❌ no clean counter | treated as a probe on this stack |

Two consequences. First, you can prove *that* a job ran on the NPU and whether it
overlapped the GPU, but you cannot compute NPU perf/watt: any power claim is
package-level and includes the CPU and iGPU. Second, a headline TFLOPS number is a
host-timed GEMM, not a utilization reading — it does not tell you how close a
kernel is to the array's roof.

## The safety state is part of the measurement

The NPU needs the IOMMU (`amd_iommu=off` → `ENODEV`). On a stock kernel,
`iommu=pt` costs the iGPU 7.9% / 7.3% prefill at 32K / 64K; the PerfOpt series
(queued for Linux 7.4) recovers it to 390.3 / 378.3 tok/s, within run noise of
turning the IOMMU off. A fixed 2000 MHz FCLK adds 1.6–2.2% prefill on its own.

Concurrent GPU+NPU work needs the hold: FCLK at level 7 (2000 MHz), NPU pmode
`performance` (not turbo), and `power/control=on`. Without it, the combination is
reported to hard-hang ([amd/xdna-driver#1605](https://github.com/amd/xdna-driver/issues/1605)).
With it, a 71-minute soak (NPU at 100% duty for 64.5 minutes) and the offload runs
had 0 machine checks, 0 `amdxdna` timeouts, and 0 GPU resets. That is a data point,
not a guarantee. **If `xdna-top` shows NPU activity while FCLK is on `auto`, that
is not a safe overlap configuration** — check the hold before drawing conclusions
from the run.

## What the envelope says to do

- **Place a second latency-tolerant job on the NPU.** This is the measured win:
  ~3× the throughput of spare CPU cores at ~3× total-board perf/watt, with
  slightly less iGPU decode loss than the CPU arm. See the
  [contention experiment](npu-igpu-contention.md).
- **Do not expect decode or draft-head speedups.** The work is memory-bound; the
  NPU is slower than the iGPU there at every measured shape.
- **Do not build a block drafter on it as-is.** Existing kernels pad a small block
  to 128–512 rows, and retention gains stay below the gate.
- **Treat dense prefill as open only with deferred consumers.** A synchronous 27B
  split was correct but 6–8% slower because only ~43% of NPU time was hidden.
- **Skip large-MoE prefill and sparse-attention indexers.** Their offloadable
  shares are too small on this box.
- **Call side models untested.** Encoder/STT/embedding placement beside a main
  model has desk analysis but no measurement here.

For workload *shape* guidance rather than device numbers, pair this with
[Workload patterns](../npu/workload-patterns.md) and
[Why the NPU?](../npu/why-the-npu.md).

## Boundaries

One box, one kernel/XRT/firmware stack, bfp8 only; the deferred-consumer result is
gufo's, on their engine and model, not reproduced here; and no NPU power,
temperature, or per-tile utilization is observable. These are conditions to
re-measure, not constants.

## Credits

- **Mike Alani** ([mikealanni/gufo](https://github.com/mikealanni/gufo), upstreaming
  as [gufo-org/gufo#568](https://github.com/gufo-org/gufo/pull/568)) wrote the bfp8
  GEMM and the deferred-consumer offload design, and documented the FCLK hold.
- **Mario Limonciello** (AMD) wrote the IOMMU PerfOpt series.
- **hipfire** ([warpfront/hipfire](https://github.com/warpfront/hipfire)) published
  the safety rules this envelope was measured under.
