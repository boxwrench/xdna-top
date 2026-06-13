"""Tests for the named baseline workflow."""

import copy
import json
from argparse import Namespace
from unittest.mock import patch

import pytest

from xdna_top.baseline import (
    baseline_list,
    baseline_main,
    baseline_path,
    baselines_dir,
)
from xdna_top.snapshot import SNAPSHOT_KIND


def _snap() -> dict:
    return {
        "kind": SNAPSHOT_KIND,
        "schema_version": "1.0",
        "host": {"kernel": {"release": "6.11.0"}},
        "commands": {"xrt_smi": {"available": True, "aie_partitions_returncode": 0}},
        "backends": {"npu": {"primary": "xrt_smi", "signals": {"sensors": None, "contexts": "xrt_smi"}}},
        "devices": {
            "accel": {"entries": [{"path": "/dev/accel/accel0", "exists": True}]},
            "npu": {"bdf": "0000:c6:00.1", "driver": {"drm_version": None, "supports_sensors": False}, "report_shape": {"has_hw_contexts": True}},
            "igpu": {"busy_path": "/sys/a", "power_path": "/sys/b"},
        },
        "degraded": {"overall": False},
    }


def _args(action, name=None, baseline_dir=None):
    return Namespace(
        command="baseline",
        action=action,
        name=name,
        baseline_dir=baseline_dir,
        bench_dir="/tmp/xdna_top",
        npu_device=None,
        idle_busy_pct=10,
        prefill_power_w=35.0,
        hysteresis_samples=3,
    )


# --- path / name handling ---------------------------------------------------


def test_baselines_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert baselines_dir() == tmp_path / "xdna-top" / "baselines"


def test_baselines_dir_override_wins(tmp_path):
    assert baselines_dir(tmp_path / "b") == tmp_path / "b"


@pytest.mark.parametrize("bad", ["../escape", "a/b", "..", ".", "", "with space"])
def test_baseline_path_rejects_unsafe_names(bad, tmp_path):
    with pytest.raises(ValueError):
        baseline_path(bad, tmp_path)


def test_baseline_path_accepts_generic_names(tmp_path):
    path = baseline_path("post-kernel-update", tmp_path)
    assert path == tmp_path / "post-kernel-update.json"


# --- save / check round trip ------------------------------------------------


@patch("xdna_top.baseline.build_snapshot")
def test_save_then_check_clean(mock_build, tmp_path, capsys):
    mock_build.return_value = _snap()

    assert baseline_main(_args("save", "known-good", str(tmp_path))) == 0
    saved = tmp_path / "known-good.json"
    assert saved.exists()
    assert json.loads(saved.read_text())["kind"] == SNAPSHOT_KIND

    # Same snapshot on check -> no drift, exit 0.
    rc = baseline_main(_args("check", "known-good", str(tmp_path)))
    assert rc == 0
    assert "no high-signal changes" in capsys.readouterr().out


@patch("xdna_top.baseline.build_snapshot")
def test_check_reports_drift(mock_build, tmp_path, capsys):
    mock_build.return_value = _snap()
    baseline_main(_args("save", "known-good", str(tmp_path)))

    drifted = copy.deepcopy(_snap())
    drifted["degraded"]["overall"] = True
    mock_build.return_value = drifted

    rc = baseline_main(_args("check", "known-good", str(tmp_path)))
    out = capsys.readouterr().out
    assert rc == 1
    assert "REGRESSION degraded.overall" in out
    assert "1 regression(s)" in out


@patch("xdna_top.baseline.build_snapshot")
def test_check_missing_baseline_is_error(mock_build, tmp_path, capsys):
    mock_build.return_value = _snap()
    rc = baseline_main(_args("check", "absent", str(tmp_path)))
    assert rc == 2
    assert "baseline failed" in capsys.readouterr().err
    # build_snapshot must not run when the baseline is absent.
    mock_build.assert_not_called()


def test_save_invalid_name_is_error(tmp_path, capsys):
    rc = baseline_main(_args("save", "../escape", str(tmp_path)))
    assert rc == 2
    assert "baseline failed" in capsys.readouterr().err


def test_no_action_is_usage_error(capsys):
    rc = baseline_main(_args(None))
    assert rc == 2
    assert "specify an action" in capsys.readouterr().err


# --- list -------------------------------------------------------------------


@patch("xdna_top.baseline.build_snapshot")
def test_list_returns_sorted_names(mock_build, tmp_path):
    mock_build.return_value = _snap()
    baseline_main(_args("save", "known-good", str(tmp_path)))
    baseline_main(_args("save", "post-update", str(tmp_path)))
    assert baseline_list(str(tmp_path)) == ["known-good", "post-update"]


def test_list_empty_when_dir_missing(tmp_path, capsys):
    rc = baseline_main(_args("list", baseline_dir=str(tmp_path / "nope")))
    assert rc == 0
    assert "No baselines saved" in capsys.readouterr().out
