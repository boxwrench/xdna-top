# Demo capture bundle (v0.1.0)

Raw telemetry captured *simultaneously* with `docs/screenshot.png` and `docs/demo.cast`,
so the counters visible in those artifacts can be cross-checked against independently
logged hardware telemetry. Capture performed on AMD Strix Halo (RyzenAI-npu5 + Radeon iGPU).

## What drove the load
- **iGPU scene** — ROCm `llama-server` (lemonade `rocm-stable` llama.cpp build) on
  `127.0.0.1:8094`, model `Qwen3.5-35B-A3B-MXFP4_MOE.gguf`, `--gpu-layers all`.
  This is the engine amdgpu sysfs reports as busy%/power.
- **NPU scene** — bare FastFlowLM `flm serve llama3.2:1b` on `127.0.0.1:13306`
  (process PID 158772 in the captures). Lemonade's own `flm:npu` recipe was
  `update_required` and its registry contains no FLM-recipe model, so the NPU was
  driven via bare FLM (the "local NPU server"), as specified.

## Files
| File | Description |
|------|-------------|
| `xdna-top_json_1hz.jsonl` | `xdna-top --json` logged at ~1 Hz across the whole capture (wall-clock + reading). |
| `xrt-smi_aie-partitions.log.gz` | Raw `xrt-smi examine --report aie-partitions` logged at ~1 Hz (gunzip to read). |
| `xrt-smi_at_screenshot.txt` | Raw xrt-smi snapshot taken at the instant of the PNG screenshot. |
| `xdna-top_json_at_screenshot.json` | `xdna-top --json` at the PNG screenshot instant. |
| `capture_instant.txt` | ISO timestamp of the PNG screenshot. |
| `screenshot_window_raw.png` | Uncropped gnome-screenshot of the real window (title bar "xdna-top"); `docs/screenshot.png` is this image cropped to TUI-only. |

## Counter corroboration (anti-fabrication cross-check)
- **PNG (`docs/screenshot.png`)** — NPU contexts show submissions/completions
  `8004 / 448 / 64 / 64` (ctx 1–4), frozen (generation complete). The same values
  appear in `xrt-smi_at_screenshot.txt` and recur throughout
  `xrt-smi_aie-partitions.log.gz`. NPU State reads **IDLE** while every context's
  Status column reads **Active** — i.e. the tool keys activity off submission *deltas*,
  not the status column.
- **Cast (`docs/demo.cast`)** — NPU ctx1 progression: idle `11607` (frozen) →
  active `12233/12232` (in-flight delta, ~t28s, `npu_active=True`) → done `12808/12808`
  (~t34s, frozen, State returns IDLE while Status stays Active). The frozen value
  `12808` appears 524× in `xrt-smi_aie-partitions.log.gz`.

## Recording method note
`docs/demo.cast` is a valid asciinema v2 cast recorded via a small pty harness
(132×38) writing real terminal output with timestamps. The `asciinema` CLI itself
defaulted its pty to 80×24 in this headless environment and the TUI failed to render
under its nested pty; the harness captures the *actual* running TUI byte stream.
