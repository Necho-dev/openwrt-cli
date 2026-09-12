# Changelog

**English** | [简体中文](CHANGELOG.zh.md)

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versioning follows [SemVer](https://semver.org/).

A `vX.Y.Z` tag is published only when **both** this file and
[CHANGELOG.zh.md](CHANGELOG.zh.md) have a matching `## [X.Y.Z]` section.
The English section becomes the GitHub Release body, with a link to the Chinese notes.

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
