"""Tests for the RLIMIT_MEMLOCK mmap-failure diagnostic."""

from __future__ import annotations

import xdna_top.diagnostics as diag

EXAMINE_ERR = (
    "xrt-smi ERROR: mmap(addr=0x7e79cc000000, len=67108864, prot=3, flags=8209, "
    "offset=4294967296) failed (err=-11): Resource temporarily unavailable"
)


def test_signature_matches_real_xrt_smi_error():
    assert diag._looks_like_memlock_mmap(EXAMINE_ERR)


def test_signature_ignores_unrelated_errors():
    assert not diag._looks_like_memlock_mmap("xrt-smi: command not found")
    assert not diag._looks_like_memlock_mmap("Resource temporarily unavailable")  # no mmap


def test_hint_when_signature_and_low_limit(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 8 * 1024 * 1024)
    hint = diag.diagnose_memlock([{"probe": "xrt_smi.examine", "message": EXAMINE_ERR}])
    assert hint is not None
    assert "memlock" in hint.lower()
    assert "8 MiB" in hint


def test_no_hint_when_limit_is_ample(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 1 << 40)  # 1 TiB
    assert diag.diagnose_memlock([{"probe": "x", "message": EXAMINE_ERR}]) is None


def test_no_hint_without_signature(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 8 * 1024 * 1024)
    assert diag.diagnose_memlock([{"probe": "x", "message": "some other failure"}]) is None


def test_no_hint_when_limit_unreadable(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: None)
    assert diag.diagnose_memlock([{"probe": "x", "message": EXAMINE_ERR}]) is None


def test_empty_errors_is_quiet():
    assert diag.diagnose_memlock([]) is None
