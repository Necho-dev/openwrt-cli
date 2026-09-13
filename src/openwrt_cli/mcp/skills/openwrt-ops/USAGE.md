# openwrt-ops tools

Use MCP tools when `openwrt-mcp` is configured. Do not invent `system_reboot` or other high-risk names.

## Read (always listed)

| Tool | When |
|---|---|
| `doctor` | First hop: connect, LuCI apps, capability |
| `system_status` | Host, load, memory, WAN, disk, temp, uptime |
| `system_processes` | Process table |
| `logs_read` | `source=system\|kernel`; `tail` / `since` / `until` only — no follow |
| `network_overview` | Interfaces + routes + DNS + LAN |
| `network_neighbors` | ARP / Bandix overlay |
| `network_leases` | DHCP leases |
| `network_metrics` | Bandix history (`luci-app-bandix`) |
| `firewall_view` | Zones / UCI / redirects (iptables needs SSH) |
| `qos_view` | SQM (`tc` needs SSH) |
| `service_list` / `service_show` | init.d |
| `passwall2_status` | Current node / ACL / running |
| `passwall2_nodes` | Node table **without** Ping |
| `passwall2_node_ping` | One node ICMP or TCPing |
| `passwall2_acl` / `passwall2_acl_show` | ACL rules |
| `passwall2_logs` | Runtime log (truncated) |
| `config_show` | Local yaml; password masked; includes `mcp.mode` |

## Write (`mcp.mode=readwrite` + user confirm)

`destructiveHint` may show a client dialog. If it does not, wait for a clear yes in chat. Never pass `confirm=true`.

| Tool | Risk |
|---|---|
| `network_set_hostname` | Bandix name only |
| `system_hostname` | System hostname |
| `wifi_set` | Can drop Wi-Fi clients |
| `lan_set` | Can knock this machine offline |
| `network_reload` | Brief connectivity loss |
| `service_action` | start/stop/restart/reload/enable/disable |
| `passwall2_node_*` / `passwall2_acl_*` | `apply` is a second write |
| `backup_create` | Writes under `/tmp` on the router |
| `user_key_add` | Installs a public key; does not add users |

## Never MCP

reboot · shutdown · backup restore · user add / passwd / delete.

Those stay human CLI (`openwrt system reboot --yes`). When MCP is available, do not mutate with `openwrt … --yes`.
