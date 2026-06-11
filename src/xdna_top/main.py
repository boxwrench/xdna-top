import os
import sys
import json
import time
import select
import tty
import termios
import argparse
from pathlib import Path

from rich.layout import Layout
from rich.panel import Panel
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.align import Align

from xdna_top.gauge import HardwareGauge, GpuState, run_xrt_smi, parse_xrt_smi


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
) -> Panel:
    """Constructs the iGPU status Panel."""
    text = Text()
    if igpu_degraded:
        text.append("[WARNING] sysfs endpoints missing or unreadable; iGPU telemetry degraded.\n\n", style="bold red")
        return Panel(text, title="[bold cyan]iGPU Telemetry[/bold cyan]", border_style="red")

    # Color coding for state
    state_color = "green"
    if state == GpuState.ACTIVE:
        state_color = "yellow"
    elif state == GpuState.PREFILL_BURST:
        state_color = "bold red"

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

    return Panel(text, title="[bold cyan]iGPU Telemetry[/bold cyan]", border_style="cyan")


def create_npu_panel(npu_active: bool, contexts: list[dict], xrt_error: bool) -> Panel:
    """Constructs the NPU status Panel."""
    text = Text()
    
    if xrt_error:
        text.append("[WARNING] xrt-smi not found or failed; NPU telemetry degraded.\n\n", style="bold red")
        return Panel(text, title="[bold magenta]Ryzen AI NPU[/bold magenta]", border_style="red")

    status_str = "ACTIVE" if npu_active else "IDLE"
    status_color = "bold green" if npu_active else "bold yellow"
    
    text.append("NPU State: ", style="bold")
    text.append(f"{status_str}\n\n", style=status_color)

    # Contexts Table
    table = Table(title="Active Hardware Contexts", expand=True, title_style="bold magenta")
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

    return Panel(Group(text, Align.left(table)), title="[bold magenta]Ryzen AI NPU[/bold magenta]", border_style="magenta")


def build_layout() -> Layout:
    """Defines terminal Grid layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=3)
    )
    layout["body"].split_row(
        Layout(name="igpu"),
        Layout(name="npu")
    )
    return layout


def main() -> int:
    parser = argparse.ArgumentParser(description="xdna-top: NPU+iGPU Telemetry Monitor")
    parser.add_argument("--json", action="store_true", help="Print a single fused reading and exit.")
    parser.add_argument("--idle-busy-pct", type=int, default=10, help="GPU idle/busy threshold percent")
    parser.add_argument("--prefill-power-w", type=float, default=35.0, help="GPU prefill power threshold (W)")
    parser.add_argument("--hysteresis-samples", type=int, default=3, help="Hysteresis majority vote window size")
    parser.add_argument("--bench-dir", type=str, default="/tmp/xdna_top", help="Directory for latest gauge readings")
    parser.add_argument("--npu-device", type=str, default=None, help="NPU device BDF override")
    args = parser.parse_args()

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
    layout = build_layout()
    
    # Header
    layout["header"].update(Panel(
        Align.center("[bold white]xdna-top[/bold white] — Unified NPU+iGPU Monitor [Strix Halo]", vertical="middle"),
        border_style="white"
    ))
    
    # Footer
    layout["footer"].update(Panel(
        Align.center("Press [bold red]q[/bold red] or [bold red]Ctrl-C[/bold red] to quit. Telemetry refresh rate: 5 Hz.", vertical="middle"),
        border_style="white"
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
                    )
                )
                layout["body"]["npu"].update(
                    create_npu_panel(
                        reading.npu_active,
                        contexts,
                        xrt_error
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

    console.print("[green]xdna-top stopped cleanly.[/green]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
