"""Tests for the ported contention benchmark harness (bench/contention_benchmark.py)."""

import json

import pytest

import contention_benchmark as cb


def test_calculate_stats():
    stats = cb.calculate_stats([10.0, 12.0, 14.0])
    assert stats["mean"] == 12.0
    assert stats["stddev"] == 2.0
    assert stats["min"] == 10.0
    assert stats["max"] == 14.0
    assert cb.calculate_stats([10.0])["stddev"] == 0.0
    assert cb.calculate_stats([])["mean"] == 0.0


def test_marginal_watt_efficiency():
    # 13.67 tok/s for +10.29 W ~= 1.33 tok/s per marginal watt (the new metric).
    assert round(cb.marginal_watt_efficiency(13.67, 10.29), 2) == 1.33
    assert round(cb.marginal_watt_efficiency(4.48, 15.43), 2) == 0.29
    assert cb.marginal_watt_efficiency(10.0, 0.0) == 0.0  # no added watts -> 0


def _snapshot_artifact() -> dict:
    """A minimal, schema-shaped xdna-top.snapshot artifact for one condition."""
    return {
        "schema_version": "1.0",
        "kind": "xdna-top.snapshot",
        "devices": {
            "npu": {
                "detected": True,
                "name": "RyzenAI-npu5",
                "contexts": [
                    {"pid": 4242, "ctx_id": 0, "source": "xrt_smi"},
                    {"pid": 4242, "ctx_id": 1, "source": "xrt_smi"},
                ],
            }
        },
        "backends": {
            "npu": {"primary": "xrt_smi", "signals": {"contexts": "xrt_smi"}},
            "igpu": {"primary": "sysfs", "signals": {"busy_pct": "sysfs"}},
        },
        "telemetry": {"npu_active": True, "gpu_busy_pct": 12},
        "degraded": {"overall": False},
    }


def test_attribution_from_snapshot_consumes_snapshot_artifact():
    """The benchmark reads its attribution evidence from a snapshot artifact."""
    attribution = cb.attribution_from_snapshot(_snapshot_artifact())
    assert attribution["npu_detected"] is True
    assert attribution["npu_name"] == "RyzenAI-npu5"
    assert attribution["context_pids"] == [4242]  # de-duplicated PID attribution
    assert attribution["context_count"] == 2
    assert attribution["npu_signal_source"] == "xrt_smi"
    assert attribution["igpu_signal_source"] == "sysfs"
    assert attribution["degraded"] is False


def test_attribution_from_snapshot_rejects_non_snapshot():
    record_event = {"type": "telemetry", "kind": "xdna-top.record"}
    with pytest.raises(ValueError):
        cb.attribution_from_snapshot(record_event)


def test_attribution_consumes_real_captured_snapshot_file():
    """Round-trip: the committed real snapshot artifact is consumable as evidence."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    path = root / "docs" / "experiments" / "artifacts" / "evidence" / "npu.snapshot.json"
    if not path.exists():
        pytest.skip("committed snapshot artifact not present")
    attribution = cb.attribution_from_snapshot(json.loads(path.read_text(encoding="utf-8")))
    assert attribution["npu_detected"] is True
    assert attribution["igpu_signal_source"] in ("sysfs", None)


def test_generate_markdown_table_generic_with_marginal_watt(tmp_path):
    m1 = {
        "baseline": {
            "prefill": {"igpu_throughput_tok_s": {"mean": 1000.0, "stddev": 0.0}},
            "decode": {
                "igpu_throughput_tok_s": {"mean": 50.0, "stddev": 0.0},
                "avg_power_w": {"mean": 20.0, "stddev": 0.0},
            },
        },
        "concurrent_npu": {
            "prefill": {"igpu_throughput_tok_s": {"mean": 950.0, "stddev": 0.0}},
            "decode": {
                "igpu_throughput_tok_s": {"mean": 47.5, "stddev": 3.536},
                "npu_throughput_tok_s": {"mean": 15.0, "stddev": 1.414},
                "avg_power_w": {"mean": 35.0, "stddev": 2.828},
                "contention_loss_pct": {"mean": 5.0, "stddev": 7.071},
            },
        },
    }
    m2 = {
        "concurrent_cpu": {
            "prefill": {"igpu_throughput_tok_s": {"mean": 990.0, "stddev": 0.0}},
            "decode": {
                "igpu_throughput_tok_s": {"mean": 49.5, "stddev": 0.707},
                "cpu_throughput_tok_s": {"mean": 5.0, "stddev": 1.414},
                "avg_power_w": {"mean": 45.0, "stddev": 1.414},
                "contention_loss_pct": {"mean": 1.0, "stddev": 1.414},
            },
        },
        "comparison": {
            "marginal_decode_power_w": {"npu": 15.0, "cpu": 25.0},
            "perf_watt": {"npu": 0.429, "cpu": 0.111},
        },
    }
    m1_path = tmp_path / "m1.json"
    m2_path = tmp_path / "m2.json"
    m1_path.write_text(json.dumps(m1), encoding="utf-8")
    m2_path.write_text(json.dumps(m2), encoding="utf-8")

    md = cb.generate_markdown_table(m1_path, m2_path)
    assert "# Strix Halo Generation & Contention Benchmark Results" in md
    assert "REM" not in md  # generic framing only
    assert "Avg Decode Power (PPT, W)" in md
    assert "tok/s per Marginal Watt" in md
    # marginal-watt: npu 15/15 = 1.000 ; cpu 5/25 = 0.200
    assert "1.000" in md
    assert "0.200" in md


def test_generate_markdown_table_partial_fallback(tmp_path):
    md = cb.generate_markdown_table(tmp_path / "none1.json", tmp_path / "none2.json")
    assert "**Baseline** (iGPU Only)" in md
    assert "**NPU (Concurrent)**" in md
