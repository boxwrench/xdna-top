"""Tests for the assert evidence checks."""

import json
from argparse import Namespace

import pytest

from xdna_top.assertions import (
    CHECKS,
    Artifact,
    assert_main,
    evaluate,
    format_result,
    load_artifact,
)
from xdna_top.record import RECORD_KIND, RECORD_SCHEMA_VERSION
from xdna_top.snapshot import SNAPSHOT_KIND


def _healthy_snapshot() -> dict:
    return {
        "kind": SNAPSHOT_KIND,
        "schema_version": "1.0",
        "backends": {"npu": {"signals": {"contexts": "xrt_smi"}}},
        "devices": {
            "npu": {"detected": True, "driver": {"supports_sensors": True}},
        },
        "telemetry": {"npu_active": True, "igpu_degraded": False},
        "degraded": {"overall": False},
    }


def _degraded_snapshot() -> dict:
    return {
        "kind": SNAPSHOT_KIND,
        "schema_version": "1.0",
        "backends": {"npu": {"signals": {"contexts": None}}},
        "devices": {
            "npu": {"detected": False, "driver": {"supports_sensors": False}},
        },
        "telemetry": {"npu_active": False, "igpu_degraded": True},
        "degraded": {"overall": True},
    }


def _telemetry(ts, submissions, npu_active, *, source="xrt_smi", degraded=False):
    return {
        "type": "telemetry",
        "schema_version": RECORD_SCHEMA_VERSION,
        "ts": ts,
        "reading": {
            "gpu_busy_pct": None if degraded else 10,
            "npu_active": npu_active,
            "igpu_degraded": degraded,
            "npu_degraded": degraded,
        },
        "contexts": [
            {
                "pid": 1234,
                "ctx_id": 1,
                "submissions": submissions,
                "completions": submissions,
                "status": "Active",
                "source": source,
            }
        ],
    }


def _record_lines(events) -> str:
    meta = {"type": "meta", "kind": RECORD_KIND, "schema_version": RECORD_SCHEMA_VERSION}
    summary = {"type": "summary", "kind": RECORD_KIND, "samples": len(events)}
    return "\n".join(json.dumps(e) for e in [meta, *events, summary]) + "\n"


# --- loader -----------------------------------------------------------------


def test_load_artifact_detects_snapshot(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_healthy_snapshot(), indent=2), encoding="utf-8")
    artifact = load_artifact(path)
    assert artifact.kind == "snapshot"
    assert artifact.snapshot["devices"]["npu"]["detected"] is True


def test_load_artifact_detects_record(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text(_record_lines([_telemetry(1.0, 10, True)]), encoding="utf-8")
    artifact = load_artifact(path)
    assert artifact.kind == "record"
    assert len(artifact.telemetry) == 1


def test_load_artifact_rejects_unknown(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"kind": "something-else"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_artifact(path)


# --- snapshot checks --------------------------------------------------------


def test_snapshot_checks_pass_on_healthy():
    artifact = Artifact(kind="snapshot", snapshot=_healthy_snapshot(), events=[])
    for name in [
        "require-npu",
        "require-npu-sensors",
        "require-context-source",
        "require-igpu",
        "require-not-degraded",
        "require-npu-activity",
    ]:
        assert evaluate(name, artifact).ok, name


def test_snapshot_checks_fail_on_degraded():
    artifact = Artifact(kind="snapshot", snapshot=_degraded_snapshot(), events=[])
    for name in [
        "require-npu",
        "require-npu-sensors",
        "require-context-source",
        "require-igpu",
        "require-not-degraded",
        "require-npu-activity",
    ]:
        assert not evaluate(name, artifact).ok, name


def test_format_result_strings():
    artifact = Artifact(kind="snapshot", snapshot=_healthy_snapshot(), events=[])
    passed = format_result(evaluate("require-npu", artifact))
    assert passed == "PASS require-npu: observed devices.npu.detected=true"

    artifact = Artifact(kind="snapshot", snapshot=_degraded_snapshot(), events=[])
    failed = format_result(evaluate("require-npu", artifact))
    assert failed == "FAIL require-npu: observed devices.npu.detected=false, required true"


# --- record checks ----------------------------------------------------------


def test_record_activity_pass_on_rising_submissions():
    events = [_telemetry(1.0, 10, False), _telemetry(1.2, 52, True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate("require-npu-activity", artifact)
    assert result.ok
    assert "submission_delta=42" in result.observed


def test_record_activity_fail_on_flat_idle_window():
    events = [_telemetry(1.0, 10, False), _telemetry(1.2, 10, False)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate("require-npu-activity", artifact)
    assert not result.ok
    assert "submission_delta=0" in result.observed


def test_record_context_source_and_npu_present():
    events = [_telemetry(1.0, 10, True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    assert evaluate("require-context-source", artifact).ok
    assert evaluate("require-npu", artifact).ok


def test_record_sensors_check_reports_unavailable():
    events = [_telemetry(1.0, 10, True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate("require-npu-sensors", artifact)
    assert not result.ok
    assert "unavailable" in result.observed


def test_record_not_degraded_fails_when_any_sample_degraded():
    events = [_telemetry(1.0, 10, True), _telemetry(1.2, 12, True, degraded=True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate("require-not-degraded", artifact)
    assert not result.ok
    assert "degraded_samples=1/2" in result.observed


# --- CLI entry point --------------------------------------------------------


def _args(artifact, **flags):
    base = {dest: False for _name, dest, _fn in CHECKS}
    base.update(flags)
    return Namespace(artifact=str(artifact), **base)


def test_assert_main_exit_zero_when_all_pass(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_healthy_snapshot()), encoding="utf-8")
    rc = assert_main(_args(path, require_npu=True, require_npu_activity=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS require-npu" in out
    assert "PASS require-npu-activity" in out


def test_assert_main_exit_one_when_any_fail(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_degraded_snapshot()), encoding="utf-8")
    rc = assert_main(_args(path, require_npu=True))
    assert rc == 1
    assert "FAIL require-npu" in capsys.readouterr().out


def test_assert_main_no_requirements_is_usage_error(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_healthy_snapshot()), encoding="utf-8")
    rc = assert_main(_args(path))
    assert rc == 2
    assert "no requirements" in capsys.readouterr().err


def test_assert_main_bad_artifact_is_error(tmp_path, capsys):
    path = tmp_path / "missing.json"
    rc = assert_main(_args(path, require_npu=True))
    assert rc == 2
    assert "assert failed" in capsys.readouterr().err
