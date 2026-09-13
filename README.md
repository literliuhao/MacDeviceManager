# MacDeviceManager — macOS 桌面设备管理工具

一个原生 macOS 本地运行的设备管理小工具，统一管理 Hermes 量化主机的三类周边设备。纯本地运行、不联网云控：所有插座控制走局域网 miIO 协议，iPad 电量走 pymobiledevice3 无线调试通道。

## 功能

### 水冷器 —— CPU 温度两阶段滞环控制
- 常态慢轮询（30s）+ 命中阈值后快速确认（5s × 5 次），防温度毛刺误触发
- `T > 60°C` 开水冷、`T < 36°C` 关水冷、中间带保持不变（滞环防震荡）
- 温度连续读取失败 → 安全回退为开启（宁费电不过热）

### 氛围灯 —— 时间窗口 + 锁屏倒计时
- 仅在时间窗口（默认 20:00–08:00，支持跨天）内动作
- 屏幕锁定 → 倒计时（默认 120s）后关灯；解锁/唤醒 → 立即开灯
- 监听 macOS 系统锁屏/唤醒事件（NSDistributedNotificationCenter）

### iPad Pro 自动充电 —— 无线电量监控
- pymobiledevice3 无线读取电量（iPad 不用插线，180s 轮询）
- `≤ 30%` 开充、`≥ 90%` 停充（充电中提前 5% 裕量，抵消插座断电后的涓流惯性）
- 双通道电量输入：Mac 无线轮询 + iPad 快捷指令 HTTP 上报（锁屏时兜底）
- 防抖设计：iOS 插电瞬间电量计校准跳变过滤、切换冷却期、连续无读数安全关插座

### GUI（PySide6 潮玻璃液态设计）
- 自定义标题栏、交通灯按钮嵌入、明暗主题切换
- 三设备独立配置面板，按页保存（互不污染）
- `⌘W` 关窗进程驻留 / `⌘Q` 真退出 / 点 Dock 图标重开窗口
- iPad 自动控制动态实时展示

## 目录结构

```
├── bin/                       # 核心可执行脚本
│   ├── device_manager.py      # GUI 主程序（PySide6）
│   ├── miplug_lock_watcher.py # 守护进程（锁屏/唤醒/温度/电量监听）
│   ├── miplug.py              # 水冷器插座控制 on|off
│   ├── ambilight_control.py   # 氛围灯插座控制 on|off|status
│   ├── ipad_plug.py           # iPad 充电插座控制（含 MOCK）
│   ├── ipad_battery.py        # iPad 无线电量读取（含 mock 兼容）
│   └── smctemp                # macOS CPU 温度读取（SMC）
├── config/
│   └── plug_config.example.yaml  # 配置模板（真实配置不入库）
├── launchd/                   # launchd plist 模板
├── app/                       # App Bundle 源文件
├── scripts/                   # 安装 / 构建 / 测试脚本 + 回归测试
├── ipad/                      # iPad 无线配对指南 & 依赖安装
├── resources/                 # 图标资源
└── doc/                       # 设计方案文档
```

## 环境要求

- macOS + Python 3.12（[pyenv](https://github.com/pyenv/pyenv)）
- 依赖：`PySide6`、`python-miio`、`pymobiledevice3`、`PyYAML`、`pyobjc`

```bash
pip install PySide6 python-miio pymobiledevice3 PyYAML pyobjc
```

## 快速开始

### 1. 配置

复制模板并填入设备信息（小米插座的 IP / miIO token 获取方式见 `doc/方案.md`）：

```bash
cp config/plug_config.example.yaml config/plug_config.yaml
```

所有阈值、IP、TOKEN、开关统一在 `config/plug_config.yaml`，代码不硬编码。

### 2. 安装守护进程（launchd）

```bash
./scripts/install.sh   # pip 依赖 + launchd plist 注册 + 启动
```

日常操作：

```bash
launchctl kickstart -k gui/$(id -u)/local.miplug.lockwatcher  # 改完配置立即重启生效
tail -f ~/Library/Logs/miplug.log                             # 实时日志
```

### 3. 启动 GUI

```bash
# 方式 A：命令行
python3 bin/device_manager.py

# 方式 B：构建 App Bundle（双击 / Spotlight 启动）
./scripts/build_app.sh
open ~/Applications/DeviceManager.app
```

### 4. iPad 无线通道（可选）

USB 配对一次后启用无线调试（详见 `ipad/pair_guide.md`）：

```bash
launchctl load ~/Library/LaunchAgents/com.hermes.pymobiledevice3-tunneld.plist
```

## iPad 快捷指令上报（可选）

iPad 上创建「充电器已连接」自动化，执行 SSH/curl 通知 Mac：

```
curl "http://<mac-ip>:8737/battery?level=<电量>&charging=1"
```

锁屏时 Mac 无线通道读不到电量的情况下，作为兜底输入。

## MOCK 模式（无真设备验证）

设备未到货 / 未配对时全链路 mock 验证：

- **插座 MOCK**：配置里 `plug_ip` / `plug_token` 留空 → 命令只写日志 + 文件缓存，不真实发送 miIO
- **电量 MOCK**：`ipad_charger.mock.enabled_battery: true` → 读取可配置的假电量，验证阈值触发

```bash
./scripts/project_mock_test.sh
```

## 测试

回归测试套件（RED/GREEN 式离线验证，不需要真设备）：

```bash
python3 scripts/.rc_cooler_red.py         # 水冷滞环 / fast-confirm / 状态恢复
python3 scripts/.ipad_cooler_red.py       # iPad 配置保存不污染水冷 + 状态恢复匹配
python3 scripts/.rc_ambil_save_key_red.py  # 面板保存键名归一化
python3 scripts/.rc_cmdw_quit_red.py      # ⌘W/⌘Q/Dock 重开窗口生命周期
```

## 排障

| 问题 | 排查 |
|---|---|
| launchd 下插座命令偶发超时 | miIO UDP 已强制绑 `en0`（避开 Parallels 桥接劫持）；检查插座离线 / token 正确性 |
| iPad 电量读不到 | 先 USB 配对过一次；tunneld 已启动（`launchctl list \| grep hermes.pymobiledevice3`）；iPad 解锁且同局域网 |
| 日志位置 | `~/Library/Logs/miplug.log` |

## 安全说明

真实配置 `config/plug_config.yaml`（含 miIO token、UDID、内网 IP）已加入 `.gitignore`，**不会入库**。仓库只提供脱敏模板 `config/plug_config.example.yaml`。

## 许可

个人自用项目，暂未指定开源协议。
