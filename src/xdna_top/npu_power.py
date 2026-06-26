"""Read the NPU's active power/clock state from the amdxdna debugfs nodes.

This is the first *direct AMDXDNA* signal in xdna-top (roadmap v0.3): the NPU's
active DPM (Dynamic Power Management) state — operating clocks (npuclk + hclk) — read
straight from ``/sys/kernel/debug/accel/<bdf>/``, with no XRT and no ioctl
version coupling.

Availability is narrow and honestly reported:

- The nodes exist only with a driver that exports them. The mainline / DKMS
  in-tree ``amdxdna`` does **not**; the staging ``amdxdna.ko`` from
  amd/xdna-driver does (see amd/xdna-driver#1447).
- ``debugfs`` is typically root-only, so an unprivileged read returns
  "unavailable" with a reason, never an exception.

Claims precision (the house rule): ``dpm_level`` is the NPU's active
clock-frequency **power state**, NOT a utilization percentage. Callers must label
it as a clock/power-state, and ``read_npu_power`` never synthesises a busy %.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DEBUGFS_ACCEL = Path("/sys/kernel/debug/accel")

# A dpm_level line looks like:  " [400,800]  600,1024  ...  847,1600 "
# Each token is "npuclk_MHz,hclk_MHz" (the driver prints `[%d,%d]` of
# dpm_clk_freq.npuclk, .hclk -- both clock frequencies, NOT a voltage); the
# [bracketed] token is the ACTIVE DPM level.
_DPM_TOKEN = re.compile(r"(?P<lb>\[?)\s*(?P<npuclk>\d+)\s*,\s*(?P<hclk>\d+)\s*(?P<rb>\]?)")


def _debugfs_dir(bdf: str | None) -> Path | None:
    """Resolve the per-device debugfs dir for ``bdf`` (or the first accel device).

    Fully defensive: every filesystem touch is guarded, so a root-only debugfs
    (the usual unprivileged case — ``/sys/kernel/debug`` is mode 700, and
    ``Path.exists()`` raises ``PermissionError`` rather than returning ``False``)
    yields ``None``, never an exception. ``None`` also covers "no accel node"
    (a driver that does not export these files, or debugfs not mounted).
    """
    try:
        if bdf:
            d = DEBUGFS_ACCEL / bdf
            if d.is_dir():
                return d
            # fall through: bdf may be formatted differently than the dir name
        subdirs = sorted(p for p in DEBUGFS_ACCEL.iterdir() if p.is_dir())
        return subdirs[0] if subdirs else None
    except OSError:
        return None


def _read(path: Path) -> str | None:
    """Best-effort text read; ``None`` on permission/IO error or missing node."""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def parse_dpm_level(text: str) -> dict[str, Any] | None:
    """Parse a ``dpm_level`` line into active / max DPM clocks.

    Returns ``None`` if no ``npuclk,hclk`` token is found. ``active`` is ``None``
    when no state is bracketed (the node read but no current state was marked),
    so the caller can distinguish "read it, no active marker" from "couldn't
    read".
    """
    levels: list[dict[str, int]] = []
    active: dict[str, int] | None = None
    for m in _DPM_TOKEN.finditer(text):
        npuclk = int(m.group("npuclk"))
        hclk = int(m.group("hclk"))
        state = {"index": len(levels), "npuclk_mhz": npuclk, "hclk_mhz": hclk}
        if m.group("lb") == "[" or m.group("rb") == "]":
            active = state
        levels.append(state)
    if not levels:
        return None
    return {
        "active": active,
        "max": max(levels, key=lambda s: s["npuclk_mhz"]),
        "levels": len(levels),
    }


def read_npu_power(bdf: str | None = None) -> dict[str, Any]:
    """Read the NPU active power/clock state from debugfs.

    Always returns a dict with ``available`` and ``source``; ``reason`` explains
    an unavailable read (driver does not export the nodes, debugfs not mounted,
    or — most often when unprivileged — the nodes are root-only). The values are
    the NPU active DPM clock state (npuclk, hclk) and the SMU powerstate string, never
    a utilization metric.
    """
    result: dict[str, Any] = {
        "available": False,
        "source": None,
        "reason": None,
        "powerstate": None,
        "dpm": None,
    }

    d = _debugfs_dir(bdf)
    if d is None:
        result["reason"] = "debugfs_accel_absent"
        return result

    dpm_raw = _read(d / "dpm_level")
    powerstate = _read(d / "powerstate")
    if dpm_raw is None and powerstate is None:
        # The directory exists but neither node could be read: either this driver
        # does not export them, or (typically) debugfs is root-only here.
        result["reason"] = "debugfs_nodes_unreadable"
        return result

    result["available"] = True
    result["source"] = "debugfs"
    result["powerstate"] = powerstate
    if dpm_raw is not None:
        result["dpm"] = parse_dpm_level(dpm_raw)
    return result
