"""Tests for the TUI theme registry."""

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from xdna_top.lemonade import main as lemonade_main
from xdna_top.main import (
    DEFAULT_THEME,
    LEMONADE_THEME,
    THEMES,
    TuiTheme,
    create_igpu_panel,
    create_npu_panel,
    list_themes,
    main,
    resolve_theme,
)
from xdna_top.gauge import GpuState


def test_registry_contains_expected_themes():
    assert {
        "default",
        "lemonade",
        "paper",
        "phosphor",
        "amber",
        "halo",
        "lime",
        "grapefruit",
        "team-red",
    } <= set(THEMES)
    assert all(isinstance(theme, TuiTheme) for theme in THEMES.values())


def test_resolve_theme_by_name():
    assert resolve_theme("phosphor") is THEMES["phosphor"]
    assert resolve_theme(None) is DEFAULT_THEME
    assert resolve_theme("nope") is None


def test_resolve_theme_env_fallback(monkeypatch):
    monkeypatch.setenv("XDNA_TOP_THEME", "amber")
    assert resolve_theme(None) is THEMES["amber"]
    # An explicit name wins over the environment.
    assert resolve_theme("halo") is THEMES["halo"]


def test_resolve_theme_default_name_used_for_lemonade():
    assert resolve_theme(None, default_name="lemonade") is LEMONADE_THEME


def test_list_themes_lists_all_names():
    listed = list_themes().splitlines()
    assert listed == sorted(THEMES)


@pytest.mark.parametrize("name", sorted(THEMES))
def test_every_theme_renders_metrics_accurately(name):
    """Themes must not rename or hide metrics; every theme renders the same
    columns and values, and every color name must be valid (renders cleanly)."""
    theme = THEMES[name]
    contexts = [
        {"pid": 93941, "ctx_id": 1, "submissions": 100, "completions": 99, "status": "Active"}
    ]
    console = Console()
    with console.capture() as capture:
        console.print(
            create_igpu_panel(
                busy_pct=50,
                power_w=25.5,
                state=GpuState.ACTIVE,
                busy_history=[0, 50],
                power_history=[10, 25.5],
                theme=theme,
            )
        )
        console.print(create_npu_panel(npu_active=True, contexts=contexts, xrt_error=False, theme=theme))
    output = capture.get()

    # Metric labels, units, state values, and observed numbers are theme-invariant.
    assert "PID" in output
    assert "Submissions" in output
    assert "Completions" in output
    assert "Status" in output
    assert "25.50 W" in output
    assert "ACTIVE" in output
    assert "93941" in output


# --- CLI behaviour ----------------------------------------------------------


@patch("xdna_top.main.run_monitor")
@patch("sys.argv", ["xdna-top", "--theme", "phosphor"])
def test_theme_flag_selects_theme(mock_run_monitor):
    mock_run_monitor.return_value = 0
    assert main() == 0
    assert mock_run_monitor.call_args.kwargs["theme"] is THEMES["phosphor"]


@patch("xdna_top.main.run_monitor")
@patch("sys.argv", ["xdna-top"])
def test_theme_env_var_selects_theme(mock_run_monitor, monkeypatch):
    monkeypatch.setenv("XDNA_TOP_THEME", "halo")
    mock_run_monitor.return_value = 0
    assert main() == 0
    assert mock_run_monitor.call_args.kwargs["theme"] is THEMES["halo"]


@patch("sys.argv", ["xdna-top", "--theme", "nope"])
def test_unknown_theme_is_error(capsys):
    assert main() == 2
    assert "unknown theme" in capsys.readouterr().err


@patch("sys.argv", ["xdna-top", "--list-themes"])
def test_list_themes_flag(capsys):
    assert main() == 0
    out = capsys.readouterr().out
    assert "phosphor" in out and "lemonade" in out


@patch("xdna_top.main.run_monitor")
@patch("sys.argv", ["lemonade-top"])
def test_lemonade_defaults_to_lemonade_theme(mock_run_monitor):
    mock_run_monitor.return_value = 0
    assert lemonade_main() == 0
    assert mock_run_monitor.call_args.kwargs["theme"] is LEMONADE_THEME


@patch("xdna_top.main.run_monitor")
@patch("sys.argv", ["lemonade-top", "--theme", "phosphor"])
def test_lemonade_honors_theme_override(mock_run_monitor):
    mock_run_monitor.return_value = 0
    assert lemonade_main() == 0
    assert mock_run_monitor.call_args.kwargs["theme"] is THEMES["phosphor"]
