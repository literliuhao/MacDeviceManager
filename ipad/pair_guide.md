# iPad 无线配对 + tunneld 一次性设置指引

> 在插座到货、pymobiledevice3 安装好（install_deps.sh）之后，按下面步骤跑一次。
>
> 本指南命令按 **pymobiledevice3 v10.7+** 新版 CLI 编写。代码内已自动兼容老版本。

## 0. 前置
- 【首次配对必需】iPad USB-C 连 Mac，iPad 解锁，iPad 上点「信任此电脑」并输入锁屏密码
- 【之前已在本台 Mac 上 USB 信任过 → 直接跳过 0 和 1.2 插线步骤，用 WiFi-only 方式开始】
- iPad 和 Mac 同 Wi-Fi（同网段）
- iPad 设置 → 隐私与安全性 → 开发者模式 → 开启（需重启）

## 1. 枚举 iPad，获取 UDID

```bash
cd /Users/liuhao/work/DeviceManager
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"

# 方式 A：usbmuxd 列出 USB + WiFi 所有已知设备（插线时最准）
"$PY" -m pymobiledevice3 usbmux list

# 方式 B：纯 WiFi / Bonjour 发现（不插线时用）
"$PY" -m pymobiledevice3 bonjour mobdev2
```

在输出里复制 40 位 UDID，填到 `config/plug_config.yaml`：
```yaml
ipad_charger:
  udid: "000081xxxxxxxxxxxxxxxxxxxxxxxxx"
```

## 2. 开启 WiFi 无线通道

```bash
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"

# v10 新版：--state on；多台 iPad 加 --udid
"$PY" -m pymobiledevice3 lockdown wifi-connections --state on --udid 你的UDID
```

> 完成后可以拔掉 USB 线。以后只需要 WiFi。

## 3. 启动 tunneld（LaunchAgent，后台常驻，iOS 17+ 必开）

tunneld 是 pymobiledevice3 的守护进程，把 iPad 的 RemoteXPC 服务在本地建立隧道，使电量读取能无线走。

```bash
mkdir -p ~/Library/LaunchAgents
cp /Users/liuhao/work/DeviceManager/ipad/com.hermes.pymobiledevice3-tunneld.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.hermes.pymobiledevice3-tunneld.plist
```

验证：
```bash
launchctl list | grep hermes.pymobiledevice3   # 应有 PID
/Users/liuhao/.pyenv/versions/3.12.4/bin/python3 -m pymobiledevice3 remote rsd-info
```

## 4. 单独验证电量读取（WiFi 无线模式）

```bash
cd /Users/liuhao/work/DeviceManager
/Users/liuhao/.pyenv/versions/3.12.4/bin/python3 bin/ipad_battery.py
```

应返回：`{"level": 58, "charging": false, "mock": false, "udid": "...", "elapsed_ms": 123}`
或直接用 CLI 原始命令（v10 新）：
```bash
/Users/liuhao/.pyenv/versions/3.12.4/bin/python3 -m pymobiledevice3 diagnostics battery single --tunnel 你的UDID
```

## 5. 插座到了之后改 plug_config.yaml（对应 config/plug_config.yaml 结构）

```yaml
ipad_charger:
  enabled: true
  udid: "你的40位UDID"
  # 米家智能插座3 / cuco.plug.v3 局域网参数（填好后插座自动从 MOCK→真实）
  plug_ip: "192.168.xx.xx"
  plug_token: "你的32位miioTOKEN"
  # 阈值
  low_pct: 20                 # ≤20% 开充
  high_pct: 95                # ≥95% 停充
  check_interval_s: 60        # 轮询间隔（秒）
  fail_max: 10                # 连续10次读不到电量就强制关插座
  sleep_shutdown: true        # Mac 休眠时立刻关插座（避免 Mac 睡了 iPad 充通宵）
  mock:
    # 电量读取：false=调 pymobiledevice3 读真实值；true=返回下方 level/charging 模拟值
    enabled_battery: false
    # 插座控制：默认不用改 enabled_plug；plug_ip/token 留空自动进入 MOCK
    enabled_plug: false
    level: 50
    charging: false
```

## 6. Kickstart 使 watcher 立即重新读配置

```bash
launchctl kickstart -k gui/$(id -u)/local.miplug.lockwatcher
```

或在管理界面 iPad 面板点「保存配置」。
