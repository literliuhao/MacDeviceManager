#!/bin/zsh
# ============================================================
# 项目级 Mock 链路验证脚本（不用真实设备，直接跑通决策）
# Usage: cd /Users/liuhao/work/DeviceManager && ./scripts/project_mock_test.sh
# ============================================================
set -e
PROJ="/Users/liuhao/work/DeviceManager"
PY="/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"
cd "$PROJ"

echo "============ [1/3] 水冷器单命令 MOCK（miplug.py）============ "
"$PY" bin/miplug.py on 2>&1 || true
"$PY" bin/miplug.py off 2>&1 || true

echo ""
echo "============ [2/3] iPad 插座 MOCK 开/关/状态 ============"
# 先清旧 cache
rm -f ~/.cache/miplug/ipad_plug_state
"$PY" bin/ipad_plug.py on && echo -n "  status (期望 on): " && "$PY" bin/ipad_plug.py status
"$PY" bin/ipad_plug.py off && echo -n "  status (期望 off): " && "$PY" bin/ipad_plug.py status

echo ""
echo "============ [3/3] watcher 3 轮 iPad 决策（mock level=15，期望开充） ============"
cp config/plug_config.yaml /tmp/dm_cfg_backup.yaml
"$PY" -c "
import yaml
with open('config/plug_config.yaml') as f: cfg = yaml.safe_load(f)
cfg['ipad_charger']['mock']['level'] = 15
cfg['ipad_charger']['mock']['charging'] = False
with open('config/plug_config.yaml', 'w') as f: yaml.dump(cfg, f, allow_unicode=True, sort_keys=False)
"
"$PY" bin/miplug_lock_watcher.py --mock-test 3 2>&1 | tail -5 || true
# 看日志最后 20 行 ipad 相关
echo '  日志片段:'
grep -E 'MOCK-MAIN|ipad:' /Users/liuhao/Library/Logs/miplug.log 2>/dev/null | tail -12 || true
echo -n "  插座状态 (期望 on): "
"$PY" bin/ipad_plug.py status

# 还原配置
cp /tmp/dm_cfg_backup.yaml config/plug_config.yaml

echo ""
echo "=== 验证结束。如所有 status 符合期望，则迁移成功 ==="
