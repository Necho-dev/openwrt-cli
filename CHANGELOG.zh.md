# 更新日志

[English](CHANGELOG.md) | **简体中文**

本文件记录项目的重要变更。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

打 `vX.Y.Z` 标签前，**本文件与** [CHANGELOG.md](CHANGELOG.md) **必须有同一节** `## [X.Y.Z]`。
GitHub Release 正文使用英文，并附上本节的链接。

## [Unreleased]

## [1.2.0] - 2026-09-13

功能版本 **1.2.0**。给 Agent 用的一层：包内技能 `openwrt-ops`、薄 FastMCP 服务 `openwrt-mcp`、以及本机分发命令。人继续用 `openwrt` / `openwrt tui`。助手走同一套 Service。JSON 契约不变（`ok` + 摊平字段）。密码只放在 `~/.openwrt-cli.yaml`，不会写进各客户端的 MCP 配置。

### Skill（`openwrt-ops`）

操作说明 + 工具速查，打进 wheel（`SKILL.md`、`USAGE.md`）。YAML description 只用英文，方便客户端按 OpenWrt / LuCI / PassWall2 / 断网等词触发。

`openwrt skill` 不连路由器：

- `detect`（无子命令时也是它）— 本机有哪些 Agent，以及用户级是否已装。列：Id、Client、Detected、Installed、Via。
- `list` — 包内文件和**用户级**安装路径；当前项目只显示有/无，不打印项目路径。
- `show` — 打印包内 `SKILL.md`。
- `install` — TTY 向导：选一个已探测客户端（或自定义目录），再选用户级 / 当前工作区，检查路径后确认。`--global` 与 `--project` 互斥；`--dir PATH` 写入 `PATH/openwrt-ops/`，不走 scope。非交互 / `--yes` 默认装到所有已探测客户端的用户级目录。
- `uninstall` — 目标规则与 install 相同。

客户端：Cursor、Claude Code、Codex、Trae、Windsurf、Qoder、OpenCode。探测依据是家目录标记和/或 PATH 上的可执行文件。`--dir` 是逃生口；不会写入 `~/.cursor/skills-cursor/`。

```bash
openwrt skill detect
openwrt skill install
openwrt skill install --agent cursor --yes
openwrt skill install --dir ~/skills --yes
```

### MCP 服务

可选 extra `openwrt-cli[mcp]`。入口：`openwrt-mcp` 与 `python -m openwrt_cli.mcp`（stdio）。工具薄封装现有 Service，共用一个 `DeviceClient`。`openwrt skill` / `openwrt mcp` 不 import 服务端，没装 extra 也能跑。

`openwrt mcp` 只打印，不改客户端配置文件：

- `json` — 可合并的 `mcpServers.openwrt`（Codex 为 TOML，OpenCode 用自己的格式）
- `prompt` — 给助手的安装说明
- `path` — 各客户端推荐的用户级 / 项目级路径

```json
{ "mcpServers": { "openwrt": { "command": "openwrt-mcp" } } }
```

```bash
openwrt mcp json --client cursor
claude mcp add openwrt -- openwrt-mcp
```

只读工具包括 `doctor`、`system_status`、`network_*`、`firewall_view`、`qos_view`、`service_*`、`passwall2_*`（节点列表不 Ping）、`logs_read`、`config_show`（密码已打码）。写入工具（`wifi_set`、`lan_set`、PassWall2 节点/ACL、`service_action`、`backup_create`、`user_key_add` 等）带 `destructiveHint`。`backup_create` 只写路由器 `/tmp`（仅文件名）。

### 权限

`~/.openwrt-cli.yaml` 的 `mcp.mode`（也可用 `openwrt config set --mcp-mode`）。默认 **readonly**：只读可用，写入返回 `mcp_readonly`，不动路由器。**readwrite** 须用户在客户端确认（或对话里明确同意）后才执行写入。`full` 在 `config set` 和 MCP 启动时都会拒绝（退出码 2）。没有 `OPENWRT_MCP_MODE` 环境变量覆盖。

门闩只拦 MCP 进程，人类 CLI 不改。MCP 已接通时，助手不要用 `openwrt … --yes` 改路由器（会绕过 `mcp.mode`）。

永不注册为 MCP 工具：重启、关机、恢复备份、用户增删改密。这些仍走人类 CLI（`openwrt system reboot --yes`）。

### 安装、向导、文档

- `install.sh` / `install.bat` 会装 `openwrt-cli[mcp]`，下一步列出 skill / MCP，并可接着跑 `openwrt setup` 和 `openwrt skill install`。
- `openwrt setup` 仍然只配语言和路由器。探测成功后展示 `mcp.mode`，下一步为 `doctor`、`skill install`、`mcp json`、`tui`。
- README **给 Agent 使用** / **Drop it into your AI agent**：三步接入、各客户端片段、工具范围，以及可粘贴的安装说明（`openwrt mcp prompt` 是按客户端生成的版本）。

### 变更

- `config show` 密码打码改为 `********`（原先 `***` 容易被终端当成格式标记）。
- Skill 安装向导改为与 `openwrt setup` 一样的单选（高亮即要安装的客户端）。一次装多个仍用 `--agent` / `--yes`。

## [1.1.1] - 2026-09-13

PassWall2 TUI 写入修订：重启成功但 UCI 未变，以及确认框吃掉服务名。

### 修复

- ACL 编辑只写入改过的字段。未改动的空端口 / 节点 Select 不再把 UCI 清回「使用全局」。
- HTTP `uci.set` / `commit` 固定在同一 rpcd 会话，带上 section type，写完回读。空写入不再接着问重启。
- 确认框不再把方括号里的名字（`[passwall2]`、ACL id）当成 Rich 标记吃掉。
- 保存节点或 ACL 分两次确认：先写入 UCI，再问是否立即重启。取消重启会提示「已保存，待重启」，之后按 `t` 再应用。

## [1.1.0] - 2026-09-13

功能版本 **1.1.0**。可选的 **PassWall2** 与 **Bandix 主机名** 成为 CLI / TUI 的一等能力。Agent JSON 契约不变：成功和失败都是带 `ok` 的同一个对象，业务字段摊平进该对象。

第 `7` 页、PassWall2 命令、Bandix 改名，只在路由器上有对应 LuCI 插件时出现。缺能力仍明确失败，不造假行。写入走 **UCI / ubus**，不是 LuCI CBI Save。成功 JSON 和日志不会打印 session / token。

### PassWall2（`luci-app-passwall2`）

只读（HTTP 或 SSH）：

- `passwall2 status` — 当前节点、ACL 开关、运行状态
- `passwall2 nodes` / `node show` / `node ping` — 列表（进入/刷新时测 Ping 与 TCPing）、单节点、ICMP / `--tcp` TCPing
- `passwall2 subscribe` / `settings` / `rules` / `components` / `components check`
- `passwall2 acl` / `acl show` / `acl log` — 仅 `acl_rule`；日志支持 `--tail` / `--since` / `--until`
- `passwall2 logs` — 运行日志，时间窗口同上

写入（UCI 键名与 LuCI 插件一致）：

- **节点** — `node add` / `node set` / `node delete`。可用分享链接（`--from-url`，vless / vmess / trojan / ss / hysteria2）或字段（`--type` `--protocol` `--remarks` `--group` `--address` `--port` `--username` `--password` `--uuid`）。额外选项：`--set key=value`（可重复）、`--unset`（set/delete）、`--raw`（允许未知键）。节点被引用时删除会再确认；`--force` 跳过该保护。
- **ACL** — `acl add` / `acl set` / `acl delete`，以及只改 `sources` 列表的 `acl source add` / `acl source remove`。一等选项：`--remarks` `--sources` `--node` `--enabled` `--log`。端口、接口、DNS 走 `--set`。空端口或空节点表示「使用全局」；`disable` 表示不使用。不会把 `Use global config (...)` 这类展示串写回 UCI。

`--apply` 提交后重启 PassWall2，使变更生效。重启失败**不会**回滚已写入的 UCI。rpcd 拒绝写入时使用独立错误 `pw2_write_denied`。

1.1.0 不做：订阅拉取/刷新、`clear_log`、组件安装或升级。

```bash
openwrt passwall2 nodes
openwrt passwall2 node add --from-url 'vless://...' --apply --yes
openwrt passwall2 node set cfgxxx --remarks 'HK' --set group=office --yes
openwrt passwall2 acl add --remarks 'iot' --sources 192.168.9.10 --node '' --yes
openwrt passwall2 acl source add cfgacl1 192.168.9.11 --yes
```

### Bandix 邻居主机名（`luci-app-bandix`）

改的是 Bandix 绑在 MAC 上的**自定义名**，不是 OpenWrt `system.@system[0].hostname`，也不是 DHCP/ARP 主机名。

- CLI：`openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name phone --yes`
- `--name` 为空则清除绑定。
- 请求体只有 `mac` + `hostname`，不碰限速类接口（如 `setRateLimit`）。
- TUI Neighbors（Bandix 叠加）：`e Edit` 打开改名；`r` 仍是刷新，两键不冲突。

```bash
openwrt network neighbors
openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name phone --yes
openwrt network set-hostname --mac aa:bb:cc:dd:ee:ff --name '' --yes
```

### TUI

- 探测到 `luci-app-passwall2` 才出现第 `7` 页 **PassWall2**。子页：节点、订阅、设置、规则、ACL、日志（`[` / `]` 切换）。
- 节点 / ACL：`a` 新建、`e` 编辑、`Del` 删除、`p` Ping、`c` TCPing、`l` 看 ACL 日志。表单与 CLI 写同一套 UCI。节点字段：备注 / 分组 / 类型 / 协议 / 地址 / 端口 / 用户名 / 密码；新建还可贴分享链接。
- Neighbors：仅 Bandix 模式下有 `e Edit`。底栏只在该页多出 `e Edit`，其它页仍是 `q` / `r` / `f` / `?`。
- 页内快捷键（节点/ACL 详情、服务操作、确认/改名/表单提示）的按键高亮与底栏一致。

### 修复

- `f` 筛选后，焦点回到表格即可再用 `e` / `a` / `Del`；只有筛选框有焦点时，这些键才当字符输入。

## [1.0.2] - 2026-09-13

打包修复。**1.0.1** 曾经上传后又删除；PyPI 不允许再次使用同一文件名（`openwrt_cli-1.0.1-py3-none-any.whl`），因此本版本为 **1.0.2**。功能与 1.0.1 相同。

- 发布打包时会把 README 里的 `docs/assets/` 图片改成 GitHub raw 地址，[PyPI 项目页](https://pypi.org/project/openwrt-cli/) 才能显示截图。仓库内 README 仍用相对路径。
- 安装：`pipx install openwrt-cli`（需要 Python >= 3.12）。

## [1.0.1] - 2026-09-12

**OpenWrt CLI** 的首个公开版本。本仓库没有更早的 1.0.0 标签，`1.0.1` 就是第一次正式发版。

OpenWrt CLI（`openwrt`）是面向 OpenWrt 路由器的远程管理工具。同一套 Service 支撑三层交互：CLI 表格（typer + rich）、setup/wizard 向导（questionary）和全屏 TUI（textual）。同一套设备模型走 **SSH** 或 **LuCI/ubus HTTP**；缺能力就明确失败，不造假数据。

主命令是 **`openwrt`**。`openwrt-cli` 仍会作为兼容别名安装；文档与 `--help` 一律写 `openwrt`。

### 亮点

- **三层交互** — `openwrt doctor` / `network` / `system` 表格、`openwrt setup` + `wizard`、以及 `openwrt tui`
- **同一套设备模型** — SSH 与 HTTP 共用 ubus / uci / shell 语义
- **doctor** — 按能力做体检，结果结构化，可直接 `-f json`
- **网络** — 接口、路由、策略规则、IPv4 邻居、带 MAC 厂商的 DHCP 租约，可选 Bandix 历史（`luci-app-bandix`）
- **Agent-ready** — `-f json` / `-f compact` / `--json`；成功和失败都是带 `ok` 的同一个对象
- **中英界面** — 命令名始终是英文；语言来自 `-L` / `OPENWRT_LANG` / 配置文件 / 系统 locale

### 命令分组

- **doctor** — 完整检查或 `--quick`
- **system** — status、info、board、cpu、memory、processes、disk、temperature、uptime、hostname；reboot / shutdown（非 TTY 需要 `--yes`）
- **logs** — system（logd）或 kernel（dmesg）；`--tail`、`--follow` / `-f`、`--since` / `--until`
- **network** — interfaces（`--rates`）、routes、rules、dns、dhcp、leases、neighbors、stats、traffic、wifi list/set、lan show/set、reload；`metrics` 读 Bandix 历史
- **firewall** — zones、rules、nat、redirects、status（走 iptables 的路径需要 SSH）
- **qos** — OpenWrt SQM / `tc`（`luci-app-sqm`），不是 Bandix 每设备限速
- **service** — list / show / start / stop / restart / reload / enable / disable（`/etc/init.d`，含 luci-app）
- **user** — list、groups、add、passwd、delete；`user key` list/add
- **backup** — create、restore、list
- **config** — 本地 `~/.openwrt-cli.yaml` 的 show / path / set
- **setup / wizard / tui** — 首次连接向导；设备上的 wifi / lan / hostname / user / service；实时仪表盘（`1`–`6` 切页，`r` 刷新，`f` 过滤，`q` 退出）

全局选项可以写在子命令前或后（`openwrt network leases -f json`）。交互命令（`setup`、`tui`、`wizard`）拒绝 JSON（`error: interactive`）。破坏性操作在 TTY 会提问；管道或 JSON 模式下必须加 `--yes`，否则退出码 `2`。

### 安装

需要 **Python >= 3.12**。

```bash
curl -fsSL https://raw.githubusercontent.com/Necho-dev/openwrt-cli/main/install.sh | bash
openwrt setup
openwrt doctor
```

```bash
pipx install git+https://github.com/Necho-dev/openwrt-cli.git
# 本版本上架 PyPI 之后：pipx install openwrt-cli
```

Windows：clone 后运行 `install.bat`，或 `pip install git+https://github.com/Necho-dev/openwrt-cli.git`。

### 路由器侧要求

- SSH（dropbear/openssh）和/或带 ubus HTTP(S) 的 LuCI
- 可选：`luci-app-bandix`，用于每设备速率和 `network metrics`
- 可选：`luci-app-sqm` / `sqm-scripts`，用于 `qos`

### 鸣谢

基于官方 OpenWrt 栈：[openwrt/openwrt](https://github.com/openwrt/openwrt)、[openwrt/luci](https://github.com/openwrt/luci)、[openwrt/uci](https://github.com/openwrt/uci)。灵感来自 [a6726170/openwrt-cli](https://github.com/a6726170/openwrt-cli)。

许可证：MIT。
