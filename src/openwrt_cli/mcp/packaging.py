"""Packaged Skill files (importlib.resources). No FastMCP dependency."""

from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path

from openwrt_cli.mcp.targets import SKILL_NAME


def skill_source_dir() -> Path:
    root = files("openwrt_cli.mcp") / "skills" / SKILL_NAME
    return Path(str(root))


def skill_source_files() -> list[Path]:
    src = skill_source_dir()
    return sorted(p for p in src.rglob("*") if p.is_file())


def copy_skill(dest: Path) -> str:
    """Copy packaged skill into dest. Returns unchanged / updated / created."""
    src = skill_source_dir()
    dest.mkdir(parents=True, exist_ok=True)
    changed = False
    existed = any(dest.iterdir()) if dest.exists() else False
    for item in skill_source_files():
        rel = item.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == item.read_bytes():
            continue
        shutil.copy2(item, target)
        changed = True
    if not existed:
        return "created"
    return "updated" if changed else "unchanged"


def remove_skill(dest: Path) -> bool:
    if not dest.exists():
        return False
    shutil.rmtree(dest)
    return True


def skill_installed(dest: Path) -> bool:
    return (dest / "SKILL.md").is_file()


def skill_text() -> str:
    return (skill_source_dir() / "SKILL.md").read_text(encoding="utf-8")


def skill_manifest() -> dict:
    src = skill_source_dir()
    return {
        "name": SKILL_NAME,
        "path": str(src / "SKILL.md"),
        "files": [p.name for p in skill_source_files()],
    }
