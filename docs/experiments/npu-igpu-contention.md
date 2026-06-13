---
title: NPU vs CPU Background-Job Contention on Strix Halo
date: 2026-06-13
kernel: 6.17.0-35-generic
hardware: AMD RYZEN AI MAX+ 395 (Strix Halo, gfx1151)
xrt_version: 2.21.75
headline: Offloading a background generation job to the NPU costs ~2.8% iGPU decode loss and is ~4.6x more marginal-watt efficient than the CPU control arm.
artifacts:
  - artifacts/contention-baseline.snapshot.json
  - artifacts/contention-baseline.record.jsonl
---

# NPU vs CPU Background-Job Contention on Strix Halo

**What this is.** Honest, reproducible numbers for deciding *what to run where* on
an AMD Strix Halo APU — the XDNA NPU, the gfx1151 iGPU, and the CPU all share one
unified ~212 GB/s memory bus. The engines are **compute-isolated but
bandwidth-shared**, so the whole story is memory-bandwidth contention. Built and
reproduced with xdna-top, which exists because `amd-smi` is broken on gfx1151.

The workload is a plain **background LLM text-generation job** on each engine; it
is not tied to any particular application. We ask one question: if an interactive
model is already running on the iGPU (the "main lane"), what does it cost to run a
second generation job concurrently on the **NPU** versus on **spare CPU cores**?

Numbers are **mean ± stddev** over N = 5 trials. Anything below measurement
resolution is labelled as such rather than published with false precision.

## Test platform

- **SoC / APU:** AMD RYZEN AI MAX+ 395 (Strix Halo)
- **NPU:** RyzenAI-npu5 (gfx1151), firmware 1.1.2.65
- **iGPU:** Radeon 8060S (gfx1151)
- **Kernel:** Linux 6.17.0-35-generic
- **XRT:** 2.21.75
- **Unified memory:** 128 GB LPDDR5x (~212 GB/s shared bus)

Each engine is driven through an OpenAI-compatible generation endpoint: the iGPU
is the interactive main lane, and the background job runs on either the NPU or on
spare CPU cores.

## Results — decode slice (N = 5)

| Condition | iGPU Prefill (tok/s) | iGPU Decode (tok/s) | iGPU Decode Loss % | Background Throughput (tok/s) | Avg Decode Power (PPT, W) | Marginal Power (W) | tok/s per Total-Board Watt | tok/s per Marginal Watt |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Baseline** (iGPU only) | 39592.49 ± 12420.74 | 45.81 ± 0.15 | 0.00% | - | 72.66 ± 5.70 | - | - | - |
| **NPU** (concurrent) | 46818.90 ± 8407.69 | 44.54 ± 0.07 | 2.76% ± 0.16% | 13.67 ± 0.94 | 82.95 ± 4.87 | +10.29 W | 0.164 | **1.33** |
| **CPU** (concurrent) | 47192.10 ± 9904.26 | 45.86 ± 0.55 | -0.11% ± 1.21% | 4.48 ± 0.17 | 88.09 ± 6.92 | +15.43 W | 0.051 | **0.29** |

### Headline findings

- **Throughput.** The background job runs at **13.67 ± 0.94 tok/s** on the NPU
  versus **4.48 ± 0.17 tok/s** on 4 CPU cores — a **~3.0× speedup**.
- **Total-board efficiency.** The NPU delivers **0.164 tok/s per total-board watt**
  versus **0.051** for the CPU arm — a **~3.2×** improvement (perf/watt here is
  background throughput ÷ total-board package power).
- **Marginal-watt efficiency (new).** Dividing background throughput by the *added*
  watts each engine costs over baseline gives **NPU ≈ 1.33** vs **CPU ≈ 0.29
  tok/s per marginal watt** — a **~4.6×** advantage. This isolates the cost of the
  offload itself: the NPU adds +10.29 W for 13.67 tok/s; the CPU adds +15.43 W for
  4.48 tok/s.
- **Contention.** A concurrent NPU job costs a negligible **2.76% ± 0.16%** decode
  loss on the iGPU main lane. The CPU arm shows no measurable decode loss
  (−0.11% ± 1.21%, i.e. within noise).

## Honest method

- **Measured:** iGPU prefill/decode throughput, background-job throughput, and
  package (PPT) power, each as mean ± stddev over N = 5 trials on real hardware.
  Decode loss % is derived per trial from the baseline decode mean.
- **Package (PPT) power, not isolated engine power.** Power is read from the amdgpu
  `power1` rail, labelled **PPT** (Package Power Tracking) — the whole-SoC rail
  (CPU + iGPU + NPU combined). It is the correct denominator for *total-board*
  perf/watt, but **the NPU's individual draw is not separable** from this shared
  rail. The marginal-watt figure is a *difference* of total-board means, so it
  attributes the added board cost to the offload without claiming a per-engine
  sensor. (See the repo's `tools/probe_sensors.py` for the read-only inventory of
  which signals this stack exposes; NPU-isolated power is not one of them.)
- **Prefill withheld.** Prefill throughput carries very high variance (~8–12k
  tok/s stddev) from the fast prefill pass plus HTTP latency. The prefill
  contention signal is smaller than that stddev, so prefill contention loss is
  **below the measurement resolution of this harness** and is not reported.
- **Attribution.** Each condition's evidence is an xdna-top `snapshot` (PID →
  NPU hardware-context attribution, backend provenance, degraded flags) plus a
  continuous `record` trace, so a claim of "the work ran on the NPU" is backed by
  a schema'd artifact, not a screenshot.

## Artifacts

The committed evidence under [`artifacts/`](artifacts/) is captured with
xdna-top's shipped primitives on the test platform:

- [`artifacts/contention-baseline.snapshot.json`](artifacts/contention-baseline.snapshot.json)
  — a schema'd platform + telemetry snapshot (NPU detection, backend provenance,
  degraded flags).
- [`artifacts/contention-baseline.record.jsonl`](artifacts/contention-baseline.record.jsonl)
  — a continuous telemetry trace (typed JSONL: `meta` → `telemetry` samples →
  `summary`).

The benchmark harness (`bench/contention_benchmark.py`) emits one such
`snapshot` + `record` pair per condition via `capture_condition_evidence`, and
reads its attribution back through `attribution_from_snapshot`.

## Reproduce

Start OpenAI-compatible generation endpoints for the iGPU main lane and the
background engine(s), then:

```bash
# Per-condition evidence (snapshot + record) is captured automatically.
python bench/contention_benchmark.py --trials 5 --output-dir bench/out

# Re-render the table from existing condition artifacts:
python bench/contention_benchmark.py --generate-table-only --output-dir bench/out
```

To capture a standalone evidence pair for any running workload with the shipped
CLI:

```bash
xdna-top snapshot --out condition.snapshot.json
xdna-top record --duration 10 --interval 0.2 --out condition.record.jsonl
xdna-top assert condition.record.jsonl --require-npu-activity \
  --between request-start request-end
```

## How to read this for your use-case

If you have an interactive model on the iGPU and a second, latency-tolerant
generation job to place, the NPU is the better home: ~3× the throughput of spare
CPU cores, ~4.6× better marginal-watt efficiency, and only a ~2.8% hit to the
main lane. The CPU arm barely touches iGPU decode but is far slower and less
efficient per added watt.

## Not yet measured (boundaries)

- Main-lane inter-token latency (p50/p99) under contention — a small throughput
  loss can still spike tail latency.
- Sustained multi-minute runs for thermal throttling.
- Achieved memory bandwidth (GB/s) attribution — a clean counter may not exist on
  gfx1151; treated as a probe.
- Deep tile-level NPU utilization beyond existence-of-activity.
