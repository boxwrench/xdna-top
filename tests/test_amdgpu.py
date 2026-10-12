"""Fake sysfs coverage for per-card telemetry and its public consumers."""
import json
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from rich.console import Console

from xdna_top.amdgpu import discover_gpus, pcie_generation
from xdna_top.exporter import XdnaCollector
from xdna_top.env_report import render_markdown, render_record_markdown
from xdna_top.gauge import GaugeReading, HardwareGauge
from xdna_top.iommu import IOMMU_HINT
from xdna_top.main import (
    create_gpu_panel,
    create_npu_panel,
    fabric_clock_warning,
    make_sparkline,
)
from xdna_top.record import build_telemetry_event
from xdna_top.snapshot import build_snapshot


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(value), encoding="utf-8")


def card(root, index=1, bdf="0000:c6:00.0", device_id="0x1586", **values):
    device = root / "devices" / bdf
    defaults = {"vendor": "0x1002", "device": device_id, "revision": "0xcc",
                "gpu_busy_percent": 73, "mem_info_vram_used": 2**29,
                "mem_info_vram_total": 2**30, "mem_info_gtt_used": 80 * 2**30,
                "mem_info_gtt_total": 96 * 2**30}
    for key, value in (defaults | values).items():
        write(device / key, value)
    driver = root / "bus/pci/drivers/amdgpu"
    driver.mkdir(parents=True, exist_ok=True)
    (device / "driver").symlink_to(driver, target_is_directory=True)
    drm = root / f"class/drm/card{index}"
    drm.mkdir(parents=True)
    (drm / "device").symlink_to(device, target_is_directory=True)
    for key, value in {"name": "amdgpu", "temp1_input": 63500, "temp2_input": 99000,
                       "power1_input": 55000000, "freq1_input": 2900000000}.items():
        write(device / "hwmon/hwmon7" / key, value)
    return device


def read(root):
    return [gpu.read() for gpu in discover_gpus(root, root / "amdgpu.ids")]


def gauge(root, monkeypatch):
    monkeypatch.setattr("xdna_top.gauge.discover_gpus", lambda: discover_gpus(root, root / "amdgpu.ids"))
    monkeypatch.setattr("xdna_top.gauge.load_sysfs_paths", lambda _: (None, None))
    return HardwareGauge(bench_dir=str(root / "bench"))


def test_apu_separate_carveout_gtt_and_units(tmp_path, monkeypatch):
    card(tmp_path, product_name="AMD Radeon 8060S", current_link_speed="16.0 GT/s PCIe", current_link_width=16)
    reading = gauge(tmp_path, monkeypatch).read_direct_from_contexts([])
    gpu = reading.gpus[0]
    assert (gpu["vram_used_bytes"], gpu["vram_total_bytes"]) == (2**29, 2**30)
    assert (gpu["gtt_used_bytes"], gpu["gtt_total_bytes"]) == (80 * 2**30, 96 * 2**30)
    assert (gpu["busy_pct"], gpu["edge_temp_c"], gpu["power_w"], gpu["sclk_mhz"]) == (73, 63.5, 55, 2900)
    assert gpu["is_apu"] is True and gpu["pcie_meaningful"] is False
    assert gpu["power_source"] == "power1_input" and gpu["power_cap_w"] is None
    assert GaugeReading.from_dict(reading.to_dict()).gpus == reading.gpus
    event = build_telemetry_event(reading, [])
    assert event["schema_version"] == "1.1" and event["reading"]["gpus"] == reading.gpus
    console = Console(width=100)
    with console.capture() as capture:
        console.print(create_gpu_panel(reading.gpus, {}))
    text = capture.get()
    for expected in ("8060S", "80.00 / 96.00 GiB", "sclk (current sample)", "CPU/SoC", "not meaningful", "N/A"):
        assert expected in text
    registry = CollectorRegistry()
    registry.register(XdnaCollector(lambda: (reading, [], None)))
    metrics = generate_latest(registry).decode()
    assert 'xdna_gpu_sclk_mhz{bdf="0000:c6:00.0",card="card1"} 2900.0' in metrics
    assert 'xdna_gpu_pcie_generation{' not in metrics


def test_two_discrete_cards_keep_own_sensors(tmp_path, monkeypatch):
    a = card(tmp_path, 2, "0000:03:00.0", "0x744c", product_name="RX 7900 XT", gpu_busy_percent=20,
             current_link_speed="16.0 GT/s PCIe", max_link_speed="16.0 GT/s PCIe", current_link_width=8, max_link_width=16)
    b = card(tmp_path, 10, "0000:06:00.0", "0x7551", product_name="R9700", gpu_busy_percent=80,
             current_link_speed="32.0 GT/s PCIe", max_link_speed="32.0 GT/s PCIe", current_link_width=16, max_link_width=16)
    write(a / "hwmon/hwmon7/power1_average", 168500000)
    write(b / "hwmon/hwmon7/power1_input", 220000000)
    write(b / "hwmon/hwmon7/power1_cap", 300000000)
    card(tmp_path, 0, "0000:01:00.0", vendor="0x10de")
    g = gauge(tmp_path, monkeypatch)
    sample = g.read_direct_from_contexts([], npu_degraded=True)
    assert [v["card"] for v in sample.gpus] == ["card2", "card10"]
    assert [v["power_w"] for v in sample.gpus] == [168.5, 220]
    assert [v["pcie_gen"] for v in sample.gpus] == [4, 5]
    assert sample.gpu_busy_pct == 20 and sample.gpu_power_w == 168.5
    assert sample.npu_degraded and not sample.igpu_degraded
    registry = CollectorRegistry()
    registry.register(XdnaCollector(lambda: (sample, [], None)))
    metrics = generate_latest(registry).decode()
    assert 'xdna_gpu_busy_percent{bdf="0000:06:00.0",card="card10"} 80.0' in metrics
    assert 'xdna_gpu_power_cap_watts{bdf="0000:06:00.0",card="card10"} 300.0' in metrics
    assert 'xdna_gpu_power_cap_watts{bdf="0000:03:00.0"' not in metrics


def test_missing_malformed_and_removed_files(tmp_path):
    d = card(tmp_path, gpu_busy_percent="garbage", mem_info_gtt_used="", device_id="0xabcd")
    (d / "hwmon/hwmon7/freq1_input").unlink()
    (d / "mem_info_vram_used").unlink()
    sample = read(tmp_path)[0]
    assert sample["busy_pct"] is None and sample["sclk_mhz"] is None
    assert sample["vram_used_bytes"] is None and sample["vram_total_bytes"] == 2**30
    assert sample["is_apu"] is None and sample["pcie_meaningful"] is None
    assert sample["degraded"] and "sclk_mhz" in sample["unavailable"]
    write(d / "gpu_busy_percent", 101)
    assert read(tmp_path)[0]["busy_pct"] is None


def test_permissions_do_not_hide_other_sensors(tmp_path, monkeypatch):
    card(tmp_path)
    original = Path.read_text
    def denied(path, *args, **kwargs):
        if path.name in {"gpu_busy_percent", "power1_input"}:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", denied)
    sample = read(tmp_path)[0]
    assert sample["busy_pct"] is None and sample["power_w"] is None
    assert sample["edge_temp_c"] == 63.5 and sample["degraded"]
    original_dirs = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda path: (_ for _ in ()).throw(PermissionError()) if path.name == "drm" else original_dirs(path))
    assert read(tmp_path) == []


def test_names_ids_and_pci_fallback(tmp_path):
    card(tmp_path, device_id="0x744c")
    write(tmp_path / "amdgpu.ids", "# header\nbad, row, ignored\n744C, CC, AMD Radeon RX 7900 XT\n")
    assert read(tmp_path)[0]["name"] == "AMD Radeon RX 7900 XT"
    (tmp_path / "amdgpu.ids").unlink()
    assert read(tmp_path)[0]["name"] == "AMD Radeon (0x744c, PCI 0000:c6:00.0)"


@pytest.mark.parametrize("speed,gen", [("2.5", 1), ("5.0", 2), ("8.0", 3), ("16.0", 4), ("32.0", 5), ("64.0", 6), ("Unknown", None), ("128.0", None)])
def test_link_generation(tmp_path, speed, gen):
    write(tmp_path / "speed", speed + " GT/s PCIe")
    assert pcie_generation(tmp_path / "speed") == gen


def test_snapshot_carries_devices_and_unreadable_reason(tmp_path, monkeypatch):
    card(tmp_path, gpu_busy_percent="bad")
    g = gauge(tmp_path, monkeypatch)
    monkeypatch.setattr("xdna_top.snapshot.HardwareGauge", lambda **kwargs: g)
    monkeypatch.setattr("xdna_top.snapshot.load_sysfs_paths", lambda _: ("/busy", "/power"))
    monkeypatch.setattr("xdna_top.snapshot.os.path.exists", lambda _: True)
    monkeypatch.setattr("xdna_top.snapshot.shutil.which", lambda _: None)
    monkeypatch.setattr("xdna_top.snapshot.read_amdxdna_info", lambda _: {"available": False})
    monkeypatch.setattr("xdna_top.snapshot.read_npu_power", lambda _: {"available": False})
    sample = build_snapshot()
    assert sample["schema_version"] == "1.1"
    assert sample["devices"]["gpus"] == sample["telemetry"]["gpus"]
    assert sample["degraded"]["igpu"]["reasons"] == ["igpu_sensor_unreadable"]
    assert sample["backends"]["igpu"]["signals"]["busy_pct"] is None
    assert "sclk (current sample): 2900.0 MHz" in render_markdown(sample)
    event = build_telemetry_event(g.read_direct_from_contexts([]), [])
    assert "GTT used: 85899345920 bytes" in render_record_markdown([event])
    assert json.loads(json.dumps(sample))["telemetry"]["gpus"][0]["busy_pct"] is None


def test_empty_discovery_and_sparkline_gap(tmp_path):
    assert read(tmp_path) == []
    assert make_sparkline([0, None, 100]) == " ·█"
    assert make_sparkline([0, 100, 0, 0], width=2) == "█ "
    assert make_sparkline([0, None, 100, 0], width=2) == "·█"


def test_missing_hwmon_and_unbound_driver(tmp_path):
    d = card(tmp_path)
    (d / "hwmon/hwmon7/name").unlink()
    sample = read(tmp_path)[0]
    assert sample["busy_pct"] == 73 and sample["power_w"] is None
    assert sample["sclk_mhz"] is None and sample["degraded"]
    (d / "driver").unlink()
    assert read(tmp_path) == []


def test_power_fallback_and_zero_are_real_samples(tmp_path):
    d = card(tmp_path, gpu_busy_percent=0)
    write(d / "hwmon/hwmon7/power1_average", "bad")
    write(d / "hwmon/hwmon7/power1_input", 0)
    sample = read(tmp_path)[0]
    assert sample["power_w"] == 0 and sample["busy_pct"] == 0
    assert sample["power_source"] == "power1_input" and not sample["degraded"]


def test_legacy_override_keeps_per_card_facts(tmp_path, monkeypatch):
    card(tmp_path)
    g = gauge(tmp_path, monkeypatch)
    g.sysfs_override = True
    busy, power = tmp_path / "override-busy", tmp_path / "override-power"
    write(busy, 5)
    write(power, 1000000)
    g.busy_path, g.power_path = str(busy), str(power)
    sample = g.read_direct_from_contexts([])
    assert sample.gpu_busy_pct == 5 and sample.gpu_power_w == 1
    assert sample.gpus[0]["busy_pct"] == 73 and sample.gpus[0]["power_w"] == 55


def test_fclk_held_not_held_and_absent(tmp_path):
    d = card(
        tmp_path,
        power_dpm_force_performance_level="manual",
        pp_dpm_fclk="0: 266Mhz\n1: 800Mhz\n2: 1600Mhz *\n",
    )
    sample = read(tmp_path)[0]
    assert sample["power_dpm_force_performance_level"] == "manual"
    assert sample["fclk"]["held_at_top"] is True
    assert (sample["fclk"]["active_index"], sample["fclk"]["top_index"]) == (2, 2)
    assert sample["fclk"]["active_mhz"] == 1600 and sample["fclk"]["reason"] is None

    # Same starred top level, but the force level is auto: not held.
    write(d / "power_dpm_force_performance_level", "auto")
    assert read(tmp_path)[0]["fclk"]["held_at_top"] is False

    # No pp_dpm_fclk node at all: unknown, with a reason (never guessed).
    (d / "pp_dpm_fclk").unlink()
    sample = read(tmp_path)[0]
    assert sample["fclk"]["held_at_top"] is None
    assert sample["fclk"]["reason"] == "pp_dpm_fclk_absent"


def test_fclk_no_star_and_missing_force_level_are_unknown(tmp_path):
    d = card(tmp_path, power_dpm_force_performance_level="manual",
             pp_dpm_fclk="0: 266Mhz\n1: 1600Mhz\n")
    sample = read(tmp_path)[0]
    assert sample["fclk"]["held_at_top"] is None
    assert sample["fclk"]["reason"] == "no_active_level"

    # A starred level but no force-level node: cannot claim held.
    write(d / "pp_dpm_fclk", "0: 266Mhz\n1: 1600Mhz *\n")
    (d / "power_dpm_force_performance_level").unlink()
    sample = read(tmp_path)[0]
    assert sample["power_dpm_force_performance_level"] is None
    assert sample["fclk"]["held_at_top"] is None
    assert sample["fclk"]["reason"] == "force_level_absent"


def _context():
    return [{"pid": 1234, "ctx_id": 1, "submissions": 5, "completions": 4,
             "status": "Active"}]


def test_fabric_clock_warning_gating():
    ctx = _context()
    assert fabric_clock_warning(ctx, [{"fclk": {"held_at_top": False}}]) is True
    assert fabric_clock_warning([], [{"fclk": {"held_at_top": False}}]) is False
    assert fabric_clock_warning(ctx, [{"fclk": {"held_at_top": True}}]) is False
    assert fabric_clock_warning(ctx, [{"fclk": {"held_at_top": None}}]) is False
    assert fabric_clock_warning(ctx, []) is False
    assert fabric_clock_warning(ctx, [{"fclk": {}}]) is False


def test_npu_panel_fabric_caution_and_iommu_hint():
    console = Console(width=120)
    with console.capture() as capture:
        console.print(create_npu_panel(True, _context(), False, fabric_warning=True))
    text = capture.get()
    assert "docs/NPU.md" in text and "without fabric clock held" in text

    with console.capture() as capture:
        console.print(create_npu_panel(True, _context(), False, fabric_warning=False))
    assert "docs/NPU.md" not in capture.get()

    with console.capture() as capture:
        console.print(create_npu_panel(False, [], True, iommu_hint=IOMMU_HINT))
    assert "SVA/PASID" in capture.get()

    with console.capture() as capture:
        console.print(create_npu_panel(False, [], True))
    assert "SVA/PASID" not in capture.get()


def test_gpu_panel_reports_fabric_clock_state(tmp_path):
    card(tmp_path, power_dpm_force_performance_level="manual",
         pp_dpm_fclk="0: 266Mhz\n1: 1600Mhz *\n")
    console = Console(width=120)
    with console.capture() as capture:
        console.print(create_gpu_panel(read(tmp_path), {}))
    text = capture.get()
    assert "held at top" in text and "force level: manual" in text
