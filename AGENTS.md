# DeviceManager — macOS 桌面设备管理系统

## 项目定位
一个原生 macOS 上运行的本地设备管理小工具，统一管理 Hermes 量化主机的三类周边：
- **水冷器**：基于 CPU 核心温度的两阶段滞环控制（慢轮询 30s + 快确认 5s/3 次）
- **氛围灯**：时间窗口 + 锁屏倒计时（晚上关灯、早上自动亮、解锁瞬亮）
- **iPad Pro 自动充电**：无线读电量，<20% 开充、>95% 停充（含连续失败关、休眠关）

纯本地运行，不联网云控。所有控制走局域网 miIO（小米智能插座3 / cuco.plug.v3），
iPad 电量走 pymobiledevice3 无线调试通道。

## 技术栈（与主 Hermes 一致）
- **Python 3.12**（pyenv path: `/Users/liuhao/.pyenv/versions/3.12.4/bin/python3`）
- **GUI**：PySide6（潮玻璃液态设计，交通灯按钮嵌入标题栏）
- **量化库**：pandas/numpy（当前未使用，保留规范）
- **插座控制**：python-miio（`miio.MiotDevice`，所有 UDP socket 强制绑定 `en0` 避开 Parallels 桥接劫持）
- **iPad 无线电量**：pymobiledevice3 + `remote tunneld`（launchd 常驻）
- **日志**：`~/Library/Logs/miplug.log`（watcher + 控制命令统一前缀）
- **启动**：launchd 两个 LaunchAgent + 一个 macOS App Bundle (`DeviceManager.app`)

## 目录结构
```
/Users/liuhao/work/DeviceManager/
├── AGENTS.md                  # 本文件
├── bin/                       # 核心可执行脚本（所有 .py / smctemp）
│   ├── device_manager.py      # GUI 主程序（PySide6）
│   ├── miplug_lock_watcher.py # 守护进程（ObjC runloop 监听锁屏/睡眠/温度/电量）
│   ├── miplug.py              # 水冷器插座控制 on|off
│   ├── ambilight_control.py   # 氛围灯插座控制 on|off|status [--force]
│   ├── ipad_plug.py           # iPad 充电插座控制 on|off|status（含 MOCK 模式）
│   ├── ipad_battery.py        # iPad 电量读取模块（mock 兼容未配对设备）
│   └── smctemp                # macOS CPU 温度读取二进制（smc 驱动）
├── config/
│   └── plug_config.yaml       # 所有阈值 / IP / TOKEN / 开关（**唯一配置源**）
├── resources/
│   ├── dm_icon.png            # 1024×1024 图标源文件
│   └── DM.icns                # macOS App Bundle 用的 .icns
├── ipad/                      # iPad 无线配对 & 依赖安装指南
│   ├── install_deps.sh        # 安装 pymobiledevice3
│   ├── pair_guide.md          # USB 一次配对 + 无线启用步骤
│   ├── mock_test.sh           # iPad 链路单独 mock 测试
│   └── com.hermes.pymobiledevice3-tunneld.plist  # tunneld LaunchAgent（可直接加载）
├── launchd/                   # 所有 launchd plist 模板
│   ├── local.miplug.lockwatcher.plist              # 水冷+氛围灯+iPad 三合一守护进程
│   └── com.hermes.pymobiledevice3-tunneld.plist    # tunneld 副本（同 ipad/ 下）
├── app/                       # App Bundle 源文件
│   ├── Info.plist
│   └── DeviceManager          # launcher shell 脚本（build_app.sh 会复制到 App bundle）
└── scripts/
    ├── install.sh             # 一键：pip 依赖 + launchd plist 复制+加载 + kickstart
    ├── build_app.sh           # 生成 /Users/liuhao/Applications/DeviceManager.app
    └── project_mock_test.sh   # 项目级全链路 mock 验证（不用真设备）
```

## 关键约定

### 1. 唯一配置源
所有阈值、IP、TOKEN、开关都在 `config/plug_config.yaml`，**任何代码都不要在脚本里硬编码数字**。
改完配置后让 watcher 立刻重读取：
```bash
launchctl kickstart -k gui/$(id -u)/local.miplug.lockwatcher
```

### 2. 路径自动探测
所有 `bin/*.py` 顶部都统一实现了 PROJECT_ROOT 探测：
- 优先读取环境变量 `DEVICE_MANAGER_HOME`（App launcher 已 export）
- 否则按 `Path(__file__).resolve().parent.parent` 推导（即 bin/ 目录的父级 = 项目根）

因此项目随便挪位置，只要 launcher 里的 `DEVICE_MANAGER_HOME` 跟着改或脚本在 bin/ 下正常运行，就不会写死路径。

### 3. MOCK 模式（设备未到货 / 未配对）
- **插座 MOCK**：`plug_ip` 或 `plug_token` 留空 → 命令只打印日志 + 文件缓存（`~/.cache/miplug/ipad_plug_state`），**不真实发送 miIO**
- **电量 MOCK**：`plug_config.yaml → ipad_charger.mock.enabled_battery=true` → 读假值
  （level/charging 可任意改，验证触发阈值）
- 全链路验证：`./scripts/project_mock_test.sh`

## 启动 & 重启

### GUI 界面
```bash
# 方案 A：命令行直接运行
python3 bin/device_manager.py

# 方案 B：生成 App Bundle（双击启动 / Spotlight 启动）
./scripts/build_app.sh
open /Users/liuhao/Applications/DeviceManager.app
```

### 守护进程（watcher，推荐 launchd）
```bash
./scripts/install.sh        # 注册 + 加载 + kickstart

# 后续日常操作
launchctl kickstart -k gui/$(id -u)/local.miplug.lockwatcher   # 立即重启（改完配置）
launchctl unload ~/Library/LaunchAgents/local.miplug.lockwatcher.plist  # 彻底停
tail -f ~/Library/Logs/miplug.log                                # 实时看日志
```

### iPad 无线通信守护（tunneld，需要时才启用）
```bash
# iPad 配对完成后执行：
launchctl load ~/Library/LaunchAgents/com.hermes.pymobiledevice3-tunneld.plist
# 验证：pymobiledevice3 list 能看到 UDID
```

## 常见问题 & 排障

1. **macOS 沙箱 Permission denied writing 文件**
   - 永远不要把运行时状态（cache/log）写在项目目录或 `~/bin/` 下，
     统一写 `~/Library/Logs/`（日志）或 `~/.cache/miplug/`（临时状态）。

2. **launchd 下插座命令偶发超时**
   - 所有 miIO UDP 已通过 `SO_BOUND_IF` 强制绑 `en0`，
     若还超时检查插头离线 / miIO TOKEN 是否正确 / 路由是否丢包。

3. **iPad 电量读不到**
   - 先 USB 配对过一次（见 `ipad/pair_guide.md`）
   - tunneld 必须起来：`launchctl list | grep hermes.pymobiledevice3`
   - iPad 解锁屏，Mac 与 iPad 在同一局域网。

## 全局硬约束（从主 Hermes AGENTS.md 继承）
- 不做毁灭性操作（`rm -rf /` 这类）
- 不修改系统级配置
- 资金/交易类操作需要二次确认
- 不访问隐私路径（`~/.ssh`、`~/Library/Messages` 等）
- 飞书推送仅限金融报告，设备管理日常对话**不推**飞书群
