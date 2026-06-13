import os
import sys
import json
import time
import select
import tty
import termios
import argparse
from pathlib import Path
from dataclasses import dataclass, replace

from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.align import Align

from xdna_top.assertions import CHECKS, assert_main
from xdna_top.baseline import baseline_main
from xdna_top.compare import compare_main
from xdna_top.env_report import env_report_main
from xdna_top.gauge import HardwareGauge, GpuState, run_xrt_smi, parse_xrt_smi
from xdna_top.record import mark_main, record_main
from xdna_top.snapshot import snapshot_main


@dataclass(frozen=True)
class TuiTheme:
    app_name: str
    header: str
    footer: str
    stopped_message: str
    header_border: str
    footer_border: str
    igpu_title: str
    igpu_border: str
    npu_title: str
    npu_border: str
    table_title: str
    table_title_style: str
    state_idle_style: str
    state_active_style: str
    state_prefill_style: str
    npu_idle_style: str
    npu_active_style: str
    header_size: int = 3
    header_art: tuple[str, ...] = ()


DEFAULT_THEME = TuiTheme(
    app_name="xdna-top",
    header="[bold white]xdna-top[/bold white] - Unified NPU+iGPU Monitor [Strix Halo]",
    footer="Press [bold red]q[/bold red] or [bold red]Ctrl-C[/bold red] to quit. Telemetry refresh rate: 5 Hz.",
    stopped_message="[green]xdna-top stopped cleanly.[/green]",
    header_border="white",
    footer_border="white",
    igpu_title="[bold cyan]iGPU Telemetry[/bold cyan]",
    igpu_border="cyan",
    npu_title="[bold magenta]Ryzen AI NPU[/bold magenta]",
    npu_border="magenta",
    table_title="Active Hardware Contexts",
    table_title_style="bold magenta",
    state_idle_style="green",
    state_active_style="yellow",
    state_prefill_style="bold red",
    npu_idle_style="bold yellow",
    npu_active_style="bold green",
)


LEMONADE_THEME = TuiTheme(
    app_name="lemonade-top",
    header="[bold yellow]lemonade-top[/bold yellow] - Fresh NPU+iGPU Stand [Strix Halo]",
    footer="Press [bold green]q[/bold green] or [bold green]Ctrl-C[/bold green] to close the stand. Fresh squeeze: 5 Hz.",
    stopped_message="[yellow]lemonade-top stand closed cleanly.[/yellow]",
    header_border="yellow",
    footer_border="green",
    igpu_title="[bold yellow]Lemon Grove iGPU[/bold yellow]",
    igpu_border="yellow",
    npu_title="[bold green]Lemon Stand NPU[/bold green]",
    npu_border="green",
    table_title="Fresh Hardware Contexts",
    table_title_style="bold yellow",
    state_idle_style="green",
    state_active_style="yellow",
    state_prefill_style="bold green",
    npu_idle_style="bold yellow",
    npu_active_style="bold green",
    header_size=7,
    header_art=(
        "      [green]██[/green]",
        "   [yellow]██████[/yellow][green]██[/green]",
        " [yellow]██████████[/yellow]",
        "  [yellow]████████[/yellow]",
    ),
)


# Additional themes vary only colors, borders, and header/footer chrome. They
# keep the default's accurate pane titles and never touch metric names, states,
# units, or values, so a screenshot in any theme stays claims-accurate.
PAPER_THEME = replace(
    DEFAULT_THEME,
    header="[bold]xdna-top[/bold] - Unified NPU+iGPU Monitor [Strix Halo]",
    footer="Press [bold]q[/bold] or [bold]Ctrl-C[/bold] to quit. Telemetry refresh rate: 5 Hz.",
    stopped_message="[bold]xdna-top stopped cleanly.[/bold]",
    header_border="blue",
    footer_border="blue",
    igpu_title="[bold blue]iGPU Telemetry[/bold blue]",
    igpu_border="blue",
    npu_title="[bold dark_orange]Ryzen AI NPU[/bold dark_orange]",
    npu_border="dark_orange",
    table_title_style="bold blue",
    state_idle_style="blue",
    state_active_style="bold blue",
    state_prefill_style="bold dark_orange",
    npu_idle_style="blue",
    npu_active_style="bold dark_orange",
)


PHOSPHOR_THEME = replace(
    DEFAULT_THEME,
    header="[bold green]xdna-top[/bold green] - Unified NPU+iGPU Monitor [Strix Halo]",
    footer="Press [bold green]q[/bold green] or [bold green]Ctrl-C[/bold green] to quit. Telemetry refresh rate: 5 Hz.",
    stopped_message="[green]xdna-top stopped cleanly.[/green]",
    header_border="green",
    footer_border="green",
    igpu_title="[bold green]iGPU Telemetry[/bold green]",
    igpu_border="green",
    npu_title="[bold green]Ryzen AI NPU[/bold green]",
    npu_border="green",
    table_title_style="bold green",
    state_idle_style="green",
    state_active_style="bright_green",
    state_prefill_style="bold bright_green",
    npu_idle_style="green",
    npu_active_style="bold bright_green",
)


AMBER_THEME = replace(
    DEFAULT_THEME,
    header="[bold orange3]xdna-top[/bold orange3] - Unified NPU+iGPU Monitor [Strix Halo]",
    footer="Press [bold orange3]q[/bold orange3] or [bold orange3]Ctrl-C[/bold orange3] to quit. Telemetry refresh rate: 5 Hz.",
    stopped_message="[orange3]xdna-top stopped cleanly.[/orange3]",
    header_border="orange3",
    footer_border="orange3",
    igpu_title="[bold orange3]iGPU Telemetry[/bold orange3]",
    igpu_border="orange3",
    npu_title="[bold orange3]Ryzen AI NPU[/bold orange3]",
    npu_border="orange3",
    table_title_style="bold orange3",
    state_idle_style="yellow",
    state_active_style="orange3",
    state_prefill_style="bold orange3",
    npu_idle_style="yellow",
    npu_active_style="bold orange3",
)


HALO_THEME = replace(
    DEFAULT_THEME,
    header="[bold grey78]xdna-top[/bold grey78] - Unified NPU+iGPU Monitor [Strix Halo]",
    footer="Press [bold steel_blue1]q[/bold steel_blue1] or [bold steel_blue1]Ctrl-C[/bold steel_blue1] to quit. Telemetry refresh rate: 5 Hz.",
    stopped_message="[grey78]xdna-top stopped cleanly.[/grey78]",
    header_border="deep_sky_blue4",
    footer_border="deep_sky_blue4",
    igpu_title="[bold steel_blue1]iGPU Telemetry[/bold steel_blue1]",
    igpu_border="deep_sky_blue4",
    npu_title="[bold grey78]Ryzen AI NPU[/bold grey78]",
    npu_border="steel_blue",
    table_title_style="bold steel_blue1",
    state_idle_style="steel_blue",
    state_active_style="deep_sky_blue1",
    state_prefill_style="bold cyan",
    npu_idle_style="grey78",
    npu_active_style="bold cyan",
)


# Theme registry. New themes should be a small data entry here, not a new
# command or renamed metric.
THEMES: dict[str, TuiTheme] = {
    "default": DEFAULT_THEME,
    "lemonade": LEMONADE_THEME,
    "paper": PAPER_THEME,
    "phosphor": PHOSPHOR_THEME,
    "amber": AMBER_THEME,
    "halo": HALO_THEME,
}


def resolve_theme(name: str | None, *, default_name: str = "default") -> TuiTheme | None:
    """Resolve a theme by name, falling back to XDNA_TOP_THEME then default_name.

    Returns ``None`` when an explicitly requested name is unknown so the caller
    can report it.
    """
    chosen = name or os.environ.get("XDNA_TOP_THEME") or default_name
    return THEMES.get(chosen.lower())


def list_themes() -> str:
    """Return the available theme names, one per line."""
    return "\n".join(sorted(THEMES))


class KeyListener:
    """Non-blocking keyboard listener for Unix terminal."""

    def __init__(self) -> None:
        self.old_settings = None
        try:
            self.fd = sys.stdin.fileno()
            self.old_settings = termios.tcgetattr(self.fd)
        except Exception:
            self.fd = None

    def set_raw(self) -> None:
        if self.fd is not None:
            try:
                tty.setcbreak(self.fd)
            except Exception:
                pass

    def restore(self) -> None:
        if self.fd is not None and self.old_settings is not None:
            try:
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)
            except Exception:
                pass

    def check_key(self) -> str | None:
        """Checks if a key is pressed (non-blocking). Returns the character or None."""
        if self.fd is None:
            return None
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                return sys.stdin.read(1)
        except Exception:
            pass
        return None


def make_bar(pct: int, width: int = 30) -> str:
    """Creates a text-based progress bar."""
    pct = max(0, min(100, pct))
    filled_chars = int((pct / 100.0) * width)
    empty_chars = width - filled_chars
    return "[" + "█" * filled_chars + "░" * empty_chars + f"] {pct}%"


def make_sparkline(values: list[float], max_val: float = 100.0) -> str:
    """Generates an ASCII sparkline of historical values."""
    bars = " ▂▃▄▅▆▇█"
    if not values:
        return ""
    res = []
    for v in values:
        if max_val <= 0:
            idx = 0
        else:
            idx = int((v / max_val) * (len(bars) - 1))
        idx = max(0, min(len(bars) - 1, idx))
        res.append(bars[idx])
    return "".join(res)


def create_igpu_panel(
    busy_pct: int | None,
    power_w: float | None,
    state: GpuState,
    busy_history: list[float],
    power_history: list[float],
    igpu_degraded: bool = False,
    theme: TuiTheme = DEFAULT_THEME,
) -> Panel:
    """Constructs the iGPU status Panel."""
    text = Text()
    if igpu_degraded:
        text.append("[WARNING] sysfs endpoints missing or unreadable; iGPU telemetry degraded.\n\n", style="bold red")
        return Panel(text, title=theme.igpu_title, border_style="red")

    # Color coding for state
    state_color = theme.state_idle_style
    if state == GpuState.ACTIVE:
        state_color = theme.state_active_style
    elif state == GpuState.PREFILL_BURST:
        state_color = theme.state_prefill_style

    # Format output text
    text.append("iGPU State: ", style="bold")
    text.append(f"{state.value}\n", style=state_color)
    
    text.append("Utilization: ", style="bold")
    text.append(f"{make_bar(busy_pct if busy_pct is not None else 0)}\n")
    
    text.append("Power Draw:  ", style="bold")
    text.append(f"{power_w:.2f} W (max 100W)\n\n" if power_w is not None else "N/A W\n\n")

    # Sparklines
    text.append("Busy (last 60s):  ", style="bold")
    text.append(f"{make_sparkline(busy_history, max_val=100.0)}\n")
    
    text.append("Power (last 60s): ", style="bold")
    # Strix Halo iGPU maximum power is typically ~80-100W, so scale sparkline to 80.0
    text.append(f"{make_sparkline(power_history, max_val=80.0)}\n")

    return Panel(text, title=theme.igpu_title, border_style=theme.igpu_border)


def create_npu_panel(
    npu_active: bool,
    contexts: list[dict],
    xrt_error: bool,
    theme: TuiTheme = DEFAULT_THEME,
) -> Panel:
    """Constructs the NPU status Panel."""
    text = Text()
    
    if xrt_error:
        text.append("[WARNING] xrt-smi not found or failed; NPU telemetry degraded.\n\n", style="bold red")
        return Panel(text, title=theme.npu_title, border_style="red")

    status_str = "ACTIVE" if npu_active else "IDLE"
    status_color = theme.npu_active_style if npu_active else theme.npu_idle_style
    
    text.append("NPU State: ", style="bold")
    text.append(f"{status_str}\n\n", style=status_color)

    # Contexts Table
    table = Table(title=theme.table_title, expand=True, title_style=theme.table_title_style)
    table.add_column("PID", style="cyan")
    table.add_column("Ctx ID", style="magenta")
    table.add_column("Submissions", style="green")
    table.add_column("Completions", style="green")
    table.add_column("Status", style="bold white")

    for ctx in contexts:
        table.add_row(
            str(ctx["pid"]),
            str(ctx["ctx_id"]),
            str(ctx["submissions"]),
            str(ctx["completions"]),
            ctx["status"]
        )

    if not contexts:
        table.add_row("N/A", "N/A", "0", "0", "No active contexts")

    return Panel(Group(text, Align.left(table)), title=theme.npu_title, border_style=theme.npu_border)


def create_header_panel(theme: TuiTheme = DEFAULT_THEME) -> Panel:
    """Constructs the themed header Panel."""
    if not theme.header_art:
        body = Align.center(theme.header, vertical="middle")
    else:
        body = Group(
            *(Align.center(line) for line in theme.header_art),
            Align.center(theme.header),
        )
    return Panel(body, border_style=theme.header_border)


def build_layout(theme: TuiTheme = DEFAULT_THEME) -> Layout:
    """Defines terminal Grid layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=theme.header_size),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3)
    )
    layout["body"].split_row(
        Layout(name="igpu"),
        Layout(name="npu")
    )
    return layout


def _add_hardware_args(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--idle-busy-pct",
        type=int,
        default=default if suppress_defaults else 10,
        help="GPU idle/busy threshold percent",
    )
    parser.add_argument(
        "--prefill-power-w",
        type=float,
        default=default if suppress_defaults else 35.0,
        help="GPU prefill power threshold (W)",
    )
    parser.add_argument(
        "--hysteresis-samples",
        type=int,
        default=default if suppress_defaults else 3,
        help="Hysteresis majority vote window size",
    )
    parser.add_argument(
        "--bench-dir",
        type=str,
        default=default if suppress_defaults else "/tmp/xdna_top",
        help="Directory for latest gauge readings",
    )
    parser.add_argument(
        "--npu-device",
        type=str,
        default=default if suppress_defaults else None,
        help="NPU device BDF override",
    )


def build_parser(
    description: str = "xdna-top: NPU+iGPU Telemetry Monitor",
    *,
    include_commands: bool = True,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--json", action="store_true", help="Print a single fused reading and exit.")
    parser.add_argument(
        "--theme",
        type=str,
        default=None,
        help="TUI theme name (see --list-themes). Overrides XDNA_TOP_THEME.",
    )
    parser.add_argument(
        "--list-themes",
        action="store_true",
        help="List available TUI theme names and exit.",
    )
    _add_hardware_args(parser)

    if include_commands:
        subparsers = parser.add_subparsers(dest="command")
        snapshot_parser = subparsers.add_parser(
            "snapshot",
            help="Capture a schema-versioned platform and telemetry snapshot.",
        )
        snapshot_parser.add_argument("--out", type=str, default=None, help="Output JSON path. Defaults to stdout.")
        _add_hardware_args(snapshot_parser, suppress_defaults=True)

        env_report_parser = subparsers.add_parser(
            "env-report",
            help="Render a Markdown report from a captured snapshot or record stream.",
        )
        env_report_parser.add_argument("snapshot", help="Snapshot JSON or record JSONL path.")
        env_report_parser.add_argument(
            "--markdown",
            action="store_true",
            help="Render Markdown output. Currently the only supported format.",
        )
        env_report_parser.add_argument("--out", type=str, default=None, help="Output Markdown path. Defaults to stdout.")

        record_parser = subparsers.add_parser(
            "record",
            help="Record typed JSONL telemetry events over a time window.",
        )
        record_parser.add_argument(
            "--duration",
            type=float,
            default=60.0,
            help="Total recording duration in seconds.",
        )
        record_parser.add_argument(
            "--interval",
            type=float,
            default=0.2,
            help="Seconds between telemetry samples.",
        )
        record_parser.add_argument("--out", type=str, default=None, help="Output JSONL path. Defaults to stdout.")
        _add_hardware_args(record_parser, suppress_defaults=True)

        mark_parser = subparsers.add_parser(
            "mark",
            help="Append a typed mark event to a record JSONL stream.",
        )
        mark_parser.add_argument("label", help="Marker label, e.g. trial-1-start.")
        mark_parser.add_argument("--out", type=str, default=None, help="Record JSONL path to append to. Defaults to stdout.")

        assert_parser = subparsers.add_parser(
            "assert",
            help="Evaluate named pass/fail checks over a snapshot or record artifact.",
        )
        assert_parser.add_argument("artifact", help="Snapshot JSON or record JSONL path.")
        for check_name, dest, _check_fn in CHECKS:
            assert_parser.add_argument(
                f"--{check_name}",
                dest=dest,
                action="store_true",
                help=f"Require {check_name.replace('require-', '').replace('-', ' ')}.",
            )

        compare_parser = subparsers.add_parser(
            "compare",
            help="Diff two snapshots and report high-signal platform drift.",
        )
        compare_parser.add_argument("before", help="Baseline snapshot JSON path.")
        compare_parser.add_argument("after", help="Newer snapshot JSON path.")

        baseline_parser = subparsers.add_parser(
            "baseline",
            help="Save and check named local baseline snapshots.",
        )
        baseline_sub = baseline_parser.add_subparsers(dest="action")

        baseline_save_parser = baseline_sub.add_parser(
            "save",
            help="Capture a snapshot and store it as a named baseline.",
        )
        baseline_save_parser.add_argument("name", help="Baseline name, e.g. known-good.")
        baseline_check_parser = baseline_sub.add_parser(
            "check",
            help="Compare a fresh snapshot against a named baseline.",
        )
        baseline_check_parser.add_argument("name", help="Baseline name to check against.")
        baseline_list_parser = baseline_sub.add_parser(
            "list",
            help="List saved baseline names.",
        )
        for baseline_action_parser in (
            baseline_save_parser,
            baseline_check_parser,
            baseline_list_parser,
        ):
            baseline_action_parser.add_argument(
                "--dir",
                dest="baseline_dir",
                default=None,
                help="Baselines directory override (defaults to XDG state dir).",
            )
        for baseline_action_parser in (baseline_save_parser, baseline_check_parser):
            _add_hardware_args(baseline_action_parser, suppress_defaults=True)

    return parser


def run_monitor(args: argparse.Namespace, theme: TuiTheme = DEFAULT_THEME) -> int:
    """Runs the live monitor or JSON output for a configured TUI theme."""

    gauge = HardwareGauge(
        gpu_idle_busy_pct=args.idle_busy_pct,
        gpu_prefill_power_w=args.prefill_power_w,
        gauge_hysteresis_samples=args.hysteresis_samples,
        bench_dir=args.bench_dir,
        pessimistic_fallback=False,
        npu_device=args.npu_device,
    )

    if args.json:
        reading = gauge.read()
        print(json.dumps(reading.to_dict(), indent=2))
        return 0

    # TUI loop
    console = Console()
    layout = build_layout(theme)
    
    # Header
    layout["header"].update(create_header_panel(theme))
    
    # Footer
    layout["footer"].update(Panel(
        Align.center(theme.footer, vertical="middle"),
        border_style=theme.footer_border,
    ))

    # History buffers for sparklines
    busy_history = []
    power_history = []
    max_history_len = 60

    key_listener = KeyListener()
    key_listener.set_raw()

    try:
        with Live(layout, refresh_per_second=5, screen=True) as live:
            while True:
                # 1. Scrape hardware state (using client's cached read fallback)
                try:
                    reading = gauge.read()
                    xrt_error = reading.npu_degraded
                except Exception:
                    # Graceful degradation on read failures
                    reading = gauge.read_direct()
                    xrt_error = reading.npu_degraded

                # Scrape raw NPU contexts directly if daemon isn't running
                contexts = []
                npu_out = run_xrt_smi(device=args.npu_device)
                if npu_out:
                    contexts = parse_xrt_smi(npu_out)
                else:
                    if reading.npu_degraded or not Path(args.bench_dir, "gauge_latest.json").exists():
                        xrt_error = True

                # Update history buffers
                if reading.gpu_busy_pct is not None:
                    busy_history.append(float(reading.gpu_busy_pct))
                else:
                    busy_history.append(0.0)

                if reading.gpu_power_w is not None:
                    power_history.append(reading.gpu_power_w)
                else:
                    power_history.append(0.0)

                if len(busy_history) > max_history_len:
                    busy_history.pop(0)
                if len(power_history) > max_history_len:
                    power_history.pop(0)

                # 2. Update Panels
                layout["body"]["igpu"].update(
                    create_igpu_panel(
                        reading.gpu_busy_pct,
                        reading.gpu_power_w,
                        reading.state,
                        busy_history,
                        power_history,
                        igpu_degraded=reading.igpu_degraded,
                        theme=theme,
                    )
                )
                layout["body"]["npu"].update(
                    create_npu_panel(
                        reading.npu_active,
                        contexts,
                        xrt_error,
                        theme=theme,
                    )
                )

                # Check for keyboard inputs
                key = key_listener.check_key()
                if key == "q":
                    break

                time.sleep(0.20)
    except KeyboardInterrupt:
        pass
    finally:
        key_listener.restore()

    console.print(theme.stopped_message)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None) == "snapshot":
        return snapshot_main(args)
    if getattr(args, "command", None) == "env-report":
        return env_report_main(args)
    if getattr(args, "command", None) == "record":
        return record_main(args)
    if getattr(args, "command", None) == "mark":
        return mark_main(args)
    if getattr(args, "command", None) == "assert":
        return assert_main(args)
    if getattr(args, "command", None) == "compare":
        return compare_main(args)
    if getattr(args, "command", None) == "baseline":
        return baseline_main(args)
    return _run_monitor_with_theme(args, default_theme_name="default")


def _run_monitor_with_theme(args: argparse.Namespace, *, default_theme_name: str) -> int:
    """Resolve the requested theme and hand off to the monitor."""
    if getattr(args, "list_themes", False):
        print(list_themes())
        return 0
    theme = resolve_theme(getattr(args, "theme", None), default_name=default_theme_name)
    if theme is None:
        print(
            f"unknown theme {args.theme!r}; available: {', '.join(sorted(THEMES))}",
            file=sys.stderr,
        )
        return 2
    return run_monitor(args, theme=theme)


if __name__ == "__main__":
    sys.exit(main())
