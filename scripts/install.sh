#!/bin/zsh
# ============================================================
# DeviceManager 一键安装脚本（把 launchd plist 注册进系统）
# Usage: cd /Users/liuhao/work/DeviceManager && ./scripts/install.sh
# ============================================================
set -e
PROJ="/Users/liuhao/work/DeviceManager"
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"
UID=$(id -u)

echo "[1/4] 安装 pymobiledevice3（iPad 无线电量读取依赖）"
"$PY" -m pip install -U pymobiledevice3 2>&1 | tail -3 || true
echo "  （如不需要 iPad 功能可忽略 pip 报错）"

echo ""
echo "[2/4] 注册 lockwatcher LaunchAgent（水冷 + 氛围灯 + iPad 守护进程）"
mkdir -p ~/Library/LaunchAgents
cp -f "$PROJ/launchd/local.miplug.lockwatcher.plist" ~/Library/LaunchAgents/
# unload 再 load 避免重复
launchctl unload ~/Library/LaunchAgents/local.miplug.lockwatcher.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/local.miplug.lockwatcher.plist
echo "  已加载 local.miplug.lockwatcher"

echo ""
echo "[3/4] 注册 tunneld LaunchAgent（iPad 无线通信，需要时才启用，默认不加载）"
cp -f "$PROJ/launchd/com.hermes.pymobiledevice3-tunneld.plist" ~/Library/LaunchAgents/
echo "  plist 已复制到 LaunchAgents。iPad 配对完成后手动加载："
echo "    launchctl load ~/Library/LaunchAgents/com.hermes.pymobiledevice3-tunneld.plist"

echo ""
echo "[4/4] kickstart lockwatcher 立即启动"
launchctl kickstart -k "gui/$UID/local.miplug.lockwatcher" && echo "  lockwatcher 已启动"
echo "  日志查看: tail -f ~/Library/Logs/miplug.log"

echo ""
echo "=== 安装完成 ==="
echo "GUI 启动请运行: ./scripts/build_app.sh && open /Users/liuhao/Applications/DeviceManager.app"
