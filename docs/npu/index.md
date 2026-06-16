# NPU Field Guide

`xdna-top` exists to make the AMD Ryzen AI NPU observable. This field guide is
the companion knowledge: what the NPU *is*, how its software stack is layered,
what you can run on it, and which jobs actually pay off there. It is
hardware-and-ecosystem reference — generic, not tied to any one application.

| Doc | What it covers |
|---|---|
| [Why the NPU?](why-the-npu.md) | XDNA2 as a spatial dataflow / DSP fabric; why DRAM bandwidth is the only contention surface; published GEMM TOPS ceilings. |
| [Software stack](software-stack.md) | The layers from high-level runtimes down to the XRT/amdxdna driver — "run a model" vs "write a kernel." |
| [Runtime landscape](runtime-landscape.md) | The four practical ways to run things on XDNA: FastFlowLM, Lemonade Server, Ryzen AI Vitis AI EP (ONNX), and GAIA. |
| [Workload patterns](workload-patterns.md) | What a laptop NPU is good and bad at, and the design heuristics that follow. |

For *measured* results on this silicon, see the
[Evidence Library](../experiments/index.md). For how `xdna-top` reads the
hardware, see [How it works](../HOW-IT-WORKS.md).
