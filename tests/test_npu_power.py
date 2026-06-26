"""Tests for the debugfs NPU power/clock (DPM) reader."""

from __future__ import annotations

import xdna_top.npu_power as npu_power

# Real dpm_level lines captured on XDNA1 (RyzenAI-npu1, staging amdxdna.ko).
DPM_IDLE = " [400,800]  600,1024  600,1024  600,1024  600,1024  720,1309  720,1309  847,1600 "
DPM_LOAD = " 400,800  600,1024  600,1024  600,1024  600,1024  720,1309  720,1309  [847,1600] "
DPM_NO_ACTIVE = " 400,800  600,1024  847,1600 "


def test_parse_idle_active_is_lowest():
    dpm = npu_power.parse_dpm_level(DPM_IDLE)
    assert dpm is not None
    assert dpm["active"] == {"index": 0, "npuclk_mhz": 400, "hclk_mhz": 800}
    assert dpm["max"] == {"index": 7, "npuclk_mhz": 847, "hclk_mhz": 1600}
    assert dpm["levels"] == 8


def test_parse_load_active_is_highest():
    dpm = npu_power.parse_dpm_level(DPM_LOAD)
    assert dpm["active"] == {"index": 7, "npuclk_mhz": 847, "hclk_mhz": 1600}


def test_parse_no_active_marker():
    dpm = npu_power.parse_dpm_level(DPM_NO_ACTIVE)
    assert dpm["active"] is None
    assert dpm["levels"] == 3
    assert dpm["max"]["npuclk_mhz"] == 847


def test_parse_garbage_returns_none():
    assert npu_power.parse_dpm_level("SMU power ON") is None
    assert npu_power.parse_dpm_level("") is None


def test_read_unavailable_when_debugfs_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(npu_power, "DEBUGFS_ACCEL", tmp_path / "nope")
    out = npu_power.read_npu_power("0000:06:00.1")
    assert out["available"] is False
    assert out["reason"] == "debugfs_accel_absent"
    assert out["source"] is None


def test_read_parses_nodes_when_present(monkeypatch, tmp_path):
    accel = tmp_path / "accel"
    dev = accel / "0000:06:00.1"
    dev.mkdir(parents=True)
    (dev / "dpm_level").write_text(DPM_LOAD, encoding="utf-8")
    (dev / "powerstate").write_text("SMU power ON\n", encoding="utf-8")
    monkeypatch.setattr(npu_power, "DEBUGFS_ACCEL", accel)

    out = npu_power.read_npu_power("0000:06:00.1")
    assert out["available"] is True
    assert out["source"] == "debugfs"
    assert out["powerstate"] == "SMU power ON"
    assert out["dpm"]["active"]["npuclk_mhz"] == 847


def test_read_falls_back_to_first_device_dir(monkeypatch, tmp_path):
    accel = tmp_path / "accel"
    dev = accel / "0000:06:00.1"
    dev.mkdir(parents=True)
    (dev / "powerstate").write_text("SMU power ON", encoding="utf-8")
    monkeypatch.setattr(npu_power, "DEBUGFS_ACCEL", accel)

    # bdf=None -> use the first accel device dir
    out = npu_power.read_npu_power(None)
    assert out["available"] is True
    assert out["powerstate"] == "SMU power ON"


def test_read_unreadable_nodes_reports_reason(monkeypatch, tmp_path):
    accel = tmp_path / "accel"
    dev = accel / "0000:06:00.1"
    dev.mkdir(parents=True)
    # directory exists but no readable nodes
    monkeypatch.setattr(npu_power, "DEBUGFS_ACCEL", accel)
    out = npu_power.read_npu_power("0000:06:00.1")
    assert out["available"] is False
    assert out["reason"] == "debugfs_nodes_unreadable"
