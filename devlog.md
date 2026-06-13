# xdna-top Devlog

## 2026-06-10 — Initial standalone extraction
Extracted `xdna-top` from the private development tree as a standalone project.

### 1. Scrub Checks
- Checked for private imports/references and absolute personal paths:
  - whole-tree information-policy scrub clean.


### 2. Standalone Verification
Created a clean installation and ran the monitor:
```bash
pip install -e .
xdna-top --json
```
Output:
```json
{
  "gpu_busy_pct": 0,
  "gpu_power_w": 33.018,
  "npu_active": false,
  "state": "IDLE",
  "ts": 1781116715.8938465
}
```

Unit tests:
```bash
pytest tests/
```
Output: 11 passed.

## 2026-06-10 — Telemetry degradation, device discovery, and clean captures
Implemented core improvements based on initial review:
1. **Explicit Telemetry Degradation**:
   - `read_igpu` returns `degraded: bool` flag and sets `gpu_busy_pct`/`gpu_power_w` to `null` if sysfs files are missing/unreadable.
   - iGPU panel displays a warning banner and changes border style to red when degraded.
   - Added `--json` output `igpu_degraded` and `npu_degraded` fields.
2. **NPU Device Discovery**:
   - Automatic NPU device BDF discovery via general `xrt-smi examine` parsing.
   - Removed all hardcoded device BDFs (`0000:c6:00.1` is no longer a literal).
   - Added `--npu-device` CLI parameter to override discovery.
3. **Clean Captures**:
   - Replaced mocked screenshot in documentation with a real, vector-based SVG layout capture `docs/screenshot.svg` displaying exact calibration telemetry values.
4. **Citation Formatting**:
   - Corrected person-form formatting in `CITATION.cff`.

## 2026-06-13 — Evidence core: `xdna-top record`
Implemented the third v0.2 evidence command, `xdna-top record`, which streams
typed JSONL telemetry events over a time window.

1. **Recorder (`src/xdna_top/record.py`)**:
   - `record --duration <s> --interval <s> --out <path>` writes a JSONL stream
     framed by a `meta` header and a `summary` footer, with one `telemetry`
     event per sample in between.
   - Each `telemetry` event carries the fused gauge reading (with
     `igpu_degraded`/`npu_degraded` flags preserved) and per-context NPU data
     tagged with its `source` backend.
   - Deadline-driven sampling loop with injectable clock/sleep; `duration 0`
     yields a single sample; `KeyboardInterrupt` stops cleanly and still writes
     a summary. Lines are flushed per-write so the stream is tail-able and
     partial artifacts stay valid.
   - Exposed `host_facts()` from `snapshot.py` (was `_host_facts`) so the record
     header reuses the same host block.
2. **CLI**: wired `record` subcommand and dispatch into `main.py`; the
   `lemonade-top` alias stays command-free.
3. **Tests (`tests/test_record.py`)**: sampling-window count via a deterministic
   fake clock, JSONL event shape, degraded path (no `xrt-smi`), interrupt
   handling, and CLI dispatch. Full suite: 36 passed.
4. **Docs**: README quick start/features, a Record Stream section in
   `SNAPSHOT-SCHEMA.md`, and HANDOFF updated to point the next step at `assert`.

Verified off-hardware: `xdna-top record` produces a valid degraded JSONL
artifact (null readings, empty contexts, exit 0) with no `xrt-smi` or sysfs
present.

## 2026-06-13 — Evidence core complete: `xdna-top assert`
Implemented the fourth and final v0.2 evidence command, `xdna-top assert`,
closing out the evidence core (`snapshot`, `env-report`, `record`, `assert`).

1. **Checks (`src/xdna_top/assertions.py`)**:
   - `assert <artifact> --require-*` loads either a snapshot JSON object or a
     record JSONL stream (auto-detected) and evaluates named checks.
   - Checks: `require-npu`, `require-npu-sensors`, `require-context-source`,
     `require-igpu`, `require-not-degraded`, `require-npu-activity`. Each prints
     the observed value next to its requirement; snapshot and record artifacts
     read the appropriate field/aggregate.
   - `require-npu-activity` on a record stream computes the max per-context
     submission delta across the window (plus active-sample count) so it proves
     measured counter movement, not assumed causality.
   - Exit codes: `0` all pass, `1` any fail, `2` usage error (no `--require-*`)
     or unreadable artifact. Unavailable signals fail honestly rather than being
     guessed.
2. **CLI**: wired the `assert` subcommand from the check registry and added
   dispatch in `main.py`.
3. **Tests (`tests/test_assertions.py`)**: artifact loader/classification, every
   check on healthy and degraded inputs for both artifact types, output strings,
   and CLI exit codes — all hardware-free. Full suite: 52 passed.
4. **Docs**: README quick start/features and v0.2 roadmap box checked; an
   expanded Assertion Guidance table in `SNAPSHOT-SCHEMA.md`; HANDOFF marks the
   evidence core done and repoints the next step at v0.3 (`compare`, `baseline`),
   with an explicit off-hardware backlog.

Verified off-hardware: built real degraded `snapshot`/`record` artifacts on this
no-hardware host and ran `assert` against them — correct FAILs and exit 1 on
degraded data, exit 2 on no-requirements, and exit 0 PASS lines on a synthesized
healthy snapshot.

## 2026-06-13 — v0.3 start: `xdna-top compare`
Implemented the first v0.3 command, `xdna-top compare`, an upgrade canary that
diffs two snapshots and surfaces only high-signal platform drift.

1. **Diff (`src/xdna_top/compare.py`)**:
   - `compare before.json after.json` runs a curated rule set over the
     "Compare Guidance" fields (schema version, kernel release, NPU primary and
     sensor backends, DRM version, sensor support, `xrt-smi` availability, the
     conditional `aie-partitions` report, accel device presence, NPU BDF,
     hardware-context presence, iGPU sysfs paths, `degraded.overall`).
   - Each change is tagged `CHANGED` or `REGRESSION`; capability losses are
     regressions while neutral drift and recoveries are plain changes.
   - Exit codes mirror `git diff --exit-code`: `0` clean, `1` drift, `2` on an
     unreadable snapshot — so it can gate CI and back `baseline check`.
   - The `aie-partitions` rule only fires on a working `0` report breaking, and
     only when contexts depend on `xrt-smi`, to avoid noise.
2. **CLI**: wired the `compare` subcommand and dispatch in `main.py`.
3. **Tests (`tests/test_compare.py`)**: per-rule change/regression classification,
   the conditional `aie-partitions` rule, format strings, and CLI exit codes —
   all hardware-free. Full suite: 66 passed.
4. **Docs**: README quick start/features and roadmap; an exit-code note added to
   the Compare Guidance in `SNAPSHOT-SCHEMA.md`; HANDOFF repointed at `baseline`.

Verified off-hardware: captured a real snapshot, synthesized a drifted copy
(kernel bump, accel0 gone), and confirmed `compare` reports `CHANGED`/`REGRESSION`
with exit 1, and exit 0 on identical inputs.

