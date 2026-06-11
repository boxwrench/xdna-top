# Origins

Short version: this tool exists because we could not answer the question
*"is the NPU actually doing anything?"* — and on Strix Halo in 2026, nothing
else could answer it either.

## The setup

The project that birthed this was an exploration of **concurrent local LLM
inference on AMD Strix Halo**: a large model serving on the iGPU while a small
model runs on the Ryzen AI NPU at the same time. To make any claim about that
setup — performance, power, even basic "it works" — you need to observe both
engines independently, live.

## The wall

The iGPU half should have been easy. It wasn't: on gfx1151, `amd-smi` returns
`N/A` for essentially every metric ([ROCm #6035](https://github.com/ROCm/ROCm/issues/6035)).
The workaround is reading the `amdgpu` driver's own counters from sysfs, which
works fine — it's just that no shipping tool did it together with anything NPU.

The NPU half was worse. First the platform fought back: Ubuntu's 6.17.0-22
through -23 kernels shipped a regression that broke SVA binding for the
`amdxdna` driver entirely (`SVA bind device failed, ret -95` —
[LP #2149766](https://bugs.launchpad.net/bugs/2149766)); until a fixed kernel
landed, the device wouldn't even open. And once it *did* open and models *were*
running on it, there was still no way to see that from the outside: no
`nvtop`-equivalent, nothing in ROCm, nothing anywhere, prints a live view of
XDNA activity.

## The find

The signal existed all along — buried in AMD's XRT tooling. `xrt-smi examine
--report aie-partitions` dumps the NPU's hardware-context table: which PID owns
a context, and cumulative submission/completion counters. Watch those counters
across two samples and you have a real activity signal: static counters = idle;
incrementing = executing; submissions ahead of completions = work in flight
*right now*. We verified the semantics empirically — idle captures vs. captures
taken mid-generation with a model actively producing tokens — and the deltas
told the truth every time.

`xdna-top` is that finding, productized: the counter-delta logic fused with the
sysfs iGPU scrape, rendered as a terminal dashboard you can leave running in a
corner while you work.

## Why the history starts at one commit

The tool was developed inside a private research monorepo and extracted cleanly
for release. The full development history — including the kernel saga and the
captures that calibrated the activity signal — stays there; the interesting
parts are retold in [HOW-IT-WORKS.md](HOW-IT-WORKS.md) with real data excerpts.

— Keith, June 2026
