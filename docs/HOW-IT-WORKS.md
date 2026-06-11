# How it works

This is the guided tour. It assumes you know what a terminal is and that your
machine has an AMD Strix Halo APU in it — nothing else. By the end you'll know
exactly where every number on the screen comes from, and just as importantly,
what those numbers can and cannot tell you.

## The two engines

A Strix Halo APU carries two compute engines that matter here:

**The iGPU** (Radeon 8060S, architecture `gfx1151`) is a conventional GPU. It
has occupancy, clocks, power draw — the familiar dials — and the `amdgpu`
kernel driver dutifully tracks them. A GPU executes work *temporally*: shader
cores grind through a queue, and "busy %" honestly summarizes how saturated
they were over a sampling window.

**The NPU** (AMD XDNA, enumerated as `RyzenAI-npu5`) is a different animal: a
*spatial dataflow* fabric. Instead of cores marching through instructions, an
array of AI-engine tiles is configured into a layout that data streams
*through*. Work isn't scheduled onto it moment-to-moment; a workload claims a
**hardware context** (a partition of tiles), and then jobs are *submitted*
through that context. This architectural difference has a consequence that
shapes this whole tool:

> **There is no honest "NPU utilization %" here.** A *temporal* busy fraction
> ("was anything running this window?") can be a legitimate metric where a
> driver exports a busy-time counter — Intel's `ivpu` does, and GUI monitors
> build their NPU percentages from it. But on Strix Halo today there is
> nothing to build from: `amdxdna` exposes no busy-time or telemetry node in
> sysfs (probed on kernel 6.17; receipts in `docs/bundle/`), and the spatial
> model means even a temporal percentage says little about how hard the tile
> array is actually working. What the hardware *does* expose is better than a
> guess: real per-context bookkeeping, which we read directly.

## Reading the iGPU: sysfs, not amd-smi

The obvious tool, `amd-smi`, returns `N/A` for essentially everything on
gfx1151 ([ROCm #6035](https://github.com/ROCm/ROCm/issues/6035)). But the
driver's own counters are sitting right there in sysfs:

- **busy:** `gpu_busy_percent` under the card's device node — the driver's own
  0–100 load figure.
- **power:** the card's `hwmon` node reports instantaneous draw in microwatts;
  we render watts.

`xdna-top` polls these at 5 Hz and keeps a 60-second rolling window for the
sparklines. There is no estimation layer: if the kernel says 87, we print 87.

## Reading the NPU: xrt-smi and the counter-delta trick

The `amdxdna` driver exposes the NPU through AMD's XRT runtime, and XRT ships
a CLI: `xrt-smi`. One report turns out to be gold:

```
xrt-smi examine --report aie-partitions
```

It dumps the NPU's hardware-context table: which **PID** owns each context,
and cumulative **submission** and **completion** counters per context. Those
two counters are the entire activity story:

| Observation across two samples | Meaning |
|---|---|
| Counters static | Context exists but is **idle** |
| Submissions incrementing | Context is **actively executing** work |
| Submissions > completions | Jobs are **in flight at this instant** |

`xdna-top` samples the report, diffs counters against the previous sample, and
derives each context's state from the delta. That's the "● ACTIVE" badge — not
a guess, a measurement.

## A real capture, annotated

These are excerpts from the actual calibration run that validated the signal
(an LLM running on the NPU via a local inference server; full raw capture in
the repo's bench data).

**Idle** — the model is loaded, a context is held, nothing is executing:

```
PID      CTX   SUBMISSIONS   COMPLETIONS   STATUS
93941     1        15 268        15 268    Active
```

Note the trap: the *status column says "Active"* because the context is
allocated — but submissions sat frozen at 15 268 across samples. By counter
delta: **idle**. (This is exactly why naive parsing misleads, and why the
delta logic exists.)

**Under load** — the same context while the model generates tokens:

```
PID      CTX   SUBMISSIONS   COMPLETIONS   STATUS
93941     1        15 461        15 460    Active    ◆ in-flight
```

Submissions climbed 15 268 → 15 461 across the generation, and at this instant
submissions lead completions by one: a job is physically executing as the
sample was taken. PID 93941 is the inference server process — attribution for
free.

## Fusing it

Each poll tick, the gauge layer takes one sysfs reading and one parsed
`xrt-smi` report, stamps them with a shared timestamp, and emits a single
fused record — which the TUI renders, and `--json` mode prints raw so you can
pipe it into anything (the hosted chart gallery is built from exactly these
records).

If a source is missing — no `xrt-smi` on PATH, unreadable sysfs node, a parse
failure after an XRT update — that pane **degrades and says so** rather than
crashing or, worse, silently showing stale numbers. A monitoring tool's first
duty is to not lie about whether it's monitoring.

## Honest limits

- **The xrt-smi output format is a load-bearing dependency.** An XRT release
  that reshapes the report breaks the NPU pane until the parser catches up
  (it will fail visibly, per above). Pinned-format test fixtures guard the
  parser.
- **5 Hz sampling bounds what you can see.** A burst shorter than ~200 ms can
  fall between samples; cumulative counters still record that it happened
  (the next delta jumps), but its precise timing is smeared.
- **No NPU power/thermals.** The platform doesn't expose a per-NPU power rail
  we trust yet; we won't print one until it does.
- **If AMD fixes `amd-smi` on gfx1151**, the iGPU half of this tool becomes
  redundant — happily. The NPU half and the unified view remain the point.

(The kernel also emits `amdxdna` ftrace tracepoints per job, which could
corroborate the delta method — but tracefs needs elevated privileges, and
xdna-top keeps its zero-root promise.)
