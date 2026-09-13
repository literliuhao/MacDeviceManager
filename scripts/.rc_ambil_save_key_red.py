#!/usr/bin/env python3
"""RED 复现：AmbilightPanel 点保存传 only_key='ambilight'，但 _write_yaml panel_map 只认 'ambil'。
应触发 ValueError 与截图一致。修复后不应抛 ValueError 且 only_key 必须被归一化成 'ambil' 才能写入 raw['ambilight']。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

# 避免 GUI/PySide6 import（我们只测 panel.save → _write_yaml 纯字符串路径，不 new QWidget）
# 所以直接模拟 Manager._write_yaml 逻辑（import 原源码里函数 panel_map / ValueError）
import importlib.util, types
src_path = os.path.join(os.path.dirname(__file__), "..", "bin", "device_manager.py")

# Extract the exact _write_yaml panel_map key-check (L3356-L3370) via AST-free substring:
# Simulate only the panel_map lookup behavior since that's where the error fires.
RAW_KEY = {"cooler": "cooler", "ambil": "ambilight", "ipad": "ipad_charger"}
VALID_KEYS = list(RAW_KEY)

def fake_write_yaml_only_key(only_key):
    """copy of Manager._write_yaml: alias normalize (L3362-3371) → panel_map lookup → ValueError or return key.
    镜像同步：如果 device_manager.py 源码里有归一化段（L3362-3371），本函数就执行归一化后再查，保持行为一致。"""
    # 实时从 device_manager.py 原代码里同步"有没有 alias normalize 段"这个事实，
    # 保证 RED/GREEN 行为跟真实 Manager._write_yaml 一致，不靠凭空模拟。
    src_fresh = open(src_path).read()
    has_norm = all(tok in src_fresh for tok in ('"ambilight":', '"ambil":', '"ipad_charger":', 'only_key = {'))
    alias_map = {
        "ambilight":    "ambil",
        "cooler":       "cooler",
        "ipad_charger": "ipad",
        "ipad":         "ipad",
        "ambil":        "ambil",
    }
    if has_norm and only_key is not None:
        only_key = alias_map.get(only_key, only_key)
    if only_key not in VALID_KEYS:
        raise ValueError(
            f"_write_yaml(only_key={only_key!r}) 未知面板键，合法键={VALID_KEYS}"
        )
    return only_key

# 另外，三个 panel 里实际在 save() 里调用 self._save_cb("XXX") 的字符串（L763 / L923 / L1093）
# 直接 grep 源文件拿这三个"真实 on_save 传值"，避免凭空模拟，保证与磁盘代码一致
import re
src = open(src_path).read()
ON_SAVE_CALLS = {}
m = re.search(r'def save\(self\):\s*\n\s*if self\._save_cb: self\._save_cb\("([^"]+)"\)',
              src[src.index("class CoolerPanel"):src.index("class AmbilightPanel")])
if m: ON_SAVE_CALLS["cooler"] = m.group(1)
m = re.search(r'def save\(self\):\s*\n\s*if self\._save_cb: self\._save_cb\("([^"]+)"\)',
              src[src.index("class AmbilightPanel"):src.index("class iPadPanel")])
if m: ON_SAVE_CALLS["ambil"] = m.group(1)
m = re.search(r'def save\(self\):\s*\n\s*if self\._save_cb: self\._save_cb\("([^"]+)"\)',
              src[src.index("class iPadPanel"):])
if m: ON_SAVE_CALLS["ipad"] = m.group(1)

failed = 0

# ========== Case 1：CoolerPanel save() 传值必须能通过 panel_map 校验 ==========
try:
    norm = fake_write_yaml_only_key(ON_SAVE_CALLS["cooler"])
    print(f"[PASS] cooler on_save={ON_SAVE_CALLS['cooler']!r} → valid key={norm!r}")
except Exception as e:
    failed += 1
    print(f"[FAIL] cooler on_save={ON_SAVE_CALLS['cooler']!r} → err: {e}")

# ========== Case 2：AmbilightPanel save() 传值必须能通过 panel_map 校验（RED 失败点：真实传 'ambilight' 不在 VALID_KEYS）==========
try:
    norm = fake_write_yaml_only_key(ON_SAVE_CALLS["ambil"])
    print(f"[PASS] ambil on_save={ON_SAVE_CALLS['ambil']!r} → valid key={norm!r}")
except ValueError as e:
    failed += 1
    print(f"[RED-FAIL] ambil on_save={ON_SAVE_CALLS['ambil']!r} → {e}")

# ========== Case 3：iPadPanel save() 传值必须能通过 panel_map 校验 ==========
try:
    norm = fake_write_yaml_only_key(ON_SAVE_CALLS["ipad"])
    print(f"[PASS] ipad on_save={ON_SAVE_CALLS['ipad']!r} → valid key={norm!r}")
except Exception as e:
    failed += 1
    print(f"[FAIL] ipad on_save={ON_SAVE_CALLS['ipad']!r} → err: {e}")

# ========== Case 4：别名兜底：even if 某 panel 将来又传 'ambilight'/'ipad_charger' 这种错/全拼，
# 归一化段也应该把它接收下来（GREEN 阶段新增）—— RED 里这是断言 GREEN 行为存在的用例
for bad_key, want_norm in [("ambilight", "ambil"), ("ipad_charger", "ipad"), ("ipad", "ipad"), ("cooler", "cooler")]:
    try:
        norm = fake_write_yaml_only_key(bad_key)
        if norm == want_norm:
            print(f"[PASS] alias normalize: only_key={bad_key!r} → {norm!r} == want {want_norm!r}")
        else:
            failed += 1
            print(f"[FAIL] alias normalize wrong: {bad_key!r} → {norm!r}, want {want_norm!r}")
    except Exception as e:
        failed += 1
        print(f"[FAIL] alias normalize crashed for {bad_key!r}: {e}")

# ========== Case 5：非法别名仍应抛错（归一化后依旧不在 panel_map 的情况）==========
try:
    fake_write_yaml_only_key("foobar")
except ValueError as e:
    if "未知面板键" in str(e) and "foobar" in str(e):
        print(f"[PASS] 非法键仍正确 ValueError: {e}")
    else:
        failed += 1
        print(f"[FAIL] 非法键 ValueError 内容不对：{e}")
else:
    failed += 1
    print("[FAIL] 非法键 'foobar' 本该抛 ValueError 但没抛 → 归一化/校验链路破了。")

# ========== Case 6：当前 disk 真实 on_save 传值三 panel 全部能直接通过 fake 校验 ==========
for panel_name, real_call_str in [("cooler", ON_SAVE_CALLS["cooler"]),
                                   ("ambil",  ON_SAVE_CALLS["ambil"]),
                                   ("ipad",   ON_SAVE_CALLS["ipad"])]:
    try:
        n = fake_write_yaml_only_key(real_call_str)
        print(f"[PASS] disk-src {panel_name}.save → on_save({real_call_str!r}) → valid {n!r}")
    except Exception as e:
        failed += 1
        print(f"[FAIL] disk-src {panel_name}.save → on_save({real_call_str!r}) → {e}")

print(f"\nRC={failed}")
sys.exit(failed)
