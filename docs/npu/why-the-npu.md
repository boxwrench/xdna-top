# Why the NPU? The AI Engine as a streaming dataflow fabric

A quick primer on *what kind of processor* the Ryzen AI NPU actually is, and
why that shape determines which jobs belong on it. This is the conceptual
backdrop for everything `xdna-top` measures. The architecture below is shared by
both generations — **XDNA1** (`aie2`, Phoenix / Hawk Point) and **XDNA2**
(`aie2p`, Strix / Strix Halo); the per-generation differences (TOPS ceilings,
which runtimes target which gen) are called out where they matter.

## What the AI Engine actually is

The "NPU" on a Ryzen AI part is not a fixed-function matmul box. It is the **AMD
AI Engine** — a spatial **dataflow** architecture: a 2D array of tiles, each
holding a vector processor, a scalar RISC core, ~32 KB of local SRAM, and
AXI4-Stream / cascade interconnects that pass data tile-to-tile. Its lineage is
the Xilinx **Versal** adaptive SoC, which scales the same fabric from tens to
hundreds of AI Engines. XDNA2 (Strix Halo) is the second generation of this
fabric; XDNA1 (Phoenix / Hawk Point) is the first, with a smaller array and a
lower TOPS ceiling but the same streaming character.

The defining property: instead of repeatedly fetching operands from a cache
hierarchy, **data streams through the tiles**. That is what makes it efficient
and low-power for *continuous* workloads — and it is why a generic "utilization
%" is a slippery number on this silicon (see
[How it works](../HOW-IT-WORKS.md)).

## It was built for DSP as much as AI

Despite the marketing name, Versal AI Engines were optimized for **both DSP and
ML**. Documented signal-processing results include real-time cyclostationary
analysis at **1.9–4.4× the throughput of a top-end GPU with >24× the energy
efficiency**, and there is active work building a BLAS library for the AI Engine
— i.e. it is a general streaming linear-algebra engine, not just an inference
ASIC.

## Why that shape matters for placement

Jobs that fit the fabric are **streaming, low-token, and bursty** — the opposite
of a sustained large-model decode, and ideally **off the critical path** of
whatever the user is waiting on. Run that class of work on the NPU and it
proceeds in the background without stealing the iGPU's cycles. (For the concrete
"what fits / what doesn't" list, see
[Workload patterns](workload-patterns.md).)

## The honest limits

- **Shared bandwidth.** The NPU and iGPU share the unified ~212 GB/s memory bus.
  The NPU adds *compute*, not *bandwidth*. Background jobs should stay
  low-token / bursty, and heavy concurrent work must be scheduled against the
  main lane.
- **Small-dense models only.** The high-level NPU runtimes target roughly
  Llama-3.1-8B / Phi-3.5-Mini class models — great for summarize / classify /
  transform, not for large-MoE generation.

## Memory bandwidth is the *dominant* contention surface — and the TOPS ceiling

The AMD GEMM study *"Striking the Balance"* (Taka et al., 2025;
[arXiv:2512.13282](https://arxiv.org/abs/2512.13282)) gives two
hardware-authoritative anchors:

- **Memory bandwidth is the dominant contention channel — not compute.** NPU
  GEMM is *memory-bound* at small/medium sizes. Because the NPU and the CPU/iGPU
  are disjoint silicon sharing one DDR5 bus, **compute-vs-compute contention is
  structurally near-zero, and the shared-memory path is the first surface to
  saturate.** It is not the *only* one, though: on a laptop the NPU and iGPU also
  share a **package power budget and thermal envelope**, and the on-die
  **memory fabric / interconnect** can bottleneck before raw DRAM bandwidth
  does. So "memory bandwidth dominates" is the right first-order model, not a
  claim that nothing else can contend. Caveat: the paper flags XDNA2's higher
  reliance on effective DRAM bandwidth, so a bigger model could push contention
  up — it is scale-dependent. (The paper measures GEMM and excludes GEMV/decode,
  so applying this to token generation is an extrapolation, not its measurement.)
- **Published TOPS ceilings (GEMM kernel, not tokens/s), by generation:** int8
  **6.76** (XDNA1) / **38.05** (XDNA2); bf16 **3.14** (XDNA1) / **14.71**
  (XDNA2). Use these as capability bounds on each fabric — note XDNA2 is ~5×
  XDNA1 at int8, which is part of why the high-level runtimes target it.

For a *measured* contention number on this exact hardware — a background NPU job
running next to an interactive iGPU model — see the
[NPU vs CPU contention experiment](../experiments/npu-igpu-contention.md) in the
Evidence Library.

## Takeaway

The NPU is not a slow second chatbot; it is a **streaming co-processor** whose
natural workload is bounded, bursty linear-algebra that can run beside the iGPU's
heavy lifting. Knowing that is what lets you read its telemetry — and decide what
to run where — honestly.
