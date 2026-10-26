# Omarchy Cast

Cast audio from one computer to another on your local network — like AirPlay, but native to Linux/PipeWire.

投送本机音频到局域网中的另一台电脑 —— 类似 AirPlay，但基于 Linux/PipeWire 原生实现。

**English** · [中文](#中文)

---

## English

### What it is

Omarchy Cast turns any PipeWire host on your LAN into a selectable audio output of another host. On the receiving machine it advertises itself with mDNS; on the sending machine it appears as a normal output device (`Cast: <host>`), so you can route a single app or all system audio to the other computer with one command.

- Lossless PCM over the LAN (~**1.5 Mbit/s**, ≈662 MiB/hour)
- Low latency (~1–3 ms on a wired LAN)
- mDNS auto-discovery — no IP addresses to remember
- Receives audio only from hosts you pick, by default
- A CLI, a GTK4 panel, and an Omarchy bar widget over the same commands

### Screenshots

Omarchy bar widget (left-click the speaker icon) and the standalone GTK4 panel:

| Bar widget | Desktop panel |
|------------|---------------|
| ![Audio Cast bar widget](docs/bar-widget.png) | ![Omarchy Cast desktop panel](docs/app-panel.png) |

### How it works

```
sender                                   receiver
┌──────────────────────────┐             ┌───────────────────────────────┐
│ app ─▶ omarchy_cast_<h>  │  TCP/4713   │ allowlist proxy               │
│   (module-tunnel-sink)   ├────────────▶│      │ unix socket            │
│                          │             │      ▼                        │
└──────────────────────────┘             │ pipewire-pulse ─▶ default out │
                                         └───────────────────────────────┘
```

- The **receiver** publishes an mDNS service (`_omarchycast._tcp`, default port `4713`) and serves that port itself, forwarding only allowed source IPs to PipeWire over its unix socket. PipeWire is given no TCP port of its own — see [Security](#security).
- The **sender** discovers it with `avahi-browse` and loads `module-tunnel-sink`, creating a virtual output device named `omarchy_cast_<host>`.
- Audio you send to that device is streamed over TCP and played on the receiver's default output.

### Requirements

- Linux with PipeWire + `pipewire-pulse` (Omarchy/Arch by default)
- `avahi` (for mDNS discovery) — `omarchy pkg add avahi`
- `python3` and `python-gobject` (GTK4 bindings are needed only for the GUI)
- `pactl` (from PipeWire/PulseAudio)

### Install

```bash
git clone https://github.com/yangkang5303/omarchy-cast.git
cd omarchy-cast
./install.sh              # installs launcher + systemd user service + desktop entry
omarchy-cast enable-receiver   # start receiving (and enable at login)
omarchy-cast allow add 192.168.1.10   # let that host cast to you
```

`./install.sh --enable` does the first two steps at once.

Re-running `./install.sh` to upgrade **restarts a receiver that is already
running**, so the version you just installed is the one enforcing your config.
Without that, the old process would keep serving the allowlist it read at
startup. `omarchy-cast status` shows which build is running.

The receiver accepts audio only from hosts you have allowed, so until you add
one it refuses every connection — see [Receiving audio](#receiving-audio).

Installed files:

| Path | Purpose |
|------|---------|
| `~/.local/share/omarchy-cast/` | the program |
| `~/.local/bin/omarchy-cast` | launcher (must be on `PATH`) |
| `~/.config/systemd/user/omarchy-cast-receiver.service` | receiver service |
| `~/.local/share/applications/org.omarchy.Cast.desktop` | launcher entry |

### Usage

Discover hosts on the network:

```bash
omarchy-cast list                 # table of discovered hosts
omarchy-cast list --json          # machine-readable
```

Cast your audio to another host:

```bash
omarchy-cast connect omarchy-44            # by display name
omarchy-cast connect 192.168.1.44          # by IP
omarchy-cast connect 0                     # by index from `list`
omarchy-cast connect omarchy-44 --no-default --no-move   # don't touch current routing
```

Stop casting:

```bash
omarchy-cast disconnect omarchy-44
omarchy-cast disconnect --all
```

### Receiving audio

Receiver control:

```bash
omarchy-cast enable-receiver     # start + enable at login
omarchy-cast start-receiver      # start now
omarchy-cast stop-receiver       # stop now
omarchy-cast disable-receiver    # stop + disable
omarchy-cast receive             # run in the foreground (used by systemd)
omarchy-cast status              # show receiver, allowlist, and connection state
```

**The receiver only accepts audio from hosts you pick.** `allow_mode` is
`specific` by default, and the allowlist starts empty, so a freshly installed
receiver advertises itself but refuses every incoming connection until you name
the hosts you trust:

```bash
omarchy-cast allow list          # show allow mode and allowed IPs
omarchy-cast allow add 192.168.1.10     # allow one host to cast here
omarchy-cast allow remove 192.168.1.10  # stop allowing a host
omarchy-cast allow mode specific # only allowed IPs may cast here (default)
omarchy-cast allow mode all      # opt out: any reachable host may cast here
```

In `specific` mode the receiver rejects connections from any source IP that is
not in the allowlist, so unallowed hosts cannot cast audio to this machine. A
running receiver re-reads the config once a second, so adding or removing a
host takes effect without a restart: a removed host can open no new connection
and the one it already had is cut off. Changing `allow_mode`, `port`, or
`listen` is picked up the same way, by rebinding the listener.

In the bar panel and the GTK4 window you pick hosts from the discovered-host
list instead: each host has an allow button, and "Restrict to allowed hosts" is
on by default.

`allow mode all` turns the restriction off and lets any host that can reach the
port cast to this machine; the receiver logs a warning while it is in that
state. Upgrading from a release before the allowlist existed switches you to
`specific` as well, so add your hosts after upgrading (or run `allow mode all`
to keep the old behaviour).

Graphical panel:

```bash
omarchy-cast gui
```

### Omarchy integration

The installer does not touch your desktop config. These are optional, manual edits.

Menu entry — add to `~/.config/omarchy/extensions/omarchy-menu.jsonc`:

```jsonc
"cast": {"icon":"󰕾","label":"Audio Cast","aliases":["cast","audio-cast"],"title":"Audio Cast"},
"cast.panel": {"icon":"󰕾","label":"Open panel","action":"uwsm-app -- \"$HOME/.local/bin/omarchy-cast\" gui"},
"cast.scan": {"icon":"󰍉","label":"Scan network","action":"omarchy-launch-floating-terminal-with-presentation omarchy-cast list"},
"cast.receive": {"icon":"󰑃","label":"Receive audio on this host","checked":"systemctl --user is-active --quiet omarchy-cast-receiver","action":"if systemctl --user is-active --quiet omarchy-cast-receiver; then omarchy-cast disable-receiver; else omarchy-cast enable-receiver; fi"},
"cast.disconnect": {"icon":"󰅖","label":"Disconnect all","action":"omarchy-cast disconnect --all"},
```

Keybinding — add to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + C", "Audio Cast", "uwsm-app -- \"$HOME/.local/bin/omarchy-cast\" gui")
```

### Omarchy shell plugin

This repository doubles as an [Omarchy shell plugin](https://plugins.omarchy.org/) that puts an **Audio Cast** icon in your bar. It is a thin front end over the CLI: it runs `omarchy-cast list --json` to discover hosts, `connect`/`disconnect` to route audio, and `enable-receiver`/`disable-receiver` to toggle receiving. Install the CLI first (see [Install](#install)); the widget shows a hint when `omarchy-cast` is missing.

```bash
omarchy plugin add https://github.com/yangkang5303/omarchy-cast.git --enable
# or, if already added:
omarchy plugin enable yangkang5303.audio-cast --section right
```

- Left-click the speaker icon to open the panel; right-click to launch the GTK4 app.
- The panel lists discovered hosts (click to connect/disconnect, or use the per-host button to add/remove that host from the allowlist), a switch to receive audio on this host, a "Restrict to allowed hosts" switch (on by default), a scan button (or press `r`), and a "disconnect all" action.

```bash
omarchy plugin disable yangkang5303.audio-cast
omarchy plugin remove yangkang5303.audio-cast
```

### Configuration

`~/.config/omarchy-cast/config.json`

| Key | Default | Meaning |
|-----|---------|---------|
| `display_name` | hostname | name other hosts see in discovery |
| `port` | `4713` | receiver TCP port |
| `listen` | `""` | bind address for the receiver (empty = all interfaces) |
| `allow_mode` | `"specific"` | `"specific"` = only `allowed_ips` may cast to this host; `"all"` = any reachable host may |
| `allowed_ips` | `[]` | IPs allowed to cast when `allow_mode` is `"specific"` (empty = none) |
| `set_default` | `true` | make the cast device the default output on connect |
| `move_streams` | `true` | move current playback to the cast device on connect |

```bash
omarchy-cast config                       # show current config
omarchy-cast config --set display_name "Living Room"
omarchy-cast config --set port 4713
omarchy-cast config --set listen 192.168.1.20   # bind one interface only
omarchy-cast config --set allow_mode specific
omarchy-cast config --set allowed_ips 192.168.1.10,192.168.1.11
```

An `allow_mode` that is neither `"specific"` nor `"all"` — a typo, a value from
an older release, an unreadable file — is treated as `"specific"`, so a config
the program cannot make sense of never opens the receiver up.

### Security

- **The receiver is restricted by default.** `allow_mode` defaults to `"specific"` and `allowed_ips` starts empty, so a new install accepts no incoming audio at all until you add the hosts you trust. The public port is served by a small TCP proxy that forwards only the source IPs in `allowed_ips`; every other source is closed before it reaches the audio server. This is enforced at the TCP layer, before the audio server sees the client at all. Manage it with `omarchy-cast allow ...`, the panel switch, or the GTK switch.
- **The audio server gets no TCP port of its own.** The proxy reaches PipeWire over its unix socket (`$XDG_RUNTIME_DIR/pulse/native`); `module-native-protocol-tcp` is never loaded, and the receiver unloads one left behind by an older version. A loopback port would be open to every other user account on the machine — loopback TCP has no owner check — so anyone with a local login could talk to the audio server directly and go around the allowlist. The socket is reachable only by its owner, because the runtime directory above it is mode `0700`. This holds in `allow_mode: "all"` too: that mode widens who the *proxy* accepts, not what PipeWire exposes.
- **Allowlist changes apply to the running receiver.** Every front end — `omarchy-cast allow`, the bar panel, the GTK window — writes the same config file, and the receiver re-reads it once a second: a removed host stops being allowed there and then, and its open connection is dropped, so what the panel shows is what the receiver enforces. No restart, and no window in which a host you just removed can still reach the audio server.
- **Only a receiver of the installed build does that.** One started by an older build keeps the allowlist it cached at startup, so an upgrade must replace the running process, not just the files on disk. Re-running the installer restarts it for you; the receiver also reports which build it is, so `omarchy-cast allow add/remove` tells you to restart instead of claiming a live apply when an older process is still running, and `omarchy-cast status` prints the running build next to the installed one and what that process is actually enforcing.
- A value of `allow_mode` that the program cannot parse is treated as `"specific"`: a missing key, a typo, a stale value, or an unreadable config file leaves the receiver restricted rather than open.
- PipeWire's `module-native-protocol-tcp` accepts only `port`, `listen`, and `auth-anonymous` — it has **no IP allowlist** and no way to restrict a port to one local user, which is why the proxy above exists and why the module is not loaded at all.
- Allowed hosts are identified by source IP only. That is a LAN trust decision, not authentication: it does not survive an attacker who can spoof or take over an allowed address. Restrict the port with a host firewall if your LAN is not a trust boundary you are comfortable with.
- **Cookie auth is not offered, because PipeWire does not enforce it.** Earlier versions documented `auth: "cookie"` as the stronger option. Loading `module-native-protocol-tcp` without `auth-anonymous=1` does not restrict anything on PipeWire's pulse server: a client presenting a forged cookie, or no cookie file at all, is still admitted and can list and control sinks. The `auth` key is gone rather than left in place as a setting that reads like a protection and is not one.
- `allow_mode: "all"` opts out of the restriction entirely: any client that can reach TCP `4713` — **not** just private address ranges — gets unrestricted access to your audio server. The receiver logs a warning for as long as it runs in that mode. If you want it, pair it with `listen` set to a single LAN address or a host firewall on TCP `4713`.
- A host name comes from the other machine's mDNS record, so it is whatever that machine published. It is kept printable and bounded, and the device description built from it drops quotes and backslashes, because it ends up inside a `sink_properties=` argument that pactl parses.
- Keep the receiver off on untrusted networks; it is off until you enable it.

### Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No hosts found` | Receiver not running on the other host (`enable-receiver`), or `avahi-daemon` down, or a firewall blocks UDP `5353` / TCP `4713` |
| Host is listed but `connect` plays nothing | The receiving host has not allowed you: run `omarchy-cast allow add <your ip>` there (or use its allow button in the panel), then try again |
| A host you removed can still cast to you | An older receiver is still running: `omarchy-cast status` shows the running build and what it enforces. Restart it (`omarchy-cast stop-receiver && omarchy-cast start-receiver`) |
| `pactl: command not found` | Install PipeWire/pulse tooling |
| GUI does not start | Install `python-gobject` |
| Audio stutters | Check Wi‑Fi signal; a wired LAN is recommended for lossless PCM |

### Uninstall

```bash
omarchy-cast uninstall      # removes launcher, service, desktop entry, program files
```

### Tests

Standard library only, no network and no audio server needed:

```bash
python3 tests/test_allowlist.py
```

---

## 中文

### 简介

Omarchy Cast 能把局域网内任意一台 PipeWire 主机变成另一台主机的可选音频输出设备。接收端通过 mDNS 广播自身；发送端会出现一个普通输出设备（`Cast: <主机名>`），一条命令即可把单个应用或全部系统声音投送到另一台电脑。

- 局域网内无损 PCM（约 **1.5 Mbit/s**，约 662 MiB/小时）
- 低延迟（有线局域网约 1–3 ms）
- mDNS 自动发现，无需记 IP
- 默认只接收你指定的主机发来的音频
- 命令行、GTK4 面板、Omarchy 状态栏小组件共用同一套命令

### 截图

Omarchy 状态栏小组件（左键点击喇叭图标）与独立的 GTK4 面板：

| 状态栏小组件 | 桌面面板 |
|--------------|----------|
| ![Audio Cast 状态栏小组件](docs/bar-widget.png) | ![Omarchy Cast 桌面面板](docs/app-panel.png) |

### 工作原理

```
发送端                                    接收端
┌──────────────────────────┐             ┌───────────────────────────────┐
│ 应用 ─▶ omarchy_cast_<h> │  TCP/4713   │ 白名单代理                    │
│   (module-tunnel-sink)   ├────────────▶│      │ unix socket            │
│                          │             │      ▼                        │
└──────────────────────────┘             │ pipewire-pulse ─▶ 默认输出    │
                                         └───────────────────────────────┘
```

- **接收端**通过 mDNS 发布服务（`_omarchycast._tcp`，默认端口 `4713`）并自己监听该端口，只把白名单内的来源 IP 转发给 PipeWire 的 unix socket。PipeWire 自身不开任何 TCP 端口，详见[安全](#安全)。
- **发送端**用 `avahi-browse` 发现它，加载 `module-tunnel-sink`，生成名为 `omarchy_cast_<主机>` 的虚拟输出设备。
- 发往该设备的音频经 TCP 传输，在接收端由默认输出设备播放。

### 环境要求

- Linux + PipeWire + `pipewire-pulse`（Omarchy/Arch 默认具备）
- `avahi`（用于 mDNS 发现）—— `omarchy pkg add avahi`
- `python3` 与 `python-gobject`（仅图形界面需要 GTK4 绑定）
- `pactl`（来自 PipeWire/PulseAudio）

### 安装

```bash
git clone https://github.com/yangkang5303/omarchy-cast.git
cd omarchy-cast
./install.sh                    # 安装启动器 + systemd 用户服务 + 桌面项
omarchy-cast enable-receiver    # 开始接收（并设为开机自启）
omarchy-cast allow add 192.168.1.10   # 允许该主机向本机投送
```

`./install.sh --enable` 可一步完成前两步。

重新执行 `./install.sh` 升级时，**会重启正在运行的接收端**，使刚安装的版本成为真正
执行配置的那个进程；否则旧进程会继续使用它启动时读到的允许列表。运行中的版本可用
`omarchy-cast status` 查看。

接收端只接受你已允许的主机，因此在添加之前会拒绝所有连接，详见[接收音频](#接收音频)。

安装位置：

| 路径 | 用途 |
|------|------|
| `~/.local/share/omarchy-cast/` | 程序本体 |
| `~/.local/bin/omarchy-cast` | 启动器（需在 `PATH` 中） |
| `~/.config/systemd/user/omarchy-cast-receiver.service` | 接收服务 |
| `~/.local/share/applications/org.omarchy.Cast.desktop` | 桌面启动项 |

### 使用

发现局域网内的主机：

```bash
omarchy-cast list                 # 表格列出
omarchy-cast list --json          # JSON 输出
```

投送音频到另一台主机：

```bash
omarchy-cast connect omarchy-44            # 按显示名
omarchy-cast connect 192.168.1.44          # 按 IP
omarchy-cast connect 0                     # 按 `list` 中的序号
omarchy-cast connect omarchy-44 --no-default --no-move   # 不改动当前路由
```

停止投送：

```bash
omarchy-cast disconnect omarchy-44
omarchy-cast disconnect --all
```

### 接收音频

接收端控制：

```bash
omarchy-cast enable-receiver     # 启动并设为开机自启
omarchy-cast start-receiver      # 立即启动
omarchy-cast stop-receiver       # 立即停止
omarchy-cast disable-receiver    # 停止并取消自启
omarchy-cast receive             # 前台运行（systemd 使用）
omarchy-cast status              # 查看接收端、允许列表与连接状态
```

**接收端只接受你指定的主机。** `allow_mode` 默认为 `specific`，且允许列表初始为空，
因此刚装好的接收端虽然会广播自身，但在你指定可信主机之前会拒绝所有入站连接：

```bash
omarchy-cast allow list          # 查看允许模式与已允许的 IP
omarchy-cast allow add 192.168.1.10     # 允许某台主机向本机投送
omarchy-cast allow remove 192.168.1.10  # 取消允许某台主机
omarchy-cast allow mode specific # 仅允许列表中的 IP 投送（默认）
omarchy-cast allow mode all      # 放开限制：任意可达主机均可投送
```

在 `specific` 模式下，接收端会拒绝所有不在允许列表中的来源 IP，未获允许的主机无法
向本机投送音频。运行中的接收端每秒重读一次配置，因此添加或移除主机无需重启即可生效：
被移除的主机无法再新建连接，已建立的连接也会被切断。修改 `allow_mode`、`port`、
`listen` 同样如此，接收端会重新绑定监听。

在状态栏面板和 GTK4 窗口中，可直接从发现的主机列表里挑选：每台主机旁有一个允许按钮，
“仅允许指定主机”开关默认开启。

`allow mode all` 会完全放开限制，让任何能连到该端口的主机都可向本机投送；处于该模式时
接收端会持续输出警告。从没有允许列表的旧版本升级同样会切到 `specific`，升级后请添加你的
主机（或执行 `allow mode all` 以保持旧行为）。

图形面板：

```bash
omarchy-cast gui
```

### Omarchy 集成

安装脚本不会改动你的桌面配置。如需把面板加入 Omarchy 菜单或设置快捷键，请手动添加以下内容（可选）。

菜单项 —— 追加到 `~/.config/omarchy/extensions/omarchy-menu.jsonc`：

```jsonc
"cast": {"icon":"󰕾","label":"Audio Cast","aliases":["cast","audio-cast"],"title":"Audio Cast"},
"cast.panel": {"icon":"󰕾","label":"Open panel","action":"uwsm-app -- \"$HOME/.local/bin/omarchy-cast\" gui"},
"cast.scan": {"icon":"󰍉","label":"Scan network","action":"omarchy-launch-floating-terminal-with-presentation omarchy-cast list"},
"cast.receive": {"icon":"󰑃","label":"Receive audio on this host","checked":"systemctl --user is-active --quiet omarchy-cast-receiver","action":"if systemctl --user is-active --quiet omarchy-cast-receiver; then omarchy-cast disable-receiver; else omarchy-cast enable-receiver; fi"},
"cast.disconnect": {"icon":"󰅖","label":"Disconnect all","action":"omarchy-cast disconnect --all"},
```

快捷键 —— 追加到 `~/.config/hypr/bindings.lua`：

```lua
o.bind("SUPER + ALT + C", "Audio Cast", "uwsm-app -- \"$HOME/.local/bin/omarchy-cast\" gui")
```

### Omarchy 状态栏插件

本仓库同时是一个 [Omarchy shell 插件](https://plugins.omarchy.org/)，会在状态栏放置一个 **Audio Cast** 图标。它是命令行工具的轻量前端：调用 `omarchy-cast list --json` 发现主机、用 `connect`/`disconnect` 投送或停止音频、用 `enable-receiver`/`disable-receiver` 开关本机接收。请先安装命令行工具（见[安装](#安装)）；若缺少 `omarchy-cast`，组件会给出提示。

```bash
omarchy plugin add https://github.com/yangkang5303/omarchy-cast.git --enable
# 或已添加过插件时：
omarchy plugin enable yangkang5303.audio-cast --section right
```

- 左键点击喇叭图标打开面板；右键启动 GTK4 图形界面。
- 面板列出发现的主机（点击连接/断开，或用每台主机旁的按钮将其加入/移出白名单）、本机接收开关、“仅允许指定主机”开关（默认开启）、扫描按钮（或按 `r`），以及“全部断开”操作。

```bash
omarchy plugin disable yangkang5303.audio-cast
omarchy plugin remove yangkang5303.audio-cast
```

### 配置

`~/.config/omarchy-cast/config.json`

| 键 | 默认值 | 含义 |
|----|--------|------|
| `display_name` | 主机名 | 其他主机在发现时看到的名字 |
| `port` | `4713` | 接收端 TCP 端口 |
| `listen` | `""` | 接收端绑定地址（留空表示所有网卡） |
| `allow_mode` | `"specific"` | `"specific"` = 仅 `allowed_ips` 可向本机投送；`"all"` = 任意可达主机均可 |
| `allowed_ips` | `[]` | `allow_mode` 为 `"specific"` 时允许投送的 IP 列表（空 = 不允许任何主机） |
| `set_default` | `true` | 连接时把投送设备设为默认输出 |
| `move_streams` | `true` | 连接时把当前播放迁移到投送设备 |

```bash
omarchy-cast config                       # 查看当前配置
omarchy-cast config --set display_name "客厅"
omarchy-cast config --set port 4713
omarchy-cast config --set listen 192.168.1.20   # 仅绑定单个网卡
omarchy-cast config --set allow_mode specific
omarchy-cast config --set allowed_ips 192.168.1.10,192.168.1.11
```

若 `allow_mode` 既不是 `"specific"` 也不是 `"all"`——拼写错误、旧版本留下的值、
文件读不出来——都会按 `"specific"` 处理，这样无法解析的配置绝不会把接收端放开。

### 安全

- **接收端默认受限。** `allow_mode` 默认为 `"specific"`，`allowed_ips` 初始为空，因此新安装的接收端在你添加可信主机之前完全不接受任何入站音频。公共端口由一个小的 TCP 代理提供服务，只转发 `allowed_ips` 中的来源 IP；其余来源在到达音频服务之前即被关闭。该限制在 TCP 层生效，客户端根本到不了音频服务。可通过 `omarchy-cast allow ...`、面板开关或 GTK 开关管理。
- **音频服务自身不开任何 TCP 端口。** 代理通过 PipeWire 的 unix socket（`$XDG_RUNTIME_DIR/pulse/native`）访问音频服务；`module-native-protocol-tcp` 完全不加载，若旧版本遗留了一个，接收端会将其卸载。回环 TCP 端口没有属主检查，对本机其他用户账号一律开放——任何拥有本地登录权限的人都能直连音频服务、绕开白名单。而该 socket 的上层运行目录权限为 `0700`，只有属主可达。`allow_mode: "all"` 同样如此：该模式放宽的是**代理**接受谁，而不是 PipeWire 对外暴露什么。
- **允许列表的修改会作用于运行中的接收端。** `omarchy-cast allow`、状态栏面板、GTK 窗口写的都是同一份配置文件，而接收端每秒重读一次：被移除的主机当即失去许可，其已建立的连接也会被切断，面板上显示的就是接收端实际执行的。无需重启，也不存在“刚移除却仍能连上音频服务”的时间窗。
- **只有与已安装版本一致的接收端才具备上述能力。** 由旧版本启动的进程会沿用它启动时缓存的允许列表，因此升级必须替换运行中的进程，而不只是替换磁盘上的文件。重新执行安装脚本会自动重启它；接收端还会上报自己的版本，因此在旧进程仍在运行时，`omarchy-cast allow add/remove` 会提示你重启，而不会声称已即时生效，`omarchy-cast status` 也会同时显示运行中与已安装的版本，以及该进程实际在执行的允许列表。
- 无法解析的 `allow_mode` 一律按 `"specific"` 处理：键缺失、拼写错误、旧值、配置文件读不出来，接收端都保持受限而不会放开。
- PipeWire 的 `module-native-protocol-tcp` 只接受 `port`、`listen` 与 `auth-anonymous`，自身**没有 IP 白名单**，也无法把端口限制给单个本地用户——这正是上述代理存在的原因，也是该模块根本不被加载的原因。
- 已允许的主机仅按来源 IP 识别。这是局域网层面的信任判断，不是身份认证：无法抵御能伪造或接管已允许地址的攻击者。若你的局域网不构成可接受的信任边界，请用主机防火墙限制该端口。
- **不提供 cookie 认证，因为 PipeWire 并不执行它。** 旧版本曾把 `auth: "cookie"` 写成更强的选项。实测表明，加载 `module-native-protocol-tcp` 时不带 `auth-anonymous=1` 并不带来任何限制：携带伪造 cookie、甚至完全没有 cookie 文件的客户端同样能连上，并可列出和控制 sink。因此 `auth` 配置项被直接移除，而不是留下一个看起来像防护、实际不是的开关。
- `allow_mode: "all"` 会完全放开该限制：任何能连到 TCP `4713` 的客户端都将获得对你音频服务的无限制访问，**并不限于私网地址段**。处于该模式期间接收端会持续输出警告。确有需要时，请把 `listen` 设为单个局域网地址，或用主机防火墙限制 TCP `4713`。
- 主机名来自对方机器的 mDNS 记录，内容完全由对方决定。本程序会将其限制为可打印字符并截断，且由它生成的设备描述会去掉引号与反斜杠——因为该字符串最终会进入由 pactl 解析的 `sink_properties=` 参数。
- 在不可信网络中请关闭接收端；接收端在你启用之前不会运行。

### 常见问题

| 现象 | 处理 |
|------|------|
| `No hosts found` | 对方接收端未运行（`enable-receiver`），或 `avahi-daemon` 未启动，或防火墙拦截了 UDP `5353` / TCP `4713` |
| 列表里能看到主机，但 `connect` 后没有声音 | 接收端尚未允许你：在接收端执行 `omarchy-cast allow add <你的 IP>`（或在面板中点该主机的允许按钮），然后重试 |
| 已移除的主机仍能向本机投送 | 说明仍有旧版本的接收端在运行：用 `omarchy-cast status` 查看运行中的版本及其实际执行的列表，然后重启（`omarchy-cast stop-receiver && omarchy-cast start-receiver`） |
| `pactl: command not found` | 安装 PipeWire/PulseAudio 工具 |
| 图形界面无法启动 | 安装 `python-gobject` |
| 声音卡顿 | 检查 Wi‑Fi 信号；无损 PCM 建议使用有线局域网 |

### 卸载

```bash
omarchy-cast uninstall      # 移除启动器、服务、桌面项与程序文件
```

### 测试

仅依赖标准库，不需要网络和音频服务：

```bash
python3 tests/test_allowlist.py
```
