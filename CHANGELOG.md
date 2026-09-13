# Changelog

**English** | [简体中文](CHANGELOG.zh.md)

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versioning follows [SemVer](https://semver.org/).

A `vX.Y.Z` tag is published only when **both** this file and
[CHANGELOG.zh.md](CHANGELOG.zh.md) have a matching `## [X.Y.Z]` section.
The English section becomes the GitHub Release body, with a link to the Chinese notes.

## [Unreleased]

## [1.2.0] - 2026-09-13

Feature release **1.2.0**. Agent surface: a packaged skill (`openwrt-ops`), a thin FastMCP server (`openwrt-mcp`), and local install commands. Humans still use `openwrt` / `openwrt tui`. Agents drive the same services through MCP tools. The JSON contract is unchanged (`ok` plus flattened fields). Passwords stay in `~/.openwrt-cli.yaml`; they are never copied into MCP client config.

### Skill (`openwrt-ops`)

Playbook + tool cheat-sheet, shipped in the wheel (`SKILL.md`, `USAGE.md`). Description is English-only so clients match on OpenWrt / LuCI / PassWall2 / outage wording.

`openwrt skill` does not talk to the router:

- `detect` (also the default with no subcommand) — which Agent clients are on this machine, and whether the skill is already installed globally. Columns: Id, Client, Detected, Installed, Via.
- `list` — packaged files plus **user-level** install paths; current-project copies show as yes/no only (no project path).
- `show` — print the packaged `SKILL.md`.
- `install` — TTY wizard: one detected client (or a custom directory), then user-level vs this workspace, then a path check and confirm. `--global` / `--project` are exclusive; `--dir PATH` writes `PATH/openwrt-ops/` and skips scope. Non-interactive / `--yes` defaults to all detected clients, user-level.
- `uninstall` — same targeting as install.

Clients: Cursor, Claude Code, Codex, Trae, Windsurf, Qoder, OpenCode. Detection is home-directory marks and/or binaries on `PATH`. `--dir` is the escape hatch; `~/.cursor/skills-cursor/` is never written.

```bash
openwrt skill detect
openwrt skill install
openwrt skill install --agent cursor --yes
openwrt skill install --dir ~/skills --yes
```

### MCP server

Optional extra `openwrt-cli[mcp]`. Entry points: `openwrt-mcp` and `python -m openwrt_cli.mcp` (stdio). Tools wrap existing services on a shared `DeviceClient`. `openwrt skill` / `openwrt mcp` do not import the server, so they run without the extra.

`openwrt mcp` only prints; it does not write client config files:

- `json` — merge-safe `mcpServers.openwrt` (TOML for Codex, OpenCode’s own shape)
- `prompt` — paste-ready Agent install text
- `path` — recommended user / project config paths

```json
{ "mcpServers": { "openwrt": { "command": "openwrt-mcp" } } }
```

```bash
openwrt mcp json --client cursor
claude mcp add openwrt -- openwrt-mcp
```

Read tools include `doctor`, `system_status`, `network_*`, `firewall_view`, `qos_view`, `service_*`, `passwall2_*` (node list without Ping), `logs_read`, `config_show` (password masked). Write tools (`wifi_set`, `lan_set`, PassWall2 node/ACL, `service_action`, `backup_create`, `user_key_add`, …) carry `destructiveHint`. `passwall2_nodes` does not ping. `backup_create` writes under `/tmp` on the router (basename only).

### Permissions

`mcp.mode` in `~/.openwrt-cli.yaml` (also `openwrt config set --mcp-mode`). Default **readonly**: read tools work; write tools return `mcp_readonly` and do not touch the router. **readwrite** runs writes only after the user confirms in the client (or a clear yes in chat). `full` is rejected at config set and at MCP startup (exit 2). There is no `OPENWRT_MCP_MODE` env override.

The guard applies to the MCP process only. Human CLI is unchanged. When MCP is connected, Agents must not mutate with `openwrt … --yes` (that skips `mcp.mode`).

Never registered as MCP tools: reboot, shutdown, backup restore, user add / passwd / delete. Those stay human CLI (`openwrt system reboot --yes`).

### Install, setup, docs

- `install.sh` / `install.bat` install `openwrt-cli[mcp]`, list skill / MCP next steps, and can run `openwrt setup` then `openwrt skill install`.
- `openwrt setup` still only configures language and the router. After a successful probe it shows `mcp.mode` and next commands: `doctor`, `skill install`, `mcp json`, `tui`.
- README **Drop it into your AI agent** / **给 Agent 使用**: three-step wiring, client snippets, what tools exist, and a paste-ready Agent prompt (`openwrt mcp prompt` is the client-specific variant).

### Changed

- `config show` masks passwords as `********` (was `***`, which some terminals treat as markup).
- Skill install wizard is a single-select like `openwrt setup` (highlight = the one client that will be installed). Multi-client install stays `--agent` / `--yes`.

## [1.1.1] - 2026-09-13

Patch for PassWall2 TUI writes: restart could succeed while UCI stayed unchanged, and confirm text could hide the service name.

### Fixed

- ACL edit writes only fields you changed. Empty untouched port or node Selects no longer clear UCI back to “use global”.
- HTTP `uci.set` / `commit` stay on one rpcd session, send the section type, and re-read after write. A silent no-op no longer offers restart.
- Confirm dialogs no longer swallow bracketed names (`[passwall2]`, ACL ids) as Rich markup.
- Saving a node or ACL asks twice: save to UCI, then whether to restart now. Cancel restart shows “saved, pending”; press `t` later to apply.

## [1.1.0] - 2026-09-13

Feature release **1.1.0**. Optional **PassWall2** and **Bandix hostname** become first-class on CLI and TUI. The Agent JSON contract is unchanged: success and failure are one object with `ok`, and service fields are flattened into that object.

Tab `7`, PassWall2 commands, and Bandix rename appear only when the matching LuCI app is on the router. Missing capability still fails loudly (no fake rows). Writes go through **UCI / ubus**, not the LuCI CBI Save form. Session cookies and tokens are never printed in success JSON or logs.

### PassWall2 (`luci-app-passwall2`)

Read (HTTP or SSH):

- `passwall2 status` — current node, ACL switch, running state
- `passwall2 nodes` / `node show` / `node ping` — list (Ping + TCPing on enter/refresh), one node, ICMP / `--tcp` TCPing
- `passwall2 subscribe` / `settings` / `rules` / `components` / `components check`
- `passwall2 acl` / `acl show` / `acl log` — `acl_rule` only; log supports `--tail` / `--since` / `--until`
- `passwall2 logs` — runtime log with the same time window flags

Write (same UCI keys the LuCI app uses):

- **Nodes** — `node add` / `node set` / `node delete`. Add from a share URL (`--from-url`, vless / vmess / trojan / ss / hysteria2) or fields (`--type` `--protocol` `--remarks` `--group` `--address` `--port` `--username` `--password` `--uuid`). Extra UCI options: `--set key=value` (repeatable), `--unset` (set/delete), `--raw` (unknown keys). Delete asks again if the node is referenced; `--force` skips that guard.
- **ACL** — `acl add` / `acl set` / `acl delete`, plus `acl source add` / `acl source remove` (only the `sources` list). First-class flags: `--remarks` `--sources` `--node` `--enabled` `--log`. Ports, interface, and DNS go through `--set`. Empty ports or an empty node means “use global”; `disable` means unused. Display strings such as `Use global config (...)` are never written back.

`--apply` commits and restarts PassWall2 so the change takes effect. A restart failure does **not** roll back the UCI commit. rpcd write denial uses a dedicated `pw2_write_denied` error.

Out of scope in 1.1.0: subscribe refresh / pull, `clear_log`, and component install or upgrade.

```bash
openwrt passwall2 nodes
openwrt passwall2 node add --from-url 'vless://...' --apply --yes
openwrt passwall2 node set cfgxxx --remarks 'HK' --set group=office --yes
openwrt passwall2 acl add --remarks 'iot' --sources 192.168.9.10 --node '' --yes
openwrt passwall2 acl source add cfgacl1 192.168.9.11 --yes
```

### Bandix neighbor hostname (`luci-app-bandix`)

Rename is the Bandix **custom name** on a MAC, not OpenWrt `system.@system[0].hostname` and not DHCP/ARP hostname.

- CLI: `openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name phone --yes`
- Empty `--name` clears the binding.
- Payload is only `mac` + `hostname`. Rate-limit APIs (`setRateLimit` and similar) are not touched.
- TUI Neighbors (Bandix overlay): `e Edit` opens the rename dialog; `r` stays Refresh so the two keys do not collide.

```bash
openwrt network neighbors
openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name phone --yes
openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name '' --yes
```

### TUI

- Tab `7` **PassWall2** is added only after probe finds `luci-app-passwall2`. Sub-pages: Nodes, Subscribe, Settings, Rules, ACL, Logs (`[` / `]` to switch).
- Nodes / ACL: `a` add, `e` edit, `Del` delete, `p` Ping, `c` TCPing, `l` ACL log. Forms write the same UCI fields as the CLI. Node form: remarks / group / type / protocol / address / port / username / password, plus share URL on add.
- Neighbors: `e Edit` only in Bandix mode. The footer shows `e Edit` on that page only; other tabs stay `q` / `r` / `f` / `?`.
- In-page shortcut lines (node/ACL detail, service actions, confirm/rename/form hints) highlight the key the same way as the footer.

### Fixed

- After `f` filter, `e` / `a` / `Del` work again once the table has focus. Those keys stay characters only while the filter input is focused.

## [1.0.2] - 2026-09-13

Packaging fix for PyPI. Version **1.0.1** was uploaded then deleted; PyPI never reuses a filename (`openwrt_cli-1.0.1-py3-none-any.whl`), so this release is **1.0.2**. Product features are the same as 1.0.1.

- Wheel README rewrites `docs/assets/` image URLs to GitHub raw links at publish time so the [PyPI page](https://pypi.org/project/openwrt-cli/) can show screenshots. The repository README stays on relative paths.
- Install: `pipx install openwrt-cli` (requires Python >= 3.12).

## [1.0.1] - 2026-09-12

First public release of **OpenWrt CLI**. There is no prior 1.0.0 on this repository — `1.0.1` is the initial tagged package.

OpenWrt CLI (`openwrt`) is a remote admin tool for OpenWrt routers. One service layer drives three surfaces: CLI tables (typer + rich), setup/wizard (questionary), and a full-screen TUI (textual). The same device model speaks **SSH** or **LuCI/ubus HTTP**; missing capabilities fail loudly instead of inventing data.

The command is **`openwrt`**. `openwrt-cli` is installed as a compatibility alias; docs and `--help` always say `openwrt`.

### Highlights

- **Three surfaces** — `openwrt doctor` / `network` / `system` tables, `openwrt setup` + `wizard`, and `openwrt tui`
- **One device model** — SSH and HTTP share ubus / uci / shell semantics
- **doctor** — capability-aware health checks, structured, `-f json` ready
- **Network** — interfaces, routes, policy rules, IPv4 neighbors, DHCP leases with MAC vendors, optional Bandix history (`luci-app-bandix`)
- **Agent-ready** — `-f json` / `-f compact` / `--json`; success and failure are one object with `ok`
- **English / 简体中文 UI** — command names stay English; language from `-L` / `OPENWRT_LANG` / config / locale

### Command groups

- **doctor** — full or `--quick` check
- **system** — status, info, board, cpu, memory, processes, disk, temperature, uptime, hostname; reboot / shutdown (need `--yes` off-TTY)
- **logs** — system (logd) or kernel (dmesg); `--tail`, `--follow` / `-f`, `--since` / `--until`
- **network** — interfaces (`--rates`), routes, rules, dns, dhcp, leases, neighbors, stats, traffic, wifi list/set, lan show/set, reload; `metrics` for Bandix history
- **firewall** — zones, rules, nat, redirects, status (iptables paths need SSH)
- **qos** — OpenWrt SQM / `tc` (`luci-app-sqm`), not Bandix per-device limits
- **service** — list / show / start / stop / restart / reload / enable / disable (`/etc/init.d`, including luci-app)
- **user** — list, groups, add, passwd, delete; `user key` list/add
- **backup** — create, restore, list
- **config** — local `~/.openwrt-cli.yaml` show / path / set
- **setup / wizard / tui** — first-run connection wizard; on-device wifi / lan / hostname / user / service; live dashboard (`1`–`6` tabs, `r` refresh, `f` filter, `q` quit)

Global flags may sit before or after a subcommand (`openwrt network leases -f json`). Interactive commands (`setup`, `tui`, `wizard`) refuse JSON (`error: interactive`). Destructive actions prompt on a TTY; in a pipe or JSON mode they need `--yes` or they exit `2`.

### Install

Requires **Python >= 3.12**.

```bash
curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh | bash
openwrt setup
openwrt doctor
```

```bash
pipx install git+https://github.com/Necho-dev/openwrt-cli.git
# after this release is on PyPI: pipx install openwrt-cli
```

Windows: clone and run `install.bat`, or `pip install git+https://github.com/Necho-dev/openwrt-cli.git`.

### Requirements on the router

- SSH (dropbear/openssh) and/or LuCI with ubus HTTP(S)
- Optional: `luci-app-bandix` for per-device rates and `network metrics`
- Optional: `luci-app-sqm` / `sqm-scripts` for `qos`

### Acknowledgments

Built on the official OpenWrt stack: [openwrt/openwrt](https://github.com/openwrt/openwrt), [openwrt/luci](https://github.com/openwrt/luci), [openwrt/uci](https://github.com/openwrt/uci). Inspired by [a6726170/openwrt-cli](https://github.com/a6726170/openwrt-cli).

License: MIT.
