"""Tests for JSONL telemetry recording."""

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from xdna_top.gauge import GaugeReading, GpuState
from xdna_top.record import (
    RECORD_KIND,
    RECORD_SCHEMA_VERSION,
    build_mark_event,
    build_telemetry_event,
    mark_main,
    record_stream,
    run_record,
)


class FakeClock:
    """Deterministic monotonic clock; ``sleep`` advances it without waiting."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _reading() -> GaugeReading:
    return GaugeReading(
        gpu_busy_pct=42,
        gpu_power_w=12.5,
        npu_active=True,
        state=GpuState.ACTIVE,
        igpu_degraded=False,
        npu_degraded=False,
        ts=123.456,
    )


def test_build_telemetry_event_shape():
    contexts = [
        {"pid": 1234, "ctx_id": 1, "submissions": 10, "completions": 9, "status": "Active", "source": "xrt_smi"}
    ]
    event = build_telemetry_event(_reading(), contexts)

    assert event["type"] == "telemetry"
    assert event["schema_version"] == RECORD_SCHEMA_VERSION
    assert event["ts"] == 123.456
    assert event["reading"]["gpu_busy_pct"] == 42
    assert event["contexts"][0]["source"] == "xrt_smi"


def test_record_stream_sample_count_matches_window():
    clock = FakeClock()
    written = []

    count = record_stream(
        duration=2.0,
        interval=0.5,
        sampler=lambda: {"type": "telemetry"},
        write_line=written.append,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    # Samples land at t = 0.0, 0.5, 1.0, 1.5, 2.0.
    assert count == 5
    assert len(written) == 5


def test_record_stream_zero_duration_yields_single_sample():
    clock = FakeClock()
    written = []

    count = record_stream(
        duration=0.0,
        interval=0.2,
        sampler=lambda: {"type": "telemetry"},
        write_line=written.append,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert count == 1
    assert len(written) == 1


def test_record_stream_rejects_nonpositive_interval():
    with pytest.raises(ValueError):
        record_stream(
            duration=1.0,
            interval=0.0,
            sampler=lambda: {},
            write_line=lambda _: None,
        )


def test_record_stream_keyboard_interrupt_returns_partial_count():
    written = []

    def sampler():
        if len(written) >= 2:
            raise KeyboardInterrupt
        return {"type": "telemetry"}

    clock = FakeClock()
    count = record_stream(
        duration=10.0,
        interval=0.1,
        sampler=sampler,
        write_line=written.append,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert count == 2
    assert len(written) == 2


@patch("xdna_top.record.run_xrt_smi")
@patch("xdna_top.record.HardwareGauge")
def test_run_record_writes_typed_jsonl(mock_gauge_class, mock_run_xrt, tmp_path):
    mock_gauge_class.return_value.read_direct.return_value = _reading()
    mock_run_xrt.return_value = "\n".join(
        [
            "| 1234 | 1 | 10 |",
            "| n/a | Active | 9 |",
        ]
    )

    out = tmp_path / "telemetry.jsonl"
    rc = run_record(duration=0.0, interval=0.2, out_path=out)

    assert rc == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]

    # meta header, one telemetry sample, summary footer.
    assert [e["type"] for e in events] == ["meta", "telemetry", "summary"]

    meta = events[0]
    assert meta["kind"] == RECORD_KIND
    assert meta["params"] == {"duration_s": 0.0, "interval_s": 0.2}
    assert "host" in meta

    telemetry = events[1]
    assert telemetry["reading"]["gpu_busy_pct"] == 42
    assert telemetry["contexts"][0]["pid"] == 1234
    assert telemetry["contexts"][0]["source"] == "xrt_smi"

    assert events[2]["samples"] == 1


@patch("xdna_top.record.run_xrt_smi", return_value=None)
@patch("xdna_top.record.HardwareGauge")
def test_run_record_degrades_without_xrt(mock_gauge_class, _mock_run_xrt, tmp_path):
    degraded = GaugeReading(
        gpu_busy_pct=None,
        gpu_power_w=None,
        npu_active=False,
        state=GpuState.UNKNOWN,
        igpu_degraded=True,
        npu_degraded=True,
        ts=1.0,
    )
    mock_gauge_class.return_value.read_direct.return_value = degraded

    out = tmp_path / "telemetry.jsonl"
    rc = run_record(duration=0.0, interval=0.2, out_path=out)

    assert rc == 0
    events = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    telemetry = next(e for e in events if e["type"] == "telemetry")
    assert telemetry["reading"]["npu_degraded"] is True
    assert telemetry["contexts"] == []


def test_run_record_rejects_nonpositive_interval(tmp_path):
    rc = run_record(duration=1.0, interval=0.0, out_path=tmp_path / "x.jsonl")
    assert rc == 2
    assert not (tmp_path / "x.jsonl").exists()


def test_build_mark_event_shape():
    event = build_mark_event("trial-1-start", ts=42.0)
    assert event == {
        "type": "mark",
        "schema_version": RECORD_SCHEMA_VERSION,
        "ts": 42.0,
        "label": "trial-1-start",
    }


def test_mark_main_appends_without_truncating(tmp_path):
    out = tmp_path / "trial.jsonl"
    out.write_text(
        json.dumps({"type": "telemetry", "ts": 1.0}) + "\n", encoding="utf-8"
    )

    rc = mark_main(Namespace(label="compaction-start", out=str(out)))
    assert rc == 0

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [e["type"] for e in lines] == ["telemetry", "mark"]
    assert lines[1]["label"] == "compaction-start"


def test_mark_main_creates_file_when_absent(tmp_path):
    out = tmp_path / "nested" / "trial.jsonl"
    rc = mark_main(Namespace(label="start", out=str(out)))
    assert rc == 0
    event = json.loads(out.read_text().strip())
    assert event["type"] == "mark"
    assert event["label"] == "start"
