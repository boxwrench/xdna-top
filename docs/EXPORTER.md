# Prometheus exporter

`xdna-top exporter` serves hardware telemetry at `/metrics` in Prometheus
format, so you can scrape it into Prometheus and graph it in Grafana.

## Install

```bash
pip install -e '.[exporter]'   # pulls in prometheus_client
```

## Run

```bash
xdna-top exporter --port 9477
# -> serving http://127.0.0.1:9477/metrics
```

The exporter reads the hardware on each scrape (stateless); Prometheus owns the
history. It accepts the same hardware-source flags as the rest of xdna-top
(`--npu-device`, `--bench-dir`, …).

`/metrics` is unauthenticated. The default bind host is `127.0.0.1`
(loopback-only) — keep it there unless you need remote scraping. Use
`--host 0.0.0.0` to expose it on all interfaces, and only on a trusted network
(or behind a firewall / reverse proxy):

```bash
xdna-top exporter --host 0.0.0.0 --port 9477   # all interfaces — trusted networks only
```

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
| `xdna_igpu_busy_percent` | gauge | First amdgpu card busy % (0–100) |
| `xdna_igpu_power_watts` | gauge | First amdgpu card power (W); APU includes CPU/SoC |
| `xdna_npu_active` | gauge | 1 if NPU has in-flight work |
| `xdna_npu_context_count` | gauge | live NPU hardware contexts |
| `xdna_npu_submissions_total` | counter | cumulative NPU submissions (use `rate()`) |
| `xdna_npu_completions_total` | counter | cumulative NPU completions |
| `xdna_igpu_degraded` / `xdna_npu_degraded` | gauge | 1 if that telemetry is degraded |
| `xdna_state{state="…"}` | gauge | current pipeline state = 1, others 0 |
| `xdna_npu_clock_mhz{domain="npuclk\|hclk"}` | gauge | NPU active DPM clocks (only when the debugfs `power_state` node is readable) |

### Per-device AMD GPU metrics

All numerical `xdna_gpu_*` families carry `card` and `bdf` labels. Missing
values omit samples. `xdna_gpu_info{card,bdf,name,type}` provides identity,
with type `apu`, `discrete`, or `unknown`; type hints are conservative (see
[HOW-IT-WORKS.md](HOW-IT-WORKS.md)).

| Metric suffix (after `xdna_gpu_`) | Unit / meaning |
|---|---|
| `busy_percent` | Driver busy % |
| `vram_used_bytes`, `vram_total_bytes` | VRAM bytes |
| `gtt_used_bytes`, `gtt_total_bytes` | GTT bytes; do not sum shared host budgets |
| `edge_temperature_celsius` | Edge °C |
| `power_watts`, `power_cap_watts` | W; APU power includes CPU/SoC |
| `sclk_mhz` | Current shader-clock sample, MHz |
| `pcie_generation`, `pcie_generation_max` | Current/max generation |
| `pcie_width`, `pcie_width_max` | Current/max lane count |
| `degraded` | 1 if busy or power is unavailable |

PCIe is not meaningful on APUs; recognized APU PCIe samples are omitted.
The legacy unlabeled `xdna_igpu_*` families remain for existing dashboards and
represent the first numeric amdgpu card, without aggregating other cards.

## Grafana

Import `docs/grafana/xdna-top-dashboard.json` and point it at your Prometheus
data source.
