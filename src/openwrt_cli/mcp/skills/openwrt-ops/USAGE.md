# openwrt-ops tools

Use MCP tools when `openwrt-mcp` is configured. Do not invent `system_reboot` or other high-risk names. `openwrt mcp privilege` lists every tool and whether the current mode allows it.

## Read (always listed)

| Tool | When |
|---|---|
| `doctor` | First hop: connect, LuCI apps, capability |
| `system_status` | Host, load, memory, WAN, disk, temp, uptime |
| `system_processes` | Process table |
| `logs_read` | `source=system\|kernel`; `tail` / `since` / `until` only — no follow |
| `samples_query` | Local SQLite history by profile, time and source, including observed coverage and recorded gaps |
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

Router tools accept optional `profile`. Omit it to use the active profile. Pass a name to query that profile for this call only. This does not change `active`. Writes require global `readwrite` and effective `readwrite` on the target profile.

Change MCP permissions locally through CLI or TUI.

## Sampled history

`samples_query` reads the local TUI sampler database `~/.openwrt/data.db`. For current router state use tools such as `system_status`, `logs_read`, `passwall2_acl`, or `network_metrics`. If a window is missing from SQLite, the live router can only return the data it still retains. A standalone collector uses its own database and may cover different periods or sources.

These are arguments to the existing MCP tool `samples_query`. Replace the example profile, section, and timestamps with the requested values. Choose fixed ISO 8601 bounds with a timezone when comparing sources. Time bounds in the tool are inclusive.

| Question | Example arguments |
|---|---|
| Was there a router or kernel event around the incident? | `{"profile":"office","source":"system","since":"2026-11-21T14:00:00+08:00","until":"2026-11-21T14:10:00+08:00"}`. Use `kernel` for the kernel log. |
| Did PassWall2 restart, and which sampled ACLs have coverage? | Query `passwall2.runtime` and `passwall2.acl` separately with the same bounds. |
| What happened in a single ACL? | `{"profile":"office","source":"passwall2.acl.cfg012abc","since":"2026-11-21T14:00:00+08:00","until":"2026-11-21T14:10:00+08:00","limit":200}` |
| Did WAN traffic or connection count rise? | `{"profile":"office","source":"openwrt.metrics","subject":"router","since":"2026-11-21T14:00:00+08:00","until":"2026-11-21T14:10:00+08:00"}` |
| Which device contributed to the traffic? | Query `bandix.devices` with `subject` set to the device MAC, or `all` for aggregate snapshots. Use `bandix.history` for Bandix history already saved in SQLite. |

For evidence and counting:

1. Check `database`, `coverage`, and `gaps` for the selected Profile and source. Use `first_success_ms` and `last_success_ms` to locate observed collection. `coverage_state=observed` establishes that some collections succeeded. Check the edges, partial coverage, and recorded gaps before drawing conclusions about the whole window. An empty response in an uncovered interval means **unknown**.
2. Results are newest first. Pass `next_cursor` as `cursor` with the same profile and source until it is null. The cursor keeps the first page’s filters and collection snapshot. If you cannot finish paging, label the analysis as partial.
3. Use `event_ms` as the log event time and `collected_ms` as the time the sampler fetched it. Fields ending in `_ms` are Unix milliseconds. When `event_ms` is absent, the entry has a collection time only. For ACL logs, use `entry.msg` as the original line. `acl_binding_at_collection` is useful context and may differ from the binding when an older event occurred.

For example, pass the cursor returned by the first ACL request:

```json
{"profile":"office","source":"passwall2.acl.cfg012abc","limit":200,"cursor":"<next_cursor>"}
```

### SOCKS5 TCP connection timeout rate

Calculate this from the sampled ACL entries with `samples_query`. The rate describes application TCP connection attempts to a SOCKS5 proxy endpoint. A TCPing probe measures a separate population.

1. Find the relevant ACL section using historical `passwall2.acl` results, or `passwall2_acl` for the current configuration. Query a single ACL by section with `limit: 200` and finish paging. Include time before and after the reporting window so you can match attempts and errors across the edges. For example, to report **14:00:00–14:10:00** you might fetch **13:59:30–14:10:30** if this covers the connection timeout delays observed in the logs. Count attempts started in the half-open reporting window `[14:00:00, 14:10:00)`.
2. Count each distinct non-DNS TCP attempt started by a line like `outbound/socks[<tag>]: outbound connection to <destination>`. Group separately by ACL and historical proxy identity. A node tag may contain only the IP. If a node changed ports on the same IP, show a port only when the logs or a verified historical binding establish it. For a matched attempt, use the ACL, SOCKS tag, connection ID, destination, and event order.
3. Count a started attempt as timed out when its corresponding error explicitly shows a TCP timeout connecting to the SOCKS5 endpoint. An error with `connection: open connection to <destination> using outbound/socks[<tag>]: dial tcp <proxy-endpoint>: i/o timeout` is an example. Pair it with the nearest preceding unmatched attempt for the same flow, and count an attempt once. Keep timeout errors without a matching start as a separate evidence count. Exclude DNS-marked flows, TCP DNS to port 53, and UDP packet forwarding. For encrypted DNS, require a DNS marker or other identifying evidence. HTTP `ECONNRESET`, application read errors, and TCPing failures are separate measurements.
4. Calculate `100 × matched timed-out attempts ÷ eligible started attempts`. With warning/error-only logs or a missing denominator, report **unknown** instead of using all lines or all errors as the denominator. If the matching or time coverage is incomplete, report counts and mark the window partial. A rate for a verified subset must be labelled with that subset. Leave enough time to observe attempts near the end of the window before reporting a final rate.

Example rows from one ACL and proxy tag:

```text
14:00:01 INFO  [101 0ms] outbound/socks[default:192.0.2.10]: outbound connection to 198.51.100.10:443
14:00:02 INFO  [102 0ms] outbound/socks[default:192.0.2.10]: outbound connection to 198.51.100.11:443
14:00:06 ERROR [101 5s] connection: open connection to 198.51.100.10:443 using outbound/socks[default:192.0.2.10]: dial tcp 192.0.2.10:1080: i/o timeout
14:00:07 ERROR [103 5s] dns: exchange failed: dial tcp 192.0.2.10:1080: i/o timeout
```

With complete coverage, this yields **1 / 2 = 50% TCP 建连超时率**. The DNS error is tracked separately. In a report, include the reporting window and timezone, ACL and proxy identity, numerator and denominator, coverage, and matching original lines. Use **TCP 建连超时** in Chinese summaries.

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
