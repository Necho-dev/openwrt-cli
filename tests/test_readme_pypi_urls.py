from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "readme_pypi_urls.py"


def _load():
    spec = importlib.util.spec_from_file_location("readme_pypi_urls", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repo_readme_keeps_relative_assets():
    text = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="docs/assets/' in text
    assert 'src="https://raw.githubusercontent.com/' not in text


def test_rewrite_uses_tag_ref(monkeypatch):
    mod = _load()
    monkeypatch.setenv("GITHUB_REPOSITORY", "Necho-dev/openwrt-cli")
    monkeypatch.setenv("GITHUB_REF_NAME", "v1.0.2")
    src = '<img src="docs/assets/cli-banner.gif">'
    out = mod.rewrite(src, mod.image_base_url())
    assert out == (
        '<img src="https://raw.githubusercontent.com/Necho-dev/openwrt-cli/'
        'v1.0.2/docs/assets/cli-banner.gif">'
    )


def test_rewrite_falls_back_to_main(monkeypatch):
    mod = _load()
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    assert mod.image_base_url().endswith("/main/docs/assets/")
