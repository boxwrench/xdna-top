"""Unit tests for xdna-top spinoff TUI logic."""

import json
import pytest
from unittest.mock import patch, MagicMock
from rich.panel import Panel
from rich.console import Console
from xdna_top.main import (
    LEMONADE_THEME,
    create_header_panel,
    make_bar,
    make_sparkline,
    create_igpu_panel,
    create_npu_panel,
    main,
)
from xdna_top.lemonade import main as lemonade_main
from xdna_top.gauge import GpuState



def test_make_bar():
    # Bounds check
    assert make_bar(-10, width=10) == "[░░░░░░░░░░] 0%"
    assert make_bar(110, width=10) == "[██████████] 100%"
    
    # Standard width
    assert make_bar(50, width=10) == "[█████░░░░░] 50%"


def test_make_sparkline():
    assert make_sparkline([], max_val=100.0) == ""
    assert make_sparkline([0, 50, 100], max_val=100.0) == " ▄█"


def test_create_igpu_panel():
    panel = create_igpu_panel(
        busy_pct=50,
        power_w=25.5,
        state=GpuState.ACTIVE,
        busy_history=[0, 50, 100],
        power_history=[10, 20, 30]
    )
    assert isinstance(panel, Panel)
    
    console = Console()
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()
    
    assert "ACTIVE" in output
    assert "25.50 W" in output


def test_create_igpu_panel_degraded():
    panel = create_igpu_panel(
        busy_pct=None,
        power_w=None,
        state=GpuState.IDLE,
        busy_history=[],
        power_history=[],
        igpu_degraded=True
    )
    assert isinstance(panel, Panel)
    
    console = Console()
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()
    
    assert "sysfs endpoints missing or unreadable" in output



def test_create_npu_panel_degraded():
    # When xrt fails/missing, check if degraded message appears
    panel = create_npu_panel(npu_active=False, contexts=[], xrt_error=True)
    assert isinstance(panel, Panel)
    
    console = Console()
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()
    
    assert "xrt-smi not found or failed" in output


def test_create_npu_panel_active():
    contexts = [
        {"pid": 93941, "ctx_id": 1, "submissions": 100, "completions": 99, "status": "Active"}
    ]
    panel = create_npu_panel(npu_active=True, contexts=contexts, xrt_error=False)
    assert isinstance(panel, Panel)
    
    console = Console()
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()
    
    assert "ACTIVE" in output
    assert "93941" in output
    assert "Active" in output


def test_lemonade_theme_panels():
    igpu_panel = create_igpu_panel(
        busy_pct=25,
        power_w=18.25,
        state=GpuState.ACTIVE,
        busy_history=[0, 25],
        power_history=[10, 18.25],
        theme=LEMONADE_THEME,
    )
    npu_panel = create_npu_panel(
        npu_active=True,
        contexts=[],
        xrt_error=False,
        theme=LEMONADE_THEME,
    )

    console = Console()
    with console.capture() as capture:
        console.print(igpu_panel)
        console.print(npu_panel)
    output = capture.get()

    assert "Lemon Grove iGPU" in output
    assert "Lemon Stand NPU" in output
    assert "Fresh Hardware Contexts" in output


def test_lemonade_header_has_pixel_lemon():
    panel = create_header_panel(LEMONADE_THEME)

    console = Console()
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()

    assert "lemonade-top" in output
    assert "██████████" in output


@patch("xdna_top.main.HardwareGauge")
@patch("sys.argv")
def test_json_flag(mock_argv, mock_gauge_class):
    mock_argv.__getitem__.side_effect = lambda idx: ["xdna-top", "--json"][idx]
    mock_argv.__len__.return_value = 2
    
    mock_gauge = MagicMock()
    mock_gauge.read.return_value.to_dict.return_value = {
        "gpu_busy_pct": 10,
        "gpu_power_w": 15.0,
        "npu_active": False,
        "state": "ACTIVE",
        "ts": 12345.67
    }
    mock_gauge_class.return_value = mock_gauge
    
    with patch("builtins.print") as mock_print:
        ret = main()
        assert ret == 0
        mock_print.assert_called_once()
        printed_str = mock_print.call_args[0][0]
        printed_json = json.loads(printed_str)
        assert printed_json["gpu_busy_pct"] == 10
        assert printed_json["state"] == "ACTIVE"


@patch("xdna_top.main.snapshot_main")
@patch("sys.argv", ["xdna-top", "snapshot", "--out", "bench/platform.json"])
def test_snapshot_subcommand(mock_snapshot_main):
    mock_snapshot_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_snapshot_main.assert_called_once()
    args = mock_snapshot_main.call_args[0][0]
    assert args.command == "snapshot"
    assert args.out == "bench/platform.json"


@patch("xdna_top.main.HardwareGauge")
@patch("sys.argv")
def test_lemonade_json_flag(mock_argv, mock_gauge_class):
    mock_argv.__getitem__.side_effect = lambda idx: ["lemonade-top", "--json"][idx]
    mock_argv.__len__.return_value = 2

    mock_gauge = MagicMock()
    mock_gauge.read.return_value.to_dict.return_value = {
        "gpu_busy_pct": 5,
        "gpu_power_w": 10.0,
        "npu_active": False,
        "state": "IDLE",
        "ts": 12345.67,
    }
    mock_gauge_class.return_value = mock_gauge

    with patch("builtins.print") as mock_print:
        ret = lemonade_main()
        assert ret == 0
        printed_json = json.loads(mock_print.call_args[0][0])
        assert printed_json["gpu_busy_pct"] == 5
        assert printed_json["state"] == "IDLE"
