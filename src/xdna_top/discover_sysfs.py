"""Compatibility path discovery, bound to the first amdgpu DRM device."""

import json
from pathlib import Path

from xdna_top.amdgpu import discover_gpus, read_text


def discover_sysfs(bench_dir: Path | str | None = None) -> dict:
    """Read-only discovery; explicitly supplied bench_dir materializes an override."""
    gpus = discover_gpus()
    gpu = gpus[0] if gpus else None
    power_path = None
    if gpu and gpu.hwmon:
        for file in ("power1_average", "power1_input"):
            candidate = gpu.hwmon / file
            if read_text(candidate) is not None:
                power_path = str(candidate)
                break
    result = {
        "gpu_busy_path": str(gpu.device / "gpu_busy_percent") if gpu else None,
        "gpu_power_path": power_path,
        "status": "AMD GPU found" if gpu else "CPU-only fallback",
    }
    if bench_dir:
        directory = Path(bench_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "e0_sysfs.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(discover_sysfs(), indent=2))
