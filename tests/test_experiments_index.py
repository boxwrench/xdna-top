"""Tests for the Evidence Library index generator (tools/build_experiments_index.py)."""

import build_experiments_index as bei


FRONT_MATTER_DOC = """\
---
title: Sample Experiment
date: 2026-06-13
kernel: 6.17.0-35-generic
hardware: AMD RYZEN AI MAX+ 395 (Strix Halo)
xrt_version: 2.21.75
headline: A one-line finding.
artifacts:
  - artifacts/a.snapshot.json
  - artifacts/a.record.jsonl
---

# Body

Some prose.
"""


def test_parse_front_matter_scalars_and_list():
    meta = bei.parse_front_matter(FRONT_MATTER_DOC)
    assert meta is not None
    assert meta["title"] == "Sample Experiment"
    assert meta["date"] == "2026-06-13"
    assert meta["xrt_version"] == "2.21.75"
    assert meta["artifacts"] == ["artifacts/a.snapshot.json", "artifacts/a.record.jsonl"]


def test_parse_front_matter_absent_returns_none():
    assert bei.parse_front_matter("# Just a heading\n\nNo front-matter.\n") is None


def test_load_experiments_skips_index_and_sorts_newest_first(tmp_path):
    exp_dir = tmp_path / "docs" / "experiments"
    exp_dir.mkdir(parents=True)
    (exp_dir / "older.md").write_text(
        FRONT_MATTER_DOC.replace("2026-06-13", "2026-06-01").replace(
            "Sample Experiment", "Older"
        ),
        encoding="utf-8",
    )
    (exp_dir / "newer.md").write_text(
        FRONT_MATTER_DOC.replace("Sample Experiment", "Newer"), encoding="utf-8"
    )
    (exp_dir / "index.md").write_text("# Evidence Library\n", encoding="utf-8")
    (exp_dir / "no_fm.md").write_text("# no front matter\n", encoding="utf-8")

    experiments = bei.load_experiments(exp_dir)
    titles = [e.title for e in experiments]
    assert titles == ["Newer", "Older"]  # index.md and no_fm.md excluded; newest first


def test_render_index_md_contains_entry_and_artifacts():
    exp = bei.Experiment(
        slug="sample",
        title="Sample Experiment",
        date="2026-06-13",
        kernel="6.17.0-35-generic",
        hardware="AMD RYZEN AI MAX+ 395",
        xrt_version="2.21.75",
        headline="A one-line finding.",
        artifacts=["artifacts/a.snapshot.json"],
    )
    md = bei.render_index_md([exp])
    assert "# Evidence Library" in md
    assert "[Sample Experiment](sample.md)" in md
    assert "artifacts/a.snapshot.json" in md


def test_html_injection_is_idempotent():
    html = "<html>\n<body>\n<main>hi</main>\n</body>\n</html>\n"
    section = bei.render_html_section([])
    once = bei.inject_html(html, section)
    twice = bei.inject_html(once, section)
    assert once == twice  # re-injection replaces between markers -> stable
    assert bei.HTML_START in once and bei.HTML_END in once
    assert once.count(bei.HTML_START) == 1


def _write_repo(tmp_path):
    root = tmp_path
    exp_dir = root / "docs" / "experiments"
    exp_dir.mkdir(parents=True)
    (exp_dir / "sample.md").write_text(FRONT_MATTER_DOC, encoding="utf-8")
    (root / "docs" / "index.html").write_text(
        "<html>\n<body>\n<main>dashboard</main>\n</body>\n</html>\n", encoding="utf-8"
    )
    return root


def test_build_is_idempotent_and_check_passes(tmp_path):
    root = _write_repo(tmp_path)
    assert bei.build(root) == 0

    index_md = (root / "docs" / "experiments" / "index.md").read_text(encoding="utf-8")
    html = (root / "docs" / "index.html").read_text(encoding="utf-8")

    # Second build produces no change.
    assert bei.build(root) == 0
    assert (root / "docs" / "experiments" / "index.md").read_text(encoding="utf-8") == index_md
    assert (root / "docs" / "index.html").read_text(encoding="utf-8") == html

    # --check reports up to date (exit 0) on a fresh tree.
    assert bei.build(root, check=True) == 0


def test_check_flags_stale_outputs(tmp_path):
    root = _write_repo(tmp_path)
    # Never built -> index.md missing -> stale.
    assert bei.build(root, check=True) == 1
