---
title: Structured-Output Reliability Across Sampling Configs (NPU LLM stack)
date: 2026-06-14
kernel: 6.17.0-35-generic
hardware: AMD RYZEN AI MAX+ 395 (Strix Halo, gfx1151)
xrt_version: 2.21.75
headline: "Greedy decoding is the most reliable for structured (JSON) output across small models; aggressive repetition penalties (rep≈1.3) collapse valid output to near zero. Failures are structural, not creative."
viz:
  type: matrix
  cell: valid_output_rate
  cols: [greedy, temp0.3, temp0.7, freq0.3, freq0.5, freq0.8, rep1.1, rep1.3, "temp0.3+freq0.4"]
  rows: [llama3.2:1b, llama3.2:3b, qwen2.5-it:3b, qwen3-it:4b]
  data:
    - [1.00, 1.00, 0.83, 1.00, 1.00, 1.00, 0.92, 0.00, 0.08]
    - [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.00, 0.42]
    - [0.25, 0.33, 0.50, 1.00, 1.00, 0.75, 0.75, 0.00, 0.25]
    - [1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 1.00, 0.42, 0.58]
  reproduce: "python tools/sampling_sweep.py --all"
---

# Structured-Output Reliability Across Sampling Configs (NPU LLM stack)

**Honest method caveat:** *isolated structured-output probe, N=3 trials per cell, mean valid-output rate across 4 input spans — not an end-to-end task-correctness measure.*

Greedy decoding is the most reliable for structured (JSON) output across small models; aggressive repetition penalties (rep≈1.3) collapse valid output to near zero. Failures are structural, not creative.
