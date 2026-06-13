#!/usr/bin/env python3
"""Generation & contention benchmark for AMD Strix Halo (NPU / iGPU / CPU).

Measures shared-memory-bandwidth contention and perf/watt on a Strix Halo APU
whose NPU, iGPU, and CPU share one unified memory bus:

1. **Baseline** — iGPU only (interactive "main lane"), nothing else running.
2. **NPU concurrent** — iGPU + a background LLM generation job on the NPU.
3. **CPU concurrent** — iGPU + a background LLM generation job on spare CPU cores.

The workload is a plain background LLM text-generation job on each engine; it is
not tied to any particular application. Each condition's evidence is captured with
xdna-top's own primitives — a schema'd `snapshot` (PID attribution + backend
provenance + degraded flags) and a continuous `record` time-series trace — so the
published numbers trace back to a real capture rather than ad-hoc parsing.

Power is read from the package (PPT) rail (amdgpu ``power1`` = whole-SoC
CPU+iGPU+NPU). It is the correct denominator for total-board perf/watt; the NPU's
individual draw is not separable from this shared rail.

This harness imports only ``xdna_top`` and the standard library. The live
benchmark additionally needs ``httpx`` (imported lazily) and three
OpenAI-compatible generation endpoints — one for the iGPU main lane and one for
the background engine(s) — already running at the configured ports. The analysis
and evidence helpers are import-safe and unit-tested without any server.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any

from xdna_top.gauge import HardwareGauge, load_sysfs_paths
from xdna_top.record import run_record
from xdna_top.snapshot import build_snapshot, write_snapshot

# Default ports for the OpenAI-compatible generation endpoints.
DEFAULT_IGPU_PORT = 8094
DEFAULT_NPU_PORT = 13306
DEFAULT_CPU_PORT = 8095

PREFILL_PROMPT = "The quick brown fox jumps over the lazy dog. " * 350
DECODE_PROMPT = "Write a highly detailed explanation of quantum computing principles."


def _httpx():
    """Import httpx lazily so the module stays import-safe without the dependency."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised only in live runs
        raise RuntimeError(
            "the live benchmark needs httpx (pip install httpx); analysis-only "
            "helpers do not"
        ) from exc
    return httpx


# --- statistics & metrics ----------------------------------------------------


def calculate_stats(samples: list[float]) -> dict[str, Any]:
    """Mean, stddev, min, max (+ rounded samples) for a list of numeric samples."""
    if not samples:
        return {"samples": [], "mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(samples) / len(samples)
    stddev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return {
        "samples": [round(x, 3) for x in samples],
        "mean": round(mean, 3),
        "stddev": round(stddev, 3),
        "min": round(min(samples), 3),
        "max": round(max(samples), 3),
    }


def marginal_watt_efficiency(throughput_tok_s: float, marginal_power_w: float) -> float:
    """Background throughput divided by the *added* watts the engine cost.

    This is the marginal-watt efficiency metric: how much extra generation a
    condition buys per extra watt over the iGPU-only baseline. It complements
    (does not replace) the total-board perf/watt number.
    """
    if marginal_power_w and marginal_power_w > 0:
        return throughput_tok_s / marginal_power_w
    return 0.0


# --- evidence (snapshot + record) --------------------------------------------


def attribution_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Consume an xdna-top ``snapshot`` artifact and extract attribution evidence.

    This replaces the old ad-hoc ``xrt-smi examine`` text parsing: a snapshot
    already carries PID-attributed NPU contexts, backend provenance, and degraded
    flags in a schema'd form, so the benchmark reads its evidence from there.

    Returns a compact dict: whether the NPU was detected, the owning PIDs/context
    ids, the backend that sourced each signal, and whether anything was degraded.
    """
    if snapshot.get("kind") != "xdna-top.snapshot":
        raise ValueError(
            "expected an xdna-top.snapshot artifact, got kind="
            f"{snapshot.get('kind')!r}"
        )
    npu = snapshot.get("devices", {}).get("npu", {})
    contexts = npu.get("contexts", []) or []
    backends = snapshot.get("backends", {})
    degraded = snapshot.get("degraded", {})
    return {
        "npu_detected": bool(npu.get("detected")),
        "npu_name": npu.get("name"),
        "context_pids": sorted(
            {c.get("pid") for c in contexts if isinstance(c, dict) and c.get("pid") is not None}
        ),
        "context_count": len(contexts),
        "npu_signal_source": backends.get("npu", {}).get("signals", {}).get("contexts"),
        "igpu_signal_source": backends.get("igpu", {}).get("primary"),
        "degraded": bool(degraded.get("overall")),
        "telemetry": snapshot.get("telemetry", {}),
    }


def capture_condition_evidence(
    name: str,
    output_dir: Path,
    *,
    record_duration_s: float = 3.0,
    record_interval_s: float = 0.2,
    npu_device: str | None = None,
    bench_dir: str = "/tmp/xdna_top",
) -> dict[str, Any]:
    """Capture a real ``snapshot`` + ``record`` trace for one benchmark condition.

    Writes ``<name>.snapshot.json`` and ``<name>.record.jsonl`` into
    ``output_dir`` using xdna-top's shipped primitives, and returns the snapshot
    attribution. Call this while the condition's load is running.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = build_snapshot(bench_dir=bench_dir, npu_device=npu_device)
    snapshot_path = output_dir / f"{name}.snapshot.json"
    write_snapshot(snapshot, snapshot_path)

    record_path = output_dir / f"{name}.record.jsonl"
    run_record(
        duration=record_duration_s,
        interval=record_interval_s,
        out_path=record_path,
        bench_dir=bench_dir,
        npu_device=npu_device,
    )
    return {
        "snapshot_path": str(snapshot_path),
        "record_path": str(record_path),
        "attribution": attribution_from_snapshot(snapshot),
    }


# --- live measurement drivers (need httpx + running endpoints) ----------------


class PowerSampler:
    """Samples package (PPT) power in a background thread during a measurement."""

    def __init__(self, power_path: str, interval_s: float = 0.1):
        self.power_path = power_path
        self.interval_s = interval_s
        self.powers: list[float] = []
        self.running = False
        self.thread: threading.Thread | None = None

    def _loop(self) -> None:
        while self.running:
            try:
                if self.power_path and os.path.exists(self.power_path):
                    with open(self.power_path, "r") as handle:
                        self.powers.append(int(handle.read().strip()) / 1_000_000.0)
            except Exception:
                pass
            time.sleep(self.interval_s)

    def start(self) -> None:
        self.powers = []
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> float:
        self.running = False
        if self.thread:
            self.thread.join()
        return sum(self.powers) / len(self.powers) if self.powers else 0.0


class BackgroundLoad:
    """Drives continuous generation requests against a background engine endpoint."""

    def __init__(self, port: int, model: str, max_tokens: int = 50):
        self.url = f"http://localhost:{port}"
        self.model = model
        self.max_tokens = max_tokens
        self.running = False
        self.tokens_generated = 0
        self.elapsed_time = 0.0
        self.samples: list[float] = []
        self.thread: threading.Thread | None = None

    def _loop(self) -> None:
        httpx = _httpx()
        client = httpx.Client()
        start = time.time()
        while self.running:
            try:
                t0 = time.perf_counter()
                r = client.post(
                    f"{self.url}/v1/completions",
                    json={
                        "model": self.model,
                        "prompt": "Explain how to sort an array of strings.",
                        "max_tokens": self.max_tokens,
                        "temperature": 0.0,
                    },
                    timeout=120.0,
                )
                dt = time.perf_counter() - t0
                if r.status_code == 200:
                    tokens = r.json()["usage"]["completion_tokens"]
                    self.tokens_generated += tokens
                    if dt > 0:
                        self.samples.append(tokens / dt)
            except Exception as exc:  # pragma: no cover - live path
                print(f"background load error: {exc}", file=sys.stderr)
                time.sleep(0.1)
        self.elapsed_time = time.time() - start

    def start(self) -> None:
        self.running = True
        self.tokens_generated = 0
        self.samples = []
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> float:
        self.running = False
        if self.thread:
            self.thread.join()
        return self.tokens_generated / self.elapsed_time if self.elapsed_time > 0 else 0.0

    def get_stats(self) -> dict[str, Any]:
        return calculate_stats(self.samples)


def wait_for_server(port: int, timeout_s: float = 30.0) -> bool:
    """Poll an endpoint's /v1/models until responsive (live path)."""
    httpx = _httpx()
    client = httpx.Client()
    start = time.time()
    while time.time() - start < timeout_s:
        try:
            if client.get(f"http://localhost:{port}/v1/models").status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_igpu_prefill(port: int, prompt: str = PREFILL_PROMPT) -> float:
    """One iGPU prefill measurement → prompt tok/s (live path)."""
    httpx = _httpx()
    t0 = time.perf_counter()
    r = httpx.post(
        f"http://localhost:{port}/v1/completions",
        json={"prompt": prompt, "max_tokens": 1},
        timeout=30.0,
    )
    dt = time.perf_counter() - t0
    assert r.status_code == 200, f"iGPU prefill failed: {r.status_code}"
    return r.json()["usage"]["prompt_tokens"] / dt if dt > 0 else 0.0


def run_igpu_decode(port: int, max_tokens: int = 150) -> float:
    """One iGPU streaming decode measurement → decode tok/s (live path)."""
    httpx = _httpx()
    client = httpx.Client()
    first = last = None
    n = 0
    with client.stream(
        "POST",
        f"http://localhost:{port}/v1/completions",
        json={"prompt": DECODE_PROMPT, "max_tokens": max_tokens, "stream": True},
        timeout=30.0,
    ) as r:
        assert r.status_code == 200, f"iGPU decode failed: {r.status_code}"
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                json.loads(payload)
            except Exception:
                continue
            n += 1
            ts = time.perf_counter()
            if n == 1:
                first = ts
            else:
                last = ts
    if first and last and n > 1:
        return (n - 1) / (last - first)
    return 0.0


# --- results table -----------------------------------------------------------


def _val_sd(data: dict, keys: list[str]) -> tuple[float, float | None]:
    curr: Any = data
    for k in keys:
        if isinstance(curr, dict) and k in curr:
            curr = curr[k]
        else:
            return 0.0, None
    if isinstance(curr, dict):
        return curr.get("mean", 0.0), curr.get("stddev")
    if isinstance(curr, (int, float)):
        return float(curr), None
    return 0.0, None


def _fmt(val: float, sd: float | None, precision: int = 2, pct: bool = False) -> str:
    suffix = "%" if pct else ""
    if sd is not None and sd > 0.0:
        return f"{val:.{precision}f}{suffix} ± {sd:.{precision}f}{suffix}"
    return f"{val:.{precision}f}{suffix}"


def generate_markdown_table(m1_path: Path, m2_path: Path) -> str:
    """Render the results table from the two condition JSON artifacts.

    Generic framing throughout; power is labelled package (PPT) total-board; both
    the total-board perf/watt and the marginal-watt efficiency are reported.
    """
    m1 = _load_json(m1_path)
    m2 = _load_json(m2_path)

    base_prefill = _val_sd(m1, ["baseline", "prefill", "igpu_throughput_tok_s"])
    base_decode = _val_sd(m1, ["baseline", "decode", "igpu_throughput_tok_s"])
    base_pwr = _val_sd(m1, ["baseline", "decode", "avg_power_w"])

    npu_prefill = _val_sd(m1, ["concurrent_npu", "prefill", "igpu_throughput_tok_s"])
    npu_decode = _val_sd(m1, ["concurrent_npu", "decode", "igpu_throughput_tok_s"])
    npu_pwr = _val_sd(m1, ["concurrent_npu", "decode", "avg_power_w"])
    npu_loss = _val_sd(m1, ["concurrent_npu", "decode", "contention_loss_pct"])
    npu_bg = _val_sd(m1, ["concurrent_npu", "decode", "npu_throughput_tok_s"])

    cpu_prefill = _val_sd(m2, ["concurrent_cpu", "prefill", "igpu_throughput_tok_s"])
    cpu_decode = _val_sd(m2, ["concurrent_cpu", "decode", "igpu_throughput_tok_s"])
    cpu_pwr = _val_sd(m2, ["concurrent_cpu", "decode", "avg_power_w"])
    cpu_loss = _val_sd(m2, ["concurrent_cpu", "decode", "contention_loss_pct"])
    cpu_bg = _val_sd(m2, ["concurrent_cpu", "decode", "cpu_throughput_tok_s"])

    comparison = m2.get("comparison", {})
    npu_perf_watt = comparison.get("perf_watt", {}).get("npu", 0.0)
    cpu_perf_watt = comparison.get("perf_watt", {}).get("cpu", 0.0)
    npu_marg = comparison.get("marginal_decode_power_w", {}).get("npu", 0.0)
    cpu_marg = comparison.get("marginal_decode_power_w", {}).get("cpu", 0.0)

    if not npu_perf_watt and npu_bg[0] and npu_pwr[0]:
        npu_perf_watt = npu_bg[0] / npu_pwr[0]
    if not cpu_perf_watt and cpu_bg[0] and cpu_pwr[0]:
        cpu_perf_watt = cpu_bg[0] / cpu_pwr[0]
    if not npu_marg and npu_pwr[0] and base_pwr[0]:
        npu_marg = npu_pwr[0] - base_pwr[0]
    if not cpu_marg and cpu_pwr[0] and base_pwr[0]:
        cpu_marg = cpu_pwr[0] - base_pwr[0]

    npu_marg_eff = marginal_watt_efficiency(npu_bg[0], npu_marg)
    cpu_marg_eff = marginal_watt_efficiency(cpu_bg[0], cpu_marg)

    def per_watt(v: float) -> str:
        return f"{v:.3f}" if v > 0.0 else "-"

    def marg(v: float) -> str:
        return f"{v:+.2f} W" if v else "-"

    return "\n".join(
        [
            "# Strix Halo Generation & Contention Benchmark Results",
            "",
            "| Condition | iGPU Prefill (tok/s) | iGPU Decode (tok/s) | iGPU Decode Loss % | "
            "Background Throughput (tok/s) | Avg Decode Power (PPT, W) | Marginal Power (W) | "
            "tok/s per Total-Board Watt | tok/s per Marginal Watt |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            f"| **Baseline** (iGPU Only) | {_fmt(*base_prefill)} | {_fmt(*base_decode)} | 0.00% | - | "
            f"{_fmt(*base_pwr)} | - | - | - |",
            f"| **NPU (Concurrent)** | {_fmt(*npu_prefill)} | {_fmt(*npu_decode)} | "
            f"{_fmt(*npu_loss, pct=True) if npu_decode[0] else '-'} | "
            f"{_fmt(*npu_bg) if npu_bg[0] else '-'} | {_fmt(*npu_pwr)} | {marg(npu_marg)} | "
            f"{per_watt(npu_perf_watt)} | {per_watt(npu_marg_eff)} |",
            f"| **CPU (Concurrent)** | {_fmt(*cpu_prefill)} | {_fmt(*cpu_decode)} | "
            f"{_fmt(*cpu_loss, pct=True) if cpu_decode[0] else '-'} | "
            f"{_fmt(*cpu_bg) if cpu_bg[0] else '-'} | {_fmt(*cpu_pwr)} | {marg(cpu_marg)} | "
            f"{per_watt(cpu_perf_watt)} | {per_watt(cpu_marg_eff)} |",
            "",
            "*Power is package (PPT) total-board (CPU+iGPU+NPU); the NPU's isolated draw "
            "is not separable. Prefill contention is below this HTTP harness's resolution.*",
            "",
        ]
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"warning: could not read {path}: {exc}", file=sys.stderr)
        return {}


# --- live orchestration ------------------------------------------------------


def _measure_phase(igpu_port: int, power_path: str, trials: int) -> dict[str, Any]:
    """Run prefill + decode trials on the iGPU main lane, sampling package power."""
    prefill, decode, decode_pwr = [], [], []
    for _ in range(trials):
        s = PowerSampler(power_path)
        s.start()
        prefill.append(run_igpu_prefill(igpu_port))
        s.stop()
        time.sleep(0.2)
    for _ in range(trials):
        s = PowerSampler(power_path)
        s.start()
        decode.append(run_igpu_decode(igpu_port))
        decode_pwr.append(s.stop())
        time.sleep(0.2)
    return {
        "prefill": {"igpu_throughput_tok_s": calculate_stats(prefill)},
        "decode": {
            "igpu_throughput_tok_s": calculate_stats(decode),
            "avg_power_w": calculate_stats(decode_pwr),
        },
    }


def run_benchmark(args: argparse.Namespace) -> tuple[Path, Path]:
    """Run the three-phase live benchmark, capturing per-condition evidence."""
    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence"
    m1_path = output_dir / "m1_contention.json"
    m2_path = output_dir / "m2_cpu_arm.json"

    busy_path, power_path = load_sysfs_paths(args.bench_dir)
    power_path = args.power_path or power_path or "/sys/class/hwmon/hwmon5/power1_input"
    print(f"package (PPT) power path: {power_path}")

    # Phase 1: baseline (iGPU only)
    capture_condition_evidence("baseline", evidence_dir, npu_device=args.npu_device, bench_dir=args.bench_dir)
    baseline = _measure_phase(args.igpu_port, power_path, args.trials)
    base_decode = baseline["decode"]["igpu_throughput_tok_s"]["mean"]
    base_pwr = baseline["decode"]["avg_power_w"]["mean"]

    # Phase 2: NPU concurrent
    npu_decode_pwr_mean = npu_bg_mean = 0.0
    npu_block = {}
    if not args.skip_npu:
        load = BackgroundLoad(args.npu_port, args.npu_model)
        load.start()
        time.sleep(1.0)
        capture_condition_evidence("npu", evidence_dir, npu_device=args.npu_device, bench_dir=args.bench_dir)
        npu_phase = _measure_phase(args.igpu_port, power_path, args.trials)
        bg_mean = load.stop()
        bg_stats = load.get_stats()
        npu_decode_pwr_mean = npu_phase["decode"]["avg_power_w"]["mean"]
        npu_bg_mean = bg_stats["mean"]
        loss = calculate_stats(
            [(base_decode - x) / base_decode * 100.0 for x in npu_phase["decode"]["igpu_throughput_tok_s"]["samples"]]
            if base_decode else []
        )
        npu_phase["decode"]["npu_throughput_tok_s"] = bg_stats
        npu_phase["decode"]["contention_loss_pct"] = loss
        npu_block = npu_phase
    m1 = {"baseline": baseline, "concurrent_npu": npu_block}
    m1_path.write_text(json.dumps(m1, indent=2), encoding="utf-8")

    # Phase 3: CPU concurrent
    if not args.skip_cpu:
        load = BackgroundLoad(args.cpu_port, args.cpu_model)
        load.start()
        time.sleep(1.0)
        capture_condition_evidence("cpu", evidence_dir, npu_device=args.npu_device, bench_dir=args.bench_dir)
        cpu_phase = _measure_phase(args.igpu_port, power_path, args.trials)
        cpu_bg_mean = load.stop()
        cpu_bg_stats = load.get_stats()
        cpu_decode_pwr_mean = cpu_phase["decode"]["avg_power_w"]["mean"]
        loss = calculate_stats(
            [(base_decode - x) / base_decode * 100.0 for x in cpu_phase["decode"]["igpu_throughput_tok_s"]["samples"]]
            if base_decode else []
        )
        cpu_phase["decode"]["cpu_throughput_tok_s"] = cpu_bg_stats
        cpu_phase["decode"]["contention_loss_pct"] = loss
        npu_marg = npu_decode_pwr_mean - base_pwr if base_pwr else 0.0
        cpu_marg = cpu_decode_pwr_mean - base_pwr if base_pwr else 0.0
        m2 = {
            "concurrent_cpu": cpu_phase,
            "comparison": {
                "marginal_decode_power_w": {"npu": round(npu_marg, 3), "cpu": round(cpu_marg, 3)},
                "perf_watt": {
                    "npu": round(npu_bg_mean / npu_decode_pwr_mean, 3) if npu_decode_pwr_mean else 0.0,
                    "cpu": round(cpu_bg_mean / cpu_decode_pwr_mean, 3) if cpu_decode_pwr_mean else 0.0,
                },
                "marginal_watt_efficiency": {
                    "npu": round(marginal_watt_efficiency(npu_bg_mean, npu_marg), 3),
                    "cpu": round(marginal_watt_efficiency(cpu_bg_mean, cpu_marg), 3),
                },
            },
        }
        m2_path.write_text(json.dumps(m2, indent=2), encoding="utf-8")

    return m1_path, m2_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--igpu-port", type=int, default=DEFAULT_IGPU_PORT)
    parser.add_argument("--npu-port", type=int, default=DEFAULT_NPU_PORT)
    parser.add_argument("--cpu-port", type=int, default=DEFAULT_CPU_PORT)
    parser.add_argument("--npu-model", default="npu-generation-model", help="Model name served at --npu-port")
    parser.add_argument("--cpu-model", default="cpu-generation-model", help="Model name served at --cpu-port")
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--bench-dir", default="/tmp/xdna_top")
    parser.add_argument("--power-path", default=None, help="Override package power sysfs node")
    parser.add_argument("--npu-device", default=None, help="NPU device BDF override")
    parser.add_argument("--skip-npu", action="store_true")
    parser.add_argument("--skip-cpu", action="store_true")
    parser.add_argument(
        "--generate-table-only",
        action="store_true",
        help="Only (re)generate the Markdown table from existing condition JSON.",
    )
    args = parser.parse_args()

    out = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent
    m1_path = out / "m1_contention.json"
    m2_path = out / "m2_cpu_arm.json"

    if args.generate_table_only:
        print(generate_markdown_table(m1_path, m2_path))
        return 0

    m1_path, m2_path = run_benchmark(args)
    print(generate_markdown_table(m1_path, m2_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
