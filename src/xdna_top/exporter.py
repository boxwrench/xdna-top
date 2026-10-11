"""Prometheus exporter for xdna-top.

Renders the existing fused hardware reading as Prometheus metric families at
scrape time. Stateless by design: Prometheus pulls on its own schedule and owns
the history, so there is no background sampling loop here.
"""

from __future__ import annotations

import time
from typing import Callable, Iterator

from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from xdna_top.gauge import GaugeReading, GpuState, HardwareGauge
from xdna_top.npu_power import read_npu_power


def _build_metrics(
    reading: GaugeReading,
    contexts: list[dict],
    power_state: dict | None = None,
) -> Iterator[Metric]:
    """Yield Prometheus metric families for one hardware reading.

    Pure and hardware-free: callers supply an already-read ``GaugeReading``, the
    parsed NPU ``contexts`` list, and an optional debugfs ``power_state`` dict.
    A ``None`` gauge value omits its sample rather than reporting a misleading 0.
    """
    yield GaugeMetricFamily(
        "xdna_up", "1 if the last hardware scrape succeeded, else 0", value=1.0
    )

    if reading.gpu_busy_pct is not None:
        busy = GaugeMetricFamily("xdna_igpu_busy_percent", "First amdgpu device busy percentage (0-100)")
        busy.add_metric([], float(reading.gpu_busy_pct))
        yield busy

    if reading.gpu_power_w is not None:
        power = GaugeMetricFamily("xdna_igpu_power_watts", "First amdgpu device power (watts); on APUs includes CPU/SoC")
        power.add_metric([], float(reading.gpu_power_w))
        yield power

    info = GaugeMetricFamily("xdna_gpu_info", "amdgpu device identity", labels=["card", "bdf", "name", "type"])
    for gpu in reading.gpus:
        kind = "apu" if gpu.get("is_apu") is True else "discrete" if gpu.get("is_apu") is False else "unknown"
        info.add_metric([gpu["card"], gpu["bdf"], gpu["name"], kind], 1)
    yield info
    fields = {
        "busy_pct": ("busy_percent", "Driver GPU busy percentage"),
        "vram_used_bytes": ("vram_used_bytes", "VRAM used in bytes"),
        "vram_total_bytes": ("vram_total_bytes", "VRAM capacity in bytes"),
        "gtt_used_bytes": ("gtt_used_bytes", "GTT used in bytes"),
        "gtt_total_bytes": ("gtt_total_bytes", "GTT budget in bytes; shared host RAM, do not sum budgets"),
        "edge_temp_c": ("edge_temperature_celsius", "GPU edge temperature"),
        "power_w": ("power_watts", "amdgpu power; on APUs includes CPU/SoC"),
        "power_cap_w": ("power_cap_watts", "amdgpu power cap"),
        "sclk_mhz": ("sclk_mhz", "Current shader clock sample from hwmon freq1_input (MHz)"),
        "pcie_gen": ("pcie_generation", "Current PCIe generation; not meaningful on APUs"),
        "pcie_gen_max": ("pcie_generation_max", "Maximum PCIe generation; not meaningful on APUs"),
        "pcie_width": ("pcie_width", "Current PCIe lanes; not meaningful on APUs"),
        "pcie_width_max": ("pcie_width_max", "Maximum PCIe lanes; not meaningful on APUs"),
        "degraded": ("degraded", "1 if busy or power is unavailable"),
    }
    for field, (suffix, help_text) in fields.items():
        metric = GaugeMetricFamily(f"xdna_gpu_{suffix}", help_text, labels=["card", "bdf"])
        for gpu in reading.gpus:
            value = gpu.get(field)
            if value is not None and not (field.startswith("pcie_") and gpu.get("is_apu") is True):
                metric.add_metric([gpu["card"], gpu["bdf"]], float(value))
        yield metric

    yield GaugeMetricFamily(
        "xdna_npu_active", "1 if the NPU has in-flight work, else 0",
        value=1.0 if reading.npu_active else 0.0,
    )
    yield GaugeMetricFamily(
        "xdna_npu_context_count", "Live NPU hardware contexts",
        value=float(len(contexts)),
    )
    yield GaugeMetricFamily(
        "xdna_igpu_degraded", "1 if iGPU telemetry is degraded/unavailable",
        value=1.0 if reading.igpu_degraded else 0.0,
    )
    yield GaugeMetricFamily(
        "xdna_npu_degraded", "1 if NPU telemetry is degraded/unavailable",
        value=1.0 if reading.npu_degraded else 0.0,
    )

    # Counter families are named WITHOUT _total; prometheus_client appends it.
    yield CounterMetricFamily(
        "xdna_npu_submissions", "Cumulative NPU work submissions (all contexts)",
        value=float(sum(c.get("submissions", 0) for c in contexts)),
    )
    yield CounterMetricFamily(
        "xdna_npu_completions", "Cumulative NPU work completions (all contexts)",
        value=float(sum(c.get("completions", 0) for c in contexts)),
    )

    state = GaugeMetricFamily(
        "xdna_state", "Current GPU/NPU pipeline state (current=1)", labels=["state"]
    )
    for member in GpuState:
        state.add_metric([member.value], 1.0 if reading.state == member else 0.0)
    yield state

    if power_state and power_state.get("available"):
        active = (power_state.get("dpm") or {}).get("active") or {}
        clk = GaugeMetricFamily(
            "xdna_npu_clock_mhz",
            "NPU active DPM clock frequency (MHz)", labels=["domain"],
        )
        if active.get("npuclk_mhz") is not None:
            clk.add_metric(["npuclk"], float(active["npuclk_mhz"]))
        if active.get("hclk_mhz") is not None:
            clk.add_metric(["hclk"], float(active["hclk_mhz"]))
        yield clk


def _hardware_reader(
    gauge: HardwareGauge, npu_device: str | None
) -> Callable[[], tuple[GaugeReading, list[dict], dict | None]]:
    """Build the scrape-time read function for the production exporter."""
    def read() -> tuple[GaugeReading, list[dict], dict | None]:
        reading, contexts = gauge.sample_direct()
        return reading, contexts, read_npu_power(npu_device)

    return read


def serve(host: str, port: int, gauge: HardwareGauge, npu_device: str | None) -> None:
    """Register the collector and serve /metrics until interrupted."""
    registry = CollectorRegistry()
    registry.register(XdnaCollector(_hardware_reader(gauge, npu_device)))
    start_http_server(port, addr=host, registry=registry)
    while True:
        time.sleep(3600)


def exporter_main(args) -> int:
    gauge = HardwareGauge(
        gpu_idle_busy_pct=args.idle_busy_pct,
        gpu_prefill_power_w=args.prefill_power_w,
        gauge_hysteresis_samples=args.hysteresis_samples,
        bench_dir=args.bench_dir,
        npu_device=args.npu_device,
    )
    print(f"xdna-top exporter listening on http://{args.host}:{args.port}/metrics")
    try:
        serve(args.host, args.port, gauge, args.npu_device)
    except KeyboardInterrupt:
        pass
    return 0


class XdnaCollector:
    """A prometheus_client collector that reads hardware at scrape time.

    ``read_fn`` returns ``(GaugeReading, contexts, power_state)`` or raises. The
    collector never propagates an exception: a failed read yields ``xdna_up 0``
    and nothing else, so a broken probe is observable rather than a 500.
    """

    def __init__(
        self,
        read_fn: Callable[[], tuple[GaugeReading, list[dict], dict | None]],
    ) -> None:
        self._read_fn = read_fn

    def collect(self) -> Iterator[Metric]:
        try:
            reading, contexts, power_state = self._read_fn()
        except Exception:
            yield GaugeMetricFamily(
                "xdna_up", "1 if the last hardware scrape succeeded, else 0",
                value=0.0,
            )
            return
        yield from _build_metrics(reading, contexts, power_state)
