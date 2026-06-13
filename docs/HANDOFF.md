# Session Handoff

This note is for picking up `xdna-top` work across sessions without relying on
chat history. Keep it public, generic, and free of private project names.

## Current Milestone

`v0.2`: evidence core — implemented.

All four evidence-core commands have landed: `xdna-top snapshot`,
`xdna-top env-report`, `xdna-top record`, and `xdna-top assert`. The next
milestone is `v0.3` (direct backend probes plus `compare` and `baseline`).

Continue preserving the current runtime behavior of:

- `xdna-top`
- `xdna-top --json`
- `lemonade-top`

The goal is to keep building evidence commands without turning `xdna-top` into a
general AMDGPU monitor.

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
  `env-report`, `compare`, and `baseline`
- [HOW-IT-WORKS.md](HOW-IT-WORKS.md): teaching guide and glossary
- [../README.md](../README.md): public positioning and related-tools language

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

The v0.2 evidence core is complete. Move to `v0.3`, starting with the commands
that build on the snapshot schema and need no hardware:

1. Implement `xdna-top compare before.json after.json` as a pure diff over two
   snapshots. Emphasize the high-signal changes listed under "Compare Guidance"
   in [SNAPSHOT-SCHEMA.md](SNAPSHOT-SCHEMA.md) (kernel, accel device, backend
   availability, sensor support, BDF, sysfs paths, `degraded.overall` regressions)
   rather than generic JSON noise. Exit non-zero when high-signal drift is found.
2. Implement `xdna-top baseline save <name>` / `baseline check <name>` as a thin
   workflow over `snapshot` + `compare`, storing named snapshots under a local
   directory (for example `~/.local/state/xdna-top/baselines/`). `check` reuses
   the compare logic; only `save` probes hardware.
3. The read-only `amdxdna_ioctl` backend (#5) is the hardware-dependent part of
   v0.3 and should stay on a separate `exp/amdxdna-backend` branch. `compare` and
   `baseline` do not depend on it.

Off-hardware backlog (good to pick up in any order, all testable with mocks):

- `compare` (#6) and `baseline` check-side (#7) — pure functions over snapshots.
- `xdna-top mark` — append a typed `{"type":"mark",...}` line to a record JSONL.
- `env-report` from a `record` stream — summarize first/last reading and any
  observed activity, reusing the Markdown renderer.
- Theme registry behind `--theme <name>` (#9), keeping `lemonade-top` as an alias.

Do not start with `workload-check`. It depends on the evidence artifact and has
the largest design surface.

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
