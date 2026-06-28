# xdna-top Devlog

## 2026-06-28 — Read-only AMDXDNA IOCTL backend (#5), validated on real silicon

Built the v0.3 direct-AMDXDNA backend (`src/xdna_top/amdxdna_ioctl.py`) and wired
it into `snapshot` (`devices.npu.ioctl`), `env-report`, and backend provenance.

Struct layouts and ioctl request numbers were taken from the actual amdxdna uAPI
header on this box (`include/uapi/drm/amdxdna_accel.h`, kernel 6.17) and
cross-checked by compiling a `sizeof`/ioctl probe against the headers:
`DRM_IOCTL_VERSION = 0xC0406400`, `DRM_IOCTL_AMDXDNA_GET_INFO = 0xC0106447`. The
Python module computes its request numbers from `ctypes.sizeof` and a test
asserts them against those header values, so struct drift fails loudly.

**Validated read-only against a real NPU** (this machine has `/dev/accel/accel0`
openable via the `render` group). Measured, not inferred:

- DRM version: `amdxdna_accel_driver` **0.6.0**, desc "AMD XDNA DRM implementation".
- AIE version **1.1**; AIE metadata **cols=8, rows=6, col_size=504**.
- Clocks: MP-NPU **1267 MHz**, H **1800 MHz** (clock frequencies, not a busy %).
- Firmware **1.1.2.65**.
- `QUERY_SENSORS` ioctl returns **EOPNOTSUPP (errno 95)** on this kernel, so
  `supports_sensors=false`, `sensors.available=false`,
  `reason="ioctl_errno_95"`. This is exactly the "unsupported sensor" degraded
  path the acceptance criteria call for — observed on hardware, not mocked.

A live `xdna-top snapshot` → `env-report` round-trip on this box renders:
`NPU direct backend (amdxdna ioctl): driver 0.6.0, AIE 1.1, clocks mp-npu 1267
MHz / h 1800 MHz, fw 1.1.2.65`.

Claims precision: clocks are reported as MHz frequencies; sensor values are not
fabricated (the SENSORS query is unsupported here, so no sensor numbers are
emitted). XRT remains the per-context PID/submission attribution path; this
backend adds identity + static metadata only. Suite: **189 passed** (12 mocked
ioctl unit tests covering unavailable / unreadable / non-amdxdna / unsupported-
sensor / happy paths, plus a snapshot-wiring test).

## 2026-06-14 — On-hardware session: test suite green, but two handoff features are absent

On the target Strix Halo box (kernel `6.17.0-35-generic`, `/dev/accel/accel0`,
`amdxdna` bound at `0000:c6:00.1`, `xrt-smi` present). NPU probes healthy:
`xrt-smi` reports `[0000:c6:00.1] : RyzenAI-npu5`; a real `snapshot` has
`bdf=0000:c6:00.1` with `igpu_degraded=false` and `npu_degraded=false`.

### Step 1 — full test suite (DONE, passing)
- `pip install -e .` is blocked here by PEP 668 (externally-managed env), but the
  package is already importable via the existing `src/xdna_top.egg-info` editable
  install; `import xdna_top, tty, termios` succeeds.
- `python3 -m pytest -q` → **131 passed**.
- The two modules that could not even be collected on Windows now collect and pass
  on Linux: `tests/test_xdna_top.py` + `tests/test_themes.py` → **34 passed**
  (`tty`/`termios` import fine). This closes the first HANDOFF checklist item.

### Step 2 — BLOCKED: the two features to validate are not in this repository
The handoff described per-context process names (`resolve_process_name()`,
`parse_xrt_smi()` setting `ctx["process_name"]`, a TUI "Process" column, field
flowing into snapshot/record JSON) and most-active-first ordering
(`sort_contexts_by_activity()`). **None of this code is present** on `main`, on any
other local/remote branch (`claude/bold-planck-64mrjz` is behind main and also
lacks it), or in stash. Measured, not inferred:
- No `resolve_process_name` / `sort_contexts_by_activity` anywhere under `src/`.
- `gauge.py:parse_xrt_smi()` (lines 215–243) builds each context with only
  `pid, ctx_id, submissions, status, completions` — it never sets `process_name`.
- `snapshot.py:66` does `ctx.get("process_name")`, which is **always `None`**
  because the key is never created. The only trace of the feature is this
  passthrough plus a `process_name: None` placeholder in a test fixture.
- No "Process" column in `main.py`.
Conclusion: the off-hardware work was apparently never pushed/transferred from the
Windows session to this box. There is nothing on silicon to validate yet.

A second, independent blocker: the NPU is currently **idle**. `xrt-smi` reports
"No hardware contexts running on device" and a real snapshot has
`/devices/npu/contexts = []`. So even with the feature code present, validating the
Process column / ordering / non-null `process_name` would require a live NPU
workload running concurrently.

### Step 3 — partial (existing v0.2/v0.3 surface on real silicon)
Genuine on-hardware results for features that DO exist:
- `snapshot` captured a real NPU BDF (`0000:c6:00.1`), accel device present, both
  degraded flags false — but `contexts: []` and `degraded.overall=false` only
  because the box is idle (no workload was running during this capture).
Remaining HANDOFF on-hardware items (TUI sparklines, `--json` live values, record
during a workload, assert/compare/baseline, themes in a real terminal) still
pending — they want a live LLM workload and are next.

Claims precision: I confirmed the test suite and that the NPU stack is live and
non-degraded. I did **not** validate process-name attribution or activity ordering
because that code does not exist in the tree, and I did not capture live context
activity because no workload was running.

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

## 2026-06-13 — v0.3: `xdna-top baseline`
Implemented the named-baseline workflow, the last off-hardware v0.3 command.

1. **Workflow (`src/xdna_top/baseline.py`)**:
   - `baseline save <name>` captures a snapshot and stores it under the XDG state
     dir (`$XDG_STATE_HOME/xdna-top/baselines/`, default `~/.local/state/...`),
     overridable with `--dir`. `baseline check <name>` re-snapshots and diffs
     against the saved one via `compare.compare_snapshots`. `baseline list` shows
     saved names.
   - `check` reuses compare's exit codes (`0` clean, `1` drift); `2` covers a
     missing baseline, an unsafe name, or an unreadable snapshot. Only `save`
     probes hardware.
   - Baseline names are validated against path traversal (`[A-Za-z0-9._-]`, no
     `/` or `..`), and `check` never re-probes when the baseline is absent.
2. **CLI**: added a `baseline` subcommand with nested `save`/`check`/`list`
   actions and dispatch in `main.py`.
3. **Tests (`tests/test_baseline.py`)**: XDG/override path resolution, name/path
   safety, save→check round trip, drift detection, missing-baseline guard, and
   exit codes — all by mocking `build_snapshot`, no real hardware. Full suite:
   83 passed.
4. **Docs**: README quick start/features/roadmap; storage + exit-code note in the
   ROADMAP baseline section; HANDOFF marks the off-hardware v0.3 commands done,
   leaving only the hardware-gated `amdxdna_ioctl` backend for `exp/`.

Verified off-hardware: ran the full `save → list → check` flow against a temp
`--dir`, confirming clean exit 0 vs itself, exit 1 on a synthesized degraded
regression, and exit 2 for a missing name and a path-traversal name.

## 2026-06-13 — Stream annotations: `mark` and record-aware `env-report`
Two small off-hardware additions that round out the recording workflow.

1. **`xdna-top mark` (`src/xdna_top/record.py`)**:
   - `mark "<label>" --out <jsonl>` appends a typed
     `{"type":"mark","schema_version":"1.0","ts":...,"label":...}` event to a
     record stream in append mode (creating the file/dirs if absent), or to
     stdout. Shares the record line serializer (`_dump`).
   - Lets scripts annotate trials (`trial-1-start`, `phase-2-start`) inline
     with `record` telemetry.
2. **`env-report` from a record stream (`src/xdna_top/env_report.py`)**:
   - `env_report_main` now auto-detects the artifact via the shared
     `assertions.load_artifact`. Snapshots still render the "Environment Report";
     record streams render a new "Telemetry Report" (recording window, host,
     observed activity — active samples, max submission delta, degraded samples,
     context sources/PIDs — first/last reading, and a Marks section).
   - Snapshot rendering is unchanged; host/reading rendering is factored into
     shared helpers.
3. **CLI**: added the `mark` subcommand and dispatch; broadened the `env-report`
   positional help to "Snapshot JSON or record JSONL path".
4. **Tests**: mark event/append behavior (append without truncation, create when
   absent) in `test_record.py`; record Markdown summary including marks in
   `test_env_report.py`; CLI dispatch for `mark`. Full suite: 89 passed.
5. **Docs**: README quick start/features, ROADMAP env-report + Event markers
   notes, and HANDOFF (these backlog items done; theme registry now the only
   remaining off-hardware work).

Verified off-hardware: recorded a stream, appended two marks, and rendered it
with `env-report` — the Telemetry Report and Marks section both appear, and the
snapshot path of `env-report` still renders the Environment Report.

## 2026-06-13 — Theme registry and on-hardware validation note
Implemented the v0.5 theme registry and recorded the pending on-hardware
validation work.

1. **Theme registry (`src/xdna_top/main.py`)**:
   - Added a `THEMES` dict and `resolve_theme`/`list_themes`, backing
     `--theme <name>` and `XDNA_TOP_THEME` (resolution order `--theme` > env >
     entry-point default). Unknown `--theme` exits 2 and lists valid names;
     `--list-themes` prints them.
   - New themes (`paper`, `phosphor`, `amber`, `halo`) are
     `dataclasses.replace(DEFAULT_THEME, ...)` data entries that vary only colors
     and chrome and keep the accurate pane titles. `lemonade-top` is now a thin
     alias defaulting to `lemonade` but honoring `--theme`.
2. **Tests (`tests/test_themes.py`)**: registry contents, name/env resolution,
   `--list-themes`, CLI selection/error paths, and a parametrized
   claims-accuracy guard that every theme still renders the PID/Submissions/
   Completions/Status columns, units, state values, and observed numbers (also
   proving every theme's color names are renderable). Full suite: 106 passed.
3. **Docs**: new `docs/THEMES.md` (usage, theme table, contribution rules);
   README quick start/features/roadmap; ROADMAP theme-registry note; and a new
   "On-Hardware Validation (pending)" checklist in HANDOFF.

On-hardware note: per the plan, HANDOFF now makes explicit that every v0.2/v0.3
command and the themes were built and tested off the target hardware (mocked
plus degraded smoke tests), and lists the full end-to-end checklist to run on the
real Strix Halo machine before treating non-degraded behavior as proven.

Verified off-hardware: `--list-themes`, `--theme <name> --json`, unknown-theme
error, and `XDNA_TOP_THEME` via `lemonade-top` all behave as expected; all six
themes render their panels without error.

