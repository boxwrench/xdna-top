# Changelog

All notable changes to `xdna-top` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Direct-AMDXDNA NPU power state** — `snapshot` now reads the NPU's active DPM
  clock state (npuclk/hclk MHz) and the SMU `powerstate` directly from
  `/sys/kernel/debug/accel/<bdf>/{dpm_level,powerstate}` (debugfs), independent
  of `xrt-smi`, exposing it as `devices.npu.power_state` with
  `backends.npu.signals.power_state = "debugfs"`; `env-report` renders an "NPU
  power state" line. This is the first direct-AMDXDNA telemetry signal (roadmap
  v0.3). It is the active *clock-state power level* in MHz, never a utilization
  percentage. Reads are unprivileged-safe: when the debugfs nodes are not
  exported (mainline driver) or not readable (non-root), it reports
  `available: false` with a reason rather than raising or guessing. Additive and
  optional — no `schema_version` bump. (#13, thanks @Scottcjn)
- **`workload-check` command** — supervised endpoint check that probes an
  OpenAI-compatible API (models GET + chat/completions POST, via stdlib `urllib`
  — no new dependency), brackets the request with before/after NPU context reads,
  and reports per-context submission/completion deltas with PID attribution in
  measured language ("Observed PID 1234 context 1 submission_delta=42 during
  request window"). JSON output separates endpoint availability, the model
  response summary, context presence, and the deltas. Honors claims precision: it
  reports counter movement, never causality (the concurrent-workload caveat ships
  in the output), and the exit code reflects only whether the endpoint responded.
  (#8)

## [0.3.0] - 2026-06-27

This release adds a Prometheus exporter for scraping `xdna-top` telemetry into
Prometheus/Grafana, strengthens per-context attribution with `/proc`-derived
process names and most-active-first ordering, and broadens hardware coverage
with a first-generation XDNA1 (Phoenix / Hawk Point) capture profile.

### Added

- **Per-context process names** — the NPU context table (and `snapshot` /
  `record` artifacts) now show a best-effort `process_name` for each owning PID,
  resolved read-only and unprivileged from `/proc/<pid>/comm` (falling back to
  the `cmdline` basename). This strengthens per-context attribution — "PID 1234
  (`llama-server`) context 1 …" — without a new dependency or any privilege. The
  name is `null` when the process can't be read (exited, another user, or
  non-Linux); it is `/proc`-derived, not from `xrt-smi`, and is never a measured
  value.
- **Most-active-first context ordering** — the live TUI now sorts the NPU
  context table by submission delta (cumulative submissions as a tiebreak), so a
  context doing work *right now* floats to the top. Display-only; never affects
  measured values or artifact contents.
- **Process names in `env-report`** — the record telemetry report's observed
  contexts now render as `PID/ctx (name)` when a process name was captured,
  falling back to `PID/ctx` otherwise.
- **Prometheus exporter** — a new `xdna-top exporter` subcommand serves hardware
  telemetry at `/metrics` in Prometheus format. The hardware is read fresh on
  every scrape (stateless; Prometheus owns the history), and a failed read is
  reported as `up=0` rather than a crash, so a degraded or absent NPU stays
  observable. Requires the optional `[exporter]` extra (`prometheus_client`) and
  is not needed for the TUI or snapshots. Binds to `127.0.0.1` by default and
  `/metrics` is unauthenticated; see [docs/EXPORTER.md](docs/EXPORTER.md) for the
  operator guide, an example Grafana dashboard, and a `prometheus.yml` scrape
  config.
- **XDNA1 (Phoenix / Hawk Point) capture profile** — documented evidence that
  `xdna-top` runs unmodified on the first-generation Ryzen AI NPU
  (`RyzenAI-npu1`, `aie2`): NPU detection, per-context attribution against a live
  hardware context, and iGPU sysfs auto-discovery all succeed with
  `degraded.overall=false`. Claims are scoped to the one measured box; see
  [docs/platforms/xdna1-phoenix-hawk-point.md](docs/platforms/xdna1-phoenix-hawk-point.md)
  and its raw capture artifacts.

### Changed

- **`snapshot` diagnoses `xrt-smi` mmap `EAGAIN`** — a low `RLIMIT_MEMLOCK`
  makes `xrt-smi`'s `MAP_LOCKED` firmware mmap fail with `EAGAIN`, which
  otherwise reads as "NPU absent". The snapshot now surfaces `RLIMIT_MEMLOCK` as
  the likely cause (parsing the attempted mmap length) with a narrowed
  remediation, instead of reporting an opaque failure.
- **Evidence Library cards** — the generated `docs/` index now renders visual
  benchmark cards (comparison + matrix) from YAML front-matter in
  `docs/experiments/`. Front-matter parsing moved to PyYAML, now declared in the
  `docs` dev extra.

## [0.2.0] - 2026-06-13

This release promotes the evidence core to a tagged version and adds a windowed
activity guard for proving NPU work happened during a specific request window
(for example, while a background LLM generation job runs concurrently).

### Added

- **Evidence core** — a set of composable, schema-versioned primitives for
  capturing reproducible NPU + iGPU evidence:
  - `snapshot` — capture a schema-versioned platform and telemetry snapshot.
  - `env-report` — render a Markdown report from a snapshot or a record stream.
  - `record` — stream typed JSONL telemetry events over a time window.
  - `mark` — append a typed, labelled marker event to a record stream.
  - `assert` — evaluate named pass/fail checks over a snapshot or record stream,
    with CI-friendly exit codes (`0` pass, non-zero fail).
  - `compare` — diff two snapshots and report high-signal platform drift.
  - `baseline` — save and re-check named local baseline snapshots.
- **`assert --between START END`** — windowed activity guard. Restricts the
  requested `--require-*` checks to the telemetry slice bounded by the first
  `START` mark and the last `END` mark, so a check can prove activity occurred
  *between two marks* (the real request window) rather than merely somewhere in
  the recording. The window is named in each check label, and resolution
  problems — a missing mark, an end that precedes the start, an empty window, or
  use against a `snapshot` artifact — fail honestly with a named reason and a
  non-zero exit instead of passing vacuously.
- **TUI theme registry** — selectable color themes via `--theme` /
  `XDNA_TOP_THEME` (and the `lemonade-top` entry point). Themes change only
  chrome and colors, never metric names, units, or measured values.
- **`tools/probe_sensors.py`** (dev tool, not shipped in the package) — a
  read-only, zero-root diagnostic that inventories which NPU/iGPU sensor signals
  the running hardware and driver stack actually expose, writing a citable
  `docs/sensor-probe-<kernel>.md` artifact. A signal that is not exposed is
  recorded as unavailable with a reason, never guessed.

### Changed

- Version bumped to `0.2.0` (`pyproject.toml`, `CITATION.cff`, package
  `__version__`).

## [0.1.0] - 2026-06-10

### Added

- Initial release: unified live NPU + iGPU telemetry TUI for AMD Strix Halo.
- iGPU `busy %` and `power (W)` from amdgpu sysfs with 60-second rolling
  sparklines; NPU hardware-context table (PID, submissions, completions, derived
  activity) from `xrt-smi`.
- `--json` mode for a single fused telemetry reading.
- Graceful degradation: panes flag themselves as degraded instead of crashing
  when a signal is missing.

[0.3.0]: https://github.com/boxwrench/xdna-top/releases/tag/v0.3.0
[0.2.0]: https://github.com/boxwrench/xdna-top/releases/tag/v0.2.0
[0.1.0]: https://github.com/boxwrench/xdna-top/releases/tag/v0.1.0
