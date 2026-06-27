# NPU Field Guide

`xdna-top` exists to make the AMD Ryzen AI NPU observable. This field guide is
the companion knowledge: what the NPU *is*, how its software stack is layered,
what you can run on it, and which jobs actually pay off there. It is
hardware-and-ecosystem reference — generic, not tied to any one application.

> **Two generations, not one.** "Ryzen AI NPU" spans two silicon generations,
> and they are *not* interchangeable for what you can run:
>
> - **XDNA1** (`aie2`) — Phoenix / Hawk Point. First-gen.
> - **XDNA2** (`aie2p`) — Strix / Strix Halo. Second-gen; ~5× the int8 GEMM
>   ceiling and the target the high-level NPU runtimes assume.
>
> The high-level LLM runtimes (FastFlowLM, Lemonade's `flm:npu`) currently
> require **XDNA2**. XDNA1 is reachable today through the **custom-kernel path**
> (XRT + IRON/mlir-aie → Peano), and `xdna-top`'s *telemetry* works on both gens
> — see the measured [XDNA1 (Phoenix / Hawk Point) capture
> profile](../platforms/xdna1-phoenix-hawk-point.md). Where a claim below is
> specific to one generation, it is labelled.

| Doc | What it covers |
|---|---|
| [Why the NPU?](why-the-npu.md) | The AI Engine as a spatial dataflow / DSP fabric; why memory bandwidth is the *dominant* (not the only) contention surface; published GEMM TOPS ceilings per generation. |
| [Software stack](software-stack.md) | The layers from high-level runtimes down to the XRT/amdxdna driver — "run a model" vs "write a kernel" — and which layers reach XDNA1. |
| [Runtime landscape](runtime-landscape.md) | The four practical ways to run things on XDNA: FastFlowLM, Lemonade Server, Ryzen AI Vitis AI EP (ONNX), and GAIA — and their generation requirements. |
| [Workload patterns](workload-patterns.md) | What a laptop NPU is good and bad at, and the design heuristics that follow. |

For *measured* results on this silicon, see the
[Evidence Library](../experiments/index.md) and the
[XDNA1 capture profile](../platforms/xdna1-phoenix-hawk-point.md). For how
`xdna-top` reads the hardware, see [How it works](../HOW-IT-WORKS.md).
