"""Telemetry recorder for xdna-top evidence workflows.

Streams typed JSONL telemetry events over a time window so that NPU + iGPU
activity during a workload can be captured as reproducible evidence. Each line
is a self-describing event with a ``type`` and ``schema_version`` so later
tooling (such as ``xdna-top assert``) can consume the stream by event type.
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from xdna_top.gauge import GaugeReading, HardwareGauge
from xdna_top.snapshot import host_facts

RECORD_SCHEMA_VERSION = "1.0"
RECORD_KIND = "xdna-top.record"


def build_telemetry_event(
    reading: GaugeReading, contexts: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build one typed telemetry event from a fused reading and NPU contexts."""
    return {
        "type": "telemetry",
        "schema_version": RECORD_SCHEMA_VERSION,
        "ts": reading.ts,
        "reading": reading.to_dict(),
        "contexts": contexts,
    }


def build_mark_event(label: str, ts: float | None = None) -> dict[str, Any]:
    """Build one typed mark event for annotating a record stream."""
    return {
        "type": "mark",
        "schema_version": RECORD_SCHEMA_VERSION,
        "ts": ts if ts is not None else time.time(),
        "label": label,
    }


def append_event(event: dict[str, Any], out_path: str | Path | None) -> None:
    """Append one JSONL event to a record stream, or stdout when no path given."""
    line = _dump(event)
    if out_path is None:
        sys.stdout.write(line)
        sys.stdout.flush()
        return
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def mark_main(args: Any) -> int:
    append_event(build_mark_event(args.label), args.out)
    return 0


def make_hardware_sampler(gauge: HardwareGauge) -> Callable[[], dict[str, Any]]:
    """Return a callable that produces one telemetry event from real hardware."""

    def sample() -> dict[str, Any]:
        reading, contexts = gauge.sample_direct()
        contexts = [dict(ctx, source="xrt_smi") for ctx in contexts]
        return build_telemetry_event(reading, contexts)

    return sample


def record_stream(
    *,
    duration: float,
    interval: float,
    sampler: Callable[[], dict[str, Any]],
    write_line: Callable[[dict[str, Any]], None],
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Sample telemetry until ``duration`` elapses, writing one event per sample.

    Returns the number of telemetry events written. A ``duration`` of 0 yields a
    single sample. ``KeyboardInterrupt`` stops sampling without re-raising, so
    callers can still write a trailing summary event for a partial recording.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if duration < 0:
        raise ValueError("duration must be >= 0")

    start = monotonic()
    count = 0
    try:
        while True:
            write_line(sampler())
            count += 1
            if monotonic() - start >= duration:
                break
            delay = (start + count * interval) - monotonic()
            if delay > 0:
                sleep(delay)
    except KeyboardInterrupt:
        pass
    return count


def run_record(
    *,
    duration: float,
    interval: float,
    out_path: str | Path | None,
    bench_dir: str = "/tmp/xdna_top",
    npu_device: str | None = None,
    gpu_idle_busy_pct: int = 10,
    gpu_prefill_power_w: float = 35.0,
    gauge_hysteresis_samples: int = 3,
) -> int:
    """Record telemetry as JSONL, returning a process exit code."""
    if interval <= 0:
        print("record failed: --interval must be > 0", file=sys.stderr)
        return 2
    if duration < 0:
        print("record failed: --duration must be >= 0", file=sys.stderr)
        return 2

    gauge = HardwareGauge(
        gpu_idle_busy_pct=gpu_idle_busy_pct,
        gpu_prefill_power_w=gpu_prefill_power_w,
        gauge_hysteresis_samples=gauge_hysteresis_samples,
        bench_dir=bench_dir,
        pessimistic_fallback=False,
        npu_device=npu_device,
    )
    sampler = make_hardware_sampler(gauge)

    started_at = _utc_now_iso()
    with _open_writer(out_path) as write_line:
        write_line(
            _meta_event(duration=duration, interval=interval, started_at=started_at)
        )
        count = record_stream(
            duration=duration,
            interval=interval,
            sampler=sampler,
            write_line=write_line,
        )
        write_line(_summary_event(started_at=started_at, samples=count))
    return 0


def record_main(args: Any) -> int:
    return run_record(
        duration=args.duration,
        interval=args.interval,
        out_path=args.out,
        bench_dir=args.bench_dir,
        npu_device=args.npu_device,
        gpu_idle_busy_pct=args.idle_busy_pct,
        gpu_prefill_power_w=args.prefill_power_w,
        gauge_hysteresis_samples=args.hysteresis_samples,
    )


def _meta_event(*, duration: float, interval: float, started_at: str) -> dict[str, Any]:
    return {
        "type": "meta",
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "started_at": started_at,
        "params": {"duration_s": duration, "interval_s": interval},
        "host": host_facts(),
    }


def _summary_event(*, started_at: str, samples: int) -> dict[str, Any]:
    return {
        "type": "summary",
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": RECORD_KIND,
        "started_at": started_at,
        "ended_at": _utc_now_iso(),
        "samples": samples,
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextmanager
def _open_writer(
    out_path: str | Path | None,
) -> Iterator[Callable[[dict[str, Any]], None]]:
    """Yield a ``write_line`` that appends one JSONL event, flushing each line.

    Flushing per line keeps the stream tail-able and leaves a valid partial
    artifact if a recording is interrupted.
    """
    if out_path is None:

        def write_stdout(event: dict[str, Any]) -> None:
            sys.stdout.write(_dump(event))
            sys.stdout.flush()

        yield write_stdout
        return

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:

        def write_file(event: dict[str, Any]) -> None:
            handle.write(_dump(event))
            handle.flush()

        yield write_file


def _dump(event: dict[str, Any]) -> str:
    return json.dumps(event) + "\n"
