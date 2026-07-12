"""Unit tests for xdna-top spinoff TUI logic."""

import json
import pytest
from unittest.mock import patch, MagicMock
from rich.panel import Panel
from rich.console import Console
from xdna_top.main import (
    DEFAULT_THEME,
    LEMONADE_THEME,
    build_layout,
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
        {"pid": 93941, "ctx_id": 1, "submissions": 100, "completions": 99,
         "status": "Active", "process_name": "llama-server"}
    ]
    panel = create_npu_panel(npu_active=True, contexts=contexts, xrt_error=False)
    assert isinstance(panel, Panel)

    # Wide console so the Process column isn't truncated by table layout.
    console = Console(width=200)
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()

    assert "ACTIVE" in output
    assert "93941" in output
    assert "Active" in output
    assert "llama-server" in output


def test_create_npu_panel_missing_process_name():
    """A context without a resolvable process name renders a placeholder."""
    contexts = [
        {"pid": 93941, "ctx_id": 1, "submissions": 100, "completions": 99,
         "status": "Active", "process_name": None}
    ]
    panel = create_npu_panel(npu_active=True, contexts=contexts, xrt_error=False)
    console = Console(width=200)
    with console.capture() as capture:
        console.print(panel)
    output = capture.get()
    assert "93941" in output
    assert "?" in output


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


def _render(panel):
    console = Console()
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


def test_header_shows_detected_device_label():
    """A detected device name is appended to the header as a [label] suffix."""
    output = _render(create_header_panel(DEFAULT_THEME, "RyzenAI-npu5"))
    assert "RyzenAI-npu5" in output


def test_header_omits_platform_label_when_undetected():
    """With no detected device, the header asserts no platform at all."""
    output = _render(create_header_panel(DEFAULT_THEME, None))
    assert "Strix Halo" not in output
    assert "RyzenAI" not in output


def test_header_never_hardcodes_strix_halo():
    """Even with a device present, the platform label is derived, not hardcoded."""
    output = _render(create_header_panel(DEFAULT_THEME, "RyzenAI-npu1"))
    assert "Strix Halo" not in output
    assert "RyzenAI-npu1" in output


def test_default_layout_is_side_by_side():
    layout = build_layout()
    body = layout["body"]
    assert [child.name for child in body.children] == ["igpu", "npu"]
    assert type(body.splitter).__name__ == "RowSplitter"


def test_stacked_layout_places_igpu_above_npu():
    layout = build_layout(layout_mode="stacked")
    body = layout["body"]
    assert [child.name for child in body.children] == ["igpu", "npu"]
    assert type(body.splitter).__name__ == "ColumnSplitter"


@pytest.mark.parametrize("layout_mode", ["side-by-side", "stacked"])
def test_npu_only_layout_has_one_pane(layout_mode):
    layout = build_layout(layout_mode=layout_mode, npu_only=True)
    assert [child.name for child in layout["body"].children] == ["npu"]


def test_unknown_layout_mode_is_rejected():
    with pytest.raises(ValueError, match="unsupported layout mode"):
        build_layout(layout_mode="diagonal")


@pytest.mark.parametrize("width", [60, 160])
@pytest.mark.parametrize(
    ("layout_mode", "npu_only"),
    [("side-by-side", False), ("stacked", False), ("stacked", True)],
)
def test_layout_options_render_at_narrow_and_wide_widths(
    width, layout_mode, npu_only
):
    layout = build_layout(layout_mode=layout_mode, npu_only=npu_only)
    layout["header"].update("HEADER")
    layout["footer"].update("FOOTER")
    if not npu_only:
        layout["body"]["igpu"].update("IGPU_PANE")
    layout["body"]["npu"].update("NPU_PANE")

    console = Console(width=width, height=24)
    with console.capture() as capture:
        console.print(layout)
    output = capture.get()

    assert "NPU_PANE" in output
    assert ("IGPU_PANE" in output) is (not npu_only)


@patch("xdna_top.main.run_monitor")
@patch("sys.argv", ["xdna-top", "--layout", "stacked", "--npu-only"])
def test_layout_flags_reach_monitor(mock_run_monitor):
    mock_run_monitor.return_value = 0
    assert main() == 0
    args = mock_run_monitor.call_args.args[0]
    assert args.layout == "stacked"
    assert args.npu_only is True


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


@patch("xdna_top.main.env_report_main")
@patch("sys.argv", ["xdna-top", "env-report", "bench/platform.json", "--markdown"])
def test_env_report_subcommand(mock_env_report_main):
    mock_env_report_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_env_report_main.assert_called_once()
    args = mock_env_report_main.call_args[0][0]
    assert args.command == "env-report"
    assert args.snapshot == "bench/platform.json"
    assert args.markdown is True


@patch("xdna_top.main.record_main")
@patch("sys.argv", ["xdna-top", "record", "--duration", "30", "--interval", "0.5", "--out", "bench/telemetry.jsonl"])
def test_record_subcommand(mock_record_main):
    mock_record_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_record_main.assert_called_once()
    args = mock_record_main.call_args[0][0]
    assert args.command == "record"
    assert args.duration == 30.0
    assert args.interval == 0.5
    assert args.out == "bench/telemetry.jsonl"


@patch("xdna_top.main.mark_main")
@patch("sys.argv", ["xdna-top", "mark", "trial-1-start", "--out", "bench/trial.jsonl"])
def test_mark_subcommand(mock_mark_main):
    mock_mark_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_mark_main.assert_called_once()
    args = mock_mark_main.call_args[0][0]
    assert args.command == "mark"
    assert args.label == "trial-1-start"
    assert args.out == "bench/trial.jsonl"


@patch("xdna_top.main.assert_main")
@patch("sys.argv", ["xdna-top", "assert", "telemetry.jsonl", "--require-npu", "--require-npu-activity"])
def test_assert_subcommand(mock_assert_main):
    mock_assert_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_assert_main.assert_called_once()
    args = mock_assert_main.call_args[0][0]
    assert args.command == "assert"
    assert args.artifact == "telemetry.jsonl"
    assert args.require_npu is True
    assert args.require_npu_activity is True
    assert args.require_npu_sensors is False


@patch("xdna_top.main.compare_main")
@patch("sys.argv", ["xdna-top", "compare", "before.json", "after.json"])
def test_compare_subcommand(mock_compare_main):
    mock_compare_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_compare_main.assert_called_once()
    args = mock_compare_main.call_args[0][0]
    assert args.command == "compare"
    assert args.before == "before.json"
    assert args.after == "after.json"


@patch("xdna_top.main.baseline_main")
@patch("sys.argv", ["xdna-top", "baseline", "check", "known-good"])
def test_baseline_subcommand(mock_baseline_main):
    mock_baseline_main.return_value = 0

    ret = main()

    assert ret == 0
    mock_baseline_main.assert_called_once()
    args = mock_baseline_main.call_args[0][0]
    assert args.command == "baseline"
    assert args.action == "check"
    assert args.name == "known-good"


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
