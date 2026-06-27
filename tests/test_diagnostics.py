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


SMALL_MMAP_ERR = (
    "xrt-smi ERROR: mmap(addr=0x1, len=16777216, prot=3, flags=8209) "
    "failed (err=-11): Resource temporarily unavailable"
)


def test_required_size_tracks_parsed_len(monkeypatch):
    # 32 MiB soft limit is BELOW the 64 MiB fallback but ABOVE the 16 MiB this
    # error actually requested, so parsing len= must suppress the hint.
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 32 * 1024 * 1024)
    assert diag.diagnose_memlock([{"probe": "x", "message": SMALL_MMAP_ERR}]) is None
    # same soft limit, 64 MiB requested -> hint
    assert diag.diagnose_memlock([{"probe": "x", "message": EXAMINE_ERR}]) is not None


def test_hint_reports_requested_size_and_narrow_remediation(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 8 * 1024 * 1024)
    hint = diag.diagnose_memlock([{"probe": "x", "message": EXAMINE_ERR}])
    assert "64 MiB requested" in hint
    assert "65536" in hint            # finite per-user KB suggestion sized to the mmap
    assert "@render" not in hint      # no group-wide unlimited grant
    assert "unlimited" not in hint


def test_uses_largest_requested_len_across_errors(monkeypatch):
    # 32 MiB soft covers the 16 MiB request but not the 64 MiB one; the diagnosis
    # must consider the largest failing mmap, not just the first in the list.
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 32 * 1024 * 1024)
    errs = [
        {"probe": "a", "message": SMALL_MMAP_ERR},  # 16 MiB
        {"probe": "b", "message": EXAMINE_ERR},      # 64 MiB
    ]
    assert diag.diagnose_memlock(errs) is not None


def test_unparsable_len_falls_back(monkeypatch):
    monkeypatch.setattr(diag, "memlock_soft_limit", lambda: 8 * 1024 * 1024)
    no_len = "xrt-smi ERROR: mmap failed (err=-11): Resource temporarily unavailable"
    assert diag.diagnose_memlock([{"probe": "x", "message": no_len}]) is not None
