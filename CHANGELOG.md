# Changelog

All notable changes to `xdna-top` are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.0]: https://github.com/boxwrench/xdna-top/releases/tag/v0.2.0
[0.1.0]: https://github.com/boxwrench/xdna-top/releases/tag/v0.1.0
