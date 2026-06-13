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

