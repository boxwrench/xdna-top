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
