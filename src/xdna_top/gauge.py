"""Telemetry gauge for measuring iGPU and NPU activity.

Provides both a standalone scraping daemon process and an in-process library API.
"""

import os
import sys
import json
import time
import logging
import argparse
import subprocess
from enum import Enum
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger("xdna_top.gauge")


class GpuState(str, Enum):
    IDLE = "IDLE"
    ACTIVE = "ACTIVE"
    PREFILL_BURST = "PREFILL_BURST"
    # Honest "no classification possible" sentinel: emitted when the iGPU is
    # degraded and pessimistic fallback is off, so we have no real inputs to
    # classify from. Serializes to JSON null (see to_dict); displays as UNKNOWN.
    UNKNOWN = "UNKNOWN"


class GaugeReading:
    """Represents a single hardware telemetry reading."""

    def __init__(
        self,
        gpu_busy_pct: int | None,
        gpu_power_w: float | None,
        npu_active: bool,
        state: GpuState,
        igpu_degraded: bool = False,
        npu_degraded: bool = False,
        ts: float | None = None,
    ) -> None:
        self.gpu_busy_pct = gpu_busy_pct
        self.gpu_power_w = gpu_power_w
        self.npu_active = npu_active
        self.state = state
        self.igpu_degraded = igpu_degraded
        self.npu_degraded = npu_degraded
        self.ts = ts or time.time()

    def to_dict(self) -> dict:
        return {
            "gpu_busy_pct": self.gpu_busy_pct,
            "gpu_power_w": round(self.gpu_power_w, 3) if self.gpu_power_w is not None else None,
            "npu_active": self.npu_active,
            "state": self.state.value if self.state != GpuState.UNKNOWN else None,
            "igpu_degraded": self.igpu_degraded,
            "npu_degraded": self.npu_degraded,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GaugeReading":
        return cls(
            gpu_busy_pct=d["gpu_busy_pct"],
            gpu_power_w=d["gpu_power_w"],
            npu_active=d["npu_active"],
            state=GpuState(d["state"]) if d["state"] is not None else GpuState.UNKNOWN,
            igpu_degraded=d.get("igpu_degraded", False),
            npu_degraded=d.get("npu_degraded", False),
            ts=d["ts"],
        )


def classify_state(
    gpu_busy_pct: int,
    gpu_power_w: float,
    gpu_idle_busy_pct: int = 10,
    gpu_prefill_power_w: float = 35.0,
) -> GpuState:
    """Classifies raw GPU metrics into GpuState using configuration thresholds."""
    if gpu_busy_pct <= gpu_idle_busy_pct:
        return GpuState.IDLE
    elif gpu_power_w >= gpu_prefill_power_w:
        return GpuState.PREFILL_BURST
    else:
        return GpuState.ACTIVE


def get_stable_state(raw_states: list[GpuState]) -> GpuState:
    """Calculates the stable state using a majority vote to prevent flapping."""
    if not raw_states:
        return GpuState.ACTIVE

    counts = {}
    for s in raw_states:
        counts[s] = counts.get(s, 0) + 1

    # Priority mapping for tie breaking (higher activity wins tie)
    priority = {
        GpuState.PREFILL_BURST: 3,
        GpuState.ACTIVE: 2,
        GpuState.IDLE: 1,
    }

    max_count = max(counts.values())
    candidates = [s for s, c in counts.items() if c == max_count]
    if len(candidates) == 1:
        return candidates[0]

    return max(candidates, key=lambda s: priority.get(s, 0))


def load_sysfs_paths(bench_dir: str) -> tuple[str | None, str | None]:
    """Loads discovered sysfs paths from bench_dir/e0_sysfs.json or discover_sysfs fallback."""
    sysfs_json = Path(bench_dir) / "e0_sysfs.json"
    if sysfs_json.exists():
        try:
            d = json.loads(sysfs_json.read_text(encoding="utf-8"))
            return d.get("gpu_busy_path"), d.get("gpu_power_path")
        except Exception:
            pass

    try:
        from xdna_top.discover_sysfs import discover_sysfs
        res = discover_sysfs(Path(bench_dir))
        return res.get("gpu_busy_path"), res.get("gpu_power_path")
    except Exception:
        return None, None


def read_igpu(busy_path: str | None, power_path: str | None) -> tuple[int | None, float | None, bool]:
    """Reads raw iGPU busy % and power (W). Returns (busy_pct, power_w, degraded)."""
    busy_pct = None
    power_w = None
    degraded = False

    if not busy_path or not os.path.exists(busy_path):
        degraded = True
    else:
        try:
            with open(busy_path, "r") as f:
                busy_pct = int(f.read().strip())
        except Exception:
            degraded = True

    if not power_path or not os.path.exists(power_path):
        degraded = True
    else:
        try:
            with open(power_path, "r") as f:
                # power1_input is typically in micro-watts
                power_w = int(f.read().strip()) / 1_000_000.0
        except Exception:
            degraded = True

    return busy_pct, power_w, degraded


@lru_cache(maxsize=1)
def discover_npu_device() -> str | None:
    """Probes xrt-smi to discover the first available NPU device BDF."""
    try:
        res = subprocess.run(
            ["xrt-smi", "examine"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if res.returncode == 0:
            lines = res.stdout.splitlines()
            for line in lines:
                if "[" in line and "]" in line and "|" in line:
                    parts = line.split("|")
                    if len(parts) >= 2:
                        bdf_part = parts[1].strip()
                        if bdf_part.startswith("[") and bdf_part.endswith("]"):
                            return bdf_part[1:-1]
    except Exception:
        pass
    return None


def run_xrt_smi(device: str | None = None) -> str | None:
    """Executes xrt-smi to query NPU partition contexts."""
    if not device:
        device = discover_npu_device()
        if not device:
            device_args = []
        else:
            device_args = ["--device", device]
    else:
        device_args = ["--device", device]

    try:
        res = subprocess.run(
            [
                "xrt-smi",
                "examine",
            ] + device_args + [
                "--report",
                "aie-partitions",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if res.returncode == 0:
            return res.stdout
    except Exception:
        pass
    return None


def parse_xrt_smi(output: str) -> list[dict]:
    """Parses contexts, submissions, completions from xrt-smi partitions report."""
    lines = [line.strip() for line in output.splitlines() if "|" in line]
    
    contexts = []
    for i in range(len(lines) - 1):
        parts0 = [p.strip() for p in lines[i].split("|")]
        if len(parts0) >= 4 and parts0[1].isdigit() and parts0[2].isdigit():
            try:
                pid = int(parts0[1])
                ctx_id = int(parts0[2])
                submissions = int(parts0[3])
                
                parts1 = [p.strip() for p in lines[i+1].split("|")]
                if len(parts1) >= 4:
                    status = parts1[2]
                    completions = int(parts1[3])
                    contexts.append(
                        {
                            "pid": pid,
                            "ctx_id": ctx_id,
                            "submissions": submissions,
                            "status": status,
                            "completions": completions,
                        }
                    )
            except (ValueError, IndexError):
                pass
    return contexts


class HardwareGauge:
    """API for reading telemetries.

    First attempts to read from gauge_latest.json written by daemon.
    Falls back to direct hardware read if daemon not active.
    """

    def __init__(
        self,
        gpu_idle_busy_pct: int = 10,
        gpu_prefill_power_w: float = 35.0,
        gauge_hysteresis_samples: int = 3,
        bench_dir: str = "/tmp/xdna_top",
        pessimistic_fallback: bool = False,
        npu_device: str | None = None,
    ) -> None:
        self.gpu_idle_busy_pct = gpu_idle_busy_pct
        self.gpu_prefill_power_w = gpu_prefill_power_w
        self.gauge_hysteresis_samples = gauge_hysteresis_samples
        self.bench_dir = bench_dir
        self.pessimistic_fallback = pessimistic_fallback
        self.npu_device = npu_device

        self.busy_path, self.power_path = load_sysfs_paths(self.bench_dir)
        self.prev_submissions = {}

    def read_direct(self) -> GaugeReading:
        """Performs a direct synchronous scrape of the hardware."""
        gpu_busy, gpu_power, igpu_degraded = read_igpu(self.busy_path, self.power_path)

        display_busy = gpu_busy
        display_power = gpu_power
        if igpu_degraded and self.pessimistic_fallback:
            display_busy = 100
            display_power = 45.0

        if igpu_degraded and not self.pessimistic_fallback:
            # Honest mode: we have no real iGPU inputs, so we refuse to
            # classify. State is UNKNOWN (serializes to JSON null).
            raw_state = GpuState.UNKNOWN
        else:
            class_busy = gpu_busy if gpu_busy is not None else 100
            class_power = gpu_power if gpu_power is not None else 45.0
            raw_state = classify_state(
                class_busy,
                class_power,
                gpu_idle_busy_pct=self.gpu_idle_busy_pct,
                gpu_prefill_power_w=self.gpu_prefill_power_w,
            )

        npu_active = False
        npu_degraded = False
        npu_out = run_xrt_smi(device=self.npu_device)
        if npu_out is None:
            npu_degraded = True
        else:
            contexts = parse_xrt_smi(npu_out)
            for ctx in contexts:
                key = (ctx["pid"], ctx["ctx_id"])
                subs = ctx["submissions"]
                comps = ctx["completions"]
                if subs > comps:
                    npu_active = True
                elif (
                    key in self.prev_submissions
                    and subs > self.prev_submissions[key]
                ):
                    npu_active = True
                self.prev_submissions[key] = subs

        return GaugeReading(
            gpu_busy_pct=display_busy,
            gpu_power_w=display_power,
            npu_active=npu_active if not npu_degraded else False,
            state=raw_state,
            igpu_degraded=igpu_degraded,
            npu_degraded=npu_degraded,
        )

    def read(self) -> GaugeReading:
        """Reads the latest state from the daemon's cache with fallback to direct read."""
        latest_file = Path(self.bench_dir) / "gauge_latest.json"
        if latest_file.exists():
            try:
                # Check file age (max 1.0s to avoid stale cache)
                mtime = latest_file.stat().st_mtime
                if time.time() - mtime < 1.0:
                    d = json.loads(latest_file.read_text(encoding="utf-8"))
                    return GaugeReading.from_dict(d)
            except Exception:
                pass

        # Daemon inactive or stale; read directly
        return self.read_direct()


def run_daemon(
    gpu_idle_busy_pct: int = 10,
    gpu_prefill_power_w: float = 35.0,
    gauge_hysteresis_samples: int = 3,
    bench_dir: str = "/tmp/xdna_top",
    pessimistic_fallback: bool = False,
    npu_device: str | None = None,
) -> None:
    """Daemon process loop that samples hardware at >= 5 Hz and caches output."""
    b_dir = Path(bench_dir)
    b_dir.mkdir(parents=True, exist_ok=True)

    latest_file = b_dir / "gauge_latest.json"
    history_file = b_dir / "gauge_history.jsonl"

    logger.info("Starting xdna-top Telemetry Daemon...")
    busy_path, power_path = load_sysfs_paths(bench_dir)
    logger.info(f"iGPU paths: busy={busy_path}, power={power_path}")

    prev_submissions = {}
    history_states = []

    # Sampling frequency >= 5 Hz (200 ms interval)
    interval = 0.20

    while True:
        t0 = time.perf_counter()

        # 1. Scrape iGPU
        gpu_busy, gpu_power, igpu_degraded = read_igpu(busy_path, power_path)

        display_busy = gpu_busy
        display_power = gpu_power
        if igpu_degraded and pessimistic_fallback:
            display_busy = 100
            display_power = 45.0

        honest_degraded = igpu_degraded and not pessimistic_fallback
        if honest_degraded:
            # Honest mode: no real iGPU inputs -> refuse to classify.
            raw_state = GpuState.UNKNOWN
        else:
            class_busy = gpu_busy if gpu_busy is not None else 100
            class_power = gpu_power if gpu_power is not None else 45.0
            raw_state = classify_state(
                class_busy,
                class_power,
                gpu_idle_busy_pct=gpu_idle_busy_pct,
                gpu_prefill_power_w=gpu_prefill_power_w,
            )

        # 2. Scrape NPU
        npu_active = False
        npu_degraded = False
        npu_out = run_xrt_smi(device=npu_device)
        if npu_out is None:
            npu_degraded = True
        else:
            contexts = parse_xrt_smi(npu_out)
            for ctx in contexts:
                key = (ctx["pid"], ctx["ctx_id"])
                subs = ctx["submissions"]
                comps = ctx["completions"]
                if subs > comps:
                    npu_active = True
                elif (
                    key in prev_submissions
                    and subs > prev_submissions[key]
                ):
                    npu_active = True
                prev_submissions[key] = subs

        # 3. Apply hysteresis majority vote (only over real classifications;
        # an UNKNOWN reading is reported honestly and never voted into a state)
        if honest_degraded:
            stable_state = GpuState.UNKNOWN
        else:
            history_states.append(raw_state)
            if len(history_states) > gauge_hysteresis_samples:
                history_states.pop(0)
            stable_state = get_stable_state(history_states)

        reading = GaugeReading(
            gpu_busy_pct=display_busy,
            gpu_power_w=display_power,
            npu_active=npu_active if not npu_degraded else False,
            state=stable_state,
            igpu_degraded=igpu_degraded,
            npu_degraded=npu_degraded,
        )
        r_dict = reading.to_dict()

        # Measure daemon's own overhead
        t_elapsed = time.perf_counter() - t0
        r_dict["daemon_overhead_s"] = round(t_elapsed, 4)

        # 4. Write cache and history atomically
        temp_latest = latest_file.with_suffix(".tmp")
        try:
            temp_latest.write_text(
                json.dumps(r_dict), encoding="utf-8"
            )
            temp_latest.replace(latest_file)

            with open(history_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(r_dict) + "\n")
        except Exception as e:
            logger.error(f"Failed to write telemetry files: {e}")

        # Sleep to maintain >= 5 Hz loop
        elapsed = time.perf_counter() - t0
        sleep_time = max(0.005, interval - elapsed)
        time.sleep(sleep_time)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    ap = argparse.ArgumentParser(description="xdna-top Telemetry Gauge Daemon")
    ap.add_argument("--idle-busy-pct", type=int, default=10, help="GPU idle/busy threshold percent")
    ap.add_argument("--prefill-power-w", type=float, default=35.0, help="GPU prefill power threshold (W)")
    ap.add_argument("--hysteresis-samples", type=int, default=3, help="Hysteresis majority vote window size")
    ap.add_argument("--bench-dir", type=str, default="/tmp/xdna_top", help="Directory for latest gauge readings")
    ap.add_argument("--pessimistic-fallback", action="store_true", help="Pessimistic fallback defaults for degraded state")
    ap.add_argument("--npu-device", type=str, default=None, help="NPU device BDF")
    args = ap.parse_args()

    try:
        run_daemon(
            gpu_idle_busy_pct=args.idle_busy_pct,
            gpu_prefill_power_w=args.prefill_power_w,
            gauge_hysteresis_samples=args.hysteresis_samples,
            bench_dir=args.bench_dir,
            pessimistic_fallback=args.pessimistic_fallback,
            npu_device=args.npu_device,
        )
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
    except Exception as e:
        logger.error(f"Daemon crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
