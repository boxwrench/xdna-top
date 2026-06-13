"""Tests for the assert evidence checks."""

import json
from argparse import Namespace

import pytest

from xdna_top.assertions import (
    CHECKS,
    Artifact,
    assert_main,
    evaluate,
    evaluate_windowed,
    format_result,
    load_artifact,
    resolve_window,
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


def _mark(ts, label):
    return {
        "type": "mark",
        "schema_version": RECORD_SCHEMA_VERSION,
        "ts": ts,
        "label": label,
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


# --- windowed activity guard (--between) ------------------------------------


def test_resolve_window_first_start_last_end():
    marks = [
        _mark(1.0, "start"),
        _mark(3.0, "start"),
        _mark(5.0, "end"),
        _mark(9.0, "end"),
    ]
    start_ts, end_ts, label, error = resolve_window(marks, "start", "end")
    assert (start_ts, end_ts) == (1.0, 9.0)
    assert label == "[start..end]"
    assert error is None


def test_windowed_activity_passes_inside_window():
    events = [
        _mark(1.0, "request-start"),
        _telemetry(1.5, 10, False),
        _telemetry(2.0, 60, True),
        _mark(2.5, "request-end"),
    ]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert result.ok
    assert result.name == "require-npu-activity[request-start..request-end]"
    assert "submission_delta=50" in result.observed
    assert (
        format_result(result)
        == "PASS require-npu-activity[request-start..request-end]: "
        "observed submission_delta=50, npu_active_samples=1/2"
    )


def test_windowed_activity_fails_when_activity_only_outside_window():
    events = [
        _telemetry(0.5, 10, True),
        _telemetry(0.8, 90, True),  # the real work happened before the window
        _mark(1.0, "request-start"),
        _telemetry(1.5, 90, False),
        _telemetry(2.0, 90, False),
        _mark(2.5, "request-end"),
    ]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert not result.ok
    assert "submission_delta=0" in result.observed


def test_windowed_missing_end_label_fails_honestly():
    events = [_mark(1.0, "request-start"), _telemetry(1.5, 50, True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert not result.ok
    assert result.name == "require-npu-activity[request-start..MISSING]"
    assert "end mark 'request-end' not found" in result.observed


def test_windowed_missing_start_label_fails_honestly():
    events = [_mark(2.5, "request-end"), _telemetry(1.5, 50, True)]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert not result.ok
    assert result.name == "require-npu-activity[MISSING..request-end]"
    assert "start mark 'request-start' not found" in result.observed


def test_windowed_end_before_start_fails():
    events = [
        _mark(5.0, "request-start"),
        _mark(1.0, "request-end"),
        _telemetry(3.0, 50, True),
    ]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert not result.ok
    assert "end precedes start" in result.observed


def test_windowed_empty_window_fails_not_vacuously():
    events = [
        _mark(1.0, "request-start"),
        _mark(2.0, "request-end"),
        _telemetry(5.0, 50, True),  # outside the window
    ]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed(
        "require-npu-activity", artifact, "request-start", "request-end"
    )
    assert not result.ok
    assert "0 samples in window" in result.observed


def test_windowed_duplicate_labels_use_first_start_last_end():
    # Activity straddles the widest window only; a narrower (wrong) choice of
    # endpoints would see a single point and a zero delta.
    events = [
        _mark(1.0, "s"),
        _mark(3.0, "s"),
        _telemetry(2.0, 10, False),
        _telemetry(8.0, 99, False),
        _mark(5.0, "e"),
        _mark(9.0, "e"),
    ]
    artifact = Artifact(kind="record", snapshot=None, events=events)
    result = evaluate_windowed("require-npu-activity", artifact, "s", "e")
    assert result.ok
    assert result.name == "require-npu-activity[s..e]"
    assert "submission_delta=89" in result.observed


def test_windowed_snapshot_misuse_is_usage_error(tmp_path, capsys):
    path = tmp_path / "s.json"
    path.write_text(json.dumps(_healthy_snapshot()), encoding="utf-8")
    rc = assert_main(
        _args(path, require_npu_activity=True, between=["request-start", "request-end"])
    )
    assert rc == 2
    assert "--between requires a record stream" in capsys.readouterr().err


def test_windowed_assert_main_full_stream_unchanged(tmp_path, capsys):
    # Without --between, behaviour is the full-stream path (between absent).
    path = tmp_path / "r.jsonl"
    path.write_text(
        _record_lines([_telemetry(1.0, 10, False), _telemetry(1.2, 52, True)]),
        encoding="utf-8",
    )
    rc = assert_main(_args(path, require_npu_activity=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS require-npu-activity:" in out  # no window suffix


def test_windowed_assert_main_cli_happy(tmp_path, capsys):
    path = tmp_path / "r.jsonl"
    path.write_text(
        _record_lines(
            [
                _mark(1.0, "request-start"),
                _telemetry(1.5, 10, False),
                _telemetry(2.0, 60, True),
                _mark(2.5, "request-end"),
            ]
        ),
        encoding="utf-8",
    )
    rc = assert_main(
        _args(path, require_npu_activity=True, between=["request-start", "request-end"])
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS require-npu-activity[request-start..request-end]" in out


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
