#!/bin/zsh
# ============================================================
# iPad 无线自动充电 — 依赖安装脚本（插座/配对完成后执行一次）
# 使用：chmod +x install_deps.sh && ./install_deps.sh
# ============================================================
set -e
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"

echo "[1/2] 安装 / 更新 pymobiledevice3（iPad 无线电量读取 + tunneld）"
"$PY" -m pip install -U pymobiledevice3

echo ""
echo "[2/2] 验证 python-miio 已安装（miplug 依赖，一般已存在，失败再装）"
"$PY" -c "import miio; print('  miio OK:', miio.__version__ if hasattr(miio, '__version__') else 'ok')" 2>/dev/null || \
"$PY" -m pip install -U python-miio

echo ""
echo "=== 安装完成 ==="
echo "- 下一步：把 iPad 用 USB-C 连 Mac，然后按 pair_guide.md 走一次无线配对"
echo "- 之后启动 tunneld（见 com.hermes.pymobiledevice3-tunneld.plist）"
echo "- 最后改 plug_config.yaml 里 ipad_charger.enabled=true + 填 IP/TOKEN/UDID，然后 kickstart 守护进程"
