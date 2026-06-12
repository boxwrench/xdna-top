# Session Handoff

This note is for picking up `xdna-top` work across sessions without relying on
chat history. Keep it public, generic, and free of private project names.

## Current Milestone

`v0.2`: evidence core implementation.

The first evidence command, `xdna-top snapshot`, has started. Continue preserving
the current runtime behavior of:

- `xdna-top`
- `xdna-top --json`
- `lemonade-top`

The goal is to build the evidence commands without turning `xdna-top` into a
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
- [../tests/test_xdna_top.py](../tests/test_xdna_top.py) and
  [../tests/test_gauge.py](../tests/test_gauge.py): current behavior checks
- [../tests/test_snapshot.py](../tests/test_snapshot.py): snapshot schema and
  degraded-path checks

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

Continue `v0.2` with `env-report`.

Recommended order:

1. Implement `xdna-top env-report <snapshot.json> --markdown`.
2. Render only facts captured in the snapshot; do not probe the system again.
3. Include platform, command availability, backend provenance, degraded flags,
   one fused reading, and NPU/iGPU visibility.
4. Add tests for healthy and degraded snapshot reports.
5. After `env-report`, implement `record`, then `assert`.

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
