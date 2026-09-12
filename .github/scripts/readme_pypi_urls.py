#!/usr/bin/env python3
"""Rewrite relative docs/assets/ URLs in README.md so PyPI can fetch images."""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
DEFAULT_REPO = "Necho-dev/openwrt-cli"
REL_SRC = 'src="docs/assets/'
REL_MD = "](docs/assets/"


def image_base_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    if not re.fullmatch(r"v?\d+\.\d+\.\d+.*", ref):
        ref = "main"
    return f"https://raw.githubusercontent.com/{repo}/{ref}/docs/assets/"


def rewrite(text: str, base: str) -> str:
    text = text.replace(REL_SRC, f'src="{base}')
    return text.replace(REL_MD, f"]({base}")


def main() -> None:
    original = README.read_text(encoding="utf-8")
    base = image_base_url()
    updated = rewrite(original, base)
    if updated == original:
        if "docs/assets/" not in original:
            print(f"{README.name}: no relative asset URLs to rewrite")
            return
        raise SystemExit(f"{README.name}: expected relative docs/assets/ image URLs")
    README.write_text(updated, encoding="utf-8")
    print(f"{README.name}: rewrote asset URLs → {base}")


if __name__ == "__main__":
    main()
