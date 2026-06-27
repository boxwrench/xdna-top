# Design: Prometheus exporter for xdna-top

**Date:** 2026-06-26
**Status:** Approved (design); pending implementation plan
**Branch:** `claude/prometheus-exporter`

## Goal

Ship a standalone `xdna-top exporter` subcommand that exposes hardware telemetry
at an HTTP `/metrics` endpoint in Prometheus text format, so **anyone** can
scrape it into Prometheus and graph it in Grafana. This is a shippable feature
of xdna-top, not a personal/one-off setup. Deliverables include docs and a
ready-to-import Grafana dashboard.

### Why

The TUI shows *now*; `snapshot`/`record` capture point-in-time or streamed JSON.
Neither gives a low-friction way to watch NPU/iGPU activity and power **over
time** in a shared dashboard. Prometheus + Grafana is the standard answer, and
the only piece xdna-top needs to provide is the exporter — Prometheus and
Grafana are off-the-shelf.

## Non-goals (YAGNI)

- No bundled `docker-compose.yml` stack (Prometheus + Grafana pre-wired). Useful,
  but adds a maintenance surface (image versions, provisioning drift); deferred
  as a possible follow-up once metric names are stable.
- No per-process / per-PID labeled metrics (cardinality risk — see Metric set).
- No background sampling loop or buffering inside xdna-top. Prometheus is
  pull-based and owns the history.
- No coupling to the existing daemon. The exporter is a standalone process.

## Architecture

```
xdna-top exporter ──serves──▶ GET /metrics ◀──scrapes── Prometheus ◀──queries── Grafana
   │
   └─ on each scrape: HardwareGauge.read() + NPU contexts → render as Prometheus text
```

- **`src/xdna_top/exporter.py`** — a custom `prometheus_client` **Collector**
  whose `.collect()` reads the hardware *at scrape time* and yields metric
  families. Stateless; matches Prometheus's pull model (no background loop,
  Prometheus stores history). Metrics are a new *rendering* of the existing
  fused reading, not a new data path.
- **`serve(host, port)`** registers the collector against a dedicated registry
  and runs `prometheus_client`'s HTTP server.
- **`exporter` subcommand** in `main.py`, mirroring `snapshot`/`record`:
  - `--host` (default `127.0.0.1`)
  - `--port` (default `9477`; configurable to avoid collisions)
  - reuses `_add_hardware_args` for hardware-source overrides.
- **`[exporter]` optional-dependency extra** in `pyproject.toml` →
  `prometheus_client`. Base install stays `rich`-only, mirroring the existing
  `[docs]` extra.

## Metric set

Aggregate gauges + global counters. No per-PID labels.

| Metric | Type | Notes |
|---|---|---|
| `xdna_up` | gauge | 1 if the scrape read hardware OK, else 0 |
| `xdna_igpu_busy_percent` | gauge | 0–100 |
| `xdna_igpu_power_watts` | gauge | iGPU package power |
| `xdna_npu_active` | gauge | 0/1 |
| `xdna_npu_context_count` | gauge | live HW contexts |
| `xdna_npu_submissions_total` | counter | global, absolute cumulative |
| `xdna_npu_completions_total` | counter | global, absolute cumulative |
| `xdna_igpu_degraded` | gauge | 0/1 |
| `xdna_npu_degraded` | gauge | 0/1 |
| `xdna_state{state="IDLE\|BUSY\|…"}` | gauge | enum pattern: current state = 1, others 0 |
| `xdna_npu_clock_mhz{domain="npuclk\|hclk"}` | gauge | **additive, present only when** debugfs power_state is available (PR #13) |

Notes:

- **Counters** are exposed as `CounterMetricFamily` set to the absolute
  cumulative value xrt already reports — the correct way to surface an
  externally-sourced counter. `rate()` in Grafana then yields NPU throughput.
- **Cardinality:** counters are global (summed across contexts), so there are no
  unbounded `{pid}` labels. Per-PID forensics stays with `record`/`snapshot`
  JSONL, which has no TSDB cost.
- **`xdna_npu_clock_mhz`** is emitted only when PR #13's debugfs `power_state`
  read returns `available: true`, mirroring that PR's "honest availability"
  design. If #13 has not landed at implementation time, omit this metric and add
  it as a follow-up — the rest of the exporter does not depend on it.

## Error handling

The collector **never raises**. If the scrape-time read fails (no NPU, xrt
error, RLIMIT_MEMLOCK trap), it emits `xdna_up 0` plus whatever gauges it can
(e.g. `xdna_npu_active 0`) rather than breaking the scrape. The endpoint staying
up while reporting "degraded" is itself a signal Prometheus can alert on.

## Testing (`tests/test_exporter.py`)

- Feed the collector a synthetic fused-reading dict, run `generate_latest()`,
  and assert the rendered text: gauge values, counter absolute exposure, the
  `state` enum (current = 1, others 0), degraded mapping.
- **NPU-absent path:** assert `xdna_npu_active 0`, `xdna_up 1`, and no
  `xdna_npu_clock_mhz` series — verifying graceful degradation. Reuse the
  XDNA1/absent reading shape from the PR #11 captures.
- **Read-failure path:** assert `xdna_up 0` and no unhandled exception.

## Deliverables

1. `src/xdna_top/exporter.py` — collector + `serve()`.
2. `exporter` subcommand wiring in `src/xdna_top/main.py`.
3. `[exporter]` extra in `pyproject.toml`.
4. `docs/EXPORTER.md` — run instructions + copy-paste `prometheus.yml` scrape
   block.
5. `docs/grafana/xdna-top-dashboard.json` — importable dashboard (iGPU busy %,
   iGPU power, NPU-active timeline, submission `rate()`).
6. `tests/test_exporter.py`.

## Open dependency note

`xdna_npu_clock_mhz` depends on PR #13 (debugfs power_state) being merged. The
exporter ships without it if #13 is not yet in; adding the metric afterward is a
small additive change.
