"""Tests for the read-only AMDXDNA DRM IOCTL backend.

The live ioctl parsing is validated on real hardware (see devlog); here we cover
the things that must hold on any machine, including CI with no NPU:

- the request numbers and struct sizes match the kernel uAPI (catch layout drift),
- device resolution never silently reads the wrong device,
- every failure path is non-fatal and machine-readable.
"""

import ctypes
from pathlib import Path
from unittest.mock import patch

from xdna_top import amdxdna_ioctl as ax


# --- request numbers + struct layouts (verified against kernel 6.17 headers) --
def test_request_numbers_match_kernel_headers():
    # _IOWR('d', 0x00, sizeof(drm_version)) and
    # _IOWR('d', DRM_COMMAND_BASE+7, sizeof(amdxdna_drm_get_info)) on amd64.
    assert ax.DRM_IOCTL_VERSION == 0xC0406400
    assert ax.DRM_IOCTL_AMDXDNA_GET_INFO == 0xC0106447


def test_struct_sizes_match_uapi():
    assert ctypes.sizeof(ax._DrmVersion) == 64
    assert ctypes.sizeof(ax._GetInfo) == 16
    assert ctypes.sizeof(ax._AieVersion) == 8
    assert ctypes.sizeof(ax._QueryClock) == 24
    assert ctypes.sizeof(ax._ClockMetadata) == 48
    assert ctypes.sizeof(ax._FirmwareVersion) == 16
    assert ctypes.sizeof(ax._AieMetadata) == 64
    assert ctypes.sizeof(ax._QuerySensor) == 168


def test_cstr_decodes_to_first_nul():
    buf = (ctypes.c_uint8 * 8)(*b"npu\0\0\0\0\0")
    assert ax._cstr(buf) == "npu"
    assert ax._cstr((ctypes.c_uint8 * 4)(0, 0, 0, 0)) is None


# --- device resolution -------------------------------------------------------
@patch("xdna_top.amdxdna_ioctl._accel_nodes", return_value=[])
def test_resolve_no_nodes(_nodes):
    assert ax._resolve_node(None) == (None, "accel_absent")


@patch("xdna_top.amdxdna_ioctl._accel_nodes", return_value=[Path("/dev/accel/accel0")])
def test_resolve_first_when_no_bdf(_nodes):
    node, reason = ax._resolve_node(None)
    assert node == Path("/dev/accel/accel0") and reason is None


@patch("xdna_top.amdxdna_ioctl.os.path.realpath", return_value="/sys/.../0000:99:00.1")
@patch(
    "xdna_top.amdxdna_ioctl._accel_nodes",
    return_value=[Path("/dev/accel/accel0"), Path("/dev/accel/accel1")],
)
def test_resolve_bdf_unmatched_multiple_refuses_to_guess(_nodes, _rp):
    assert ax._resolve_node("0000:c6:00.1") == (None, "bdf_not_matched")


@patch("xdna_top.amdxdna_ioctl.os.path.realpath", return_value="/sys/.../other")
@patch("xdna_top.amdxdna_ioctl._accel_nodes", return_value=[Path("/dev/accel/accel0")])
def test_resolve_bdf_unmatched_single_is_unambiguous(_nodes, _rp):
    node, reason = ax._resolve_node("0000:c6:00.1")
    assert node == Path("/dev/accel/accel0") and reason is None


# --- read_amdxdna_info orchestration ----------------------------------------
@patch("xdna_top.amdxdna_ioctl._resolve_node", return_value=(None, "accel_absent"))
def test_read_unavailable_when_no_node(_resolve):
    info = ax.read_amdxdna_info()
    assert info["available"] is False
    assert info["reason"] == "accel_absent"
    assert info["source"] is None


@patch("xdna_top.amdxdna_ioctl.os.open", side_effect=PermissionError(13, "denied"))
@patch(
    "xdna_top.amdxdna_ioctl._resolve_node",
    return_value=(Path("/dev/accel/accel0"), None),
)
def test_read_node_unreadable(_resolve, _open):
    info = ax.read_amdxdna_info()
    assert info["available"] is False
    assert info["reason"] == "node_unreadable_errno_13"


@patch("xdna_top.amdxdna_ioctl.os.close")
@patch("xdna_top.amdxdna_ioctl.os.open", return_value=999)
@patch(
    "xdna_top.amdxdna_ioctl._drm_version",
    return_value={"name": "i915", "version": "1.6.0", "description": "Intel"},
)
@patch(
    "xdna_top.amdxdna_ioctl._resolve_node",
    return_value=(Path("/dev/accel/accel0"), None),
)
def test_read_rejects_non_amdxdna_driver(_resolve, _ver, _open, _close):
    info = ax.read_amdxdna_info()
    assert info["available"] is False
    assert info["reason"] == "not_amdxdna_driver"
    # driver info still surfaced for debugging
    assert info["driver"]["name"] == "i915"


def _fake_get_info(fd, param, struct_type):
    if param == ax._PARAM_AIE_VERSION:
        return ax._AieVersion(major=1, minor=1)
    if param == ax._PARAM_AIE_METADATA:
        return ax._AieMetadata(col_size=504, cols=8, rows=6)
    if param == ax._PARAM_CLOCK_METADATA:
        m = ax._ClockMetadata()
        m.mp_npu_clock.freq_mhz = 1267
        m.h_clock.freq_mhz = 1800
        return m
    if param == ax._PARAM_FIRMWARE_VERSION:
        return ax._FirmwareVersion(major=1, minor=1, patch=2, build=65)
    return None


@patch("xdna_top.amdxdna_ioctl.os.close")
@patch("xdna_top.amdxdna_ioctl.os.open", return_value=999)
@patch("xdna_top.amdxdna_ioctl._query_sensors", return_value=([], None))
@patch("xdna_top.amdxdna_ioctl._get_info_fixed", side_effect=_fake_get_info)
@patch(
    "xdna_top.amdxdna_ioctl._drm_version",
    return_value={
        "name": "amdxdna_accel_driver",
        "version": "0.6.0",
        "description": "AMD XDNA DRM implementation",
    },
)
@patch(
    "xdna_top.amdxdna_ioctl._resolve_node",
    return_value=(Path("/dev/accel/accel0"), None),
)
def test_read_happy_path(_resolve, _ver, _info, _sensors, _open, _close):
    info = ax.read_amdxdna_info()
    assert info["available"] is True
    assert info["source"] == "amdxdna_ioctl"
    assert info["driver"]["version"] == "0.6.0"
    assert info["aie_version"] == "1.1"
    assert info["aie"] == {"cols": 8, "rows": 6, "col_size": 504}
    assert info["clocks"] == {"mp_npu_mhz": 1267, "h_mhz": 1800}
    assert info["firmware_version"] == "1.1.2.65"
    assert info["supports_sensors"] is True
    assert info["sensors"]["available"] is True


@patch("xdna_top.amdxdna_ioctl.os.close")
@patch("xdna_top.amdxdna_ioctl.os.open", return_value=999)
@patch(
    "xdna_top.amdxdna_ioctl._query_sensors",
    return_value=(None, "ioctl_errno_95"),
)
@patch("xdna_top.amdxdna_ioctl._get_info_fixed", side_effect=_fake_get_info)
@patch(
    "xdna_top.amdxdna_ioctl._drm_version",
    return_value={"name": "amdxdna_accel_driver", "version": "0.6.0", "description": "x"},
)
@patch(
    "xdna_top.amdxdna_ioctl._resolve_node",
    return_value=(Path("/dev/accel/accel0"), None),
)
def test_read_sensors_unsupported_is_honest(_resolve, _ver, _info, _sensors, _open, _close):
    """A kernel that doesn't support the sensors query stays available, but
    reports supports_sensors=False with the errno reason (observed: EOPNOTSUPP)."""
    info = ax.read_amdxdna_info()
    assert info["available"] is True
    assert info["supports_sensors"] is False
    assert info["sensors"]["available"] is False
    assert info["sensors"]["reason"] == "ioctl_errno_95"
