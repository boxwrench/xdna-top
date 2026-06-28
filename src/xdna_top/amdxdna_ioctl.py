"""Read-only AMDXDNA DRM IOCTL backend (experimental, additive).

Direct, unprivileged probing of the AMD XDNA NPU through ``/dev/accel/accelN``
using the amdxdna DRM uAPI: the generic ``DRM_IOCTL_VERSION`` (driver name +
version) and ``DRM_IOCTL_AMDXDNA_GET_INFO`` (AIE version/metadata, clocks,
firmware version, and power/thermal sensors where the kernel exposes them).

This is the v0.3 "direct backend" first cut. It does **not** replace XRT for
per-context PID / submission / completion attribution — that stays ``xrt-smi``.
It adds device + driver identity and static metadata read straight from the
kernel, independent of ``xrt-smi``, plus sensor values when supported.

Defensive, like :mod:`xdna_top.npu_power`: every ``open`` and every ``ioctl`` is
guarded, so a missing ``/dev/accel``, an unreadable node (permission), a
non-amdxdna accel device, or an unsupported ``GET_INFO`` query yields
``available: False`` (or a ``None`` sub-probe) **with a reason** — never an
exception.

Claims precision (the house rule): clock values are clock *frequencies* in MHz,
never a utilization %. Sensor values are reported with their raw
``input``/``units``/``unitm`` so callers can scale honestly; this module never
fabricates a watts figure or a busy %.

The ioctl request codes and ``ctypes`` struct layouts mirror the amdxdna uAPI
(``include/uapi/drm/amdxdna_accel.h``). The request numbers are *computed* from
``ctypes.sizeof`` (so they can't drift from the structs), and the test-suite
asserts both the request numbers and the struct sizes against the kernel-header
values.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
from pathlib import Path
from typing import Any

ACCEL_DIR = Path("/dev/accel")

# --- ioctl encoding (linux asm-generic/ioctl.h, DRM uses type 'd') ----------
_IOC_WRITE = 1
_IOC_READ = 2
_DRM_IOCTL_BASE = ord("d")
_DRM_COMMAND_BASE = 0x40

# enum amdxdna_drm_ioctl_id
_DRM_AMDXDNA_GET_INFO = 7

# enum amdxdna_drm_get_param
_PARAM_AIE_METADATA = 1
_PARAM_AIE_VERSION = 2
_PARAM_CLOCK_METADATA = 3
_PARAM_SENSORS = 4
_PARAM_FIRMWARE_VERSION = 8


def _iowr(nr: int, size: int) -> int:
    """Compute an ``_IOWR('d', nr, size)`` request number."""
    return ((_IOC_READ | _IOC_WRITE) << 30) | (size << 16) | (_DRM_IOCTL_BASE << 8) | nr


# --- struct layouts (match include/uapi/drm/{drm.h,amdxdna_accel.h}) --------
class _DrmVersion(ctypes.Structure):
    _fields_ = [
        ("version_major", ctypes.c_int),
        ("version_minor", ctypes.c_int),
        ("version_patchlevel", ctypes.c_int),
        ("name_len", ctypes.c_size_t),
        ("name", ctypes.c_void_p),
        ("date_len", ctypes.c_size_t),
        ("date", ctypes.c_void_p),
        ("desc_len", ctypes.c_size_t),
        ("desc", ctypes.c_void_p),
    ]


class _GetInfo(ctypes.Structure):
    _fields_ = [
        ("param", ctypes.c_uint32),
        ("buffer_size", ctypes.c_uint32),
        ("buffer", ctypes.c_uint64),
    ]


class _AieVersion(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint32), ("minor", ctypes.c_uint32)]


class _QueryClock(ctypes.Structure):
    _fields_ = [("name", ctypes.c_uint8 * 16), ("freq_mhz", ctypes.c_uint32), ("pad", ctypes.c_uint32)]


class _ClockMetadata(ctypes.Structure):
    _fields_ = [("mp_npu_clock", _QueryClock), ("h_clock", _QueryClock)]


class _FirmwareVersion(ctypes.Structure):
    _fields_ = [
        ("major", ctypes.c_uint32),
        ("minor", ctypes.c_uint32),
        ("patch", ctypes.c_uint32),
        ("build", ctypes.c_uint32),
    ]


class _AieTileMetadata(ctypes.Structure):
    _fields_ = [
        ("row_count", ctypes.c_uint16),
        ("row_start", ctypes.c_uint16),
        ("dma_channel_count", ctypes.c_uint16),
        ("lock_count", ctypes.c_uint16),
        ("event_reg_count", ctypes.c_uint16),
        ("pad", ctypes.c_uint16 * 3),
    ]


class _AieMetadata(ctypes.Structure):
    _fields_ = [
        ("col_size", ctypes.c_uint32),
        ("cols", ctypes.c_uint16),
        ("rows", ctypes.c_uint16),
        ("version", _AieVersion),
        ("core", _AieTileMetadata),
        ("mem", _AieTileMetadata),
        ("shim", _AieTileMetadata),
    ]


class _QuerySensor(ctypes.Structure):
    _fields_ = [
        ("label", ctypes.c_uint8 * 64),
        ("input", ctypes.c_uint32),
        ("max", ctypes.c_uint32),
        ("average", ctypes.c_uint32),
        ("highest", ctypes.c_uint32),
        ("status", ctypes.c_uint8 * 64),
        ("units", ctypes.c_uint8 * 16),
        ("unitm", ctypes.c_int8),
        ("type", ctypes.c_uint8),
        ("pad", ctypes.c_uint8 * 6),
    ]


# Computed from the struct sizes so they cannot drift; verified against the
# kernel headers in tests (DRM_IOCTL_VERSION == 0xC0406400,
# DRM_IOCTL_AMDXDNA_GET_INFO == 0xC0106447 on amd64).
DRM_IOCTL_VERSION = _iowr(0x00, ctypes.sizeof(_DrmVersion))
DRM_IOCTL_AMDXDNA_GET_INFO = _iowr(
    _DRM_COMMAND_BASE + _DRM_AMDXDNA_GET_INFO, ctypes.sizeof(_GetInfo)
)


def _cstr(arr: Any) -> str | None:
    """Decode a fixed-size C char array up to its first NUL; ``None`` if empty."""
    s = bytes(arr).split(b"\0", 1)[0].decode("utf-8", "replace").strip()
    return s or None


def _accel_nodes() -> list[Path]:
    try:
        return sorted(p for p in ACCEL_DIR.glob("accel*"))
    except OSError:
        return []


def _resolve_node(bdf: str | None) -> tuple[Path | None, str | None]:
    """Pick the accel node for ``bdf`` (or the only/first node when ``bdf`` is
    ``None``). Returns ``(node, reason)``; ``node`` is ``None`` with a reason when
    nothing usable is found. Never silently substitutes a *different* device for a
    specific requested BDF when more than one node exists."""
    nodes = _accel_nodes()
    if not nodes:
        return None, "accel_absent"
    if bdf:
        for n in nodes:
            try:
                dev = os.path.realpath(f"/sys/class/accel/{n.name}/device")
            except OSError:
                dev = ""
            if os.path.basename(dev) == bdf:
                return n, None
        # BDF requested but not matched: only safe to use a node when there is
        # exactly one (unambiguous single NPU); otherwise refuse to guess.
        if len(nodes) == 1:
            return nodes[0], None
        return None, "bdf_not_matched"
    return nodes[0], None


def _drm_version(fd: int) -> dict[str, Any] | None:
    v = _DrmVersion()
    name = ctypes.create_string_buffer(64)
    date = ctypes.create_string_buffer(64)
    desc = ctypes.create_string_buffer(128)
    v.name_len, v.name = 64, ctypes.cast(name, ctypes.c_void_p)
    v.date_len, v.date = 64, ctypes.cast(date, ctypes.c_void_p)
    v.desc_len, v.desc = 128, ctypes.cast(desc, ctypes.c_void_p)
    try:
        fcntl.ioctl(fd, DRM_IOCTL_VERSION, v, True)
    except OSError:
        return None
    return {
        "name": name.value.decode("utf-8", "replace"),
        "version": f"{v.version_major}.{v.version_minor}.{v.version_patchlevel}",
        "major": v.version_major,
        "minor": v.version_minor,
        "patchlevel": v.version_patchlevel,
        "description": _cstr(desc.raw),
    }


def _get_info_fixed(fd: int, param: int, struct_type):
    """Issue a fixed-size GET_INFO query; return the filled struct or ``None``."""
    inner = struct_type()
    info = _GetInfo(
        param=param,
        buffer_size=ctypes.sizeof(inner),
        buffer=ctypes.cast(ctypes.pointer(inner), ctypes.c_void_p).value,
    )
    try:
        fcntl.ioctl(fd, DRM_IOCTL_AMDXDNA_GET_INFO, info, True)
    except OSError:
        return None
    return inner


def _query_sensors(fd: int) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Two-step (size, then fill) sensors query. Returns ``(items, reason)``;
    ``items`` is ``None`` (with a reason) when the query is unsupported or fails,
    and an empty list when supported but no sensors are reported."""
    size_q = _GetInfo(param=_PARAM_SENSORS, buffer_size=0, buffer=0)
    try:
        fcntl.ioctl(fd, DRM_IOCTL_AMDXDNA_GET_INFO, size_q, True)
    except OSError as exc:
        return None, f"ioctl_errno_{exc.errno}"
    need = size_q.buffer_size
    if not need:
        return [], None
    count = need // ctypes.sizeof(_QuerySensor)
    arr = (_QuerySensor * count)()
    fill_q = _GetInfo(
        param=_PARAM_SENSORS,
        buffer_size=need,
        buffer=ctypes.cast(arr, ctypes.c_void_p).value,
    )
    try:
        fcntl.ioctl(fd, DRM_IOCTL_AMDXDNA_GET_INFO, fill_q, True)
    except OSError as exc:
        return None, f"ioctl_errno_{exc.errno}"
    items = []
    for s in arr:
        items.append(
            {
                "label": _cstr(s.label),
                "input": s.input,
                "max": s.max,
                "average": s.average,
                "highest": s.highest,
                "status": _cstr(s.status),
                "units": _cstr(s.units),
                "unitm": s.unitm,
                "type": s.type,
            }
        )
    return items, None


def read_amdxdna_info(bdf: str | None = None) -> dict[str, Any]:
    """Probe the AMDXDNA NPU read-only over DRM ioctls.

    Returns a dict that always has ``available``, ``source`` and ``reason``.
    ``available`` is ``True`` only when an ``amdxdna`` accel node was opened and
    its DRM version read; individual sub-probes (aie version/metadata, clocks,
    firmware, sensors) are best-effort and independently ``None`` when the kernel
    does not support that query. Never raises.
    """
    result: dict[str, Any] = {
        "available": False,
        "source": None,
        "reason": None,
        "node": None,
        "driver": None,
        "aie_version": None,
        "aie": None,
        "clocks": None,
        "firmware_version": None,
        "supports_sensors": False,
        "sensors": {"available": False, "reason": None, "items": []},
    }

    node, reason = _resolve_node(bdf)
    if node is None:
        result["reason"] = reason
        return result

    try:
        fd = os.open(str(node), os.O_RDONLY)
    except OSError as exc:
        result["reason"] = f"node_unreadable_errno_{exc.errno}"
        return result

    try:
        driver = _drm_version(fd)
        if driver is None:
            result["reason"] = "drm_version_failed"
            return result
        if not driver["name"].startswith("amdxdna"):
            # A different accel driver lives here; not our device.
            result["reason"] = "not_amdxdna_driver"
            result["driver"] = driver
            return result

        result["available"] = True
        result["source"] = "amdxdna_ioctl"
        result["node"] = str(node)
        result["driver"] = driver

        aie_ver = _get_info_fixed(fd, _PARAM_AIE_VERSION, _AieVersion)
        if aie_ver is not None:
            result["aie_version"] = f"{aie_ver.major}.{aie_ver.minor}"

        meta = _get_info_fixed(fd, _PARAM_AIE_METADATA, _AieMetadata)
        if meta is not None:
            result["aie"] = {
                "cols": meta.cols,
                "rows": meta.rows,
                "col_size": meta.col_size,
            }

        clocks = _get_info_fixed(fd, _PARAM_CLOCK_METADATA, _ClockMetadata)
        if clocks is not None:
            result["clocks"] = {
                "mp_npu_mhz": clocks.mp_npu_clock.freq_mhz,
                "h_mhz": clocks.h_clock.freq_mhz,
            }

        fw = _get_info_fixed(fd, _PARAM_FIRMWARE_VERSION, _FirmwareVersion)
        if fw is not None:
            result["firmware_version"] = f"{fw.major}.{fw.minor}.{fw.patch}.{fw.build}"

        items, sensor_reason = _query_sensors(fd)
        result["sensors"] = {
            "available": items is not None,
            "reason": sensor_reason,
            "items": items or [],
        }
        result["supports_sensors"] = items is not None
        return result
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
