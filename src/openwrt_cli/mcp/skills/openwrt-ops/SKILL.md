---
name: openwrt-ops
description: >-
  Operate an OpenWrt router through openwrt-cli MCP tools or read-only CLI JSON.
  Use when the user mentions OpenWrt, LuCI, ubus, UCI, DHCP leases, neighbors/ARP,
  WAN/LAN, Wi-Fi, firewall, SQM/QoS, services, PassWall2, bypass or sidecar proxy,
  Bandix, doctor, router outage, or home-network troubleshooting.
---

# OpenWrt ops

Drive the **openwrt** MCP (`openwrt-mcp`) when it is configured. Fall back to
`openwrt -f json` for **reads only**.

## Golden path

```
doctor
  ├─ connect fail → user runs `openwrt setup` / checks host
  ├─ capability missing → switch to SSH, or say the feature is unavailable
  └─ ok → read-only inspect (system / network / passwall2)
         → preview fields you would change
         → writes need mcp.mode=readwrite (user: `openwrt config set --mcp-mode readwrite`)
         → call a write tool only after the user agrees in chat
         → apply / restart is a second write (confirm again)
```

## Permissions

`~/.openwrt-cli.yaml` → `mcp.mode`:

- **readonly** (default): read tools work; write tools return `mcp_readonly`
- **readwrite**: write tools run after the **user** confirms (client `destructiveHint` dialog, or a clear yes in chat)
- **full** does not exist. Never invent it.

High-risk ops are **never** MCP tools: reboot, shutdown, backup restore, user add/passwd/delete. Tell the user to run those in a human terminal (`openwrt system reboot --yes`). Do not run them yourself.

## Do not bypass the guard

- When MCP is available, **do not** mutate the router with `openwrt … --yes` or `openwrt -f json` writes. That skips `mcp.mode`.
- Without MCP: CLI reads are fine (`openwrt -f json doctor`). Do not pass `--yes` for the user.
- Never put `password` or key material into MCP config, chat, or tool output.

## Setup (if MCP / skill missing)

```
openwrt skill install --yes
openwrt mcp json
openwrt mcp prompt
```

Ask the user to merge the snippet into their client config (key `openwrt`) and reload MCP.

## Tool cheat-sheet

See `USAGE.md` for the full table.

**Read:** `doctor` · `system_status` · `system_processes` · `logs_read` · `network_overview` · `network_neighbors` · `network_leases` · `network_metrics` · `firewall_view` · `qos_view` · `service_list` · `service_show` · `passwall2_status` · `passwall2_nodes` (no Ping) · `passwall2_node_ping` · `passwall2_acl` · `passwall2_logs` · `config_show` (password masked).

**Write** (readwrite + user confirm): `network_set_hostname` · `wifi_set` · `lan_set` · `network_reload` · `system_hostname` · `service_action` · `passwall2_node_add|set|delete` · `passwall2_acl_add|set|delete` · `passwall2_acl_source_add|remove` · `backup_create` · `user_key_add`.

`lan_set` / `wifi_set` can knock the router offline — say so before asking to confirm.

HTTP transport cannot run iptables/`tc` paths (`firewall` rules, some `qos`). Use SSH or report `error: capability`.

## CLI read-only fallback

```
openwrt -f json doctor
openwrt -f json system status
openwrt -f json network leases
openwrt -f json passwall2 status
```
