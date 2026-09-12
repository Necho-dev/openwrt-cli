"""Package version from Poetry (pyproject.toml) or installed metadata."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

REPO_URL = "https://github.com/Necho-dev/openwrt-cli"
# Font Awesome GitHub (U+F09B); shown by Nerd Fonts / many terminal fonts.
GITHUB_ICON = "\uf09b"


def package_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        import tomllib

        found = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {}).get("version")
        if found:
            return str(found)
    try:
        return version("openwrt-cli")
    except PackageNotFoundError:
        return "0.0.0"


def display_version() -> str:
    return f"V{package_version()}"
