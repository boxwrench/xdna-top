"""Environment diagnostics that turn an opaque probe failure into an actionable hint.

Claims-precise by construction: a hint is emitted only when BOTH the failure
signature AND a low ``RLIMIT_MEMLOCK`` are observed, so we never imply a cause we
did not actually detect. When either signal is absent the functions return
``None`` — a missing hint is a result, not a guess.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

try:
    import resource
except ImportError:  # pragma: no cover - non-Unix; xrt-smi is Linux-only anyway
    resource = None  # type: ignore[assignment]

# xrt-smi mmaps the NPU firmware region with MAP_LOCKED. That mmap is checked
# against RLIMIT_MEMLOCK and fails with EAGAIN (errno 11) when the limit is below
# the region size. Match conservatively on the kernel's / xrt-smi's own wording
# so an unrelated mmap failure does not get mislabelled as a memlock problem.
_MMAP_SIGNATURES = ("resource temporarily unavailable", "err=-11", "errno=11")

# Fallback only: used when the failing mmap message carries no parseable
# ``len=``. The NPU firmware region was observed mmap'd at 64 MiB and the
# default desktop RLIMIT_MEMLOCK is 8 MiB, but we prefer the exact size the
# kernel/xrt-smi actually reported.
_MEMLOCK_FALLBACK_BYTES = 64 * 1024 * 1024

# xrt-smi's mmap error carries the requested length, e.g. ``len=67108864``.
_MMAP_LEN_RE = re.compile(r"\blen=(\d+)")


def _looks_like_memlock_mmap(message: str) -> bool:
    """True if ``message`` reads like a MAP_LOCKED mmap rejected for lack of room."""
    text = message.lower()
    if "mmap" not in text:
        return False
    return any(sig in text for sig in _MMAP_SIGNATURES)


def _mmap_len_bytes(message: str) -> int | None:
    """Requested mmap length in bytes parsed from ``len=...``, or ``None``."""
    match = _MMAP_LEN_RE.search(message)
    if not match:
        return None
    try:
        value = int(match.group(1))
    except ValueError:
        return None
    return value if value > 0 else None


def memlock_soft_limit() -> int | None:
    """Current ``RLIMIT_MEMLOCK`` soft limit in bytes, or ``None`` if unavailable."""
    if resource is None:
        return None
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    except (ValueError, OSError, AttributeError):
        return None
    return soft


def diagnose_memlock(errors: Iterable[dict[str, Any]]) -> str | None:
    """Return an actionable hint when a probe error looks like an ``RLIMIT_MEMLOCK``
    mmap failure *and* the current soft limit is below the firmware mmap size.

    ``errors`` is the snapshot's list of ``{"probe", "message"}`` dicts. Returns
    ``None`` unless both the mmap/EAGAIN signature and a low soft limit are
    present, so the caller can surface a precise cause instead of a bare
    "NPU absent". An unlimited or sufficiently large limit yields ``None`` even
    when the signature matches (the failure was something else).
    """
    matched = [
        str(e.get("message") or "")
        for e in errors
        if isinstance(e, dict) and _looks_like_memlock_mmap(str(e.get("message") or ""))
    ]
    if not matched:
        return None

    soft = memlock_soft_limit()
    if soft is None:
        return None
    if resource is not None and soft == resource.RLIM_INFINITY:
        return None

    # Compare against the LARGEST size any failing mmap requested, not a fixed
    # assumption and not just the first match; fall back to the observed 64 MiB
    # only when no matching error carries a parseable ``len=``.
    parsed = [n for n in (_mmap_len_bytes(m) for m in matched) if n is not None]
    required = max(parsed) if parsed else _MEMLOCK_FALLBACK_BYTES
    if soft >= required:
        return None

    soft_mib = soft / (1024 * 1024)
    required_mib = required / (1024 * 1024)
    required_kib = (required + 1023) // 1024
    return (
        f"xrt-smi mmap failed and RLIMIT_MEMLOCK is low ({soft_mib:.0f} MiB soft "
        f"limit vs {required_mib:.0f} MiB requested); the NPU firmware region is "
        f"mapped MAP_LOCKED and needs at least that much locked memory. The NPU "
        f"may be present despite this failure. Raise your user's limit to at "
        f"least {required_kib} KB (e.g. /etc/security/limits.d/xdna.conf: "
        f"'<your-user> - memlock {required_kib}'), or run with elevated "
        f"privileges, then re-probe."
    )
