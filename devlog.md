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

