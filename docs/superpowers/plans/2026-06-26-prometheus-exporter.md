# Prometheus Exporter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone `xdna-top exporter` subcommand that serves hardware telemetry at an HTTP `/metrics` endpoint in Prometheus format, with docs and an example Grafana dashboard.

**Architecture:** A custom `prometheus_client` collector reads the existing `HardwareGauge` fused reading plus NPU contexts *at scrape time* (stateless; Prometheus owns history) and renders them as metric families. A pure `_build_metrics()` function does the rendering (unit-testable without hardware); `XdnaCollector` wraps a read function and degrades to `xdna_up 0` on failure; `serve()` + an `exporter` subcommand expose it.

**Tech Stack:** Python 3.11+, `prometheus_client` (opt-in `[exporter]` extra), `pytest`.

## Global Constraints

- Base install stays `rich`-only; `prometheus_client` lives in the `[exporter]` optional-dependency extra (mirrors the existing `[docs]` extra).
- No per-PID / per-process labels (cardinality). NPU submission/completion data is exposed as **global** counters only.
- The collector MUST NOT raise; a failed scrape emits `xdna_up 0` and returns.
- `xdna_npu_clock_mhz` is additive and gated on PR #13's debugfs `power_state`. The production reader passes `power_state=None` until #13 lands; `_build_metrics` already supports the populated branch and is tested.
- Counter families are named **without** the `_total` suffix (e.g. `xdna_npu_submissions`); `prometheus_client` appends `_total` in the rendered output.
- To run tests: `pip install -e '.[exporter]'` (provides `prometheus_client`).
- Reference data shapes (verbatim from the codebase):
  - `GaugeReading(gpu_busy_pct: int|None, gpu_power_w: float|None, npu_active: bool, state: GpuState, igpu_degraded=False, npu_degraded=False)` — from `src/xdna_top/gauge.py`.
  - `GpuState` enum members include `IDLE`, `PREFILL_BURST`, `UNKNOWN` (iterate the enum; don't hard-code the list).
  - `parse_xrt_smi(out)` returns `list[dict]` with keys `pid, ctx_id, submissions, completions, process_name, status`.
  - `run_xrt_smi(device=...)` returns `str | None`.
  - Hardware CLI args (from `_add_hardware_args`): `args.idle_busy_pct`, `args.prefill_power_w`, `args.hysteresis_samples`, `args.bench_dir`, `args.npu_device`.

---

### Task 1: Metric renderer `_build_metrics` + `[exporter]` extra

**Files:**
- Modify: `pyproject.toml` (add `[exporter]` extra)
- Create: `src/xdna_top/exporter.py`
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes: `GaugeReading`, `GpuState` from `xdna_top.gauge`.
- Produces: `_build_metrics(reading: GaugeReading, contexts: list[dict], power_state: dict | None = None) -> Iterator[Metric]` — yields `prometheus_client` metric families. Used by `XdnaCollector` (Task 2).

- [ ] **Step 1: Add the `[exporter]` extra**

In `pyproject.toml`, under `[project.optional-dependencies]` (next to the existing `docs = [...]`):

```toml
# Prometheus exporter (`xdna-top exporter`). Not needed for the TUI/snapshots.
exporter = [
    "prometheus_client",
]
```

Then install it: `pip install -e '.[exporter]'`

- [ ] **Step 2: Write the failing test**

```python
# tests/test_exporter.py
from __future__ import annotations

from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client.core import GaugeMetricFamily

from xdna_top.exporter import _build_metrics
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_exporter.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_metrics'`

- [ ] **Step 4: Implement `_build_metrics`**

```python
# src/xdna_top/exporter.py
"""Prometheus exporter for xdna-top.

Renders the existing fused hardware reading as Prometheus metric families at
scrape time. Stateless by design: Prometheus pulls on its own schedule and owns
the history, so there is no background sampling loop here.
"""

from __future__ import annotations

from typing import Iterator

from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric

from xdna_top.gauge import GaugeReading, GpuState


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

    busy = GaugeMetricFamily("xdna_igpu_busy_percent", "iGPU busy percentage (0-100)")
    if reading.gpu_busy_pct is not None:
        busy.add_metric([], float(reading.gpu_busy_pct))
    yield busy

    power = GaugeMetricFamily("xdna_igpu_power_watts", "iGPU package power (watts)")
    if reading.gpu_power_w is not None:
        power.add_metric([], float(reading.gpu_power_w))
    yield power

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/xdna_top/exporter.py tests/test_exporter.py
git commit -m "feat(exporter): metric renderer + [exporter] extra"
```

---

### Task 2: `XdnaCollector` with graceful failure

**Files:**
- Modify: `src/xdna_top/exporter.py`
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes: `_build_metrics` (Task 1).
- Produces: `XdnaCollector(read_fn: Callable[[], tuple[GaugeReading, list[dict], dict | None]])` with a `.collect()` method. `read_fn` returns `(reading, contexts, power_state)` or raises. Used by `serve()` (Task 3).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exporter.py`:

```python
from xdna_top.exporter import XdnaCollector


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_exporter.py -k collector -v`
Expected: FAIL with `ImportError: cannot import name 'XdnaCollector'`

- [ ] **Step 3: Implement `XdnaCollector`**

Add to `src/xdna_top/exporter.py` (update the import line and append the class):

```python
from typing import Callable, Iterator  # replace the existing Iterator import line
```

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/xdna_top/exporter.py tests/test_exporter.py
git commit -m "feat(exporter): scrape-time collector with up=0 on read failure"
```

---

### Task 3: `serve()` + `exporter` subcommand

**Files:**
- Modify: `src/xdna_top/exporter.py` (add `_hardware_reader`, `serve`, `exporter_main`)
- Modify: `src/xdna_top/main.py` (register subcommand + dispatch)
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes: `XdnaCollector` (Task 2); `HardwareGauge`, `run_xrt_smi`, `parse_xrt_smi` from `xdna_top.gauge`.
- Produces: `serve(host: str, port: int, gauge: HardwareGauge, npu_device: str | None) -> None`; `exporter_main(args) -> int`.

- [ ] **Step 1: Write the failing test** (covers the reader wiring without opening a socket)

Append to `tests/test_exporter.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_exporter.py -k hardware_reader -v`
Expected: FAIL with `ImportError: cannot import name '_hardware_reader'`

- [ ] **Step 3: Implement `_hardware_reader`, `serve`, `exporter_main`**

Add to `src/xdna_top/exporter.py` (extend imports + append):

```python
import time

from prometheus_client import CollectorRegistry, start_http_server

from xdna_top.gauge import HardwareGauge, parse_xrt_smi, run_xrt_smi
```

```python
def _hardware_reader(
    gauge: HardwareGauge, npu_device: str | None
) -> Callable[[], tuple[GaugeReading, list[dict], dict | None]]:
    """Build the scrape-time read function for the production exporter."""
    def read() -> tuple[GaugeReading, list[dict], dict | None]:
        reading = gauge.read()
        out = run_xrt_smi(device=npu_device)
        contexts = parse_xrt_smi(out) if out else []
        # power_state stays None until PR #13 (debugfs read_npu_power) lands.
        return reading, contexts, None

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
```

- [ ] **Step 4: Wire the subcommand in `main.py`**

In `build_parser`, inside the `subparsers` block (alongside the other `add_parser` calls), add:

```python
        exporter_parser = subparsers.add_parser(
            "exporter",
            help="Serve Prometheus metrics at /metrics for Prometheus/Grafana.",
        )
        exporter_parser.add_argument(
            "--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)."
        )
        exporter_parser.add_argument(
            "--port", type=int, default=9477, help="Listen port (default 9477)."
        )
        _add_hardware_args(exporter_parser)
```

In `main()`, add the dispatch line next to the other commands:

```python
    if getattr(args, "command", None) == "exporter":
        from xdna_top.exporter import exporter_main
        return exporter_main(args)
```

- [ ] **Step 5: Run tests + verify the CLI parses**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS (9 tests)

Run: `xdna-top exporter --help`
Expected: help text showing `--host`, `--port`, and the hardware args.

- [ ] **Step 6: Commit**

```bash
git add src/xdna_top/exporter.py src/xdna_top/main.py tests/test_exporter.py
git commit -m "feat(exporter): serve() + xdna-top exporter subcommand"
```

---

### Task 4: Operator docs

**Files:**
- Create: `docs/EXPORTER.md`
- Modify: `README.md` (one-line pointer, optional)

**Interfaces:** none (documentation).

- [ ] **Step 1: Write `docs/EXPORTER.md`**

````markdown
# Prometheus exporter

`xdna-top exporter` serves hardware telemetry at `/metrics` in Prometheus
format, so you can scrape it into Prometheus and graph it in Grafana.

## Install

```bash
pip install -e '.[exporter]'   # pulls in prometheus_client
```

## Run

```bash
xdna-top exporter --host 0.0.0.0 --port 9477
# -> serving http://0.0.0.0:9477/metrics
```

The exporter reads the hardware on each scrape (stateless); Prometheus owns the
history. It accepts the same hardware-source flags as the rest of xdna-top
(`--npu-device`, `--bench-dir`, …).

## Scrape it from Prometheus

Add to `prometheus.yml`:

```yaml
scrape_configs:
  - job_name: xdna-top
    static_configs:
      - targets: ["localhost:9477"]
```

## Metrics

| Metric | Type | Meaning |
|---|---|---|
| `xdna_up` | gauge | 1 if the scrape read hardware OK |
| `xdna_igpu_busy_percent` | gauge | iGPU busy % (0–100) |
| `xdna_igpu_power_watts` | gauge | iGPU package power (W) |
| `xdna_npu_active` | gauge | 1 if NPU has in-flight work |
| `xdna_npu_context_count` | gauge | live NPU hardware contexts |
| `xdna_npu_submissions_total` | counter | cumulative NPU submissions (use `rate()`) |
| `xdna_npu_completions_total` | counter | cumulative NPU completions |
| `xdna_igpu_degraded` / `xdna_npu_degraded` | gauge | 1 if that telemetry is degraded |
| `xdna_state{state="…"}` | gauge | current pipeline state = 1, others 0 |
| `xdna_npu_clock_mhz{domain="npuclk\|hclk"}` | gauge | NPU active DPM clocks (only when the debugfs power-state node is readable) |

## Grafana

Import `docs/grafana/xdna-top-dashboard.json` and point it at your Prometheus
data source.
````

- [ ] **Step 2: Commit**

```bash
git add docs/EXPORTER.md README.md
git commit -m "docs(exporter): operator guide + prometheus.yml scrape config"
```

---

### Task 5: Example Grafana dashboard

**Files:**
- Create: `docs/grafana/xdna-top-dashboard.json`

**Interfaces:** none (importable Grafana dashboard JSON).

- [ ] **Step 1: Write `docs/grafana/xdna-top-dashboard.json`**

A minimal, importable dashboard with four time-series panels driven by a
templated Prometheus datasource (`${DS_PROMETHEUS}`):

1. iGPU busy % — `xdna_igpu_busy_percent`
2. iGPU power (W) — `xdna_igpu_power_watts`
3. NPU active timeline — `xdna_npu_active`
4. NPU submission rate — `rate(xdna_npu_submissions_total[1m])`

```json
{
  "__inputs": [
    {
      "name": "DS_PROMETHEUS",
      "label": "Prometheus",
      "description": "",
      "type": "datasource",
      "pluginId": "prometheus",
      "pluginName": "Prometheus"
    }
  ],
  "title": "xdna-top — NPU + iGPU",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "5s",
  "time": { "from": "now-15m", "to": "now" },
  "templating": {
    "list": [
      {
        "name": "DS_PROMETHEUS",
        "type": "datasource",
        "query": "prometheus",
        "current": {},
        "hide": 0
      }
    ]
  },
  "panels": [
    {
      "type": "timeseries",
      "title": "iGPU busy %",
      "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 0 },
      "fieldConfig": { "defaults": { "unit": "percent", "min": 0, "max": 100 }, "overrides": [] },
      "targets": [
        { "expr": "xdna_igpu_busy_percent", "refId": "A", "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" } }
      ]
    },
    {
      "type": "timeseries",
      "title": "iGPU power (W)",
      "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 0 },
      "fieldConfig": { "defaults": { "unit": "watt", "min": 0 }, "overrides": [] },
      "targets": [
        { "expr": "xdna_igpu_power_watts", "refId": "A", "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" } }
      ]
    },
    {
      "type": "timeseries",
      "title": "NPU active",
      "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" },
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 8 },
      "fieldConfig": { "defaults": { "unit": "short", "min": 0, "max": 1 }, "overrides": [] },
      "targets": [
        { "expr": "xdna_npu_active", "refId": "A", "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" } }
      ]
    },
    {
      "type": "timeseries",
      "title": "NPU submissions / sec",
      "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" },
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 8 },
      "fieldConfig": { "defaults": { "unit": "ops", "min": 0 }, "overrides": [] },
      "targets": [
        { "expr": "rate(xdna_npu_submissions_total[1m])", "refId": "A", "datasource": { "type": "prometheus", "uid": "${DS_PROMETHEUS}" } }
      ]
    }
  ]
}
```

- [ ] **Step 2: Validate the JSON parses**

Run: `python -c "import json; json.load(open('docs/grafana/xdna-top-dashboard.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add docs/grafana/xdna-top-dashboard.json
git commit -m "docs(exporter): example Grafana dashboard"
```

---

## Self-Review

- **Spec coverage:** standalone subcommand (Task 3) ✓; `prometheus_client` opt-in extra (Task 1) ✓; metric set incl. global counters + state enum + degraded + optional clocks (Task 1) ✓; never-raise / `xdna_up 0` (Task 2) ✓; tests incl. NPU-absent + read-failure (Tasks 1–3) ✓; docs + `prometheus.yml` (Task 4) ✓; example dashboard (Task 5) ✓. No gaps.
- **Placeholders:** none — every code/doc step shows full content.
- **Type consistency:** `_build_metrics(reading, contexts, power_state)` and `read_fn() -> (reading, contexts, power_state)` line up across Tasks 1–3; counter families named without `_total` (constraint) match the assertions on `xdna_npu_submissions_total`.
- **Open dependency:** `xdna_npu_clock_mhz` populated branch is implemented + tested now; production reader passes `None` until PR #13 lands (constraint documented).
