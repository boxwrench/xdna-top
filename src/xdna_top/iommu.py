"""Read-only IOMMU observation for the NPU's SVA/PASID requirement.

The ``amdxdna`` driver binds the NPU to an IOMMU domain at open time
(``iommu_sva_bind_device()``). When the kernel boots with ``amd_iommu=off`` (or
the NPU otherwise has no IOMMU group) that bind fails and ``open()`` returns
``ENODEV`` -- observed on a Strix Halo box as
``amdxdna_drm_open: SVA bind device failed, ret -19``.

Everything here is a zero-root read of facts that are already on disk:

- ``/proc/cmdline``: the IOMMU tokens that are *actually present*, recorded
  verbatim. A normalized ``mode`` is a convenience over those tokens, never a
  substitute for them.
- ``/sys/bus/pci/devices/<bdf>/iommu_group``: whether the NPU PCI device has an
  IOMMU group symlink.

Claims precision (the house rule): report only what was read. A mode is never
inferred when the command line is unreadable, and "no evidence" is never treated
as "IOMMU disabled".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

CMDLINE_PATH = Path("/proc/cmdline")
PCI_DEVICES = Path("/sys/bus/pci/devices")

# Any token with one of these prefixes is retained verbatim.
_IOMMU_PREFIXES = ("amd_iommu=", "iommu=", "iommu.passthrough=", "intel_iommu=")

_OFF_TOKENS = {"amd_iommu=off", "iommu=off"}
_PT_TOKENS = {"iommu=pt", "amd_iommu=pt"}
_PASSTHROUGH_TOKENS = {"iommu.passthrough=1"}
_ON_TOKENS = {"amd_iommu=on", "intel_iommu=on", "iommu=on", "iommu.passthrough=0"}

# Shown by the TUI and env-report when the evidence identifies disabled
# IOMMU/SVA. It explains a driver requirement, not a performance claim.
IOMMU_HINT = (
    "the NPU driver needs the IOMMU (SVA/PASID); amd_iommu=off blocks it, "
    "and iommu=pt restores it (note: iommu=pt can cost iGPU throughput)"
)


def parse_iommu_tokens(cmdline: str | None) -> list[str]:
    """Return the IOMMU-related kernel command-line tokens, in order.

    The tokens are returned unchanged so a consumer can see exactly what booted;
    an empty list means either no IOMMU tokens or an unreadable command line
    (callers distinguish those with ``cmdline_readable``).
    """
    if not cmdline:
        return []
    return [token for token in cmdline.split() if token.startswith(_IOMMU_PREFIXES)]


def classify_iommu_mode(tokens: list[str]) -> str:
    """Classify observed IOMMU tokens into one of a small set of modes.

    Possible values: ``off``, ``pt``, ``passthrough``, ``on``, ``other``,
    ``none``. This is a convenience over the raw tokens; ``off`` is the only
    value used as evidence for the NPU ENODEV diagnosis.
    """
    if not tokens:
        return "none"
    if any(token in _OFF_TOKENS for token in tokens):
        return "off"
    if any(token in _PT_TOKENS for token in tokens):
        return "pt"
    if any(token in _PASSTHROUGH_TOKENS for token in tokens):
        return "passthrough"
    if any(token in _ON_TOKENS for token in tokens):
        return "on"
    return "other"


def _read_cmdline(path: Path | str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def observe_iommu(
    bdf: str | None,
    *,
    cmdline_path: Path | str = CMDLINE_PATH,
    pci_devices: Path | str = PCI_DEVICES,
) -> dict[str, Any]:
    """Read the IOMMU cmdline tokens and the NPU's IOMMU-group presence.

    Returns ``{"cmdline": {...}, "npu_iommu_group": {...}}``. Every field is a
    direct observation; a missing BDF yields ``null`` for the group probe rather
    than a guessed value. Never raises.
    """
    cmdline = _read_cmdline(cmdline_path)
    tokens = parse_iommu_tokens(cmdline)
    group_path = Path(pci_devices) / bdf / "iommu_group" if bdf else None
    return {
        "cmdline": {
            "readable": cmdline is not None,
            "tokens": tokens,
            "mode": "unknown" if cmdline is None else classify_iommu_mode(tokens),
        },
        "npu_iommu_group": {
            "bdf": bdf,
            "path": str(group_path) if group_path is not None else None,
            "exists": group_path.exists() if group_path is not None else None,
        },
    }


def iommu_blocks_sva(observation: dict[str, Any] | None) -> bool:
    """True only when the observation is positive evidence that IOMMU/SVA is off.

    That means ``amd_iommu=off``/``iommu=off`` on the command line, or a probed
    NPU PCI device that has no ``iommu_group``. An unreadable command line, a
    missing BDF, or an unprobed group (``exists: null``) is *not* evidence.
    """
    if not isinstance(observation, dict):
        return False
    cmdline = observation.get("cmdline") or {}
    if cmdline.get("mode") == "off":
        return True
    group = observation.get("npu_iommu_group") or {}
    return group.get("exists") is False
