# NPU runtime landscape — four ways to run things on AMD XDNA

If you want to actually *run* something on a Ryzen AI NPU, there are four
practical routes today. They sit at the top "high-level runtime" layer of the
[software stack](software-stack.md) — you bring a model, they handle the
hardware.

> **Generation requirement.** The NPU paths of the first two runtimes
> (FastFlowLM, and Lemonade's `flm:npu` backend) currently require **XDNA2**
> (`aie2p`, Strix / Strix Halo). On **XDNA1** (`aie2`, Phoenix / Hawk Point)
> they do not yet target the NPU — gen-1 is reachable instead through the
> custom-kernel path (XRT + IRON/mlir-aie → Peano; see the
> [software stack](software-stack.md)). `xdna-top`'s telemetry works on both
> gens regardless — see the [XDNA1 capture
> profile](../platforms/xdna1-phoenix-hawk-point.md).

## 1. FastFlowLM (FLM)

A lightweight LLM runtime built for the Ryzen AI NPU. Serves generative LLMs
(and Whisper) on Linux behind an OpenAI-ish API (commonly on port `13306`).
Closest to "just run a model on the NPU." **Targets XDNA2** (`aie2p`); it does
not currently run on first-gen XDNA1. Note that its bare `/v1/embeddings`
endpoint is a non-functional stub on current builds — for embeddings, use
Lemonade (below).

## 2. Lemonade Server (`lemonade-sdk`)

An OpenAI / Ollama / Anthropic-compatible local server with **multiple
backends** and automatic hardware detection. Backends include `flm:npu` (NPU
LLMs, **XDNA2-only** — it is FastFlowLM under the hood), `llamacpp:rocm` (iGPU),
`vllm:rocm`, and more. It serves **completions,
embeddings, and rerankers** — embeddings/rerankers run through the `llamacpp`
backend (e.g. Qwen3-Embedding, nomic-embed, bge-reranker), which on this class
of machine means the **iGPU**, not the NPU. Linux + Windows. This is the
practical answer when you need more than generative LLMs.

## 3. Ryzen AI Vitis AI EP (ONNX Runtime)

The most general route: run arbitrary **ONNX** models on the NPU via ONNX
Runtime's **Vitis AI Execution Provider**
(`providers=['VitisAIExecutionProvider']`). It **auto-partitions** the graph —
NPU-supported subgraphs run on the NPU, the rest fall back to CPU transparently.
Recommends ONNX opset 17; the first load **compiles** the model to NPU format and
caches it (an EP-context file), so subsequent loads skip recompilation. Not
limited to LLMs — any ONNX model. OS support (Linux vs Windows) and the
NPU/CPU partition ratio for a given model are the things to verify up front.
Reference: <https://ryzenai.docs.amd.com/en/latest/modelrun.html>.

## 4. GAIA (AMD)

A generative-AI **agent framework** that can run agents on CPU / GPU / **Ryzen
AI NPU** (`--device npu`, `gaia init --profile npu`). Built on Lemonade for
inference. Notable design detail: **per-agent MCP tool activation** — rather than
dumping every tool into every agent's prompt, each agent gets only the tools it
needs, which keeps small-model tool selection sharp. Also ships native document
RAG and an Agent Hub TUI. Windows / Ryzen AI 300-series-centric historically;
check current Linux / Strix Halo status.

## Quick chooser

| You want to… | Use |
|---|---|
| Run a generative LLM on the NPU, minimal setup | **FastFlowLM** |
| One server for completions **+ embeddings + rerank**, multi-backend | **Lemonade Server** |
| Run an arbitrary **ONNX** model (not just LLMs) on the NPU | **Vitis AI EP** |
| Build a local **agent** that can target the NPU | **GAIA** |

For *what kinds of jobs* actually pay off on the NPU once you can run them, see
[Workload patterns](workload-patterns.md).
