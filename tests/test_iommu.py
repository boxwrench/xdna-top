"""Tests for the read-only IOMMU command-line / sysfs observation.

These are pure, off-hardware tests: the fixtures simulate ``/proc/cmdline`` and
the PCI ``iommu_group`` link so every branch is exercised without a real NPU.
"""

from pathlib import Path

from xdna_top import iommu


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


# --- token parsing / mode classification ------------------------------------
def test_parse_iommu_tokens_keeps_only_iommu_tokens():
    cmdline = "BOOT_IMAGE=/vmlinuz root=UUID=x amd_iommu=off iommu=pt iommu.passthrough=0 quiet"
    assert iommu.parse_iommu_tokens(cmdline) == [
        "amd_iommu=off",
        "iommu=pt",
        "iommu.passthrough=0",
    ]


def test_parse_iommu_tokens_none_and_unreadable():
    assert iommu.parse_iommu_tokens("quiet splash") == []
    assert iommu.parse_iommu_tokens(None) == []
    assert iommu.parse_iommu_tokens("") == []


def test_classify_iommu_mode_variants():
    assert iommu.classify_iommu_mode([]) == "none"
    assert iommu.classify_iommu_mode(["amd_iommu=off"]) == "off"
    assert iommu.classify_iommu_mode(["iommu=off"]) == "off"
    assert iommu.classify_iommu_mode(["iommu=pt"]) == "pt"
    assert iommu.classify_iommu_mode(["amd_iommu=pt"]) == "pt"
    assert iommu.classify_iommu_mode(["iommu.passthrough=1"]) == "passthrough"
    assert iommu.classify_iommu_mode(["amd_iommu=on"]) == "on"
    assert iommu.classify_iommu_mode(["intel_iommu=strict"]) == "other"


# --- observation ------------------------------------------------------------
def test_observe_iommu_off_without_group(tmp_path):
    cmdline = tmp_path / "cmdline"
    write(cmdline, "root=x amd_iommu=off quiet")
    obs = iommu.observe_iommu(
        "0000:c6:00.1",
        cmdline_path=cmdline,
        pci_devices=tmp_path / "devices",
    )
    assert obs["cmdline"] == {
        "readable": True,
        "tokens": ["amd_iommu=off"],
        "mode": "off",
    }
    assert obs["npu_iommu_group"]["exists"] is False
    assert obs["npu_iommu_group"]["bdf"] == "0000:c6:00.1"
    assert iommu.iommu_blocks_sva(obs) is True


def test_observe_iommu_pt_with_group(tmp_path):
    cmdline = tmp_path / "cmdline"
    write(cmdline, "root=x iommu=pt")
    group = tmp_path / "devices" / "0000:c6:00.1" / "iommu_group"
    group.mkdir(parents=True)
    obs = iommu.observe_iommu(
        "0000:c6:00.1",
        cmdline_path=cmdline,
        pci_devices=tmp_path / "devices",
    )
    assert obs["cmdline"]["mode"] == "pt"
    assert obs["npu_iommu_group"]["exists"] is True
    assert iommu.iommu_blocks_sva(obs) is False


def test_observe_iommu_none_tokens_group_absent_is_evidence(tmp_path):
    cmdline = tmp_path / "cmdline"
    write(cmdline, "root=x quiet")
    obs = iommu.observe_iommu(
        "0000:c6:00.1",
        cmdline_path=cmdline,
        pci_devices=tmp_path / "devices",
    )
    assert obs["cmdline"]["mode"] == "none"
    # No cmdline token, but the probed NPU has no IOMMU group: still evidence.
    assert iommu.iommu_blocks_sva(obs) is True


def test_observe_iommu_unreadable_cmdline_with_group_is_not_evidence(tmp_path):
    group = tmp_path / "devices" / "0000:c6:00.1" / "iommu_group"
    group.mkdir(parents=True)
    obs = iommu.observe_iommu(
        "0000:c6:00.1",
        cmdline_path=tmp_path / "missing",
        pci_devices=tmp_path / "devices",
    )
    assert obs["cmdline"] == {"readable": False, "tokens": [], "mode": "unknown"}
    assert iommu.iommu_blocks_sva(obs) is False


def test_observe_iommu_unreadable_cmdline_but_group_absent_is_evidence(tmp_path):
    # No cmdline evidence, but the probed NPU genuinely has no IOMMU group.
    obs = iommu.observe_iommu(
        "0000:c6:00.1",
        cmdline_path=tmp_path / "missing",
        pci_devices=tmp_path / "devices",
    )
    assert iommu.iommu_blocks_sva(obs) is True


def test_observe_iommu_without_bdf_does_not_claim_group():
    obs = iommu.observe_iommu(None)
    assert obs["npu_iommu_group"] == {"bdf": None, "path": None, "exists": None}
    assert iommu.iommu_blocks_sva(obs) is False


def test_iommu_blocks_sva_tolerates_missing_observation():
    assert iommu.iommu_blocks_sva(None) is False
    assert iommu.iommu_blocks_sva({}) is False
