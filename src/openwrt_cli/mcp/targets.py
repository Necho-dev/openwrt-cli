"""Agent client registry (skills + MCP paths). No FastMCP dependency.

Add a client by appending one ``ClientTarget`` to ``CLIENTS``. Detection,
``openwrt skill``, and ``openwrt mcp path|json`` pick it up automatically.
Relocatable homes use ``home_env`` + ``home_prefix`` (e.g. ``CODEX_HOME``
rewrites paths under ``~/.codex``).
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


SKILL_NAME = "openwrt-ops"
FORBIDDEN_SKILL_ROOTS = frozenset({
    str(Path.home() / ".cursor" / "skills-cursor"),
})


@dataclass(frozen=True)
class ClientTarget:
    """One Agent product. Keep path policy here; do not special-case ids in CLI."""

    id: str
    label: str
    home_marks: tuple[str, ...]
    binaries: tuple[str, ...]
    global_skills: str
    project_skills: str
    mcp_user: str
    mcp_project: str
    mcp_format: str = "json"
    mcp_add: str | None = None
    home_env: str | None = None
    home_prefix: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


CLIENTS: tuple[ClientTarget, ...] = (
    ClientTarget(
        id="cursor",
        label="Cursor",
        home_marks=("~/.cursor",),
        binaries=("cursor",),
        global_skills="~/.cursor/skills",
        project_skills=".cursor/skills",
        mcp_user="~/.cursor/mcp.json",
        mcp_project=".cursor/mcp.json",
    ),
    ClientTarget(
        id="claude-code",
        label="Claude Code",
        home_marks=("~/.claude",),
        binaries=("claude",),
        global_skills="~/.claude/skills",
        project_skills=".claude/skills",
        mcp_user="~/.claude.json",
        mcp_project=".mcp.json",
        mcp_add="claude mcp add openwrt -- {command}",
        home_env="CLAUDE_CONFIG_DIR",
        home_prefix="~/.claude",
        aliases=("claude",),
    ),
    ClientTarget(
        id="codex",
        label="Codex",
        home_marks=("~/.codex",),
        binaries=("codex",),
        global_skills="~/.codex/skills",
        project_skills=".agents/skills",
        mcp_user="~/.codex/config.toml",
        mcp_project=".codex/config.toml",
        mcp_format="toml",
        home_env="CODEX_HOME",
        home_prefix="~/.codex",
    ),
    ClientTarget(
        id="trae",
        label="Trae",
        home_marks=("~/.trae",),
        binaries=("trae",),
        global_skills="~/.trae/skills",
        project_skills=".trae/skills",
        mcp_user="~/.trae/mcp.json",
        mcp_project=".trae/mcp.json",
    ),
    ClientTarget(
        id="windsurf",
        label="Windsurf",
        home_marks=("~/.codeium/windsurf", "~/.windsurf"),
        binaries=("windsurf",),
        global_skills="~/.codeium/windsurf/skills",
        project_skills=".windsurf/skills",
        mcp_user="~/.codeium/windsurf/mcp_config.json",
        mcp_project=".windsurf/mcp_config.json",
        aliases=("codeium",),
    ),
    ClientTarget(
        id="qoder",
        label="Qoder",
        home_marks=("~/.qoder",),
        binaries=("qoder",),
        global_skills="~/.qoder/skills",
        project_skills=".qoder/skills",
        mcp_user="~/.qoder/settings.json",
        mcp_project=".mcp.json",
        home_env="QODER_CONFIG_DIR",
        home_prefix="~/.qoder",
    ),
    ClientTarget(
        id="opencode",
        label="OpenCode",
        home_marks=("~/.config/opencode", "~/.opencode"),
        binaries=("opencode",),
        global_skills="~/.config/opencode/skills",
        project_skills=".opencode/skills",
        mcp_user="~/.config/opencode/opencode.json",
        mcp_project="opencode.json",
        mcp_format="opencode",
        home_env="XDG_CONFIG_HOME",
        home_prefix="~/.config",
        aliases=("open-code",),
    ),
)


def _index() -> dict[str, ClientTarget]:
    out: dict[str, ClientTarget] = {}
    for client in CLIENTS:
        out[client.id] = client
        for alias in client.aliases:
            out[alias] = client
    return out


_BY_ID = _index()


def client_ids() -> tuple[str, ...]:
    return tuple(c.id for c in CLIENTS)


def mcp_client_ids() -> tuple[str, ...]:
    return ("generic", *client_ids())


def expand(path: str, client: ClientTarget | None = None) -> str:
    """Expand ``~`` and optional ``home_env`` rewrite for a client."""
    if client and client.home_env and client.home_prefix:
        prefix = client.home_prefix
        custom = os.environ.get(client.home_env)
        if custom and (path == prefix or path.startswith(f"{prefix}/")):
            rest = path[len(prefix):].lstrip("/")
            base = Path(custom).expanduser()
            return str(base / rest) if rest else str(base)
    if path.startswith("~/"):
        return str(Path.home() / path[2:])
    if path == "~":
        return str(Path.home())
    return path


def client_by_id(name: str) -> ClientTarget | None:
    return _BY_ID.get(name.strip().lower())


def parse_agent_ids(raw: list[str] | None) -> list[str]:
    ids: list[str] = []
    for item in raw or []:
        for part in str(item).split(","):
            name = part.strip().lower()
            if name and name not in ids:
                ids.append(name)
    return ids


def detect_via(client: ClientTarget) -> list[str]:
    """How this client was found: ``home`` and/or ``bin``."""
    via: list[str] = []
    if any(Path(expand(mark, client)).exists() for mark in client.home_marks):
        via.append("home")
    if any(shutil.which(binary) for binary in client.binaries):
        via.append("bin")
    return via


def is_detected(client: ClientTarget) -> bool:
    return bool(detect_via(client))


def detected_clients() -> list[ClientTarget]:
    return [c for c in CLIENTS if is_detected(c)]


def skill_dest(client: ClientTarget, *, project: bool, cwd: Path | None = None) -> Path:
    root = client.project_skills if project else client.global_skills
    if project:
        base = (cwd or Path.cwd()) / root
    else:
        base = Path(expand(root, client))
    return base / SKILL_NAME


def custom_skill_dest(directory: str) -> Path:
    return Path(directory).expanduser().resolve() / SKILL_NAME


def forbidden_skill_path(path: Path) -> bool:
    resolved = path.resolve()
    for raw in FORBIDDEN_SKILL_ROOTS:
        root = Path(raw).resolve()
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
        except OSError:
            if str(resolved).startswith(str(root)):
                return True
    return False


def client_public(client: ClientTarget, *, detected: bool | None = None) -> dict:
    via = detect_via(client)
    found = bool(via) if detected is None else detected
    return {
        "id": client.id,
        "label": client.label,
        "detected": found,
        "via": via,
        "global": expand(client.global_skills, client),
        "project": client.project_skills,
        "mcp_user": expand(client.mcp_user, client),
        "mcp_project": client.mcp_project,
        "mcp_format": client.mcp_format,
    }
