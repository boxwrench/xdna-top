# NPU workload patterns — what fits a laptop NPU

A field note on *which jobs pay off* on a Ryzen AI NPU, distilled from running
small models on XDNA2 and watching the telemetry. It is workload-shape guidance,
not a benchmark — pair it with [Why the NPU?](why-the-npu.md) for the hardware
reasoning and the [Evidence Library](../experiments/index.md) for measured
numbers.

## The short version

A laptop NPU is strongest at **bounded, streaming, background maintenance work
with deterministic state around the model.** It is weakest when a tiny model
must be the sole source of truth, produce fragile schemas perfectly, or resolve
conflicts unaided.

## Good candidate jobs

These fit the NPU-friendly shape (low-token, bursty, off the critical path):

- rolling summarization or context compression
- fact extraction into a deterministic store
- classification, routing, filtering, or ranking
- background cache or index maintenance
- guardrail, audit, or policy sidecars
- sensor-stream preprocessing
- low-latency transforms that can run while the iGPU handles a larger model

## Poor candidate jobs (risky without extra control layers)

- tasks where a small model must be the only source of truth
- strict schema generation with no repair or validator loop
- state correction delegated entirely to prose generation
- jobs whose outputs must silently alter a stable, reused prompt prefix
- workloads that must keep up continuously but only pass on a larger, slower model

## Why the line falls there

Patterns observed across small models on XDNA2:

| Workload shape | What tends to happen | The lesson |
|---|---|---|
| Strict structured extraction | Very small models miss required fields / break JSON. | Add schema repair, retries, or constrained decoding alongside the model. |
| Deterministic capture + small LLM | Hybrid pipelines do well: code preserves exact facts, the model compresses/classifies around them. | Let code own exact state; let the NPU model do the fuzzy part. |
| Compression | Some small models compress usefully **if** exact state is protected outside the prose. | Don't trust a prose summary to carry precise values. |
| Continuous background service | A high-quality but slow model can fail the keep-up rate even while scoring well on quality. | Measure throughput *and* quality — either can fail the system. |
| Current-state correction | Models leak stale values when asked to enforce "what's true now" unaided. | Use deterministic state and slot-level rules, not the model alone, for current truth. |
| Prefix stability | Background edits can disturb a prompt prefix and break downstream KV-cache reuse. | Treat prefix stability as a first-class constraint when output feeds a larger model. |

## Design heuristics

1. Put exact facts and current-state rules in deterministic code.
2. Use the NPU model for fuzzy compression, classification, or triage around that
   deterministic core.
3. Measure both quality and keep-up rate; either one can fail the system.
4. Treat prefix stability as a performance constraint when the result feeds a
   larger autoregressive model.
5. Keep hardware telemetry (e.g. `xdna-top` captures) as an observability record,
   not as the only proof a workload is useful.
