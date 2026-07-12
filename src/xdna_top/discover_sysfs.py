"""Discovers the working sysfs paths for iGPU telemetry (busy % and power)."""

import json
import os
from pathlib import Path


def discover_sysfs(bench_dir: Path | str | None = None) -> dict:
    """Probe candidate sysfs paths for iGPU monitoring.

    ``bench_dir`` is retained for callers that explicitly want to materialize
    an ``e0_sysfs.json`` override. Normal runtime discovery passes ``None`` and
    remains entirely in memory.
    """
    result = {
        "gpu_busy_path": None,
        "gpu_power_path": None,
        "status": "CPU-only fallback",
    }
    
    # Check busy percent candidates
    busy_candidates = [
        "/sys/class/drm/card0/device/gpu_busy_percent",
        "/sys/class/drm/card1/device/gpu_busy_percent",
        "/sys/class/drm/card2/device/gpu_busy_percent",
    ]
    for p in busy_candidates:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    _ = f.read().strip()
                result["gpu_busy_path"] = p
                result["status"] = "iGPU found"
                break
            except (PermissionError, OSError):
                continue
                
    # Check power candidates
    hwmon_dir = Path("/sys/class/hwmon")
    if hwmon_dir.exists():
        for path in hwmon_dir.iterdir():
            power_file = path / "power1_input"
            name_file = path / "name"
            if power_file.exists():
                try:
                    with open(power_file, "r") as f:
                        _ = f.read().strip()
                    
                    name = ""
                    if name_file.exists():
                        with open(name_file, "r") as nf:
                            name = nf.read().strip()
                    
                    # Store power path. If we find 'amdgpu', we prefer it.
                    if name == "amdgpu" or not result["gpu_power_path"]:
                        result["gpu_power_path"] = str(power_file)
                except (PermissionError, OSError):
                    continue

    if bench_dir:
        b_dir = Path(bench_dir)
        b_dir.mkdir(parents=True, exist_ok=True)
        with open(b_dir / "e0_sysfs.json", "w") as f:
            json.dump(result, f, indent=2)
        
    return result


if __name__ == "__main__":
    res = discover_sysfs()
    print(json.dumps(res, indent=2))
