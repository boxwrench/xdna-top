"""Read-only, per-device amdgpu sysfs telemetry; unavailable values stay null.

Sensor mapping adapted from Project Maya commit 5d63c79 (Apache-2.0).
Discovery uses DRM card identities, not HIP indices or visibility masks.
"""

from pathlib import Path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def directories(path: Path) -> list[Path]:
    try:
        return sorted(path.iterdir())
    except OSError:
        return []


def number(path: Path, scale: int = 1) -> int | float | None:
    try:
        value = int(read_text(path))
        return value / scale if scale != 1 else value
    except (TypeError, ValueError):
        return None


def pcie_generation(path: Path) -> int | None:
    try:
        speed = float((read_text(path) or "").split()[0])
        return {2.5: 1, 5.0: 2, 8.0: 3, 16.0: 4, 32.0: 5, 64.0: 6}.get(speed)
    except (ValueError, IndexError):
        return None


# Conservative identity hints for the verified platforms, not a VRAM-size
# heuristic. Unlisted devices retain unknown type and the general APU caveat.
APU_IDS = {"0x1586", "0x13c0"}  # Strix Halo and the verified desktop integrated device
DISCRETE_IDS = {"0x744c", "0x7551"}  # Navi 31, Navi 48


class AmdGpu:
    """One DRM card and only its own hwmon sensors."""

    def __init__(self, card: Path, ids_path: Path):
        self.card = card.name
        self.device = (card / "device").resolve()
        self.hwmon = next((h for h in directories(self.device / "hwmon")
                           if read_text(h / "name") == "amdgpu"), None)
        self.device_id = read_text(self.device / "device")
        self.is_apu = (True if self.device_id in APU_IDS else
                       False if self.device_id in DISCRETE_IDS else None)
        self.name = read_text(self.device / "product_name")
        if not self.name:
            revision = read_text(self.device / "revision")
            for line in (read_text(ids_path) or "").splitlines():
                fields = line.split(",", 2)
                try:
                    if (len(fields) == 3 and int(fields[0], 16) == int(self.device_id, 16)
                            and int(fields[1], 16) == int(revision, 16)):
                        self.name = fields[2].strip() or None
                        break
                except (TypeError, ValueError):
                    continue
        self.name = self.name or f"AMD Radeon ({self.device_id or '?'}, PCI {self.device.name})"

    def read(self) -> dict:
        d, h = self.device, self.hwmon
        def sensor(file: str, scale: int = 1):
            return number(h / file, scale) if h else None

        power_source = "power1_average"
        power = sensor(power_source, 1_000_000)
        if power is None:
            power_source = "power1_input"
            power = sensor(power_source, 1_000_000)
        busy = number(d / "gpu_busy_percent")
        if busy is not None and not 0 <= busy <= 100:
            busy = None
        result = {
            "card": self.card, "bdf": d.name, "name": self.name,
            "device_id": self.device_id, "is_apu": self.is_apu,
            "busy_pct": busy,
            "vram_used_bytes": number(d / "mem_info_vram_used"),
            "vram_total_bytes": number(d / "mem_info_vram_total"),
            "gtt_used_bytes": number(d / "mem_info_gtt_used"),
            "gtt_total_bytes": number(d / "mem_info_gtt_total"),
            "edge_temp_c": sensor("temp1_input", 1000),
            "power_w": power, "power_cap_w": sensor("power1_cap", 1_000_000),
            "power_source": power_source if power is not None else None,
            "sclk_mhz": sensor("freq1_input", 1_000_000),
            "pcie_gen": pcie_generation(d / "current_link_speed"),
            "pcie_gen_max": pcie_generation(d / "max_link_speed"),
            "pcie_width": number(d / "current_link_width"),
            "pcie_width_max": number(d / "max_link_width"),
            "pcie_meaningful": False if self.is_apu else (True if self.is_apu is False else None),
        }
        result["unavailable"] = [k for k, v in result.items() if v is None]
        result["degraded"] = busy is None or power is None
        return result


def discover_gpus(sysfs_root: Path | str = "/sys",
                  ids_path: Path | str = "/usr/share/libdrm/amdgpu.ids") -> list[AmdGpu]:
    """Enumerate all amdgpu cards in numeric DRM order, once per gauge."""
    cards = sorted((p for p in directories(Path(sysfs_root) / "class/drm")
                    if p.name.startswith("card") and p.name[4:].isdigit()),
                   key=lambda p: int(p.name[4:]))
    result = []
    seen = set()
    for card in cards:
        try:
            device = (card / "device").resolve()
            if (read_text(device / "vendor") != "0x1002"
                    or (device / "driver").resolve().name != "amdgpu" or device in seen):
                continue
            result.append(AmdGpu(card, Path(ids_path)))
            seen.add(device)
        except (OSError, RuntimeError):
            continue
    return result
