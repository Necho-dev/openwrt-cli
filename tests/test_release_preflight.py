from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "release_preflight.py"


def _load():
    spec = importlib.util.spec_from_file_location("release_preflight", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_changelogs_align_with_pyproject(monkeypatch):
    # Branch pushes set GITHUB_REF_NAME=main; the zh URL then has no version.
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    mod = _load()
    version = mod.pyproject_version()
    mod.require_changelog_alignment(version)
    assert version in (_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert version in (_ROOT / "CHANGELOG.zh.md").read_text(encoding="utf-8")
    notes = mod.release_notes(version)
    body = mod.changelog_notes(version).strip()
    assert body
    assert notes.startswith(body.splitlines()[0])
    assert "CHANGELOG.zh.md" in notes
    assert "/blob/main/CHANGELOG.zh.md" in notes


def test_tag_must_match_pyproject(monkeypatch):
    mod = _load()
    version = mod.pyproject_version()
    monkeypatch.setenv("GITHUB_REF_NAME", f"v{version}")
    assert mod.tag_version() == version
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.0.0-not-a-release")
    assert mod.tag_version() == "0.0.0-not-a-release"


def test_thin_changelog_is_rejected(tmp_path, monkeypatch):
    mod = _load()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [9.9.9] - 2026-01-01\n\n- only one item\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CHANGELOG", changelog)
    with pytest.raises(SystemExit):
        mod.changelog_notes("9.9.9")


def test_missing_changelog_section_is_rejected(tmp_path, monkeypatch):
    mod = _load()
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [1.0.0] - 2026-01-01\n\n- a\n- b\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CHANGELOG", changelog)
    with pytest.raises(SystemExit):
        mod.changelog_notes("9.9.9")


def test_en_zh_version_mismatch_is_rejected(tmp_path, monkeypatch):
    mod = _load()
    en = tmp_path / "CHANGELOG.md"
    zh = tmp_path / "CHANGELOG.zh.md"
    en.write_text("## [1.0.1] - 2026-09-12\n\n- a\n- b\n", encoding="utf-8")
    zh.write_text("## [1.0.0] - 2026-09-12\n\n- a\n- b\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CHANGELOG", en)
    monkeypatch.setattr(mod, "CHANGELOG_ZH", zh)
    with pytest.raises(SystemExit):
        mod.require_changelog_alignment("1.0.1")


def test_zh_notes_required_for_alignment(tmp_path, monkeypatch):
    mod = _load()
    en = tmp_path / "CHANGELOG.md"
    zh = tmp_path / "CHANGELOG.zh.md"
    body = "## [1.0.1] - 2026-09-12\n\n- a\n- b\n"
    en.write_text(body, encoding="utf-8")
    zh.write_text("## [1.0.1] - 2026-09-12\n\n- only one\n", encoding="utf-8")
    monkeypatch.setattr(mod, "CHANGELOG", en)
    monkeypatch.setattr(mod, "CHANGELOG_ZH", zh)
    with pytest.raises(SystemExit):
        mod.require_changelog_alignment("1.0.1")
