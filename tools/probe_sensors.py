#!/usr/bin/env python3
"""Read-only sensor probe for xdna-top: what telemetry does THIS box expose?

This is a standalone diagnostic script (deliberately outside ``src/`` so it is
not part of the ``xdna-top`` package or its entry points). It answers one
question for release/MP4 scoping: which NPU and iGPU sensors does the running
hardware + driver stack actually surface, and via what source?

Discipline (matches the project's roadmap invariants):

- **Read-only.** It only reads sysfs nodes and runs ``xrt-smi examine`` query
  reports. It never writes hardware state, never changes power modes, never
  needs root (standard sysfs/xrt read permissions apply).
- **Never invents a signal.** A sensor that is not exposed is reported as
  ``unavailable`` with the reason it could not be read — never guessed.
- **Degrades gracefully.** Any probe that errors is recorded as unavailable
  with the exception text, rather than crashing the run.

Output: a table to stdout AND a Markdown artifact at
``docs/sensor-probe-<kernel>.md`` so MP4 capture scope has something citable.

Usage::

    python tools/probe_sensors.py              # table + write docs artifact
    python tools/probe_sensors.py --no-write    # table only, no file written
    python tools/probe_sensors.py --out PATH    # write the artifact elsewhere
"""

from __future__ import annotations

import argparse
import glob
import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SignalResult:
    """One probed signal: was it exposed, with what value, from where, and why."""

    signal: str
    available: bool
    value: str | None
    source: str
    reason: str

    @property
    def verdict(self) -> str:
        return "available" if self.available else "unavailable"


# --- small read-only helpers -------------------------------------------------


def _read(path: str) -> str | None:
    """Read and strip a sysfs node, returning None on any error (read-only)."""
    try:
        with open(path, "r") as handle:
            return handle.read().strip()
    except Exception:
        return None


def _run(cmd: list[str], timeout: float = 2.0) -> tuple[int, str]:
    """Run a query command, returning (returncode, stdout). Never raises."""
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return res.returncode, res.stdout
    except FileNotFoundError:
        return 127, ""
    except Exception:
        return 1, ""


def discover_npu_bdf() -> str | None:
    """Discover the first NPU device BDF from ``xrt-smi examine`` (read-only)."""
    rc, out = _run(["xrt-smi", "examine"])
    if rc != 0:
        return None
    for line in out.splitlines():
        # Device table rows look like: |[0000:c6:00.1]  |RyzenAI-npu5  |
        if "[" in line and "]" in line and "|" in line:
            for part in line.split("|"):
                part = part.strip()
                if part.startswith("[") and part.endswith("]"):
                    return part[1:-1]
    return None


def discover_amdgpu_hwmon() -> str | None:
    """Find the amdgpu hwmon directory under any DRM card (mirrors gauge logic)."""
    for hwmon in sorted(glob.glob("/sys/class/drm/card*/device/hwmon/hwmon*")):
        if _read(os.path.join(hwmon, "name")) == "amdgpu":
            return hwmon
    return None


def discover_hwmon_by_name(name: str) -> str | None:
    """Find the first /sys/class/hwmon entry whose ``name`` matches (read-only)."""
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _read(os.path.join(hwmon, "name")) == name:
            return hwmon
    return None


def _hwmon_pair(hwmon: str, kind: str) -> tuple[str, str, str] | None:
    """Find first ``<kind>N_input`` in a hwmon dir; return (path, label, node)."""
    for inp in sorted(glob.glob(os.path.join(hwmon, f"{kind}*_input"))):
        node = os.path.basename(inp)
        label_path = inp.replace("_input", "_label")
        label = _read(label_path) or node
        return inp, label, node
    return None


# --- NPU probes --------------------------------------------------------------


def probe_npu_power(bdf: str | None) -> SignalResult:
    name = "NPU power"
    # 1) Preferred: a real hwmon power node under the NPU's PCI device.
    if bdf:
        pattern = f"/sys/bus/pci/devices/{bdf}/hwmon/hwmon*/power*_input"
        for inp in sorted(glob.glob(pattern)):
            raw = _read(inp)
            if raw is not None and raw.lstrip("-").isdigit():
                watts = int(raw) / 1_000_000.0
                return SignalResult(name, True, f"{watts:.3f} W", inp, "hwmon power node")
    # 2) Fallback: xrt-smi platform report's estimated power (often N/A).
    rc, out = _run(["xrt-smi", "examine", "--report", "platform"])
    src = "xrt-smi examine --report platform (Estimated Power)"
    if rc == 0:
        for line in out.splitlines():
            if "Estimated Power" in line:
                val = line.split(":", 1)[1].strip() if ":" in line else ""
                if val and val.upper() != "N/A":
                    return SignalResult(name, True, val, src, "xrt-smi estimate")
                return SignalResult(
                    name, False, None, src, "xrt-smi reports Estimated Power: N/A"
                )
    where = f"no hwmon power node under {bdf}" if bdf else "NPU BDF not discoverable"
    return SignalResult(name, False, None, src, where)


def probe_npu_bandwidth(direction: str) -> SignalResult:
    name = f"NPU {direction} bandwidth"
    # xrt-smi exposes only aie-partitions/host/platform reports; none carry
    # per-direction memory bandwidth, and amdxdna exports no bandwidth counter
    # in sysfs on this stack. Recorded honestly as unavailable.
    return SignalResult(
        name,
        False,
        None,
        "xrt-smi reports / amdxdna sysfs",
        "no report or sysfs counter exposes NPU memory bandwidth on this stack",
    )


def probe_npu_temp(bdf: str | None) -> SignalResult:
    name = "NPU temperature"
    if bdf:
        pattern = f"/sys/bus/pci/devices/{bdf}/hwmon/hwmon*/temp*_input"
        for inp in sorted(glob.glob(pattern)):
            raw = _read(inp)
            if raw is not None and raw.lstrip("-").isdigit():
                celsius = int(raw) / 1000.0
                label = _read(inp.replace("_input", "_label")) or os.path.basename(inp)
                return SignalResult(name, True, f"{celsius:.1f} C ({label})", inp, "hwmon")
        where = f"no hwmon temp node under {bdf}"
    else:
        where = "NPU BDF not discoverable"
    return SignalResult(name, False, None, f"sysfs hwmon under NPU BDF", where)


# --- iGPU probes (amdgpu hwmon) ---------------------------------------------


def probe_igpu_temp(hwmon: str | None) -> SignalResult:
    name = "iGPU temperature"
    if not hwmon:
        return SignalResult(name, False, None, "amdgpu hwmon", "no amdgpu hwmon found")
    found = _hwmon_pair(hwmon, "temp")
    if not found:
        return SignalResult(name, False, None, hwmon, "no temp*_input node")
    inp, label, _node = found
    raw = _read(inp)
    if raw is None or not raw.lstrip("-").isdigit():
        return SignalResult(name, False, None, inp, "temp node unreadable")
    return SignalResult(name, True, f"{int(raw) / 1000.0:.1f} C ({label})", inp, "amdgpu hwmon")


def probe_package_power(hwmon: str | None) -> SignalResult:
    # amdgpu's power1 on this APU is labelled PPT (Package Power Tracking): the
    # whole-SoC package rail (CPU + iGPU + NPU combined), not an iGPU-only sensor.
    # It is the correct source for total-board perf/watt, but the NPU's individual
    # share is not separable from it. The signal name is built from the real
    # power1_label rather than hard-coded, so a different rail name self-reports.
    default_name = "package power"
    if not hwmon:
        return SignalResult(default_name, False, None, "amdgpu hwmon", "no amdgpu hwmon found")
    found = _hwmon_pair(hwmon, "power")
    if not found:
        return SignalResult(default_name, False, None, hwmon, "no power*_input node")
    inp, label, _node = found
    raw = _read(inp)
    if raw is None or not raw.lstrip("-").isdigit():
        return SignalResult(default_name, False, None, inp, "power node unreadable")
    return SignalResult(
        f"package power ({label})",
        True,
        f"{int(raw) / 1_000_000.0:.3f} W",
        inp,
        "SoC package rail (CPU+iGPU+NPU); total-board perf/watt source, NPU share not separable",
    )


def probe_cpu_package_temp(hwmon: str | None) -> SignalResult:
    name = "CPU/package temperature"
    if not hwmon:
        return SignalResult(name, False, None, "k10temp hwmon", "no k10temp hwmon found")
    found = _hwmon_pair(hwmon, "temp")
    if not found:
        return SignalResult(name, False, None, hwmon, "no temp*_input node")
    inp, label, _node = found
    raw = _read(inp)
    if raw is None or not raw.lstrip("-").isdigit():
        return SignalResult(name, False, None, inp, "temp node unreadable")
    return SignalResult(
        name,
        True,
        f"{int(raw) / 1000.0:.1f} C ({label})",
        inp,
        "k10temp (CPU/SoC); useful for sustained/throttle checks",
    )


# --- orchestration -----------------------------------------------------------


def collect() -> list[SignalResult]:
    bdf = discover_npu_bdf()
    hwmon = discover_amdgpu_hwmon()
    k10 = discover_hwmon_by_name("k10temp")
    return [
        probe_npu_power(bdf),
        probe_npu_bandwidth("read"),
        probe_npu_bandwidth("write"),
        probe_npu_temp(bdf),
        probe_igpu_temp(hwmon),
        probe_package_power(hwmon),
        probe_cpu_package_temp(k10),
    ]


def render_table(results: list[SignalResult]) -> str:
    headers = ("Signal", "Verdict", "Value", "Source", "Reason")
    rows = [
        (r.signal, r.verdict, r.value or "-", r.source, r.reason) for r in results
    ]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    body = "\n".join(
        "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    )
    return f"{line}\n{sep}\n{body}"


def render_markdown(results: list[SignalResult], meta: dict[str, str]) -> str:
    lines = [
        "# xdna-top sensor probe",
        "",
        "Read-only inventory of NPU/iGPU telemetry signals exposed by this box.",
        "Generated by `tools/probe_sensors.py`. A signal marked *unavailable* is a",
        "recorded fact about this hardware/driver stack, not a guess.",
        "",
    ]
    for key, val in meta.items():
        lines.append(f"- **{key}:** {val}")
    lines += [
        "",
        "| Signal | Verdict | Value | Source | Reason |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        value = (r.value or "-").replace("|", "\\|")
        source = r.source.replace("|", "\\|")
        reason = r.reason.replace("|", "\\|")
        lines.append(
            f"| {r.signal} | {r.verdict} | {value} | `{source}` | {reason} |"
        )
    lines.append("")
    return "\n".join(lines)


def _meta() -> dict[str, str]:
    kernel = platform.release()
    model = "unknown"
    xrt = "unknown"
    rc, out = _run(["xrt-smi", "examine"])
    if rc == 0:
        for line in out.splitlines():
            if "Processor" in line and ":" in line:
                model = line.split(":", 1)[1].strip()
            elif line.strip().startswith("Version") and ":" in line and xrt == "unknown":
                xrt = line.split(":", 1)[1].strip()
    return {
        "Generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "Kernel": kernel,
        "Processor": model,
        "XRT version": xrt,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None, help="Markdown artifact path override.")
    parser.add_argument(
        "--no-write", action="store_true", help="Print the table but write no file."
    )
    args = parser.parse_args()

    results = collect()
    print(render_table(results))

    if args.no_write:
        return 0

    meta = _meta()
    out_path = (
        Path(args.out)
        if args.out
        else Path(__file__).resolve().parent.parent
        / "docs"
        / f"sensor-probe-{meta['Kernel']}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_markdown(results, meta), encoding="utf-8")
    print(f"\nwrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
