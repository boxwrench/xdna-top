"""Unit tests for the telemetry gauge."""

import json
from unittest.mock import patch, MagicMock
import pytest
from xdna_top.gauge import (
    GpuState,
    GaugeReading,
    classify_state,
    get_stable_state,
    parse_xrt_smi,
    resolve_process_name,
    sort_contexts_by_activity,
    HardwareGauge,
    discover_npu_device,
    load_sysfs_paths,
    run_xrt_smi,
)


def test_classify_state():
    # IDLE
    assert classify_state(5, 5.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.IDLE
    assert classify_state(10, 30.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.IDLE

    # ACTIVE (busy, power below prefill)
    assert classify_state(15, 20.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.ACTIVE
    assert classify_state(50, 30.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.ACTIVE

    # PREFILL_BURST (busy, power at/above prefill)
    assert classify_state(15, 35.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.PREFILL_BURST
    assert classify_state(90, 50.0, gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0) == GpuState.PREFILL_BURST


def test_get_stable_state():
    # Single element
    assert get_stable_state([GpuState.IDLE]) == GpuState.IDLE

    # Majority vote
    assert get_stable_state([GpuState.IDLE, GpuState.ACTIVE, GpuState.IDLE]) == GpuState.IDLE
    assert get_stable_state([GpuState.PREFILL_BURST, GpuState.ACTIVE, GpuState.ACTIVE]) == GpuState.ACTIVE

    # Tie break (priority: PREFILL_BURST > ACTIVE > IDLE)
    assert get_stable_state([GpuState.IDLE, GpuState.ACTIVE]) == GpuState.ACTIVE
    assert get_stable_state([GpuState.PREFILL_BURST, GpuState.ACTIVE]) == GpuState.PREFILL_BURST


def test_parse_xrt_smi():
    canned_idle = """
------------------------------
[0000:c6:00.1] : RyzenAI-npu5
------------------------------
AIE Partitions
  Total Memory Usage: N/A
  Partition Index   : 0
    Columns: [0, 1, 2, 3, 4, 5, 6, 7]
    HW Contexts:
      |PID                 |Ctx ID     |Submissions |Migrations  |Err  |Priority |
      |Process Name        |Status     |Completions |Suspensions |     |GOPS     |
      |Memory Usage        |Instr BO   |            |            |     |FPS      |
      |                    |           |            |            |     |Latency  |
      |====================|===========|============|============|=====|=========|
      |93941               |1          |15399       |0           |0    |N/A      |
      |N/A                 |Active     |15399       |0           |     |N/A      |
      |--------------------|-----------|------------|------------|-----|---------|
      |93941               |2          |2800        |0           |0    |N/A      |
      |N/A                 |Active     |2800        |0           |     |N/A      |
"""
    res = parse_xrt_smi(canned_idle)
    assert len(res) == 2
    assert res[0]["pid"] == 93941
    assert res[0]["ctx_id"] == 1
    assert res[0]["submissions"] == 15399
    assert res[0]["completions"] == 15399
    assert res[0]["status"] == "Active"
    # Every context carries a process_name field (best-effort, may be None when
    # the owning PID can't be resolved from /proc, e.g. off-hardware).
    assert "process_name" in res[0]


def test_parse_xrt_smi_resolves_process_name():
    """parse_xrt_smi enriches contexts with the /proc-resolved process name."""
    canned = """
      |PID                 |Ctx ID     |Submissions |Migrations  |Err  |Priority |
      |Process Name        |Status     |Completions |Suspensions |     |GOPS     |
      |====================|===========|============|============|=====|=========|
      |4242                |1          |10          |0           |0    |N/A      |
      |N/A                 |Active     |9           |0           |     |N/A      |
"""
    with patch(
        "xdna_top.gauge.resolve_process_name", return_value="llama-server"
    ) as mock_resolve:
        res = parse_xrt_smi(canned)
    assert res[0]["process_name"] == "llama-server"
    mock_resolve.assert_called_once_with(4242)


def test_resolve_process_name_reads_comm(tmp_path, monkeypatch):
    """resolve_process_name prefers /proc/<pid>/comm and trims whitespace."""
    proc = tmp_path / "proc" / "777"
    proc.mkdir(parents=True)
    (proc / "comm").write_text("model-server\n", encoding="utf-8")
    monkeypatch.setattr(
        "xdna_top.gauge.Path",
        lambda p: tmp_path / p.lstrip("/"),
    )
    assert resolve_process_name(777) == "model-server"


def test_resolve_process_name_missing_returns_none(monkeypatch):
    """An unresolvable PID degrades to None rather than guessing."""
    # PID 0 / nonexistent: on any platform the /proc read fails -> None.
    assert resolve_process_name(2**31) is None


def test_sort_contexts_by_activity_orders_by_delta():
    contexts = [
        {"pid": 1, "ctx_id": 1, "submissions": 100},  # delta 0
        {"pid": 2, "ctx_id": 1, "submissions": 50},   # delta 30 (active now)
        {"pid": 3, "ctx_id": 1, "submissions": 200},  # delta 5
    ]
    prev = {(1, 1): 100, (2, 1): 20, (3, 1): 195}
    ordered = sort_contexts_by_activity(contexts, prev)
    assert [c["pid"] for c in ordered] == [2, 3, 1]


def test_sort_contexts_by_activity_first_sight_uses_submissions():
    contexts = [
        {"pid": 1, "ctx_id": 1, "submissions": 10},
        {"pid": 2, "ctx_id": 1, "submissions": 30},
    ]
    # No prior samples: deltas are 0, so the higher cumulative count leads.
    ordered = sort_contexts_by_activity(contexts, {})
    assert [c["pid"] for c in ordered] == [2, 1]


@patch("xdna_top.gauge.run_xrt_smi")
@patch("xdna_top.gauge.read_igpu")
def test_hardware_gauge_direct_read(mock_read_igpu, mock_run_xrt_smi):
    mock_read_igpu.return_value = (50, 25.0, False)  # active, not degraded
    mock_run_xrt_smi.return_value = """
|====================|===========|============|============|=====|=========|
|93941               |1          |15000       |0           |0    |N/A      |
|N/A                 |Active     |14999       |0           |     |N/A      |
"""  # in-flight submissions > completions => npu_active
    
    gauge = HardwareGauge(gpu_idle_busy_pct=10, gpu_prefill_power_w=35.0)
    reading, contexts = gauge.sample_direct()
    
    assert reading.gpu_busy_pct == 50
    assert reading.gpu_power_w == 25.0
    assert reading.npu_active is True
    assert reading.state == GpuState.ACTIVE
    assert reading.igpu_degraded is False
    assert reading.npu_degraded is False
    assert contexts[0]["submissions"] == 15000
    mock_run_xrt_smi.assert_called_once_with(device=None)


@patch("xdna_top.gauge.run_xrt_smi")
@patch("xdna_top.gauge.read_igpu", return_value=(5, 5.0, False))
def test_sample_direct_preserves_submission_delta_activity(
    _mock_read_igpu, mock_run_xrt_smi
):
    mock_run_xrt_smi.side_effect = [
        "| 42 | 1 | 10 |\n| n/a | Idle | 10 |",
        "| 42 | 1 | 11 |\n| n/a | Idle | 11 |",
    ]
    gauge = HardwareGauge()

    first, first_contexts = gauge.sample_direct()
    second, second_contexts = gauge.sample_direct()

    assert first.npu_active is False
    assert second.npu_active is True
    assert first_contexts[0]["submissions"] == 10
    assert second_contexts[0]["submissions"] == 11
    assert mock_run_xrt_smi.call_count == 2


def test_read_is_direct_compatibility_alias():
    gauge = HardwareGauge.__new__(HardwareGauge)
    expected = MagicMock()
    with patch.object(gauge, "read_direct", return_value=expected) as read_direct:
        assert gauge.read() is expected
    read_direct.assert_called_once_with()


def test_load_sysfs_paths_discovers_in_memory_without_writing(tmp_path):
    load_sysfs_paths.cache_clear()
    with patch(
        "xdna_top.discover_sysfs.discover_sysfs",
        return_value={"gpu_busy_path": "/busy", "gpu_power_path": "/power"},
    ) as discover:
        assert load_sysfs_paths(str(tmp_path)) == ("/busy", "/power")
        assert load_sysfs_paths(str(tmp_path)) == ("/busy", "/power")

    discover.assert_called_once_with()
    assert not (tmp_path / "e0_sysfs.json").exists()


def test_load_sysfs_paths_honors_explicit_override(tmp_path):
    load_sysfs_paths.cache_clear()
    (tmp_path / "e0_sysfs.json").write_text(
        json.dumps(
            {
                "gpu_busy_path": "/override-busy",
                "gpu_power_path": "/override-power",
            }
        ),
        encoding="utf-8",
    )

    with patch("xdna_top.discover_sysfs.discover_sysfs") as discover:
        assert load_sysfs_paths(str(tmp_path)) == (
            "/override-busy",
            "/override-power",
        )

    discover.assert_not_called()


@patch("xdna_top.gauge.load_sysfs_paths")
@patch("builtins.open")
@patch("os.path.exists")
def test_igpu_degradation_honest_vs_pessimism(mock_exists, mock_open, mock_load_paths):
    # Simulate both paths missing
    mock_exists.return_value = False
    mock_load_paths.return_value = (None, None)
    
    # 1. Honest degradation (default, pessimistic_fallback=False)
    gauge_honest = HardwareGauge(gpu_idle_busy_pct=10, pessimistic_fallback=False)
    reading_honest = gauge_honest.read_direct()
    
    assert reading_honest.gpu_busy_pct is None
    assert reading_honest.gpu_power_w is None
    assert reading_honest.igpu_degraded is True
    # Honest mode refuses to classify from invented inputs: state is UNKNOWN
    # in the enum and null in JSON.
    assert reading_honest.state == GpuState.UNKNOWN
    assert reading_honest.to_dict()["state"] is None

    # 2. Pessimistic fallback (pessimistic_fallback=True)
    gauge_pessimistic = HardwareGauge(gpu_idle_busy_pct=10, pessimistic_fallback=True)
    reading_pessimistic = gauge_pessimistic.read_direct()
    
    assert reading_pessimistic.gpu_busy_pct == 100
    assert reading_pessimistic.gpu_power_w == 45.0
    assert reading_pessimistic.igpu_degraded is True
    assert reading_pessimistic.state == GpuState.PREFILL_BURST


@patch("xdna_top.gauge.run_xrt_smi")
@patch("xdna_top.gauge.load_sysfs_paths")
@patch("builtins.open")
@patch("os.path.exists")
def test_degraded_honest_no_classified_state(mock_exists, mock_open, mock_load_paths, mock_run_xrt_smi):
    # Both sysfs paths missing -> iGPU degraded. NPU mocked out so it can't
    # influence iGPU state classification.
    mock_exists.return_value = False
    mock_load_paths.return_value = (None, None)
    mock_run_xrt_smi.return_value = None

    gauge_honest = HardwareGauge(gpu_idle_busy_pct=10, pessimistic_fallback=False)
    reading = gauge_honest.read_direct()

    # Honest degradation: values are null...
    assert reading.gpu_busy_pct is None
    assert reading.gpu_power_w is None
    assert reading.igpu_degraded is True

    # ...and state is NOT classified from invented inputs.
    # JSON must be null, enum must be UNKNOWN, and it must never be a
    # classified iGPU activity state.
    assert reading.to_dict()["state"] is None
    assert reading.state == GpuState.UNKNOWN
    assert reading.state not in (GpuState.IDLE, GpuState.ACTIVE, GpuState.PREFILL_BURST)


@patch("subprocess.run")
def test_discover_npu_device(mock_run):
    discover_npu_device.cache_clear()
    # 1. Success case
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = """
Device(s) Present
|BDF             |Name          |
|----------------|--------------|
|[0000:c6:00.1]  |RyzenAI-npu5  |
"""
    mock_run.return_value = mock_proc
    assert discover_npu_device() == "0000:c6:00.1"

    # 2. Fail case
    discover_npu_device.cache_clear()
    mock_proc.returncode = 1
    assert discover_npu_device() is None


@patch("xdna_top.gauge.discover_npu_device")
@patch("subprocess.run")
def test_run_xrt_smi_device_arg(mock_run, mock_discover):
    mock_discover.return_value = "0000:ab:00.1"
    
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "fake contexts output"
    mock_run.return_value = mock_proc

    # 1. No device passed -> uses discover_npu_device BDF
    run_xrt_smi(device=None)
    mock_run.assert_called_with(
        ["xrt-smi", "examine", "--device", "0000:ab:00.1", "--report", "aie-partitions"],
        capture_output=True,
        text=True,
        timeout=1.5,
    )

    # 2. Override device passed
    run_xrt_smi(device="0000:99:99.9")
    mock_run.assert_called_with(
        ["xrt-smi", "examine", "--device", "0000:99:99.9", "--report", "aie-partitions"],
        capture_output=True,
        text=True,
        timeout=1.5,
    )
