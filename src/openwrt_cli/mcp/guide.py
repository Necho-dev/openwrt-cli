"""mcp.json / Agent prompt text. No FastMCP dependency."""

from __future__ import annotations

from openwrt_cli.i18n import t
from openwrt_cli.mcp.extra import mcp_extra_installed, resolve_mcp_launch
from openwrt_cli.mcp.targets import CLIENTS, ClientTarget, client_by_id, expand


def launch_block(command: str | None = None) -> dict:
    return resolve_mcp_launch(command)


def json_snippet(client: str = "generic", command: str | None = None) -> dict:
    launch = launch_block(command)
    entry = {"command": launch["command"]}
    if launch.get("args"):
        entry["args"] = launch["args"]
    target = client_by_id(client)
    fmt = target.mcp_format if target else "json"
    if fmt == "toml":
        args = launch.get("args") or []
        arg_line = f"\nargs = {args!r}" if args else ""
        snippet = f"[mcp_servers.openwrt]\ncommand = {launch['command']!r}{arg_line}\n"
        return {"client": client, "format": "toml", "snippet": snippet, "entry": entry}
    if fmt == "opencode":
        argv = [launch["command"], *(launch.get("args") or [])]
        snippet_obj = {
            "mcp": {
                "openwrt": {
                    "type": "local",
                    "command": argv,
                    "enabled": True,
                }
            }
        }
        return {"client": client, "format": "opencode", "snippet": snippet_obj, "entry": entry}
    snippet_obj = {"mcpServers": {"openwrt": entry}}
    return {"client": client, "format": "json", "snippet": snippet_obj, "entry": entry}


def recommended_paths(client: str | None = None) -> list[dict]:
    picked: list[ClientTarget]
    if client and client != "generic":
        one = client_by_id(client)
        picked = [one] if one else list(CLIENTS)
    else:
        picked = list(CLIENTS)
    rows = []
    for item in picked:
        rows.append({
            "id": item.id,
            "label": item.label,
            "user": expand(item.mcp_user, item),
            "project": item.mcp_project,
            "format": item.mcp_format,
            "add": item.mcp_add.format(command=launch_block()["command"]) if item.mcp_add else None,
        })
    return rows


def agent_prompt(client: str = "generic", command: str | None = None) -> str:
    info = json_snippet(client, command)
    paths = recommended_paths(client)
    extra_ok = mcp_extra_installed()
    lines = [
        t("mcp.prompt.intro"),
        "",
        t("mcp.prompt.extra") if extra_ok else t("mcp.prompt.extra_missing"),
        "",
        t("mcp.prompt.merge"),
        "",
    ]
    if info["format"] == "toml":
        lines.append(str(info["snippet"]).rstrip())
    else:
        import json
        lines.append(json.dumps(info["snippet"], indent=2, ensure_ascii=False))
    lines.extend(["", t("mcp.prompt.paths")])
    for row in paths:
        add = f"  ({row['add']})" if row.get("add") else ""
        lines.append(f"- {row['id']}: {row['user']} / {row['project']}{add}")
    lines.extend([
        "",
        t("mcp.prompt.reload"),
        t("mcp.prompt.secrets"),
        t("mcp.prompt.mode"),
        t("mcp.prompt.forbidden"),
        t("mcp.prompt.no_yes"),
    ])
    return "\n".join(lines)
