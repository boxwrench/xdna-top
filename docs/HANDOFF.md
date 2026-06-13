# Session Handoff

This note is for picking up `xdna-top` work across sessions without relying on
chat history. Keep it public, generic, and free of private project names.

## Current Milestone

`v0.2`: evidence core — implemented.

All four evidence-core commands have landed: `xdna-top snapshot`,
`xdna-top env-report`, `xdna-top record`, and `xdna-top assert`. `v0.3` is now in
progress: `xdna-top compare` and `xdna-top baseline` are implemented; the only
remaining v0.3 work is the read-only direct AMDXDNA backend, which needs the
hardware and belongs on its own branch.

Continue preserving the current runtime behavior of:

- `xdna-top`
- `xdna-top --json`
- `lemonade-top`

The goal is to keep building evidence commands without turning `xdna-top` into a
general AMDGPU monitor.

## On-Hardware Validation (pending)

Important: every command added in the `v0.2`/`v0.3` evidence work and the theme
registry was developed and tested **off the target hardware**. Coverage so far is
unit tests with mocked `xrt-smi`/sysfs plus degraded-path smoke tests on a machine
with no NPU. None of it has yet been exercised against a real Strix Halo NPU +
iGPU, so the non-degraded paths are unproven on real silicon.

When back on the target machine, validate the full surface end to end and record
results in the devlog:

- [ ] `python3 -m pytest -q` passes on the target box.
- [ ] `xdna-top` TUI shows live iGPU busy/power sparklines and a populated NPU
      context table (not the degraded banners).
- [ ] `xdna-top --json` reports real values with `igpu_degraded`/`npu_degraded`
      both false.
- [ ] `xdna-top snapshot` captures a real NPU BDF/name, accel device present,
      and per-context PID/submission/completion data; `degraded.overall` is false.
- [ ] `xdna-top env-report <snapshot.json> --markdown` renders those real facts.
- [ ] `xdna-top record --duration 10 --interval 0.2 --out trial.jsonl` during a
      local LLM workload captures rising submission counters; interleave
      `xdna-top mark` calls and confirm marks land in the stream.
- [ ] `xdna-top env-report trial.jsonl` shows non-zero activity and the marks.
- [ ] `xdna-top assert trial.jsonl --require-npu-activity` exits `0` during a
      workload and non-zero on an idle capture; `assert snapshot.json
      --require-npu --require-context-source` passes on healthy hardware.
- [ ] `xdna-top compare` flags real drift between two snapshots (for example
      before/after an `xrt`/kernel update).
- [ ] `xdna-top baseline save known-good` then `baseline check known-good` after
      an update behaves as expected.
- [ ] `lemonade-top` and `xdna-top --theme <name>` (each of `default`, `lemonade`,
      `paper`, `phosphor`, `amber`, `halo`) render correctly in a real terminal;
      confirm themes change only colors/chrome, never metrics, units, or values.

Until this checklist is done, treat non-degraded behavior as unverified and keep
claims about live capture appropriately hedged.

## Current Direction

`xdna-top` should be framed as a Ryzen AI workload-evidence tool, not as a
general AMDGPU monitor.

The project should stay complementary to
[`amdgpu_top`](https://github.com/Umio-Yasuno/amdgpu_top):

- use `amdgpu_top` for broad AMDGPU/APU monitoring
- use `xdna-top` for NPU context ownership, submission/completion deltas, and
  concurrent iGPU activity during local AI workloads
- take inspiration from direct driver-facing telemetry, but keep this project
  scoped to evidence and attribution

## Files To Read First

Read these before changing implementation:

- [ROADMAP.md](ROADMAP.md): release order, scope boundaries, and issue policy
- [SNAPSHOT-SCHEMA.md](SNAPSHOT-SCHEMA.md): artifact shape for `snapshot`,
  `env-report`, `record`, `assert`, `compare`, and `baseline`
- [HOW-IT-WORKS.md](HOW-IT-WORKS.md): teaching guide, now including the
  "From live view to evidence" tour of the evidence commands
- [THEMES.md](THEMES.md): theme list, `--theme`/`XDNA_TOP_THEME` usage, and how to
  contribute a theme
- [../README.md](../README.md): public positioning and related-tools language
- [index.html](index.html): the GitHub Pages dashboard; its interactive glossary
  now has an "Evidence" category covering snapshot/record/mark/assert/compare/baseline

Implementation entry points:

- [../src/xdna_top/main.py](../src/xdna_top/main.py): current CLI, TUI, theme
  rendering, and `--json`
- [../src/xdna_top/gauge.py](../src/xdna_top/gauge.py): sysfs reads, XRT probe,
  context parsing, and fused reading model
- [../src/xdna_top/snapshot.py](../src/xdna_top/snapshot.py): snapshot artifact
  builder and JSON writer
- [../src/xdna_top/env_report.py](../src/xdna_top/env_report.py): Markdown
  report renderer for captured snapshots
- [../src/xdna_top/record.py](../src/xdna_top/record.py): JSONL telemetry
  recorder, sampling loop, and event builders
- [../src/xdna_top/assertions.py](../src/xdna_top/assertions.py): artifact
  loader, named checks, and CI exit codes for `assert`
- [../src/xdna_top/compare.py](../src/xdna_top/compare.py): high-signal snapshot
  diff rules and CI exit codes for `compare`
- [../src/xdna_top/baseline.py](../src/xdna_top/baseline.py): named snapshot
  save/check/list workflow over `snapshot` + `compare`
- [../tests/test_xdna_top.py](../tests/test_xdna_top.py) and
  [../tests/test_gauge.py](../tests/test_gauge.py): current behavior checks
- [../tests/test_snapshot.py](../tests/test_snapshot.py): snapshot schema and
  degraded-path checks
- [../tests/test_env_report.py](../tests/test_env_report.py): Markdown report
  rendering and invalid-input checks
- [../tests/test_record.py](../tests/test_record.py): record timing, JSONL
  shape, and degraded-path checks
- [../tests/test_assertions.py](../tests/test_assertions.py): artifact loading,
  per-check pass/fail, and exit codes for both artifact types
- [../tests/test_compare.py](../tests/test_compare.py): per-rule change/regression
  classification and compare exit codes
- [../tests/test_baseline.py](../tests/test_baseline.py): name/path safety,
  save/check round trip, and baseline exit codes

## Completed Groundwork

- README now states that the current NPU attribution path is XRT and the
  planned low-level probe path is AMDXDNA DRM IOCTLs through `/dev/accel/*`.
- README now acknowledges `amdgpu_top` as the broad AMDGPU/APU monitor and
  positions `xdna-top` as complementary.
- Roadmap now has versioned milestones from `v0.1.x` through `v0.5`.
- Roadmap now has telemetry inventory, in-scope/out-of-scope lines, and issue
  policy for new signal requests.
- Snapshot schema draft now includes `schema_version`, backend provenance,
  degraded reasons, direct backend fields, and sensor provenance.
- Teaching docs and glossary now explain DRM, IOCTL, `/dev/accel`, AMDXDNA,
  XRT, column utilization, and backend provenance.
- The teaching guide (HOW-IT-WORKS.md) and the dashboard glossary (index.html)
  now also cover the evidence commands and concepts: snapshot, env-report,
  record, mark, assert, compare, baseline, schema version, and degraded flags.
- `xdna-top snapshot` now emits a schema-versioned JSON artifact using the
  current sysfs and XRT sources.
- Snapshot tests cover healthy and degraded paths without requiring real
  hardware.
- `xdna-top env-report <snapshot.json> --markdown` now renders a report from
  captured snapshot facts without probing the machine again.
- `xdna-top record --duration <s> --interval <s> --out <path>` now streams typed
  JSONL telemetry events (`meta`, `telemetry`, `summary`) framed by a header and
  footer, with per-context backend provenance and degraded flags preserved.
- Record tests cover sampling-window count, JSONL shape, and degraded paths with
  a deterministic fake clock and no real hardware.
- `xdna-top assert <artifact> --require-*` now evaluates named checks over both a
  snapshot JSON and a record JSONL stream, prints observed values next to
  requirements, and exits `0`/`1`/`2` for CI. Checks report unavailable signals
  honestly instead of guessing.
- Assert tests cover the artifact loader, every check on healthy and degraded
  inputs, and exit codes, with no real hardware.
- `xdna-top compare before.json after.json` now diffs two snapshots over the
  curated high-signal field list, tagging each change `CHANGED` or `REGRESSION`,
  and exits `0`/`1`/`2` like `git diff --exit-code` so it can gate CI and back
  `baseline check`.
- Compare tests cover each rule's change/regression classification (including the
  conditional `aie-partitions` rule) and the CLI exit codes, with no real
  hardware.
- `xdna-top baseline save/check/list` now wraps `snapshot` + `compare` into a
  named local workflow under the XDG state dir (override with `--dir`). Names are
  validated against path traversal; `check` reuses `compare` exit codes and never
  re-probes when the baseline is absent.
- Baseline tests cover name/path safety, the save/check round trip, drift
  detection, and exit codes by mocking `build_snapshot` (no real hardware).
- `xdna-top mark "<label>" --out <jsonl>` now appends a typed
  `{"type":"mark",...}` event to a record stream (append mode, creating the file
  if needed), so scripts can annotate trials alongside `record`.
- `xdna-top env-report` now accepts a record JSONL stream as well as a snapshot:
  it auto-detects the artifact and renders a "Telemetry Report" (recording window,
  host, observed activity, first/last reading, and any marks) reusing the
  Markdown helpers. Snapshot rendering is unchanged.
- Mark and record-report tests cover the mark event/append behavior and the
  record Markdown summary, including marks, with no real hardware.
- A TUI theme registry now backs `--theme <name>` (and `XDNA_TOP_THEME`), with
  `default`, `lemonade`, `paper`, `phosphor`, `amber`, and `halo`. `lemonade-top`
  is now a thin alias that defaults to the `lemonade` theme but honors `--theme`.
  New themes are `dataclasses.replace` data entries that vary only colors and
  chrome; metric names, states, units, and values are theme-invariant.
- Theme tests assert the registry, name/env resolution, `--list-themes`, and a
  claims-accuracy guard that every theme still renders the PID/Submissions/
  Completions/Status columns, units, state values, and observed numbers (which
  also validates that every theme's color names are renderable).

## Next Public Issues

Public tracking issues:

- [#1](https://github.com/boxwrench/xdna-top/issues/1): `v0.2:
  implement xdna-top snapshot`
- [#2](https://github.com/boxwrench/xdna-top/issues/2): `v0.2:
  implement xdna-top env-report`
- [#3](https://github.com/boxwrench/xdna-top/issues/3): `v0.2:
  implement xdna-top record`
- [#4](https://github.com/boxwrench/xdna-top/issues/4): `v0.2:
  implement xdna-top assert`
- [#5](https://github.com/boxwrench/xdna-top/issues/5): `v0.3:
  prototype read-only AMDXDNA IOCTL backend`
- [#6](https://github.com/boxwrench/xdna-top/issues/6): `v0.3:
  implement snapshot compare`
- [#7](https://github.com/boxwrench/xdna-top/issues/7): `v0.3:
  implement local baseline save/check`
- [#8](https://github.com/boxwrench/xdna-top/issues/8): `v0.4:
  design supervised workload-check`
- [#9](https://github.com/boxwrench/xdna-top/issues/9): `v0.5:
  add theme registry and THEMES.md`

Each issue should include:

- the command or signal being added
- the exact JSON/report output expected
- graceful-degradation behavior
- tests required
- claim language to avoid overstatement

## Next Implementation Step

The v0.2 evidence core plus `xdna-top compare` and `xdna-top baseline` are done.
The remaining v0.3 item, the read-only `amdxdna_ioctl` backend (#5), needs the
hardware and should be built on a separate `exp/amdxdna-backend` branch under the
rules in the "Backend Experiment" section below. `compare` and `baseline` do not
depend on it.

All off-hardware command and theme work for `v0.2`/`v0.3`/`v0.5` is now done. The
remaining items genuinely need the target machine:

1. Run the "On-Hardware Validation" checklist above and record results in the
   devlog. This is the top priority once hardware is available, since it is what
   turns the off-hardware unit/degraded coverage into proven live behavior.
2. Build the read-only `amdxdna_ioctl` backend (#5) on a separate
   `exp/amdxdna-backend` branch under the "Backend Experiment" rules below.
3. Only after the evidence core is proven on hardware, design `workload-check`
   (v0.4). It depends on the evidence artifact and has the largest design surface.

Smaller community follow-ups that stay off-hardware: add more candidate themes
from [ROADMAP.md](ROADMAP.md) (`fabric`, `team-red`, `lime`, `grapefruit`) as data
entries, and a screenshot gallery for `THEMES.md`. These are good
first-contribution issues, not blockers.

## Backend Experiment

Use a separate branch for direct AMDXDNA work:

```bash
git switch -c exp/amdxdna-backend
```

Rules for that branch:

- read-only probes only
- no root requirement
- no hard dependency on a specific kernel version
- every signal carries provenance
- missing sensors degrade explicitly
- do not replace XRT context attribution until direct PID/context/submission
  data is available

## Claim Language

Use measured language:

```text
Observed PID 1234 context 1 submission_delta=42 completion_delta=42 during the request window.
```

Avoid stronger language:

```text
The request ran on the NPU.
```

Reason: concurrent workloads can move counters too. `xdna-top` can provide
strong evidence, but it should not claim causality beyond what it measured.

## Validation Commands

Before handing off or committing:

```bash
python3 -m pytest -q
git diff --check
```

Also scan public docs for any private project names or local-only code names
before committing. The public repo should only use generic examples.
