"""Snapshot comparison for xdna-top upgrade canaries.

`xdna-top compare before.json after.json` diffs two snapshots and surfaces only
the high-signal platform changes that affect trust in an experiment (kernel,
accel device, backend/sensor availability, NPU BDF, sysfs paths, degraded
regressions) rather than generic JSON noise. Like ``git diff --exit-code`` it
exits non-zero when any high-signal drift is found, so it can gate CI and back a
``baseline check`` workflow.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable

from xdna_top.env_report import load_snapshot


@dataclass
class Change:
    name: str
    before: Any
    after: Any
    severity: str  # "regression" or "change"


def compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> list[Change]:
    """Return the list of high-signal changes between two snapshots."""
    changes: list[Change] = []
    for rule in _RULES:
        change = rule(before, after)
        if change is not None:
            changes.append(change)
    return changes


def format_change(change: Change) -> str:
    tag = "REGRESSION" if change.severity == "regression" else "CHANGED"
    return f"{tag} {change.name}: {_fmt(change.before)} -> {_fmt(change.after)}"


def compare_main(args: Any) -> int:
    try:
        before = load_snapshot(args.before)
        after = load_snapshot(args.after)
    except Exception as exc:
        print(f"compare failed: {exc}", file=sys.stderr)
        return 2

    changes = compare_snapshots(before, after)
    if not changes:
        print("No high-signal changes detected.")
        return 0

    for change in changes:
        print(format_change(change))
    regressions = sum(1 for c in changes if c.severity == "regression")
    print(f"\n{len(changes)} change(s) detected ({regressions} regression(s)).")
    return 1


# --- comparison rules --------------------------------------------------------

Rule = Callable[[dict[str, Any], dict[str, Any]], "Change | None"]


def _field_rule(
    name: str,
    keys: tuple[str, ...],
    *,
    regression_when: Callable[[Any, Any], bool] | None = None,
) -> Rule:
    def rule(before: dict[str, Any], after: dict[str, Any]) -> Change | None:
        b = _get(before, *keys)
        a = _get(after, *keys)
        if b == a:
            return None
        severity = "regression" if regression_when and regression_when(b, a) else "change"
        return Change(name=name, before=b, after=a, severity=severity)

    return rule


def _aie_partitions_rule(
    before: dict[str, Any], after: dict[str, Any]
) -> Change | None:
    # Only meaningful when contexts depend on xrt-smi for attribution.
    depends = (
        _get(before, "backends", "npu", "signals", "contexts") == "xrt_smi"
        or _get(after, "backends", "npu", "signals", "contexts") == "xrt_smi"
    )
    if not depends:
        return None
    b = _get(before, "commands", "xrt_smi", "aie_partitions_returncode")
    a = _get(after, "commands", "xrt_smi", "aie_partitions_returncode")
    # High-signal specifically when a working report (0) stops working.
    if b == 0 and a != 0:
        return Change(
            name="commands.xrt_smi.aie_partitions_returncode",
            before=b,
            after=a,
            severity="regression",
        )
    return None


def _accel_rule(before: dict[str, Any], after: dict[str, Any]) -> Change | None:
    b = _accel_present(before)
    a = _accel_present(after)
    if b == a:
        return None
    disappeared = b - a
    return Change(
        name="devices.accel.present",
        before=sorted(b) or None,
        after=sorted(a) or None,
        severity="regression" if disappeared else "change",
    )


def _accel_present(snapshot: dict[str, Any]) -> set[str]:
    entries = _get(snapshot, "devices", "accel", "entries") or []
    return {
        entry.get("path")
        for entry in entries
        if isinstance(entry, dict) and entry.get("exists")
    }


def _became_none(before: Any, after: Any) -> bool:
    return before is not None and after is None


def _true_to_false(before: Any, after: Any) -> bool:
    return before is True and after is False


def _false_to_true(before: Any, after: Any) -> bool:
    return before is False and after is True


_RULES: list[Rule] = [
    _field_rule("schema_version", ("schema_version",)),
    _field_rule("host.kernel.release", ("host", "kernel", "release")),
    _field_rule(
        "backends.npu.primary",
        ("backends", "npu", "primary"),
        regression_when=_became_none,
    ),
    _field_rule(
        "backends.npu.signals.sensors",
        ("backends", "npu", "signals", "sensors"),
        regression_when=_became_none,
    ),
    _field_rule(
        "devices.npu.driver.drm_version",
        ("devices", "npu", "driver", "drm_version"),
    ),
    _field_rule(
        "devices.npu.driver.supports_sensors",
        ("devices", "npu", "driver", "supports_sensors"),
        regression_when=_true_to_false,
    ),
    _field_rule(
        "commands.xrt_smi.available",
        ("commands", "xrt_smi", "available"),
        regression_when=_true_to_false,
    ),
    _aie_partitions_rule,
    _accel_rule,
    _field_rule(
        "devices.npu.bdf",
        ("devices", "npu", "bdf"),
        regression_when=_became_none,
    ),
    _field_rule(
        "devices.npu.report_shape.has_hw_contexts",
        ("devices", "npu", "report_shape", "has_hw_contexts"),
        regression_when=_true_to_false,
    ),
    _field_rule("devices.igpu.busy_path", ("devices", "igpu", "busy_path")),
    _field_rule("devices.igpu.power_path", ("devices", "igpu", "power_path")),
    _field_rule(
        "degraded.overall",
        ("degraded", "overall"),
        regression_when=_false_to_true,
    ),
]


def _get(obj: Any, *keys: str) -> Any:
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _fmt(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(str(item) for item in value) + "]"
    return str(value)
