# openwrt-ops tools

Use MCP tools when `openwrt-mcp` is configured. Do not invent `system_reboot` or other high-risk names. `openwrt mcp privilege` lists every tool and whether the current mode allows it.

## Read (always listed)

| Tool | When |
|---|---|
| `doctor` | First hop: connect, LuCI apps, capability |
| `system_status` | Host, load, memory, WAN, disk, temp, uptime |
| `system_processes` | Process table |
| `logs_read` | `source=system\|kernel`; `tail` / `since` / `until` only — no follow |
| `network_overview` | Interfaces + routes + DNS + LAN |
| `network_neighbors` | ARP / Bandix overlay |
| `network_leases` | Active DHCP leases |
| `network_static_leases` | Configured static DHCP leases |
| `network_metrics` | Bandix history (`luci-app-bandix`) |
| `firewall_view` | Firewall zones and redirects with enabled state and address family |
| `qos_view` | SQM (`tc` needs SSH) |
| `service_list` / `service_show` | init.d |
| `passwall2_status` | Current node / ACL / running |
| `passwall2_nodes` | Node table **without** Ping |
| `passwall2_node_ping` | One node ICMP or TCPing |
| `passwall2_acl` / `passwall2_acl_show` | ACL rules |
| `passwall2_logs` | Runtime log (truncated) |
| `profiles_current` | Active profile: name, target, effective permission. Does not switch or dial |
| `profiles_list` | Saved profiles (name, target, effective permission, active flag). No passwords or key paths |
| `config_show` | Global config, active profile name, effective `mcp.mode`. No passwords |

Router tools accept optional `profile`. Omit it to use the active profile. Pass a name to query that profile for this call only. This does not change `active`. Writes follow that profile's effective `mcp.mode`.

## Write (`mcp.mode=readwrite` + user confirm)

`destructiveHint` may show a client dialog. If it does not, wait for a clear yes in chat. Never pass `confirm=true`.

| Tool | Risk |
|---|---|
| `network_set_static_lease` | Reserve an IPv4 address by MAC and apply the DHCP configuration |
| `network_set_hostname` | Bandix name only |
| `system_hostname` | System hostname |
| `wifi_set` | Can drop Wi-Fi clients |
| `firewall_set_redirect` | Create or update and apply a DNAT rule by its unique name |
| `lan_set` | Can knock this machine offline |
| `network_reload` | Brief connectivity loss |
| `service_action` | start/stop/restart/reload/enable/disable |
| `passwall2_node_*` / `passwall2_acl_*` | `apply` is a second write |
| `backup_create` | Writes under `/tmp` on the router |
| `user_key_add` | Installs a public key; does not add users |

## Never MCP

reboot · shutdown · backup restore · user add / passwd / delete.

Those stay human CLI (`openwrt system reboot --yes`). When MCP is available, do not mutate with `openwrt … --yes`.

## Static DHCP leases

Use `openwrt network static-leases` to list configured reservations. To create or update one, run:

```sh
openwrt network set-static-lease --mac 00:11:22:33:44:55 --ip 192.168.1.50 --hostname example
```

Both commands support LuCI HTTP and SSH. The write command checks static reservations and active leases for conflicts before changing the configuration.

## Port forwarding

Use `openwrt firewall redirects` to list rules. To create or update a rule:

```sh
openwrt firewall set-redirect --name example-ssh --src-port 30022 --dest-ip 192.168.1.50 --dest-port 22
```

The defaults are WAN to LAN, TCP, and enabled. The address family follows the destination IP.
Use the same name to update an existing rule. HTTP and SSH both check for overlapping rules and verify the configuration after applying it.
