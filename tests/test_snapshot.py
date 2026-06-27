"""Tests for schema-backed snapshot capture."""

import json
from unittest.mock import MagicMock, patch

from xdna_top.snapshot import build_snapshot, write_snapshot


@patch("xdna_top.snapshot._accel_entries")
@patch("xdna_top.snapshot.HardwareGauge")
@patch("xdna_top.snapshot.os.path.exists")
@patch("xdna_top.snapshot.load_sysfs_paths")
@patch("xdna_top.snapshot._probe_xrt_smi")
def test_build_snapshot_happy_path(
    mock_probe_xrt,
    mock_load_sysfs_paths,
    mock_exists,
    mock_gauge_class,
    mock_accel_entries,
):
    mock_probe_xrt.return_value = {
        "path": "/usr/bin/xrt-smi",
        "available": True,
        "version_output": "XRT build version 1.2.3",
        "examine_returncode": 0,
        "aie_partitions_returncode": 0,
        "device": {"bdf": "0000:c6:00.1", "name": "RyzenAI-npu5"},
        "contexts": [
            {
                "pid": 1234,
                "ctx_id": 1,
                "submissions": 10,
                "completions": 9,
                "status": "Active",
            }
        ],
    }
    mock_load_sysfs_paths.return_value = (
        "/sys/class/drm/card1/device/gpu_busy_percent",
        "/sys/class/hwmon/hwmon4/power1_input",
    )
    mock_exists.return_value = True
    mock_accel_entries.return_value = [{"path": "/dev/accel/accel0", "exists": True}]

    reading = MagicMock()
    reading.to_dict.return_value = {
        "gpu_busy_pct": 42,
        "gpu_power_w": 12.5,
        "npu_active": True,
        "state": "ACTIVE",
        "igpu_degraded": False,
        "npu_degraded": False,
        "ts": 123.456,
    }
    mock_gauge_class.return_value.read_direct.return_value = reading

    snapshot = build_snapshot(bench_dir="/tmp/xdna-top-test")

    assert snapshot["schema_version"] == "1.0"
    assert snapshot["kind"] == "xdna-top.snapshot"
    assert snapshot["commands"]["xrt_smi"]["available"] is True
    assert snapshot["backends"]["npu"]["primary"] == "xrt_smi"
    assert snapshot["backends"]["npu"]["signals"]["contexts"] == "xrt_smi"
    assert snapshot["devices"]["npu"]["detected"] is True
    assert snapshot["devices"]["npu"]["bdf"] == "0000:c6:00.1"
    assert snapshot["devices"]["npu"]["contexts"][0]["source"] == "xrt_smi"
    assert snapshot["devices"]["igpu"]["busy_path_exists"] is True
    assert snapshot["degraded"]["overall"] is False
    assert snapshot["telemetry"]["gpu_busy_pct"] == 42


@patch("xdna_top.snapshot._accel_entries")
@patch("xdna_top.snapshot.HardwareGauge")
@patch("xdna_top.snapshot.os.path.exists")
@patch("xdna_top.snapshot.load_sysfs_paths")
@patch("xdna_top.snapshot._probe_xrt_smi")
def test_build_snapshot_degraded_path(
    mock_probe_xrt,
    mock_load_sysfs_paths,
    mock_exists,
    mock_gauge_class,
    mock_accel_entries,
):
    mock_probe_xrt.return_value = {
        "path": None,
        "available": False,
        "version_output": None,
        "examine_returncode": None,
        "aie_partitions_returncode": None,
        "device": {"bdf": None, "name": None},
        "contexts": [],
    }
    mock_load_sysfs_paths.return_value = (None, None)
    mock_exists.return_value = False
    mock_accel_entries.return_value = [{"path": "/dev/accel/accel0", "exists": False}]

    reading = MagicMock()
    reading.to_dict.return_value = {
        "gpu_busy_pct": None,
        "gpu_power_w": None,
        "npu_active": False,
        "state": None,
        "igpu_degraded": True,
        "npu_degraded": True,
        "ts": 123.456,
    }
    mock_gauge_class.return_value.read_direct.return_value = reading

    snapshot = build_snapshot(bench_dir="/tmp/xdna-top-test")

    assert snapshot["backends"]["npu"]["primary"] is None
    assert snapshot["backends"]["igpu"]["primary"] is None
    assert snapshot["devices"]["npu"]["detected"] is False
    assert snapshot["devices"]["igpu"]["busy_path_exists"] is False
    assert snapshot["degraded"]["overall"] is True
    assert "xrt_smi_not_found" in snapshot["degraded"]["npu"]["reasons"]
    assert "igpu_busy_path_missing" in snapshot["degraded"]["igpu"]["reasons"]
    assert "igpu_power_path_missing" in snapshot["degraded"]["igpu"]["reasons"]


def test_write_snapshot_to_file(tmp_path):
    out = tmp_path / "platform.json"
    write_snapshot({"schema_version": "1.0", "kind": "xdna-top.snapshot"}, out)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "1.0"
    assert data["kind"] == "xdna-top.snapshot"


@patch("xdna_top.snapshot._accel_entries")
@patch("xdna_top.snapshot.HardwareGauge")
@patch("xdna_top.snapshot.os.path.exists")
@patch("xdna_top.snapshot.load_sysfs_paths")
@patch("xdna_top.snapshot._probe_xrt_smi")
def test_build_snapshot_surfaces_memlock_reason(
    mock_probe_xrt,
    mock_load_sysfs_paths,
    mock_exists,
    mock_gauge_class,
    mock_accel_entries,
    monkeypatch,
):
    """build_snapshot must wire a real memlock diagnosis into npu reasons + errors."""
    mmap_err = (
        "xrt-smi ERROR: mmap(addr=0x7e79cc000000, len=67108864, prot=3, flags=8209, "
        "offset=4294967296) failed (err=-11): Resource temporarily unavailable"
    )

    def fake_probe(*, npu_device, errors):
        errors.append({"probe": "xrt_smi.examine", "message": mmap_err})
        return {
            "path": "/usr/bin/xrt-smi",
            "available": True,
            "version_output": "Version : 2.25.0",
            "examine_returncode": 1,
            "aie_partitions_returncode": 1,
            "device": {"bdf": None, "name": None},
            "contexts": [],
        }

    mock_probe_xrt.side_effect = fake_probe
    mock_load_sysfs_paths.return_value = (None, None)
    mock_exists.return_value = False
    mock_accel_entries.return_value = [{"path": "/dev/accel/accel0", "exists": True}]

    reading = MagicMock()
    reading.to_dict.return_value = {
        "npu_active": False,
        "state": "IDLE",
        "igpu_degraded": True,
        "npu_degraded": True,
        "ts": 1.0,
    }
    mock_gauge_class.return_value.read_direct.return_value = reading

    # force a low soft limit so the real diagnose_memlock fires end-to-end
    monkeypatch.setattr("xdna_top.diagnostics.memlock_soft_limit", lambda: 8 * 1024 * 1024)

    snapshot = build_snapshot(bench_dir="/tmp/xdna-top-test")

    assert "rlimit_memlock_too_low" in snapshot["degraded"]["npu"]["reasons"]
    memlock_errors = [e for e in snapshot["errors"] if e.get("probe") == "rlimit_memlock"]
    assert len(memlock_errors) == 1
    assert "memlock" in memlock_errors[0]["message"].lower()
