"""Machine-readable pass/fail checks over xdna-top evidence artifacts.

`xdna-top assert` reads either a `snapshot` JSON object or a `record` JSONL
stream and evaluates named requirements. Each check prints the observed value
next to its requirement, and the process exit code is `0` only when every
requested requirement passes. Checks never convert missing data into guessed
values: an unavailable signal fails its requirement and says so.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xdna_top.record import RECORD_KIND
from xdna_top.snapshot import SNAPSHOT_KIND


@dataclass
class Artifact:
    """A loaded evidence artifact: a snapshot object or a record event stream."""

    kind: str  # "snapshot" or "record"
    snapshot: dict[str, Any] | None
    events: list[Any]

    @property
    def telemetry(self) -> list[dict[str, Any]]:
        return [
            e
            for e in self.events
            if isinstance(e, dict) and e.get("type") == "telemetry"
        ]


@dataclass
class CheckResult:
    name: str
    ok: bool
    observed: str
    requirement: str


def load_artifact(path: str | Path) -> Artifact:
    """Load and classify an evidence artifact from disk."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("artifact is empty")

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None

    if isinstance(obj, dict):
        kind = obj.get("kind")
        if kind == SNAPSHOT_KIND:
            return Artifact(kind="snapshot", snapshot=obj, events=[])
        if kind == RECORD_KIND:
            return Artifact(kind="record", snapshot=None, events=[obj])
        raise ValueError(f"unrecognized artifact kind: {kind!r}")

    events: list[Any] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            events.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {lineno}: {exc}") from exc

    if not events:
        raise ValueError("artifact has no events")
    kinds = {e.get("kind") for e in events if isinstance(e, dict)}
    if RECORD_KIND not in kinds:
        raise ValueError("unrecognized record stream (no xdna-top.record events)")
    return Artifact(kind="record", snapshot=None, events=events)


def evaluate(name: str, artifact: Artifact) -> CheckResult:
    """Run a single named check against an artifact."""
    ok, observed, requirement = _CHECK_FN[name](artifact)
    return CheckResult(name=name, ok=ok, observed=observed, requirement=requirement)


def format_result(result: CheckResult) -> str:
    if result.ok:
        return f"PASS {result.name}: observed {result.observed}"
    return f"FAIL {result.name}: observed {result.observed}, required {result.requirement}"


def assert_main(args: Any) -> int:
    checks = [name for name, dest, _ in CHECKS if getattr(args, dest, False)]
    if not checks:
        print(
            "assert: no requirements specified; pass one or more --require-* flags",
            file=sys.stderr,
        )
        return 2

    try:
        artifact = load_artifact(args.artifact)
    except Exception as exc:
        print(f"assert failed: {exc}", file=sys.stderr)
        return 2

    failed = False
    for name in checks:
        result = evaluate(name, artifact)
        print(format_result(result))
        if not result.ok:
            failed = True
    return 1 if failed else 0


# --- check implementations ---------------------------------------------------

CheckOutcome = tuple[bool, str, str]


def _check_require_npu(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        detected = _get(artifact.snapshot, "devices", "npu", "detected")
        return detected is True, f"devices.npu.detected={_fmt(detected)}", "true"
    tel = artifact.telemetry
    with_ctx = sum(1 for e in tel if e.get("contexts"))
    return (
        with_ctx > 0,
        f"npu_context_samples={with_ctx}/{len(tel)}",
        ">0",
    )


def _check_require_npu_sensors(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        supported = _get(
            artifact.snapshot, "devices", "npu", "driver", "supports_sensors"
        )
        return (
            supported is True,
            f"devices.npu.driver.supports_sensors={_fmt(supported)}",
            "true",
        )
    return (
        False,
        "supports_sensors=unavailable (sensor support is a snapshot field)",
        "snapshot artifact",
    )


def _check_require_context_source(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        source = _get(artifact.snapshot, "backends", "npu", "signals", "contexts")
        return (
            source is not None,
            f"backends.npu.signals.contexts={_fmt(source)}",
            "non-null",
        )
    sources = sorted(
        {
            ctx.get("source")
            for e in artifact.telemetry
            for ctx in (e.get("contexts") or [])
            if isinstance(ctx, dict) and ctx.get("source")
        }
    )
    return (
        bool(sources),
        f"context_sources={','.join(sources) if sources else 'none'}",
        "present",
    )


def _check_require_igpu(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        degraded = _get(artifact.snapshot, "telemetry", "igpu_degraded")
        return degraded is False, f"telemetry.igpu_degraded={_fmt(degraded)}", "false"
    tel = artifact.telemetry
    degraded = sum(
        1 for e in tel if (e.get("reading") or {}).get("igpu_degraded") is True
    )
    return (
        len(tel) > 0 and degraded == 0,
        f"igpu_degraded_samples={degraded}/{len(tel)}",
        "0",
    )


def _check_require_not_degraded(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        overall = _get(artifact.snapshot, "degraded", "overall")
        return overall is False, f"degraded.overall={_fmt(overall)}", "false"
    tel = artifact.telemetry
    degraded = sum(
        1
        for e in tel
        if (e.get("reading") or {}).get("igpu_degraded") is True
        or (e.get("reading") or {}).get("npu_degraded") is True
    )
    return (
        len(tel) > 0 and degraded == 0,
        f"degraded_samples={degraded}/{len(tel)}",
        "0",
    )


def _check_require_npu_activity(artifact: Artifact) -> CheckOutcome:
    if artifact.kind == "snapshot":
        active = _get(artifact.snapshot, "telemetry", "npu_active")
        return active is True, f"telemetry.npu_active={_fmt(active)}", "true"
    max_delta, active_samples, total = _submission_stats(artifact.telemetry)
    return (
        max_delta > 0 or active_samples > 0,
        f"submission_delta={max_delta}, npu_active_samples={active_samples}/{total}",
        ">0",
    )


CHECKS: list[tuple[str, str, Callable[[Artifact], CheckOutcome]]] = [
    ("require-npu", "require_npu", _check_require_npu),
    ("require-npu-sensors", "require_npu_sensors", _check_require_npu_sensors),
    ("require-context-source", "require_context_source", _check_require_context_source),
    ("require-igpu", "require_igpu", _check_require_igpu),
    ("require-not-degraded", "require_not_degraded", _check_require_not_degraded),
    ("require-npu-activity", "require_npu_activity", _check_require_npu_activity),
]

_CHECK_FN = {name: fn for name, _dest, fn in CHECKS}


def _submission_stats(telemetry: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Return (max submission delta across contexts, active samples, total)."""
    per_key: dict[tuple[Any, Any], list[float]] = {}
    active_samples = 0
    for event in telemetry:
        reading = event.get("reading") or {}
        if reading.get("npu_active") is True:
            active_samples += 1
        for ctx in event.get("contexts") or []:
            if not isinstance(ctx, dict):
                continue
            submissions = ctx.get("submissions")
            if not isinstance(submissions, (int, float)) or isinstance(
                submissions, bool
            ):
                continue
            key = (ctx.get("pid"), ctx.get("ctx_id"))
            if key not in per_key:
                per_key[key] = [submissions, submissions]
            else:
                per_key[key][0] = min(per_key[key][0], submissions)
                per_key[key][1] = max(per_key[key][1], submissions)
    max_delta = max((hi - lo for lo, hi in per_key.values()), default=0)
    return int(max_delta), active_samples, len(telemetry)


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
    return str(value)
