# OpenWrt CLI

**English** | [简体中文](README.zh.md)

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![Unit Test](https://github.com/Necho-dev/openwrt-cli/actions/workflows/unit-test.yml/badge.svg)](https://github.com/Necho-dev/openwrt-cli/actions/workflows/unit-test.yml)
[![Publish](https://img.shields.io/github/v/release/Necho-dev/openwrt-cli?label=Publish)](https://github.com/Necho-dev/openwrt-cli/releases)
[![PyPI](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fopenwrt-cli%2Fjson&query=%24.info.version&label=PyPI&prefix=v)](https://pypi.org/project/openwrt-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-Necho--dev%2Fopenwrt--cli-181717.svg)](https://github.com/Necho-dev/openwrt-cli)

Remote OpenWrt admin over **SSH** or **LuCI/ubus HTTP**. The same services power CLI tables (typer + rich), the setup/wizard flows (questionary), and the full-screen TUI (textual).

The command is **`openwrt`**. `openwrt-cli` is still installed as a compatibility alias; docs and `--help` always say `openwrt`.

<p align="center">
  <img src="docs/assets/cli-banner.gif" alt="openwrt setup / tui / network interfaces / neighbors / leases" width="2000">
</p>

<p align="center">
  <img src="docs/assets/tui-overview.png" alt="TUI Overview — load, bandwidth, and connections" width="2000">
</p>

<table>
  <tr>
    <td align="center" valign="top" width="33%">
      <p><strong>Network</strong></p>
      <img src="docs/assets/tui-network.png" alt="TUI Network" width="2000">
    </td>
    <td align="center" valign="top" width="33%">
      <p><strong>Neighbors</strong></p>
      <img src="docs/assets/tui-neighbors.png" alt="TUI Neighbors" width="2000">
    </td>
    <td align="center" valign="top" width="33%">
      <p><strong>PassWall2*</strong></p>
      <img src="docs/assets/tui-passwall2.png" alt="TUI PassWall2 — node table, Ping / TCPing, add / edit / delete" width="2000">
    </td>
  </tr>
</table>

<table>
  <tr>
    <td align="center" valign="top" width="33%">
      <p><strong>Services</strong></p>
      <img src="docs/assets/tui-services.png" alt="TUI Services" width="2000">
    </td>
    <td align="center" valign="top" width="33%">
      <p><strong>Process</strong></p>
      <img src="docs/assets/tui-process.png" alt="TUI Process" width="2000">
    </td>
    <td align="center" valign="top" width="33%">
      <p><strong>Logs</strong></p>
      <img src="docs/assets/tui-logs.png" alt="TUI Logs" width="2000">
    </td>
  </tr>
</table>

\* PassWall2 requires **openwrt-cli &gt;= 1.1.0** and `luci-app-passwall2` on the router.

## Features

- **Three surfaces** — CLI tables, `setup` + `wizard`, and `openwrt tui`
- **doctor** — capability-aware SSH/HTTP health checks, structured, `-f json` ready
- **Network** — interfaces, routes, rules, neighbors, active DHCP leases, IPv4 DHCP reservations, optional Bandix history
- **Firewall** — list, create, and update DNAT port-forwarding rules
- **PassWall2** — optional `luci-app-passwall2`; **requires openwrt-cli &gt;= 1.1.0**. Read status / nodes / ACL / logs; add, edit, delete nodes and ACL; TUI tab `7` when the package is present
- **One device model** — SSH and HTTP share ubus / uci / shell semantics; missing capability fails loudly (no fake data)
- **Agent-ready** — `-f json` / `-f compact`, no TTY or color required
- **Profiles** — several router logins in one file. `openwrt profiles` manages them; the TUI host label switches the active one
- **TUI Settings** — manage language, MCP permissions, and profiles. Set up SQLite sampling and view local runtime logs.
- **MCP / Skills** — wire Cursor / Claude Code / Codex in [Drop it into your AI agent](#drop-it-into-your-ai-agent). A tool can name a profile for that call. Default `mcp.mode` is **readonly**
- **English / 简体中文 UI** — command names stay English

## Quick Start

```bash
curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh | bash
openwrt setup
openwrt doctor
openwrt tui
```

Using this from Cursor, Claude Code, or another Agent? Jump to [Drop it into your AI agent](#drop-it-into-your-ai-agent).

Non-interactive equivalent:

```bash
openwrt -H 192.168.1.1 -u root --password your_password --save-config
```

<p align="center">
  <img src="docs/assets/cli-setup.png" alt="openwrt setup — language and connection wizard" width="2000">
</p>

## Installation

Requires **Python >= 3.12**.

**Linux / macOS**

```bash
curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh | bash
```

**Windows** — clone, then run `install.bat`, or:

```cmd
pip install "openwrt-cli[mcp] @ git+https://github.com/Necho-dev/openwrt-cli.git"
```

**pip / pipx**

```bash
pipx install "openwrt-cli[mcp] @ git+https://github.com/Necho-dev/openwrt-cli.git"
```

**Development (Poetry)**

```bash
git clone https://github.com/Necho-dev/openwrt-cli.git
cd openwrt-cli
poetry install
poetry run openwrt --help
```

```bash
poetry install --with dev
poetry run pytest -m "not live"           # no device, CI-safe
OPENWRT_LIVE=1 poetry run pytest -m live  # read-only against ~/.openwrt/config.yaml
```

Destructive commands (reboot, reload, service restart, …) are not in the live set.

**Release** — bump `[project].version` in `pyproject.toml`, add a matching `## [x.y.z]` section to both [CHANGELOG.md](CHANGELOG.md) and [CHANGELOG.zh.md](CHANGELOG.zh.md), then tag `vx.y.z` and push the tag. The publish workflow runs unit tests, checks the tag against `pyproject.toml` and PyPI (refuses a version that already exists), requires the two changelogs to list the same versions, builds the wheel, uploads it, and opens a GitHub Release from the English notes (with a link to the Chinese changelog).

## Command Overview

Flags may sit before or after a subcommand (`openwrt network leases -f json`). Full help: `openwrt --help` and `openwrt <group> --help`.

| Option | Description |
|--------|-------------|
| `-H`, `--host` | Device IP |
| `-u`, `--user` | Username |
| `-p`, `--port` | Port (SSH 22 / HTTP 80 / HTTPS 443) |
| `-i`, `--identity-file` | SSH private key path (same as `ssh -i`) |
| `--password` | Login password |
| `--ssh` | Connect over SSH |
| `--http` | LuCI/ubus HTTP |
| `--https` | LuCI/ubus HTTPS |
| `--config` | Config file path |
| `-L`, `--language` | UI language: `en` / `zh` |
| `-f`, `--format` | Output format: `text` / `json` / `compact` |
| `--json` | Same as `-f json`; Agent-friendly |
| `--yes`, `-y` | Skip confirmation |
| `-v`, `--version` | Show version and exit |

Success and failure are one object with `ok`. Interactive commands (`setup`, `tui`, `wizard`) refuse JSON (`error: interactive`). Destructive actions prompt on a TTY; in a pipe or JSON mode they need `--yes` or they exit `2`.

`-H`, `-u`, `-p`, `--ssh`, `--http`, and `--https` on `profiles` and `config` are fields of the profile being edited. On every other command they override the connection for this process only, and they do not rewrite `~/.openwrt/config.yaml` unless you also pass `--save-config`.

### doctor / system / logs

```bash
openwrt doctor
openwrt doctor --quick
openwrt -f json doctor
openwrt system status
openwrt system memory
openwrt system processes
openwrt logs system --tail 80
openwrt logs system -f
openwrt logs kernel --tail 80
openwrt logs system --since 10m
```

### network

```bash
openwrt network interfaces
openwrt network interfaces --rates
openwrt network routes
openwrt network rules
openwrt network neighbors          # IPv4 neighbors; Bandix overlays device rates when present
openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name phone --yes
openwrt network leases
openwrt network static-leases
openwrt network set-static-lease --mac 02:11:22:33:44:55 --ip 192.168.1.50 --hostname printer
openwrt network metrics            # Bandix history (needs luci-app-bandix)
openwrt network metrics --ip 192.168.1.50 --since 30m
openwrt network wifi list
openwrt network lan show
openwrt network reload --yes
```

Use `network leases` for active leases and `network static-leases` for configured reservations. `set-static-lease` applies an IPv4 reservation by MAC after checking both lists for conflicts. Static lease commands support LuCI HTTP(S) and SSH.

`qos` is OpenWrt SQM / `tc` (`luci-app-sqm`), not Bandix per-device limits.

### passwall2

Requires **openwrt-cli &gt;= 1.1.0** and `luci-app-passwall2` on the router. Older CLI builds do not have this command group or TUI tab.

When the package is present, TUI adds tab `7` with Nodes / Subscribe / Settings / Rules / ACL / Logs. The node list shows type, protocol, address, port, Ping, and TCPing; the right pane is the selected node. Keys: `a` add, `e` edit, `Del` delete, `p` Ping, `c` TCPing, `[` `]` switch sub-pages.

Read: status, nodes (Ping + TCPing on list enter/refresh), subscribe, settings, components, ACL, and logs. Write nodes and ACL through UCI (`node add/set/delete`, `acl add/set/delete`, `acl source add/remove`); `--apply` restarts PassWall2. No subscribe refresh, clear_log, or component update.

```bash
openwrt passwall2 status
openwrt passwall2 nodes
openwrt passwall2 node show <id>
openwrt passwall2 node add --from-url 'vless://...' --apply --yes
openwrt passwall2 node set <id> --remarks HK --yes
openwrt passwall2 acl
openwrt passwall2 acl add --remarks iot --sources 192.168.9.10 --yes
openwrt passwall2 logs --tail 80
```

### firewall / qos

UCI views work over HTTP. Commands that need iptables or `tc` fail on HTTP instead of inventing data — use `--ssh`.

```bash
openwrt --https firewall zones     # UCI, OK
openwrt --https firewall rules     # needs iptables → fail (use --ssh)
openwrt firewall redirects
openwrt firewall set-redirect --name example-web --src-port 8443 --dest-ip 192.168.1.50 --dest-port 443
openwrt qos status
```

`set-redirect` applies the rule through LuCI HTTP(S) or SSH. Reuse its unique name to update it. The defaults are WAN to LAN, TCP, and enabled. The destination IP determines the address family. The redirect list shows the address family and enabled state.

### service / user / backup

```bash
openwrt service list --running
openwrt service show firewall
openwrt service restart firewall --yes
openwrt user key add --yes
openwrt backup create -o /tmp/bak.tar.gz
openwrt backup restore /tmp/bak.tar.gz --yes
```

### profiles / config

Saved router logins live under `openwrt profiles`. `openwrt config` is the rest of the file: UI language, the global MCP permission, and which profile is active. It does not print the host or the password. `openwrt user` is the router system user, not this list.

```bash
openwrt profiles list                  # active row marked with ●
openwrt profiles show                  # active profile; password masked
openwrt profiles show lab
openwrt profiles add shop-rpa          # terminal: host and login
openwrt profiles add home -H 192.168.1.1 -u root --ssh
openwrt profiles use lab               # switch the active profile
openwrt profiles use                   # arrow keys, then Enter
openwrt profiles update home           # edit each field; Enter keeps the current value
openwrt profiles update lab --mcp-mode readwrite
openwrt profiles update lab --mcp-mode inherit
openwrt profiles del lab
openwrt config show
openwrt config path
openwrt config set --language en
openwrt config set --mcp-mode readonly # global permission limit
```

Every `profiles` subcommand accepts `--json`. Omit the name on `show` to print the active profile. On a terminal, `add`, `update`, and `use` ask for anything you left out. Deleting the last profile is allowed; language and the global MCP permission stay. File layout and the upgrade from an older single-host file are in [Configuration](#configuration).

### mcp / skill

These commands do not connect to the router. `mcp` prints client snippets and the permission table. `skill` installs the `openwrt-ops` playbook.

```bash
openwrt mcp privilege                  # each tool vs readonly / readwrite
openwrt mcp json
openwrt mcp json --client cursor
openwrt mcp prompt
openwrt mcp path
openwrt skill detect
openwrt skill install
openwrt skill show
```

An MCP router tool can take `profile` for that call only. Omit it to use the active profile. See [Drop it into your AI agent](#drop-it-into-your-ai-agent).

### setup / wizard / tui

```bash
openwrt setup
openwrt wizard                 # menu
openwrt wizard wifi            # user | hostname | wifi | lan | service
openwrt tui
```

TUI keys: `1`–`6` tabs (`7` PassWall2 when `luci-app-passwall2` is present; **openwrt-cli &gt;= 1.1.0**), `r` refresh, `e` edit (Neighbors hostname / PassWall2 node or ACL), `u` switch profile (or click the host in the bottom-right corner), `f` filter, `q` quit, `?` help.

#### TUI Settings

Settings is the last tab. Press `8` to open it.

Settings, local sampling, runtime logs, and `samples_query` were added in **1.4.0**. See [release notes](CHANGELOG.md#140---2026-11-21).

- **Languages** switches English and Simplified Chinese in the running TUI and saves the choice.
- **MCP** sets the global permission limit and shows the active profile's configured and effective modes.
- **Profiles** adds, edits, deletes, and switches profiles. Switch away from the active profile before editing or deleting it. The TUI checks the new connection before switching.
- **Sample** groups collection settings and keeps the collector status and actions alongside them. Use **Save** or `Ctrl+S`, then **Start**, or enable startup sampling. Exiting the TUI stops its collector.

Changing language, the global MCP mode, or the active profile prompts for confirmation. Press `y` or select **Confirm** to apply the change.

Sampling uses a separate read-only router connection and stores data in `~/.openwrt/data.db`. System and kernel logs come from OpenWrt. When PassWall2 is available, its runtime log and ACL logs are separate sources. An empty `acls.sections` includes every active ACL with logging enabled. The TUI saves selections per profile under `sampling.profiles.<name>.sections`, with the shared list as the fallback. Intervals are in seconds.

```yaml
sampling:
  enabled: false
  on_start: false
  logs:
    enabled: false
    interval: 10
    system: true
    kernel: false
    passwall2:
      enabled: false
      runtime: true
      acls:
        enabled: true
        sections: []
  metrics:
    enabled: false
    interval: 60
    bandix:
      enabled: false
```

OpenWrt metrics include load, memory, WAN traffic, and connections. On routers with Bandix, the optional Bandix sampler also saves per-device traffic rates and available connection counts across hours. History uses the router's Bandix history when it covers the selected range, with separate views of partial router history and locally saved data when coverage is incomplete.

The collector checkpoints overlapping log snapshots in SQLite and resumes from them on restart. It flags rollovers, ambiguous overlap, and read errors. Router log buffers are finite. A sampling pause or rollover can leave gaps, and the collector can only preserve entries still readable from the router. History shows coverage and can display recent raw entries per source.

The built-in collector writes the router's own logs and network metrics. An existing standalone collector keeps its own database, including any TCPing measurements it collects.

#### Foreground sampling without the TUI

```bash
openwrt sample config show
openwrt sample config set --enable --logs --system --metrics --log-interval 10 --metric-interval 60
openwrt sample run --profile home --show-logs
```

`sample config set --help` lists the switches for TUI startup, OpenWrt system and kernel logs, PassWall2 runtime and ACL logs, and Bandix metrics. Repeat `--acl-section` for selected ACLs, or use `--all-acls` to collect all eligible ACLs with logging enabled. Add `--profile NAME` to change only that profile’s ACL selection. Use `--profile NAME --inherit-acls` to restore the shared selection. Pass `--json` to `sample config show` or `set` for a single JSON response.

`sample run` requires sampling to be enabled with at least one source. It reads the active profile by default, or uses `--profile NAME` for that run. It uses the same `~/.openwrt/data.db` and checkpoints as the TUI, and writes diagnostic events under `~/.openwrt/logs/`. Restart the command to pick up configuration edits. The database allows one sampler at a time. If TUI sampling is already running, `sample run` exits with `sampling_busy`. If `sample run` starts first, the TUI reports the conflict and stays open. TUI and MCP history reads remain available in either case.

The terminal shows batch counts, collection failures, gaps, and recovery events. `--show-logs` also prints newly stored router log entries after the first historical fetch for each source. `openwrt --json sample run` emits one JSON object per line, including startup failures. Press `Ctrl+C` or send `SIGTERM` to stop and release the writer lock.

#### Local runtime logs

The TUI, MCP server, and TUI sampler record runtime events in JSONL files under `~/.openwrt/logs/`. Open **Settings → Logs** for an automatically refreshed view of the latest runtime events. Use `e` to change retention days or `Del` to confirm removal of archived logs.

MCP events include the tool name, profile, effective permission, duration, and result code. They omit tool arguments and response bodies. If a write times out, its execution is marked unknown. Router logs, metrics, and sampling batches stay in `~/.openwrt/data.db`.

Runtime log retention defaults to **7 days**. The background storage target defaults to **50 MiB**. Automatic cleanup removes expired or excess archives. Manual cleanup removes archived files and leaves active segments alone. Each process rotates its own files, so the total can temporarily exceed the target while files are active.

```yaml
runtime_logs:
  days: 7
```

## Configuration

`openwrt setup` or `--save-config` writes `~/.openwrt/config.yaml`. The file holds every saved router as a named profile, plus one `active` name. CLI commands, the TUI, and MCP all start from that name. Host, user, port, transport, password, and key belong to the profile. `language` and `mcp.mode` stay global. Global `readonly` blocks all MCP writes. With global `readwrite`, a profile can restrict access to `readonly` or use the global mode.

Configuration precedence is `--config`, `OPENWRT_CONFIG`, then `~/.openwrt/config.yaml`. When using the default location, the first CLI or MCP use copies a readable legacy `~/.openwrt-cli.yaml` to the new path if the destination is absent. The legacy file is kept. If the copy fails, the process reports the error and uses the legacy path. An explicit path is used as supplied.

The first `openwrt setup` asks for a profile name (Enter keeps `home`), then the host and login. A later setup updates the profile you name and leaves the others in place. `-H`, `-u`, and `-p` on an ordinary command override the connection for that process only and do not rewrite the file unless you also pass `--save-config`. `--save-config` updates the active profile; it does not create one.

An older file with the host at the top level loads as a profile named `default`. Reading it does not rewrite the file. The next save writes the shape below and keeps the language and the global MCP permission.

```yaml
language: en
mcp:
  mode: readonly          # global permission limit
active: home
profiles:
  - name: home
    host: 192.168.1.1
    user: root
    port: 22
    transport: ssh
    identity_file: ~/.ssh/id_ed25519_openwrt
  - name: lab
    host: 10.0.0.1
    user: root
    transport: http
    scheme: https
    port: 443
    verify_ssl: false
    mcp:
      mode: readwrite     # this profile only
```

```bash
openwrt config show          # language, global mcp.mode, active name, effective mode
openwrt config path
openwrt config set --language en
openwrt config set --mcp-mode readonly     # global permission limit
openwrt config set --mcp-mode readwrite    # MCP write tools (user must confirm)
openwrt profiles list
openwrt profiles show                  # active profile; password masked
openwrt profiles show lab
openwrt profiles add shop-rpa          # host and login, step by step
openwrt profiles add home -H 192.168.1.1 -u root --ssh -i ~/.ssh/id_ed25519_openwrt
openwrt profiles add lab -H 10.0.0.1 --https --mcp-mode readwrite
openwrt profiles use lab
openwrt profiles use                 # arrow keys, then Enter
openwrt profiles update                 # arrow keys, then edit each field
openwrt profiles update home            # edit each field; Enter keeps the current value
openwrt profiles update home --name house
openwrt profiles update lab --mcp-mode inherit   # inherit the global mode
openwrt profiles del lab
```

`profiles add` requires a name you choose. `-u` is the login user on that router, not the profile name. `openwrt user` is still the router system user. `profiles show` and `profiles list --json` mask the password. `profiles del` may remove the last profile; language and the global MCP permission stay, and the next connection needs a new profile. In the TUI, `u` or a click on the bottom-right host switches the active profile after the new connection succeeds.

## Drop it into your AI agent

The CLI is for you. The **skill** (`openwrt-ops`) is the playbook; **MCP** (`openwrt-mcp`) is the tool server. Together they let an Agent run doctor, inspect the LAN / PassWall2, and (only after you allow it) apply a change — without pasting passwords into chat.

Two ways in:

1. **You wire it** (below). Then ask in plain language: *is the router OK? who is on the LAN? PassWall2 status?*
2. **Copy for agent** — paste the block at the end of this section into Cursor / Claude Code / Codex and let it walk the same steps.

### 1. Connect the router once

```bash
openwrt setup
openwrt doctor
```

This writes `~/.openwrt/config.yaml`. The MCP server reads that file. **Never copy `password` or `identity_file` into MCP JSON.**

### 2. Install the skill

For Codex, follow [the Codex setup below](#codex-cli-and-desktop) to install to its current user or repository skill directory.

```bash
openwrt skill install            # interactive: pick a detected client
openwrt skill install --yes      # all detected clients, user-level
```

That copies `openwrt-ops` into the Agent skill directory (Cursor `~/.cursor/skills`, Claude Code `~/.claude/skills`, …). `install.sh` / `install.bat` can run this step for you.

### 3. Merge MCP (keep other servers)

```bash
pip install 'openwrt-cli[mcp]'   # skip if install.sh already did this
openwrt mcp privilege            # tools allowed under the current MCP permission
openwrt mcp json                 # generic mcpServers.openwrt
openwrt mcp json --client cursor
openwrt mcp path                 # recommended file per client
```

**Cursor** (`~/.cursor/mcp.json`) · **Trae / Windsurf / Qoder** (same JSON shape):

```json
{
  "mcpServers": {
    "openwrt": {
      "command": "openwrt-mcp"
    }
  }
}
```

**Claude Code**

```bash
claude mcp add openwrt -- openwrt-mcp
```

#### Codex CLI and desktop

Run these commands on the machine where Codex runs, after installing `openwrt-cli[mcp]` and connecting your router in step 1. Add the MCP server once and install the Skill in Codex's user skill directory:

```bash
codex mcp add openwrt -- "$(command -v openwrt-mcp)"
openwrt skill install --dir "$HOME/.agents/skills" --yes
codex mcp list
```

Run the first command from the environment where `openwrt-mcp` is on `PATH`. The absolute path lets Codex start it outside that shell. If you already have an `openwrt` entry, update that entry. For desktop setup or manual configuration, run `openwrt mcp json --client codex` and merge the generated TOML into `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`). If the snippet uses a bare command name, use its absolute path when Codex does not inherit your shell's `PATH`. [Codex MCP guide](https://developers.openai.com/codex/mcp)

To install the Skill for one repository, run `openwrt skill install --agent codex --project --yes` from that repository. Start a new Codex session, check that `openwrt` is connected, and ask “Use `$openwrt-ops` to run a read-only health check on my active OpenWrt profile.” [Codex Skill locations](https://developers.openai.com/codex/skills#where-codex-loads-local-skills)

Reload MCP in the client. The server key must stay `openwrt`. If `openwrt-mcp` is not on `PATH`, use `python -m openwrt_cli.mcp` instead (`openwrt mcp json` already picks the right launch).

### What the agent can do

| | Tools |
|---|---|
| **First hop** | `profiles_current` · `profiles_list` · `doctor` · `config_show` (no passwords) |
| **Read** | `system_status` · `network_overview` · `network_neighbors` · `network_leases` · `network_static_leases` · `firewall_view` · `service_list` · `passwall2_status` · `passwall2_nodes` (no Ping) · `passwall2_logs` · `logs_read` · `samples_query` |
| **Write** | only when `mcp.mode=readwrite` **and** you confirm in chat (`network_set_static_lease`, `firewall_set_redirect`, `wifi_set`, `lan_set`, PassWall2 node/ACL, `service_action`, …) |
| **Never MCP** | reboot · shutdown · backup restore · user add / passwd / delete — human CLI only (`openwrt system reboot --yes`) |

### Local sampling history

Use the read-only MCP tool `samples_query` to read `~/.openwrt/data.db` for a saved router. Pass `profile` to select a router or omit it to use the active profile. The query resolves the profile to the endpoint used by the sampler and runs without a router connection. Profiles on the same endpoint share its stored sampling history.

| `source` | Data |
|---|---|
| `system`, `kernel` | OpenWrt system or kernel log |
| `passwall2.runtime` | PassWall2 runtime log |
| `passwall2.acl`, `passwall2.acl.<section>` | All ACL logs or one ACL section |
| `openwrt.metrics` | Local snapshots of load, memory, WAN traffic, and connections |
| `bandix.devices`, `bandix.history` | Local Bandix device snapshots or Bandix history saved by the sampler |

`since` and `until` are inclusive and accept ISO 8601 with an offset, Unix seconds or milliseconds, and relative times such as `3h`. The default window is one hour ending at `until` or now. Set `subject` to a MAC address, `all`, or `router` to filter metric records. Results are newest first. Pages return 100 records by default, up to 200. For the next page, pass `next_cursor` as `cursor` with the same source and profile. The cursor preserves the first page’s time bounds, subject, and collection snapshot. An explicit `offset` starts a fresh query and may shift if new samples arrive.

`coverage` gives the first and last collection batches and successful reads observed in the window. `gaps` gives recorded rollovers, missed metric intervals, and read errors detected in the window. Fields ending in `_ms` use Unix milliseconds. Logs without an event timestamp are filtered by collection time. A backfilled record can exist in a window with unknown collection coverage.

This tool is available in both MCP permission modes. Change MCP permissions through the local CLI or TUI.

### Which router the agent talks to

`profiles_current` is the active profile. `profiles_list` lists every saved name, target, and effective permission. Neither tool dials the router, and neither returns a password or a key path.

Router tools take an optional `profile`. Omit it to use the active profile. Pass a name to use that profile for this call only. The result includes `profile`, so two routers in one turn stay distinct. This does not change the profile selected in the CLI or TUI. Do not have the agent run `openwrt profiles use` to retarget later calls.

Different profiles can be queried at the same time. A write is allowed only when **that** profile's effective mode is `readwrite`.

The global `mcp.mode` defaults to **readonly** and blocks writes for every profile, including those set to `readwrite`. To allow a write, the global mode and the target profile’s effective mode must both be `readwrite`. Changing the global mode to `readwrite` also grants write access to profiles that inherit it.

When MCP is connected, **do not** mutate the router with `openwrt … --yes` — that skips `mcp.mode`. `openwrt mcp privilege` shows the same allow / deny table the server uses.

### Copy for agent

Paste this into Cursor, Claude Code, Codex, or any Agent and ask it to finish setup:

```
You're setting up openwrt-cli so I can operate an OpenWrt router from this chat.

WHAT IT IS
  Command: openwrt. Humans use CLI/TUI. You use MCP tools (openwrt-mcp) plus the
  openwrt-ops skill. Transport is SSH or LuCI/ubus HTTP.
  Config lives in ~/.openwrt/config.yaml — the MCP server reads it. Never copy
  password or identity_file into MCP JSON, chat, or tool output.

INSTALL
  pip install 'openwrt-cli[mcp]'          # or: the repo install.sh / install.bat
  openwrt setup                           # language + host + SSH/HTTP
  openwrt doctor                          # first hop
  openwrt skill install                   # copies openwrt-ops into this Agent
  openwrt mcp json                        # MERGE mcpServers.openwrt; keep other servers

CONNECT MCP
  Cursor:      ~/.cursor/mcp.json  →  { "mcpServers": { "openwrt": { "command": "openwrt-mcp" } } }
  Claude Code: claude mcp add openwrt -- openwrt-mcp
  Codex:       openwrt mcp json --client codex   (TOML → ~/.codex/config.toml)
  If openwrt-mcp is missing: python -m openwrt_cli.mcp
  Reload MCP after saving.

GOLDEN PATH
  profiles_current → doctor on that router
        → read-only inspect (system / network / passwall2)
        → another saved router: pass profile=<name> on that call only
        → writes need global readwrite and effective readwrite on the target profile
        → call a write tool only after a clear yes in chat
        → apply / restart is a second write (confirm again)

RULES
  1) Prefer MCP tools when the openwrt server is connected.
     Read-only CLI fallback: openwrt -f json doctor|system status|network leases
  2) Omit profile to use the active profile. Do not run openwrt profiles use
     to retarget later MCP calls. profiles_list / profiles_current omit passwords.
  3) Global mcp.mode defaults to readonly and blocks writes for all profiles.
     A profile can further restrict permissions when global mode is readwrite.
  4) When MCP is available, do not mutate with openwrt … --yes (bypasses the guard).
  5) Never invent reboot, shutdown, backup restore, or user add/passwd/delete as MCP tools.

Now: run doctor, then a read-only look at neighbors and PassWall2 status.
Full skill: packaged as openwrt-ops (openwrt skill show).
```

`openwrt mcp prompt` prints a client-specific variant of the same instructions.

UI language (tables, TUI, setup, help) resolves as:

1. `-L/--language` or `OPENWRT_LANG` (`en` / `zh`)
2. `language` in the config file
3. System locale (`LANG` / `LC_ALL`) — Chinese locales get 简体中文, everything else English

`openwrt setup` detects the system language, asks you to confirm, and writes it to the config file. To add another language, see [Contributing a language](#contributing-a-language).

## Project Layout

```
src/openwrt_cli/
  app.py          # entry (openwrt / openwrt-cli)
  commands/       # Typer
  services/       # presentation-free business logic
  mcp/            # Skill pack, MCP guide, FastMCP server
  tui/            # textual dashboard
  ui/             # Rich / questionary
  core/           # DeviceClient, SSH / HTTP channels
  i18n/
```

```mermaid
flowchart LR
  CLI[CLI / wizard / TUI] --> Services
  Services --> DeviceClient
  DeviceClient --> SSH
  DeviceClient --> HTTP
  SSH --> Channels[ubus / uci / shell]
  HTTP --> Channels
```

## Contributing a language

UI strings live in `src/openwrt_cli/i18n/locales/`. One JSON file is one language. The file name, without `.json`, is the language code. `locales/*.json` is already packaged, and the loader picks up every file that does not start with `_`. No Python registration is required.

Command names stay English. Translate the values only. MCP tool descriptions and the `openwrt-ops` skill are English and are not in these files.

Russian (`ru`) is the example below. The same steps apply to any other language.

1. Copy `en.json` to `src/openwrt_cli/i18n/locales/ru.json`. Use the ISO 639-1 primary code in lowercase. Name the file `ru.json`, not `ru_RU.json`. `LANG=ru_RU.UTF-8` is normalized to `ru`. Chinese is the only alias that is special-cased (`zh_CN`, `zh-Hans`, and `cn` all select `zh`).
2. Translate the values. Keep every key, and keep placeholders such as `{name}`, `{error}`, and `{langs}`. A key that starts with `_` is ignored, so it does not count as a translation.
3. Add the native name to **every** catalog, including `en.json`, `zh.json`, and `ru.json`:

```json
"setup.lang.ru": "Русский"
```

`openwrt setup` lists the languages it finds and shows this label. Help text that contains `{langs}` lists the codes on its own.

4. Run `pytest tests/test_i18n.py`. Each catalog must have the same set of keys as `en.json`. A missing or extra key fails the test. At runtime a missing string would fall back to English, but the test does not allow a partial file.
5. Try it:

```bash
openwrt -L ru doctor
OPENWRT_LANG=ru openwrt config show
```

Then update the `en` / `zh` lists in this README so the new code is documented.

`tests/test_i18n.py` treats `de_DE` as an unsupported locale. Adding `ru` does not affect that assertion. Adding `de` does: change the assertion in the same pull request.

## FAQ

**Q:** SSH will not connect. What should I check?

**A:** Run setup again, then compare it with OpenSSH:

```bash
openwrt setup
ssh -v -p 22 root@192.168.1.1
```

**Q:** How do I use a key instead of a password?

**A:** Install the public key on the router, then save the private-key path into the active profile:

```bash
openwrt user key add --yes
openwrt -H 192.168.1.1 -i ~/.ssh/id_ed25519_openwrt --save-config
```

**Q:** The router has a web admin and no SSH. How do I connect?

**A:** Choose the HTTP API in setup, then call commands over HTTPS:

```bash
openwrt setup          # pick HTTP API
openwrt --https system status
```

**Q:** Why does `firewall rules` fail over HTTP?

**A:** That path needs iptables. Use `--ssh`, or stay with UCI commands such as `firewall zones`.

**Q:** Why do reboot, reload, and restart fail in JSON or a pipe?

**A:** Those actions ask for confirmation on a terminal. Add `--yes` when there is no terminal.

**Q:** Why is `passwall2` unknown, or why is TUI tab 7 missing?

**A:** That feature shipped in **openwrt-cli &gt;= 1.1.0**. Upgrade the CLI, and install `luci-app-passwall2` on the router. `openwrt -v` prints the package version.

**Q:** Why is `openwrt` not on PATH?

**A:** A `pip install --user` may have placed the script in `python -m site --user-base` + `/bin`. Add that directory, or install with `pipx`.

**Q:** How do I use more than one router?

**A:** `openwrt profiles use`, or `u` in the TUI, changes the active profile for the CLI, the TUI, and the default MCP target. An MCP tool can still pass `profile` for one call without changing that selection.

**Q:** How do I switch the UI language?

**A:** `-L` applies to one command. `config set` saves the choice:

```bash
openwrt -L zh doctor
openwrt config set --language zh
```

## Acknowledgments

This project talks to OpenWrt through the official stack:

- [openwrt/openwrt](https://github.com/openwrt/openwrt) — the OpenWrt operating system
- [openwrt/luci](https://github.com/openwrt/luci) — LuCI web interface and ubus/HTTP API
- [openwrt/uci](https://github.com/openwrt/uci) — Unified Configuration Interface

Thanks to [a6726170/openwrt-cli](https://github.com/a6726170/openwrt-cli) for the original inspiration.

## License

MIT — see [LICENSE](LICENSE).
