"""Tests for snapshot comparison."""

import copy
import json
from argparse import Namespace

from xdna_top.compare import compare_main, compare_snapshots, format_change
from xdna_top.snapshot import SNAPSHOT_KIND


def _base() -> dict:
    return {
        "kind": SNAPSHOT_KIND,
        "schema_version": "1.0",
        "host": {"kernel": {"release": "6.11.0"}},
        "commands": {"xrt_smi": {"available": True, "aie_partitions_returncode": 0}},
        "backends": {
            "npu": {
                "primary": "xrt_smi",
                "signals": {"sensors": None, "contexts": "xrt_smi"},
            }
        },
        "devices": {
            "accel": {"entries": [{"path": "/dev/accel/accel0", "exists": True}]},
            "npu": {
                "bdf": "0000:c6:00.1",
                "driver": {"drm_version": None, "supports_sensors": False},
                "report_shape": {"has_hw_contexts": True},
            },
            "igpu": {
                "busy_path": "/sys/class/drm/card1/device/gpu_busy_percent",
                "power_path": "/sys/class/hwmon/hwmon4/power1_input",
            },
        },
        "degraded": {"overall": False},
    }


def _changes_by_name(before, after) -> dict:
    return {c.name: c for c in compare_snapshots(before, after)}


def test_identical_snapshots_have_no_changes():
    assert compare_snapshots(_base(), _base()) == []


def test_kernel_change_is_flagged_as_change():
    after = copy.deepcopy(_base())
    after["host"]["kernel"]["release"] = "6.12.3"
    changes = _changes_by_name(_base(), after)
    assert "host.kernel.release" in changes
    assert changes["host.kernel.release"].severity == "change"
    assert changes["host.kernel.release"].after == "6.12.3"


def test_degraded_regression_is_flagged():
    after = copy.deepcopy(_base())
    after["degraded"]["overall"] = True
    change = _changes_by_name(_base(), after)["degraded.overall"]
    assert change.severity == "regression"


def test_degraded_recovery_is_change_not_regression():
    before = copy.deepcopy(_base())
    before["degraded"]["overall"] = True
    change = _changes_by_name(before, _base())["degraded.overall"]
    assert change.severity == "change"


def test_sensor_support_loss_is_regression():
    before = copy.deepcopy(_base())
    before["devices"]["npu"]["driver"]["supports_sensors"] = True
    change = _changes_by_name(before, _base())[
        "devices.npu.driver.supports_sensors"
    ]
    assert change.severity == "regression"


def test_accel_device_disappearance_is_regression():
    after = copy.deepcopy(_base())
    after["devices"]["accel"]["entries"] = [
        {"path": "/dev/accel/accel0", "exists": False}
    ]
    change = _changes_by_name(_base(), after)["devices.accel.present"]
    assert change.severity == "regression"
    assert change.after is None


def test_bdf_loss_is_regression():
    after = copy.deepcopy(_base())
    after["devices"]["npu"]["bdf"] = None
    change = _changes_by_name(_base(), after)["devices.npu.bdf"]
    assert change.severity == "regression"


def test_aie_partitions_break_flagged_when_contexts_depend_on_xrt():
    after = copy.deepcopy(_base())
    after["commands"]["xrt_smi"]["aie_partitions_returncode"] = 1
    change = _changes_by_name(_base(), after)[
        "commands.xrt_smi.aie_partitions_returncode"
    ]
    assert change.severity == "regression"


def test_aie_partitions_change_ignored_when_contexts_not_from_xrt():
    before = copy.deepcopy(_base())
    after = copy.deepcopy(_base())
    # Contexts no longer attributed via xrt-smi in either snapshot.
    before["backends"]["npu"]["signals"]["contexts"] = None
    after["backends"]["npu"]["signals"]["contexts"] = None
    after["commands"]["xrt_smi"]["aie_partitions_returncode"] = 1
    assert "commands.xrt_smi.aie_partitions_returncode" not in _changes_by_name(
        before, after
    )


def test_format_change_strings():
    after = copy.deepcopy(_base())
    after["degraded"]["overall"] = True
    change = _changes_by_name(_base(), after)["degraded.overall"]
    assert format_change(change) == "REGRESSION degraded.overall: false -> true"


# --- CLI entry point --------------------------------------------------------


def _write(tmp_path, name, snap) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(snap), encoding="utf-8")
    return str(path)


def test_compare_main_exit_zero_when_identical(tmp_path, capsys):
    before = _write(tmp_path, "b.json", _base())
    after = _write(tmp_path, "a.json", _base())
    rc = compare_main(Namespace(before=before, after=after))
    assert rc == 0
    assert "No high-signal changes" in capsys.readouterr().out


def test_compare_main_exit_one_on_drift(tmp_path, capsys):
    drifted = copy.deepcopy(_base())
    drifted["degraded"]["overall"] = True
    before = _write(tmp_path, "b.json", _base())
    after = _write(tmp_path, "a.json", drifted)
    rc = compare_main(Namespace(before=before, after=after))
    out = capsys.readouterr().out
    assert rc == 1
    assert "REGRESSION degraded.overall" in out
    assert "1 regression(s)" in out


def test_compare_main_bad_artifact_is_error(tmp_path, capsys):
    before = _write(tmp_path, "b.json", _base())
    rc = compare_main(Namespace(before=before, after=str(tmp_path / "missing.json")))
    assert rc == 2
    assert "compare failed" in capsys.readouterr().err
