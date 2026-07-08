<div align="center">

# `xdna-top`

**The missing system monitor for AMD Ryzen AI NPUs.**

Unified, real-time NPU + iGPU telemetry for Strix Halo — in your terminal,
where `amd-smi` comes up empty.

[![CI](https://github.com/boxwrench/xdna-top/actions/workflows/ci.yml/badge.svg)](https://github.com/boxwrench/xdna-top/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Strix%20Halo-orange.svg)](docs/HOW-IT-WORKS.md)
[![Status](https://img.shields.io/badge/status-v0.4-blue.svg)](#roadmap)

</div>

<div align="center">

![xdna-top Telemetry TUI](docs/screenshot.png)

[Interactive telemetry dashboard](https://boxwrench.github.io/xdna-top/)

</div>


---

## Why this exists

If you run local AI on an AMD Strix Halo machine, you are flying blind twice over:

| You want to know… | `amd-smi` | `nvtop` / `amdgpu_top` | **`xdna-top`** |
|---|---|---|---|
| iGPU busy % / power on gfx1151 | `N/A` ([ROCm #6035](https://github.com/ROCm/ROCm/issues/6035)) | partial | ✅ live, from kernel sysfs |
| Is the **NPU** doing anything at all? | — | — | ✅ live contexts + activity |
| Both, side by side, while two models run | — | — | ✅ that's the whole point |

When this tool was built, the NPU half didn't exist anywhere: nothing surfaced
XDNA activity. (GNOME Resources 1.10, Feb 2026, has since added a desktop GUI
view of AMD NPUs.) `xdna-top` remains the only *terminal* monitor for it, the
only *unified* NPU + iGPU view on this silicon, and the only *per-context* one —
owning PID and live submission counters, not a single aggregate number. Today it
reads NPU context attribution through AMD's XRT tooling (`xrt-smi`) and pairs it
with iGPU telemetry scraped directly from `sysfs`; the roadmap moves low-level
NPU device and sensor probes toward direct AMDXDNA DRM IOCTLs through
`/dev/accel/*`.

Born from a practical need: while experimenting with **concurrent NPU + iGPU
local LLM inference** on Strix Halo, "is the NPU actually executing?" turned out
to be unanswerable with stock tools. So we built the answer.
*(More war stories in [docs/ORIGINS.md](docs/ORIGINS.md).)*

## What it shows — precisely

Honesty matters in a measurement tool, so here is exactly what each pane is:

- **iGPU:** `busy %` and `power (W)` read from the kernel's `amdgpu` sysfs
  endpoints at 5 Hz, with 60-second rolling sparklines. These are the same
  counters the driver itself maintains — no estimation.
- **NPU:** hardware context list from `xrt-smi examine --report aie-partitions` —
  owning PID, context ID, submission/completion counters, and an **activity
  state derived from counter deltas** (a context whose submissions are
  incrementing is doing work; an in-flight gap between submissions and
  completions means work is queued *right now*).
- **NPU power state (when available):** on driver stacks that export the
  `amdxdna` debugfs nodes, `snapshot` and `env-report` also report the NPU's
  active DPM clock state (npuclk / hclk in MHz) and SMU powerstate, read
  directly from debugfs and independent of `xrt-smi`. This is a clock-state
  power *level*, **not** a utilization percentage, and it reads `unavailable`
  (with a reason) when the nodes aren't exported or aren't readable.
- What it does **not** show: a made-up generic NPU "utilization %." Newer
  AMDXDNA stacks may expose direct sensor values such as column utilization,
  and `xdna-top` should label those precisely when available. For request
  attribution, the truthful unprivileged signal remains per-context
  submission-counter deltas, so that is what we show — and explain in
  [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).

## Quick start

```bash
pipx install git+https://github.com/boxwrench/xdna-top.git
xdna-top                    # live TUI, q to quit
xdna-top --json             # one fused telemetry reading to stdout, then exit
xdna-top snapshot --out platform.json
xdna-top env-report platform.json --markdown
xdna-top record --duration 60 --interval 0.2 --out telemetry.jsonl
xdna-top mark --out telemetry.jsonl "trial-1-start"      # annotate the stream
xdna-top env-report telemetry.jsonl --markdown           # report from a recording
xdna-top assert telemetry.jsonl --require-npu-activity   # exit 0/1 for CI
xdna-top assert telemetry.jsonl --require-npu-activity \
  --between request-start request-end                    # prove activity *in* the window
xdna-top compare before.json after.json   # flag high-signal platform drift
xdna-top baseline save known-good          # store a named known-good snapshot
xdna-top baseline check known-good         # re-check the platform after updates
xdna-top exporter --port 9477              # serve Prometheus metrics at /metrics
xdna-top workload-check --chat-url http://localhost:8000/v1/chat/completions \
  --model my-model            # supervised endpoint probe + NPU context evidence
xdna-top --theme phosphor   # pick a TUI theme (see --list-themes)
lemonade-top                # same monitor, lemonade-stand theme
```

Prefer direct GitHub install for now while package-index publication settles. If
you prefer plain `pip`, this also works:

```bash
pip install git+https://github.com/boxwrench/xdna-top.git
```

`lemonade-top` is the very same telemetry engine - identical NPU + iGPU signals
and `--json` output - just wearing a lemonade-stand theme. It's purely cosmetic
(no extra dependencies, nothing to do with the Lemonade SDK); pick whichever
palette you prefer.

**Requirements:** Linux with the `amdxdna` driver bound (`/dev/accel/accel0`),
Python ≥ 3.11, and `xrt-smi` on your PATH for the NPU pane. No ROCm required.
Missing a piece? The tool **degrades gracefully** — panes flag themselves as
degraded instead of crashing.

## Features

- Unified live view: both engines, one screen, 5 Hz
- Rolling ASCII sparklines for iGPU busy and power
- Per-context NPU table: PID, process name, submissions, completions, derived
  activity — ordered most-active-first by submission delta
- `--json` mode for scripts, logging, and dashboards
- `snapshot` mode for schema-versioned platform and telemetry evidence
- `env-report` mode for Markdown summaries from captured snapshots or recordings
- `record` mode for streaming typed JSONL telemetry events over a time window
- `mark` mode for annotating a recording with named trial markers
- `assert` mode for named pass/fail evidence checks with CI-friendly exit codes,
  including `--between` to require activity within a marked request window
- `compare` mode for spotting high-signal platform drift between two snapshots
- `baseline` mode for saving a known-good snapshot and re-checking it after updates
- `exporter` mode serving Prometheus metrics at `/metrics` for scraping into
  Prometheus + Grafana — optional `[exporter]` extra; see
  [docs/EXPORTER.md](docs/EXPORTER.md)
- Direct-AMDXDNA **NPU power state** when the `amdxdna` debugfs nodes are
  readable: active DPM clock state (npuclk/hclk MHz) + SMU powerstate — a clock
  level, never a utilization %
- `workload-check` mode: probe an OpenAI-compatible endpoint and report
  per-context NPU submission/completion deltas across the request window —
  measured evidence, never a causality claim
- Themes via `--theme`/`XDNA_TOP_THEME` (colors only — never the measured values)
- Pessimistic fallbacks everywhere — built for imperfect driver stacks
- Zero daemon, zero root*, zero ROCm dependency
  <sub>*standard sysfs/xrt permissions apply</sub>

## Themes

Pick a skin with `--theme <name>` (or set `XDNA_TOP_THEME`); run `xdna-top
--list-themes` to see them all. Themes change only colors and chrome — never the
metric names, states, units, or measured values — so a screenshot stays
claims-accurate in any theme.

![xdna-top themes gallery](docs/themes/gallery.png)

> Rendered with illustrative values to show the palettes. See
> [docs/THEMES.md](docs/THEMES.md) for the full list and how to contribute one.

## How it works (30 seconds)

`sysfs` supplies the iGPU's own driver counters; `xrt-smi` exposes the NPU's
hardware-context bookkeeping; `xdna-top` polls both, derives activity from
submission-counter deltas, fuses them into one reading, and renders it with
[rich](https://github.com/Textualize/rich). The full guided tour — including an
annotated real capture of an LLM generation lighting up the NPU pane — is in
**[docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md)**, and the hosted interactive
view lives at **[boxwrench.github.io/xdna-top](https://boxwrench.github.io/xdna-top/)**.

For what the hardware *is* — the NPU (XDNA 2), iGPU, and CPU spec, how they
compare, and exactly which sensors this silicon does and doesn't expose — see
**[docs/HARDWARE.md](docs/HARDWARE.md)**. For a worked use-case built on this
platform, see **[REM](https://github.com/boxwrench/rem)**.

## Related tools

- [`amdgpu_top`](https://github.com/Umio-Yasuno/amdgpu_top) is the mature,
  broad AMDGPU/APU monitor, with GPU metrics, sensors, fdinfo, process memory
  views, TUI/GUI/JSON modes, and XDNA device support. If you want a general AMD
  GPU monitor, start there.

  `xdna-top` is complementary. It focuses on Ryzen AI workload evidence: NPU
  context ownership, submission/completion deltas, and what the iGPU was doing
  at the same time. The planned direct AMDXDNA backend is inspired by
  `amdgpu_top`'s driver-facing approach while keeping `xdna-top` scoped to
  workload evidence and NPU+iGPU concurrency.

## NPU Field Guide

Companion reference for the silicon `xdna-top` watches — generic NPU knowledge,
no application specifics. Start at [docs/npu/](docs/npu/index.md).

| Doc | What it covers |
|---|---|
| [Why the NPU?](docs/npu/why-the-npu.md) | XDNA2 as a dataflow/DSP fabric; DRAM bandwidth as the contention surface; GEMM TOPS ceilings. |
| [Software stack](docs/npu/software-stack.md) | Runtime → kernel-authoring → compiler → driver layers. |
| [Runtime landscape](docs/npu/runtime-landscape.md) | FastFlowLM, Lemonade Server, Ryzen AI Vitis AI EP, GAIA. |
| [Workload patterns](docs/npu/workload-patterns.md) | What a laptop NPU is good/bad at, plus design heuristics. |

## Roadmap

- [x] v0.2 evidence core: `snapshot`, `env-report`, `record`, `mark`, `assert`,
      `compare`, `baseline`, and `assert --between` windowed activity guard
- [x] Prometheus `exporter` for scraping NPU+iGPU telemetry into Prometheus/Grafana
- [x] v0.3 read-only direct AMDXDNA backend: debugfs power/clock state + DRM-IOCTL
      driver/AIE/clocks/firmware identity (validated on real silicon)
- [x] v0.4 supervised `workload-check`: endpoint probe + per-context NPU
      submission/completion deltas across the request window
- [ ] v0.5 community/reporting work: theme registry (done), HTML reports, and
      more Ryzen AI captures
- [ ] Ongoing: configurable poll rate, per-context history, and more APUs
      (Phoenix/Hawk Point XDNA1) — testers welcome

See [docs/ROADMAP.md](docs/ROADMAP.md) for the prioritized command plan and
[docs/SNAPSHOT-SCHEMA.md](docs/SNAPSHOT-SCHEMA.md) for the snapshot artifact
draft.

## Contributing

Issues and PRs welcome — especially captures from other Ryzen AI machines.
Read [CONTRIBUTING.md](CONTRIBUTING.md) first; the one house rule is
**claims precision**: docs never promise a signal the hardware doesn't give.

## License & citation

Apache-2.0 © Keith. If this tool helped your research, see
[CITATION.cff](CITATION.cff).
