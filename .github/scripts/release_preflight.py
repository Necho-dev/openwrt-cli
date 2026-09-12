#!/usr/bin/env python3
"""Preflight for tag releases: pyproject, PyPI, and bilingual CHANGELOG notes."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
CHANGELOG_ZH = ROOT / "CHANGELOG.zh.md"
PYPROJECT = ROOT / "pyproject.toml"
NOTES = ROOT / "release-notes.md"
PROJECT = "openwrt-cli"
USER_AGENT = "openwrt-cli-release-preflight"
VERSION_HEADING = re.compile(r"^##[ \t]+\[v?([0-9][^]\s]*)\]", re.MULTILINE)


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    sys.exit(1)


def pyproject_version() -> str:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    found = data.get("project", {}).get("version")
    if not found:
        fail("pyproject.toml is missing [project].version")
    return str(found)


def tag_version() -> str:
    raw = os.environ.get("GITHUB_REF_NAME") or ""
    if not raw:
        fail("GITHUB_REF_NAME is not set")
    return raw[1:] if raw.startswith("v") else raw


def changelog_versions(path: Path) -> list[str]:
    if not path.is_file():
        fail(f"{path.name} is missing")
    return VERSION_HEADING.findall(path.read_text(encoding="utf-8"))


def changelog_notes(version: str, path: Path | None = None) -> str:
    target = path or CHANGELOG
    if not target.is_file():
        fail(f"{target.name} is missing; add a ## [{version}] section before tagging")
    text = target.read_text(encoding="utf-8")
    pattern = rf"^##[ \t]+(?:\[)?v?{re.escape(version)}(?:\])?[^\n]*\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, flags=re.MULTILINE | re.DOTALL)
    if not match:
        fail(
            f"{target.name} has no '## [{version}]' section. "
            "Write complete release notes before tagging."
        )
    body = match.group(1).strip()
    items = [line for line in body.splitlines() if line.strip().startswith(("-", "*"))]
    if not body or len(items) < 2:
        fail(
            f"{target.name} section for {version} is too thin. "
            "Add clear notes (at least two bullet points) before tagging."
        )
    return body + "\n"


def require_changelog_alignment(version: str) -> None:
    en_versions = changelog_versions(CHANGELOG)
    zh_versions = changelog_versions(CHANGELOG_ZH)
    if en_versions != zh_versions:
        fail(
            "CHANGELOG.md and CHANGELOG.zh.md version headings do not match: "
            f"en={en_versions} zh={zh_versions}"
        )
    if version not in en_versions:
        fail(f"CHANGELOG.md / CHANGELOG.zh.md are missing ## [{version}]")
    changelog_notes(version, CHANGELOG)
    changelog_notes(version, CHANGELOG_ZH)


def zh_changelog_url(version: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "Necho-dev/openwrt-cli")
    ref = os.environ.get("GITHUB_REF_NAME") or f"v{version}"
    return f"https://github.com/{repo}/blob/{ref}/CHANGELOG.zh.md"


def release_notes(version: str) -> str:
    body = changelog_notes(version, CHANGELOG).rstrip()
    url = zh_changelog_url(version)
    return (
        f"{body}\n\n"
        f"---\n\n"
        f"简体中文说明见 [CHANGELOG.zh.md]({url})。\n"
    )


def pypi_has_version(version: str) -> None:
    url = f"https://pypi.org/pypi/{PROJECT}/{version}/json"
    request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            if response.status == 200:
                fail(
                    f"{PROJECT} {version} is already on PyPI "
                    f"(https://pypi.org/project/{PROJECT}/{version}/). "
                    "Bump the version; do not republish."
                )
            fail(f"Unexpected PyPI response HTTP {response.status} for {url}")
    except HTTPError as exc:
        if exc.code == 404:
            return
        fail(f"PyPI version check failed: HTTP {exc.code} for {url}")
    except URLError as exc:
        fail(f"PyPI version check failed: {exc}")


def write_output(version: str) -> None:
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return
    with open(github_output, "a", encoding="utf-8") as handle:
        handle.write(f"version={version}\n")


def main() -> None:
    version = pyproject_version()
    tag = tag_version()
    if tag != version:
        ref = os.environ.get("GITHUB_REF_NAME", "")
        fail(f"Tag '{ref}' (version {tag}) does not match pyproject.toml ({version})")
    require_changelog_alignment(version)
    pypi_has_version(version)
    NOTES.write_text(release_notes(version), encoding="utf-8")
    write_output(version)
    print(f"Preflight OK: {PROJECT} {version} (tag v{version})")
    print("--- release notes ---")
    print(NOTES.read_text(encoding="utf-8"), end="")


if __name__ == "__main__":
    main()
