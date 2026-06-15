---
title: NPU vs CPU Background-Job Contention on Strix Halo
date: 2026-06-13
kernel: 6.17.0-35-generic
hardware: AMD RYZEN AI MAX+ 395 (Strix Halo, gfx1151)
xrt_version: 2.21.75
headline: For a latency-tolerant background generation job next to an interactive iGPU model, the NPU does ~3x the throughput at ~3x better total-board perf/watt and contends slightly LESS with the main lane (~3.8% vs ~4.8% decode loss) than spare CPU cores.
artifacts:
  - artifacts/measurements/run1.m1_contention.json
  - artifacts/measurements/run1.m2_cpu_arm.json
  - artifacts/measurements/run2.m1_contention.json
  - artifacts/measurements/run2.m2_cpu_arm.json
  - artifacts/measurements/run3.m1_contention.json
  - artifacts/measurements/run3.m2_cpu_arm.json
  - artifacts/evidence/baseline.snapshot.json
  - artifacts/evidence/npu.snapshot.json
  - artifacts/evidence/cpu.snapshot.json
viz:
  type: comparison
  unit_groups:
    - label: background-job throughput
      unit: tok/s
      highlight: "2.96×"
      series: { NPU: 12.15, CPU: 4.11 }
    - label: total-board perf/watt
      unit: tok/s/W
      highlight: "2.95×"
      series: { NPU: 0.143, CPU: 0.049 }
  reproduce: "python bench/contention_benchmark.py --trials 20 --output-dir bench/out"
---

# NPU vs CPU Background-Job Contention on Strix Halo

**What this is.** Honest, reproducible numbers for deciding *what to run where* on
an AMD Strix Halo APU — the XDNA NPU, the gfx1151 iGPU, and the CPU all share one
unified ~256 GB/s memory bus. The engines are **compute-isolated but
bandwidth-shared**, so the whole story is memory-bandwidth contention. Built and
reproduced with xdna-top, which exists because `amd-smi` is broken on gfx1151.

The workload is a plain **background LLM text-generation job** on each engine; it
is not tied to any particular application. We ask one question: if an interactive
model is already running on the iGPU (the "main lane"), what does it cost to run a
second generation job concurrently on the **NPU** versus on **spare CPU cores**,
and which placement does more work per watt?

## The short answer

**Place a second, latency-tolerant generation job on the NPU, not on spare CPU
cores.** The NPU does **~3× the background throughput** at **~3× better
total-board perf/watt**, and it contends **slightly *less*** with the interactive
main lane (~3.8% vs ~4.8% decode loss). Both offloads cost the main lane a real,
single-digit-percent hit — the NPU's win is **throughput and efficiency**, not
zero impact.

## Test platform

- **SoC / APU:** AMD RYZEN AI MAX+ 395 (Strix Halo)
- **NPU:** RyzenAI-npu5 (gfx1151) — background job: FLM `gemma4-it:e2b`
- **iGPU:** Radeon 8060S (gfx1151) — interactive main lane: `qwen3.5-35b-a3b` (llama.cpp/ROCm)
- **CPU:** background job: `gemma-4-12b` Q4 on 4 threads (llama.cpp, `--gpu-layers 0`)
- **Kernel:** Linux 6.17.0-35-generic · **XRT:** 2.21.75
- **Unified memory:** 128 GB LPDDR5x (shared bus)

Each engine is driven through an OpenAI-compatible generation endpoint.

## Results

Aggregated over **three independent runs of N = 20 trials each (60 decode samples
per condition)**. Per-run means are shown as a range so the run-to-run spread is
visible; pooled mean ± stddev is over all 60 samples.

| Condition | iGPU Decode (tok/s) | iGPU Decode Loss % | Background Throughput (tok/s) | Avg Decode Power (PPT, W) | tok/s per Total-Board Watt |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Baseline** (iGPU only) | 48.16 ± 0.10 | 0.00% | — | 83.64 | — |
| **NPU** (concurrent) | 46.32 ± 0.22 | **3.81%** (runs 3.4–4.2%) | **12.15 ± 1.20** | 84.86 | **0.143** |
| **CPU** (concurrent) | 45.86 ± 0.43 | **4.77%** (runs 4.2–5.1%) | 4.11 ± 0.80 | 84.58 | 0.049 |

### Headline findings

- **Throughput — ~3×.** The background job runs at **12.15 ± 1.20 tok/s** on the
  NPU versus **4.11 ± 0.80 tok/s** on 4 CPU cores — a **2.96× speedup**. Reproduced
  across every run.
- **Total-board efficiency — ~3×.** The NPU delivers **0.143 tok/s per total-board
  watt** versus **0.049** for the CPU arm — a **2.95×** improvement (background
  throughput ÷ total-board package power).
- **Contention — both ~4–5%, NPU slightly lower.** A concurrent NPU job costs
  **~3.8%** iGPU decode loss; the CPU arm costs **~4.8%**. The NPU consistently
  contends about **one point less**, run to run. Neither offload is free: both pull
  on the same shared memory bus.

## Honest method

- **Measured:** iGPU decode throughput, background-job throughput, and package
  (PPT) power, each on real hardware. Decode loss % is computed per trial against
  the run's own baseline decode mean. Reported as pooled mean ± stddev over 3×20
  trials, with the per-run range shown so run-to-run variance is explicit.
- **Why three runs.** An earlier single N=5 measurement reported a lower, tighter
  NPU contention (~2.8%) and a near-zero CPU contention. That did **not** reproduce:
  three independent N=20 runs put NPU at ~3.4–4.2% and CPU at ~4.2–5.1%. We report
  the fuller-variance result. The throughput (~3×) and total-board perf/watt (~3×)
  advantages reproduced in every run.
- **Marginal-watt efficiency: withdrawn.** Dividing throughput by the *added* watts
  each engine costs over baseline is **below this setup's reliable resolution** and
  is **not reported**. The marginal power deltas are tiny (NPU ≈ +0.7–2.3 W, CPU
  ≈ +0.4–2.1 W) and are dominated by thermal drift: across the study the package
  temperature climbed from ~48 °C to ~87 °C (k10temp) under sustained load, so idle
  baseline power rose to meet concurrent power and the marginal-watt ratio became
  unstable (it swung from ~5× to ~18× across runs). We keep the **total-board**
  perf/watt number, which is stable, and explicitly do not headline marginal-watt.
- **Package (PPT) power, not isolated engine power.** Power is read from the amdgpu
  `power1` rail, labelled **PPT** (Package Power Tracking) — the whole-SoC rail
  (CPU + iGPU + NPU combined). It is the correct denominator for *total-board*
  perf/watt, but **the NPU's individual draw is not separable** from this shared
  rail. (See the repo's `tools/probe_sensors.py` for the read-only inventory of
  which signals this stack exposes; NPU-isolated power is not one of them.)
- **Prefill withheld.** Prefill throughput carries very high variance (~6–14k tok/s
  stddev) from the fast prefill pass plus HTTP latency; the prefill contention
  signal is smaller than that stddev, so prefill contention is **below the
  measurement resolution of this harness** and is not reported.
- **Attribution.** Each condition's evidence is an xdna-top `snapshot` (PID → NPU
  hardware-context attribution, backend provenance, degraded flags) plus a
  continuous `record` trace, so "the work ran on the NPU" is backed by a schema'd
  artifact, not a screenshot.

## Artifacts

All numbers above are backed by committed evidence under
[`artifacts/`](artifacts/):

- [`measurements/`](artifacts/measurements/) — the three runs' raw measurement JSON
  (`runN.m1_contention.json` = baseline + NPU arm; `runN.m2_cpu_arm.json` = CPU arm),
  including every per-trial sample, mean, and stddev the table aggregates.
- [`evidence/`](artifacts/evidence/) — the per-condition `snapshot` + `record` pair
  for one representative run (`baseline`, `npu`, `cpu`), demonstrating the schema'd,
  PID-attributed evidence the harness emits for each condition via
  `capture_condition_evidence`.

## Reproduce

Start OpenAI-compatible generation endpoints for the iGPU main lane and the
background engine(s), then:

```bash
# Per-condition evidence (snapshot + record) is captured automatically.
python bench/contention_benchmark.py --trials 20 --output-dir bench/out \
  --npu-model gemma4-it:e2b --cpu-model gemma-4-12b-it-qat-q4_0

# Re-render the table from existing condition artifacts:
python bench/contention_benchmark.py --generate-table-only --output-dir bench/out
```

To capture a standalone evidence pair for any running workload with the shipped CLI:

```bash
xdna-top snapshot --out condition.snapshot.json
xdna-top record --duration 10 --interval 0.2 --out condition.record.jsonl
xdna-top assert condition.record.jsonl --require-npu-activity \
  --between request-start request-end
```

## How to read this for your use-case

If you have an interactive model on the iGPU and a second, latency-tolerant
generation job to place, the NPU is the better home: **~3× the throughput of spare
CPU cores at ~3× the total-board efficiency**, and it leans on your main model
*slightly less* (~3.8% vs ~4.8% decode loss). This is a placement decision the
data supports — not "use the NPU because it's there." If your second job is
throughput-sensitive or you care about perf/watt on a shared power budget, the NPU
wins clearly; if it is latency-critical on the main lane, note that *either*
offload costs the interactive model a real ~4–5%.

## Not yet measured (boundaries)

- Main-lane inter-token latency (p50/p99) under contention — a small throughput
  loss can still spike tail latency.
- Sustained multi-minute steady-state past the warm-up thermal ramp seen here.
- Achieved memory bandwidth (GB/s) attribution — a clean counter may not exist on
  gfx1151; treated as a probe.
- NPU-isolated power, and deep tile-level NPU utilization beyond existence-of-activity.
