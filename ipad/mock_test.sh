#!/bin/zsh
# ============================================================
# iPad 充电链路 — 本地 MOCK 验证脚本（不用等插座/配对，直接跑）
# 使用：cd /Users/liuhao/bin/ipad && ./mock_test.sh
# ============================================================
set -e
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"
cd /Users/liuhao/bin

echo ">>> 测试 1: ipad_plug.py 手动开/关（MOCK 模式，会在 ~/.cache 写状态缓存）"
"$PY" ipad_plug.py on
echo "--- status:"
"$PY" ipad_plug.py status
"$PY" ipad_plug.py off
echo "--- status:"
"$PY" ipad_plug.py status

echo ""
echo ">>> 测试 2: ipad_battery.py 单独读电量（mock_cfg 直接写在测试里，不用 pymobiledevice3）"
"$PY" -c "
import importlib.util
spec = importlib.util.spec_from_file_location('ib', '/Users/liuhao/bin/ipad_battery.py')
ib = importlib.util.module_from_spec(spec); spec.loader.exec_module(ib)
# 模拟 35%，未充电
print('case A:', ib.get_ipad_battery(mock_cfg={'enabled': True, 'level': 35, 'charging': False}))
# 模拟 98%，正在充电
print('case B:', ib.get_ipad_battery(mock_cfg={'enabled': True, 'level': 98, 'charging': True}))
"

echo ""
echo ">>> 测试 3: miplug_lock_watcher.py 的 --mock-test（不启动真实 launchd，只跑两轮逻辑）"
echo "    这会读取 plug_config.yaml 的 ipad_charger.mock.enabled_battery/level 来驱动决策。"
"$PY" miplug_lock_watcher.py --mock-test --mock-rounds 3 || true

echo ""
echo "=== 跑完。真实设备到了之后：改 plug_config.yaml 填 IP+TOKEN+UDID，然后执行 pair_guide.md 第 1-6 步 ==="
