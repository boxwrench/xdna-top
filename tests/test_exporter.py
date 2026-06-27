from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from xdna_top.exporter import XdnaCollector, _build_metrics
from xdna_top.gauge import GaugeReading, GpuState


def _render(*families) -> str:
    """Render a list of metric families to Prometheus text via a throwaway collector."""
    class _Static:
        def collect(self):
            yield from families

    reg = CollectorRegistry()
    reg.register(_Static())
    return generate_latest(reg).decode()


def test_core_gauges_and_up():
    reading = GaugeReading(
        gpu_busy_pct=42, gpu_power_w=18.5, npu_active=True, state=GpuState.IDLE
    )
    out = _render(*_build_metrics(reading, [], None))
    assert "xdna_up 1.0" in out
    assert "xdna_igpu_busy_percent 42.0" in out
    assert "xdna_igpu_power_watts 18.5" in out
    assert "xdna_npu_active 1.0" in out
    assert 'xdna_state{state="IDLE"} 1.0' in out


def test_global_counters_sum_contexts():
    reading = GaugeReading(
        gpu_busy_pct=5, gpu_power_w=12.0, npu_active=True, state=GpuState.IDLE
    )
    contexts = [
        {"pid": 1, "ctx_id": 1, "submissions": 1865, "completions": 1864},
        {"pid": 2, "ctx_id": 1, "submissions": 10, "completions": 9},
    ]
    out = _render(*_build_metrics(reading, contexts, None))
    assert "xdna_npu_context_count 2.0" in out
    assert "xdna_npu_submissions_total 1875.0" in out
    assert "xdna_npu_completions_total 1873.0" in out


def test_none_gauge_omits_sample():
    reading = GaugeReading(
        gpu_busy_pct=None, gpu_power_w=None, npu_active=False,
        state=GpuState.UNKNOWN, igpu_degraded=True,
    )
    out = _render(*_build_metrics(reading, [], None))
    # No misleading 0 when the input is genuinely absent.
    assert "xdna_igpu_busy_percent " not in out.replace("# ", "")
    assert "xdna_igpu_degraded 1.0" in out
    assert "xdna_npu_active 0.0" in out


def test_power_state_clocks_when_available():
    reading = GaugeReading(
        gpu_busy_pct=5, gpu_power_w=12.0, npu_active=True, state=GpuState.IDLE
    )
    power_state = {
        "available": True,
        "dpm": {"active": {"npuclk_mhz": 847, "hclk_mhz": 1600}},
    }
    out = _render(*_build_metrics(reading, [], power_state))
    assert 'xdna_npu_clock_mhz{domain="npuclk"} 847.0' in out
    assert 'xdna_npu_clock_mhz{domain="hclk"} 1600.0' in out


def test_power_state_absent_emits_no_clock():
    reading = GaugeReading(
        gpu_busy_pct=5, gpu_power_w=12.0, npu_active=False, state=GpuState.IDLE
    )
    out = _render(*_build_metrics(reading, [], {"available": False}))
    assert "xdna_npu_clock_mhz" not in out


def test_collector_renders_from_read_fn():
    reading = GaugeReading(
        gpu_busy_pct=7, gpu_power_w=13.0, npu_active=False, state=GpuState.IDLE
    )
    reg = CollectorRegistry()
    reg.register(XdnaCollector(lambda: (reading, [], None)))
    out = generate_latest(reg).decode()
    assert "xdna_up 1.0" in out
    assert "xdna_igpu_busy_percent 7.0" in out


def test_collector_reports_down_on_read_failure():
    def boom():
        raise RuntimeError("xrt exploded")

    reg = CollectorRegistry()
    reg.register(XdnaCollector(boom))
    out = generate_latest(reg).decode()
    assert "xdna_up 0.0" in out
    # No other families when the read failed.
    assert "xdna_igpu_busy_percent" not in out


from xdna_top.exporter import _hardware_reader


class _FakeGauge:
    def read(self):
        return GaugeReading(
            gpu_busy_pct=3, gpu_power_w=11.0, npu_active=True, state=GpuState.IDLE
        )


def test_hardware_reader_collects_reading_and_contexts(monkeypatch):
    import xdna_top.exporter as exp
    monkeypatch.setattr(exp, "run_xrt_smi", lambda device=None: "RAW")
    monkeypatch.setattr(
        exp, "parse_xrt_smi",
        lambda out: [{"pid": 1, "ctx_id": 1, "submissions": 5, "completions": 4}],
    )
    read = _hardware_reader(_FakeGauge(), None)
    reading, contexts, power_state = read()
    assert reading.gpu_busy_pct == 3
    assert len(contexts) == 1
    assert power_state is None  # gated on PR #13


def test_hardware_reader_empty_contexts_when_xrt_absent(monkeypatch):
    import xdna_top.exporter as exp
    monkeypatch.setattr(exp, "run_xrt_smi", lambda device=None: None)
    read = _hardware_reader(_FakeGauge(), None)
    _reading, contexts, _ps = read()
    assert contexts == []
