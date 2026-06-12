"""Tests for rendering snapshot environment reports."""

import json

from xdna_top.env_report import env_report_main, load_snapshot, render_markdown


def sample_snapshot(degraded: bool = False) -> dict:
    return {
        "schema_version": "1.0",
        "kind": "xdna-top.snapshot",
        "captured_at": "2026-06-12T00:00:00Z",
        "host": {
            "hostname": "workstation",
            "kernel": {"release": "6.17.0-test", "version": "#1 test"},
            "os": {"id": "linuxmint", "version_id": "22.3", "pretty_name": "Linux Mint 22.3"},
            "python": {"version": "3.12.3"},
            "xdna_top": {"version": "0.1.0"},
        },
        "commands": {
            "xrt_smi": {
                "path": "/usr/bin/xrt-smi",
                "available": not degraded,
                "version_output": "Version : 2.21.75",
                "examine_returncode": 0 if not degraded else None,
                "aie_partitions_returncode": 0 if not degraded else None,
            }
        },
        "backends": {
            "npu": {
                "primary": "xrt_smi" if not degraded else None,
                "fallbacks_used": [],
                "signals": {
                    "device": "xrt_smi" if not degraded else None,
                    "sensors": None,
                    "contexts": "xrt_smi" if not degraded else None,
                },
            },
            "igpu": {
                "primary": "sysfs",
                "signals": {"busy_pct": "sysfs", "power_w": "sysfs"},
            },
        },
        "devices": {
            "accel": {"entries": [{"path": "/dev/accel/accel0", "exists": not degraded}]},
            "npu": {
                "detected": not degraded,
                "bdf": "0000:c6:00.1" if not degraded else None,
                "name": "RyzenAI-npu5" if not degraded else None,
                "driver": {"drm_version": None, "supports_sensors": False},
                "sensors": {
                    "power_w": {"value": None, "source": None},
                    "column_utilization_pct": {"value": None, "source": None},
                },
                "contexts": [
                    {
                        "pid": 1234,
                        "process_name": None,
                        "ctx_id": 1,
                        "submissions": 10,
                        "completions": 9,
                        "status": "Active",
                        "source": "xrt_smi",
                    }
                ]
                if not degraded
                else [],
                "report_shape": {
                    "has_aie_partitions": not degraded,
                    "has_hw_contexts": not degraded,
                    "context_count": 1 if not degraded else 0,
                },
            },
            "igpu": {
                "busy_path": "/sys/class/drm/card1/device/gpu_busy_percent",
                "power_path": "/sys/class/hwmon/hwmon4/power1_input",
                "busy_path_exists": True,
                "power_path_exists": True,
            },
        },
        "telemetry": {
            "gpu_busy_pct": 42,
            "gpu_power_w": 12.5,
            "npu_active": not degraded,
            "state": "ACTIVE",
            "igpu_degraded": False,
            "npu_degraded": degraded,
            "ts": 123.456,
        },
        "degraded": {
            "overall": degraded,
            "igpu": {"degraded": False, "reasons": []},
            "npu": {
                "degraded": degraded,
                "reasons": ["xrt_smi_not_found"] if degraded else [],
            },
        },
        "errors": [{"probe": "xrt_smi.aie_partitions", "message": "failed"}] if degraded else [],
    }


def test_render_markdown_healthy_snapshot():
    report = render_markdown(sample_snapshot())

    assert "# xdna-top Environment Report" in report
    assert "- Schema version: 1.0" in report
    assert "- OS: Linux Mint 22.3" in report
    assert "- NPU primary backend: xrt_smi" in report
    assert "- NPU context count: 1" in report
    assert "- iGPU busy: 42 %" in report
    assert "- NPU active: yes" in report
    assert "- Overall degraded: no" in report


def test_render_markdown_degraded_snapshot():
    report = render_markdown(sample_snapshot(degraded=True))

    assert "- xrt-smi available: no" in report
    assert "- NPU primary backend: unavailable" in report
    assert "- NPU detected: no" in report
    assert "- Overall degraded: yes" in report
    assert "- NPU reasons: xrt_smi_not_found" in report
    assert "## Probe Errors" in report
    assert "- xrt_smi.aie_partitions: failed" in report


def test_load_snapshot_requires_snapshot_kind(tmp_path):
    path = tmp_path / "not-snapshot.json"
    path.write_text(json.dumps({"kind": "other"}), encoding="utf-8")

    try:
        load_snapshot(path)
    except ValueError as exc:
        assert "expected kind" in str(exc)
    else:
        raise AssertionError("load_snapshot should reject non-snapshot JSON")


def test_env_report_main_writes_file(tmp_path):
    snapshot_path = tmp_path / "snapshot.json"
    out_path = tmp_path / "report.md"
    snapshot_path.write_text(json.dumps(sample_snapshot()), encoding="utf-8")

    args = type("Args", (), {"snapshot": str(snapshot_path), "out": str(out_path)})
    assert env_report_main(args) == 0

    report = out_path.read_text(encoding="utf-8")
    assert "# xdna-top Environment Report" in report
    assert "- Kernel: 6.17.0-test" in report
