"""Named local baseline workflow for xdna-top.

`xdna-top baseline save <name>` captures a snapshot and stores it under a local
directory; `xdna-top baseline check <name>` captures a fresh snapshot and diffs
it against the saved one using the same high-signal `compare` rules. This wraps
`snapshot` + `compare` into a known-good / post-update canary; only `save` probes
hardware. Baseline names should stay generic, such as `known-good` or
`post-kernel-update`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from xdna_top.compare import Change, compare_snapshots, format_change
from xdna_top.env_report import load_snapshot
from xdna_top.snapshot import build_snapshot, write_snapshot

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def baselines_dir(override: str | Path | None = None) -> Path:
    """Return the directory where named baselines are stored."""
    if override:
        return Path(override)
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state_home) / "xdna-top" / "baselines"


def baseline_path(name: str, override: str | Path | None = None) -> Path:
    """Return the on-disk path for a named baseline, validating the name."""
    _validate_name(name)
    return baselines_dir(override) / f"{name}.json"


def baseline_list(override: str | Path | None = None) -> list[str]:
    """Return the sorted names of saved baselines."""
    directory = baselines_dir(override)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def baseline_save(
    name: str,
    *,
    override: str | Path | None = None,
    snapshot_kwargs: dict[str, Any] | None = None,
) -> Path:
    """Capture a snapshot and store it as the named baseline."""
    path = baseline_path(name, override)
    snapshot = build_snapshot(**(snapshot_kwargs or {}))
    write_snapshot(snapshot, path)
    return path


def baseline_check(
    name: str,
    *,
    override: str | Path | None = None,
    snapshot_kwargs: dict[str, Any] | None = None,
) -> list[Change]:
    """Capture a fresh snapshot and diff it against the named baseline."""
    path = baseline_path(name, override)
    if not path.exists():
        raise FileNotFoundError(f"no baseline named {name!r} at {path}")
    before = load_snapshot(path)
    after = build_snapshot(**(snapshot_kwargs or {}))
    return compare_snapshots(before, after)


def baseline_main(args: Any) -> int:
    action = getattr(args, "action", None)
    if action == "save":
        return _run_save(args)
    if action == "check":
        return _run_check(args)
    if action == "list":
        return _run_list(args)
    print(
        "baseline: specify an action (save <name>, check <name>, or list)",
        file=sys.stderr,
    )
    return 2


def _run_save(args: Any) -> int:
    try:
        path = baseline_save(
            args.name,
            override=args.baseline_dir,
            snapshot_kwargs=_snapshot_kwargs(args),
        )
    except ValueError as exc:
        print(f"baseline failed: {exc}", file=sys.stderr)
        return 2
    print(f"Saved baseline {args.name!r} to {path}")
    return 0


def _run_check(args: Any) -> int:
    try:
        changes = baseline_check(
            args.name,
            override=args.baseline_dir,
            snapshot_kwargs=_snapshot_kwargs(args),
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"baseline failed: {exc}", file=sys.stderr)
        return 2

    if not changes:
        print(f"Baseline {args.name!r}: no high-signal changes detected.")
        return 0

    for change in changes:
        print(format_change(change))
    regressions = sum(1 for c in changes if c.severity == "regression")
    print(
        f"\nBaseline {args.name!r}: {len(changes)} change(s) detected "
        f"({regressions} regression(s))."
    )
    return 1


def _run_list(args: Any) -> int:
    names = baseline_list(args.baseline_dir)
    if not names:
        print("No baselines saved.")
        return 0
    for name in names:
        print(name)
    return 0


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise ValueError(
            f"invalid baseline name: {name!r} (use letters, digits, '.', '_', '-')"
        )


def _snapshot_kwargs(args: Any) -> dict[str, Any]:
    return {
        "bench_dir": getattr(args, "bench_dir", "/tmp/xdna_top"),
        "npu_device": getattr(args, "npu_device", None),
        "gpu_idle_busy_pct": getattr(args, "idle_busy_pct", 10),
        "gpu_prefill_power_w": getattr(args, "prefill_power_w", 35.0),
        "gauge_hysteresis_samples": getattr(args, "hysteresis_samples", 3),
    }
