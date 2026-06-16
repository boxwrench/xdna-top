# The NPU software stack (where each tool sits)

A map so you don't confuse *"run a model on the NPU"* with *"write code for the
NPU."* There are two very different altitudes, and almost everyone lives at the
top one.

## The layers, top (easy) to bottom (metal)

```
  Most users live here ┐
                       v
┌─────────────────────────────────────────────────────────────┐
│ HIGH-LEVEL RUNTIME                                           │
│  Lemonade Server  /  FastFlowLM (FLM)                        │
│  "Ollama for the NPU." Hand it a model + a prompt over an    │
│  OpenAI-compatible API. No compiler, no kernels.            │
└─────────────────────────────────────────────────────────────┘
                       │  (only if you write CUSTOM kernels)
                       v
┌─────────────────────────────────────────────────────────────┐
│ KERNEL AUTHORING — two front-ends, same backend             │
│  (a) Triton-XDNA (amd/Triton-XDNA) ← the approachable one   │
│      Write standard Triton @triton.jit kernels and lower    │
│      them to the NPU: Triton → triton-shared (Linalg) →     │
│      MLIR-AIR/AIE. Supports AIE2 and AIE2P (Strix).         │
│      matmul / softmax / layernorm / elementwise / reductions.│
│  (b) IRON (amd/IRON, on Xilinx/mlir-aie) ← close-to-metal   │
│      Wraps the MLIR-AIE bindings, with a pre-built operator  │
│      library for aie2p (GEMM/GEMV, MHA/GQA, softmax, RMSNorm,│
│      RoPE, activations) each shipped with a CPU reference +  │
│      pytest validation, plus an end-to-end small-LLM app.    │
└─────────────────────────────────────────────────────────────┘
                       │  (both lower through MLIR-AIE → Peano)
                       v
┌─────────────────────────────────────────────────────────────┐
│ COMPILER BACKEND                                             │
│  llvm-aie ("Peano") — github.com/Xilinx/llvm-aie            │
│  An LLVM/Clang fork that compiles to the AI Engine VLIW ISA.│
│  Targets:  XDNA (Phoenix/Hawk) = --target=aie2-...-elf      │
│            XDNA2 (Strix/Strix Halo) = --target=aie2p-...-elf │
│  Maturity: "experimental" LLVM architecture.                │
└─────────────────────────────────────────────────────────────┘
                       │
                       v
┌─────────────────────────────────────────────────────────────┐
│ RUNTIME + DRIVER (load & run on hardware)                   │
│  XRT (xrt-smi)  +  amdxdna kernel driver  +  /dev/accel/... │
│  This is the layer xdna-top reads for telemetry.           │
└─────────────────────────────────────────────────────────────┘
```

## What this means in practice

- **To run a model, you only need the top layer.** Lemonade or FastFlowLM
  running a small model over an OpenAI-compatible endpoint is the whole story for
  most use cases — no IRON, no llvm-aie. (See the
  [Runtime landscape](runtime-landscape.md) for the runtimes at this layer.)
- **The custom-kernel path is the deep path** — relevant only if you write your
  *own* NPU math (a real DSP kernel, a bespoke GEMM/embedding kernel, etc.).
- **Triton-XDNA lowers the bar for that deep path.** Instead of hand-writing
  IRON / MLIR-AIE, you can write **Triton** (Python, familiar from GPU work) and
  get near-handwritten NPU performance, targeting `aie2p`. It reports >90% of
  matmul configs reaching ≥90% of a handwritten baseline. Official AMD repo;
  experimental.
- **IRON upgrades the deep path further:** a validated operator library for
  `aie2p` (compose operators instead of authoring kernels), an independent
  end-to-end small-LLM inference app, and full ownership of the inference loop
  (so logits/hidden states are accessible by construction). Also worth stealing:
  its per-operator test discipline — an NPU `design.py`, a CPU `reference.py`,
  and a `test.py` asserting NPU ≈ CPU.
- **The bottom two layers are what `xdna-top` observes.** XRT + the `amdxdna`
  driver + `/dev/accel/*` are where activity and sensors actually surface.

## One-line takeaway

For everyday use you live at the **FastFlowLM / Lemonade** altitude and write no
kernels. If you ever need custom NPU math, **Triton-XDNA** is the approachable
front-end and **llvm-aie / Peano** is the compiler underneath — both official
AMD, both supporting Strix.
