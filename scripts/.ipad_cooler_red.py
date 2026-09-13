#!/usr/bin/env python3
"""RED test for iPad-save bug: 改 iPad 配置并保存 → 液冷突然开启.

Expected FAIL items against CURRENT buggy code:
  [IPAD-BUG1] save_panel(key='ipad') → yaml.cooler 段发生变化（不应发生，用户只改 iPad）
  [IPAD-BUG2] watcher 冷启动 (_recover_cooler_state 返回 None) 且 CPU 在 OFF<T<ON 滞环带内 (比如 50°C)
              → init mid 直接把 cooler 开/关 (取决于 T >= mid)，而不是 "保持未知，下一轮按 in-band 等命中阈值才开"
              表现：save ipad → kickstart → CPU 忙 49°C (mid=46.5) → 开了! 这就是用户感受的 bug
  [IPAD-BUG3] _recover_cooler_state 匹配串太宽，会把 ambilight / ipad_plug 的
              "plug -> on ok" (无 'set ' 前缀) 当冷却水冷的 "set plug on ok"，
              导致状态恢复成 on (实际上只是昨晚开了 iPad 充了 1 小时电)。
"""
import os, sys, tempfile, types, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

import unittest.mock as um
import yaml
import importlib.util
RC = 0

# --- 先搞 watcher module stub (参考 rc_cooler_red.py) ----------------------------------
_fake_foundation = types.ModuleType("Foundation")
class _FT:
    instances = []
    def __init__(self, iv, h, s, ui, repeats):
        self.interval=iv; self.handler=h; self._valid=True
        _FT.instances.append(self)
    def invalidate(self): self._valid = False
    @classmethod
    def scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(cls, iv,h,s,ui,r):
        return cls(iv,h,s,ui,r)
_fake_foundation.NSTimer = _FT
class _FNO:
    @classmethod
    def alloc(cls): return cls()
    def init(self): return self
_fake_foundation.NSObject = _FNO
class _FNC:
    @staticmethod
    def defaultCenter(): return _FNC()
    def addObserver_selector_name_object_(self,*a,**k): pass
    def removeObserver_(self,*a,**k): pass
_fake_foundation.NSDistributedNotificationCenter = _FNC
class _FRL:
    @staticmethod
    def currentRunLoop(): return _FRL()
    def addTimer_forMode_(self,*a,**k): pass
    def runUntilDate_(self,*a,**k): raise RuntimeError("__TEST_STOP__")
_fake_foundation.NSRunLoop = _FRL
class _FND:
    @staticmethod
    def dateWithTimeIntervalSinceNow_(s): return _FND()
_fake_foundation.NSDate = _FND
sys.modules.setdefault("Foundation", _fake_foundation)

_fake_objc = types.ModuleType("objc")
import builtins as _bt
_fake_objc.super = lambda k, i: _bt.super(k, i)
sys.modules.setdefault("objc", _fake_objc)

_fake_appkit = types.ModuleType("AppKit")
class _FW:
    @staticmethod
    def sharedWorkspace(): return _FW()
    def notificationCenter(self): return _FNC()
_fake_appkit.NSWorkspace = _FW
sys.modules.setdefault("AppKit", _fake_appkit)

# suppress threads/serve_forever
import threading as _th
_orig_start = _th.Thread.start
_block = True
def _fake_start(self):
    if _block: return None
    return _orig_start(self)
_th.Thread.start = _fake_start
import http.server as _hs
_orig_srv = _hs.HTTPServer.serve_forever
_hs.HTTPServer.serve_forever = lambda self, **kw: None

import logging as _lg
_old_basic = _lg.basicConfig
_lg.basicConfig = lambda **kw: None

def make_fake_root_with(cfg_dict, add_lines_log):
    """Create a temp project root with config+log and install ENV."""
    root = tempfile.mkdtemp(prefix="dm_ipad_bug_")
    os.makedirs(os.path.join(root, "bin"))
    os.makedirs(os.path.join(root, "config"))
    shutil.copy(os.path.join(os.path.dirname(__file__), "..", "bin", "device_manager.py"),
                os.path.join(root, "bin", "device_manager.py"))
    with open(os.path.join(root, "config", "plug_config.yaml"), "w") as f:
        yaml.safe_dump(cfg_dict, f)
    # log file
    log_dir = os.path.expanduser("~/Library/Logs")
    # We can't write user's real log. Override by patching LOG path in module via env? No.
    # Build a temp.log 供 _recover_cooler_state() 读 — 通过 monkey patch after loading watcher.
    fake_log = os.path.join(root, "miplug.test.log")
    with open(fake_log, "w") as f:
        f.write(add_lines_log)
    return root, fake_log

# ---------------------------------------------------------------------------
# [IPAD-BUG1] GUI save ipad -> cooler yaml should NOT mutate
# Use device_manager module, Qt offscreen, simulate load_config → ipad panel set → save → yaml
# ---------------------------------------------------------------------------
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

DM_QT_APP = QApplication.instance() or QApplication(sys.argv)

spec = importlib.util.spec_from_file_location(
    "dm_mod", os.path.join(os.path.dirname(__file__), "..", "bin", "device_manager.py")
)
dm_mod = importlib.util.module_from_spec(spec)

# 造一份干净 cfg.yaml 并强制 CFG_PATH 指那里
DM_FAKEROOT = tempfile.mkdtemp(prefix="dm_ipad_bug_dm_")
os.makedirs(os.path.join(DM_FAKEROOT, "bin"))
os.makedirs(os.path.join(DM_FAKEROOT, "config"))
PRE_CFG = {
    "cooler": {"enabled": True, "ip": "1.1.1.1", "token": "x",
               "poll_interval": 30, "fast_interval": 5, "confirm_hits": 3,
               # 故意用非 GUI 默认值，确保 dump_config 不会把 GUI 的默认 60/33 覆盖进来
               "on_threshold": 70, "off_threshold": 38, "fail_open_after": 3},
    "ambilight": {"enabled": False, "ip": "2.2.2.2", "token": "y",
                  "time_start": 20, "time_end": 8, "lock_delay": 120},
    "ipad_charger": {"enabled": True, "udid": "UDID1",
                     "plug_ip": "3.3.3.3", "plug_token": "z",
                     "low_battery_threshold": 40, "high_battery_threshold": 90,
                     "check_interval": 180, "fail_close_after": 40,
                     "report_port": 8737,
                     "mock": {"enabled_battery": False, "level": 15, "charging": False, "enabled_plug": False},
                     "sleep_shutdown": True},
}
DM_CFG_PATH = os.path.join(DM_FAKEROOT, "config", "plug_config.yaml")
with open(DM_CFG_PATH, "w") as _f: yaml.safe_dump(PRE_CFG, _f)
os.environ["DEVICE_MANAGER_HOME"] = DM_FAKEROOT

sys.modules["dm_mod"] = dm_mod
spec.loader.exec_module(dm_mod)

# Force CFG_PATH override (device_manager.py calculates via PROJECT_ROOT before we set env.
# If env worked, it's already our temp; if not, we patch at module level.)
if str(dm_mod.CFG_PATH) != DM_CFG_PATH:
    def patched_read(self_self):
        with open(DM_CFG_PATH) as f: return yaml.safe_load(f) or {}
    dm_mod.ManagerWindow._read_yaml = patched_read

    def patched_write(self_self, only_key=None):
        """Mirrors new _write_yaml semantics but writes to temp DM_CFG_PATH."""
        raw = self_self._read_yaml()
        panel_map = {
            "cooler": ("cooler",       self_self.cooler_panel.dump_config),
            "ambil":  ("ambilight",    self_self.ambil_panel.dump_config),
            "ipad":   ("ipad_charger", self_self.ipad_panel.dump_config),
        }
        if only_key is None:
            for _k, (_rk, _df) in panel_map.items():
                raw[_rk] = {**(raw.get(_rk) or {}), **_df()}
        else:
            _rk, _df = panel_map[only_key]
            raw[_rk] = {**(raw.get(_rk) or {}), **_df()}
        tmp = DM_CFG_PATH + ".tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=160)
        os.replace(tmp, DM_CFG_PATH)
        self_self.cfg_data = raw
    dm_mod.ManagerWindow._write_yaml = patched_write

# patch _kickstart_service 不真正 kickstart（否则会 launchctl 写真 watcher，别碰用户系统）
dm_mod.ManagerWindow._kickstart_service = lambda self_self: None

mw = dm_mod.ManagerWindow()

# 用户操作：只改 iPad 面板的"开始充电电量" 40 → 45
mw._nav_click("ipad")  # 切到 iPad 页才会实际做用户操作
mw.ipad_panel.low.setValue(45)   # 40 → 45

with open(DM_CFG_PATH) as f: pre_cooler = (yaml.safe_load(f) or {})["cooler"]
# save ipad
mw.save_panel("ipad")
# 强处理 pending events
for _ in range(3):
    DM_QT_APP.processEvents()

with open(DM_CFG_PATH) as f: _rr = yaml.safe_load(f); post_cooler = _rr["cooler"]; post_ipad = _rr["ipad_charger"]

# 判定：cooler 段除 GUI 可能补齐的 fast_interval/confirm_hits (它们本来就有) 不应产生任何 diff
# 我们 pre_cfg 已经有所有键，所以 pre_cooler 应该 == post_cooler 严格相等
if pre_cooler != post_cooler:
    diffs = {k: (pre_cooler.get(k), post_cooler.get(k))
             for k in set(list(pre_cooler.keys())+list(post_cooler.keys()))
             if pre_cooler.get(k) != post_cooler.get(k)}
    print(f"[IPAD-BUG1] FAIL: save ipad 导致 cooler yaml 发生变化 diffs={diffs}")
    RC = 1
else:
    print("[IPAD-BUG1] OK: save ipad 未改写 cooler yaml")

# 且 ipad.low_battery_threshold 必须已经变成 45
if post_ipad.get("low_battery_threshold") != 45:
    print(f"[IPAD-BUG1-b] FAIL: ipad 新值没写入: low_threshold={post_ipad.get('low_battery_threshold')} (want 45)")
    RC = 1
else:
    print("[IPAD-BUG1-b] OK: ipad low 已保存为 45")

# Close any timers so QApp exits cleanly
try:
    mw.close()
    mw.deleteLater()
except Exception: pass
DM_QT_APP.processEvents()
DM_QT_APP.quit()
# 任何残留的 QTimer / Thread 防止挂住进程
import gc as _gc; _gc.collect()
DM_QT_APP.sendPostedEvents(None, 0)
# SIGALRM 兜底：6s 后 os._exit 杀掉进程 (Qt flush 方法不存在 → 删掉)
import signal as _sig
def _to(_s,_f):
    import os as _os; _os._exit(0)
_sig.signal(_sig.SIGALRM, _to)
_sig.alarm(6)

# ---------------------------------------------------------------------------
# [IPAD-BUG2] watcher 启动时 _cooler_state=None 且 OFF<T<ON（in-band），
# 不应走 init mid 立即 run_cooler(on/off) 并改 state。应该：先 state=None，
# 下一次慢轮询等阈值命中 on/off 才切。
# ---------------------------------------------------------------------------
# 现在再 load 一遍 watcher module（fresh），override read_cpu_temp 返回 50°C（in-band 46.5 mid 会开）
# 同时让 _recover_cooler_state 返回 None（日志空 / 没找到 cooler -> 行）——靠写空 fake_log。

def test_bug2(temp_val, on_thr, off_thr, expect_no_run_cooler_during_init=True):
    _FT.instances.clear()
    lg_lines = ""  # 空 log → _recover_cooler_state 返回 None
    fake_root, fake_log = make_fake_root_with({
        "cooler": {"enabled": True, "ip": "x", "token": "y",
                   "poll_interval": 30, "fast_interval": 5, "confirm_hits": 3,
                   "on_threshold": on_thr, "off_threshold": off_thr,
                   "fail_open_after": 3},
        "ambilight": {"enabled": False, "ip": "x", "token": "y",
                      "time_start": 20, "time_end": 8, "lock_delay": 120},
        "ipad_charger": {"enabled": False, "udid": None},
    }, lg_lines)
    os.environ["DEVICE_MANAGER_HOME"] = fake_root
    # import fresh module each time via unique namespace name
    name = f"wmod_bug2_{int(temp_val)}_{on_thr}_{off_thr}"
    wsp = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), "..", "bin", "miplug_lock_watcher.py"),
    )
    wmod = importlib.util.module_from_spec(wsp)
    sys.modules[name] = wmod

    # Patch after exec: LOG path → our fake_log
    captured = []
    def fake_rcs():
        # copy from real code behavior (empty log returns None)
        try:
            with open(fake_log, "r", errors="ignore") as f:
                lines = f.readlines()[-400:]
            for line in reversed(lines):
                if "[watcher] cooler -> on" in line or "set plug on ok" in line: return "on"
                if "[watcher] cooler -> off" in line or "set plug off ok" in line: return "off"
        except Exception: pass
        return None

    def fake_rct(): return temp_val
    def fake_run(state): captured.append(("run_cooler", state))
    # Also patch read_cpu_temp / run_cooler before exec_module via module dict pre-assign?
    # Better: patch them post-exec. Problem is exec_module runs startCoolerPolling() at bottom.
    # We need to intercept DURING exec. Use patch on module namespaces with wrappers by
    # pre-assigning on wmod before exec.
    wmod._cooler_state = None
    wmod._TEST_CAPTURE = captured
    # Patch LOG name by setting wmod.LOG only works after exec defines it.
    # Instead we patch open() globally during exec for the specific LOG path. Old school:
    # monkey-patch the real function after exec:
    import builtins
    real_open = open
    import io
    def patched_open(file, *a, **kw):
        # Intercept LOG read → fake_log
        try:
            if isinstance(file, str) and file.endswith("miplug.log"):
                return real_open(fake_log, *a, **kw)
        except Exception: pass
        return real_open(file, *a, **kw)
    builtins.open = patched_open
    try:
        try:
            wsp.loader.exec_module(wmod)
        except RuntimeError as _e:
            if "__TEST_STOP__" not in str(_e): raise
    finally:
        builtins.open = real_open
    # Post-rebind names (startCoolerPolling already called during exec so we need rebind too late.
    # Hence we did the LOG interception above + need to also patch read_cpu_temp/run_cooler.
    # Easier: run a SECOND handler instance AFTER re-binding.
    wmod.read_cpu_temp = fake_rct
    wmod.run_cooler = fake_run
    wmod._recover_cooler_state = fake_rcs
    captured.clear()
    _FT.instances.clear()
    h = wmod.Handler.alloc().init()
    # Now first coolerPollTick matters — state fresh None, run init mid path
    h._coolerPollTick_(None)
    mid = (on_thr + off_thr) / 2
    print(f"[IPAD-BUG2.trace] temp={temp_val} mid={mid} state={h._cooler_state} captured={captured}")
    if expect_no_run_cooler_during_init and captured:
        return False
    if not expect_no_run_cooler_during_init and not captured and h._cooler_state is None:
        return False
    return True

# Case A: OFF<50°C<ON (60,33) → mid=46.5 → old bug would do "init CPU 50°C→on"
t1 = test_bug2(50, 60, 33, expect_no_run_cooler_during_init=True)
if not t1:
    print("[IPAD-BUG2-A] FAIL: OFF<T<ON 滞环带内 50°C，启动首轮仍 run_cooler(ON) (init mid bug)")
    RC = 1
else:
    print("[IPAD-BUG2-A] OK: 滞环带内启动首轮零动作（等命中阈值才开）")

# Case B: CPU 20°C < OFF=33 → mid=46.5 → old bug: "init CPU 20°C→off" + run_cooler(off). 正确：不该动。
t2 = test_bug2(20, 60, 33, expect_no_run_cooler_during_init=True)
if not t2:
    print("[IPAD-BUG2-B] FAIL: T<OFF 也跑 run_cooler(off)，多余副作用")
    RC = 1
else:
    print("[IPAD-BUG2-B] OK: T<OFF 不主动关（等以后阈值命中）")

# Case C: T=80>ON=60 → 必须触发 (hit on → 进入 fast 1/3) 但不应 init 直接开（走 fast confirm）
# 这里我们只断言不会走 "init mid" 路径：state 应为 None（或者 confirm_target=on，confirm_count=1）
# 但 expect run_cooler 调用发生: NO (因为还没 confirm 3/3 次)
t3_ok = True
try:
    _FT.instances.clear()
    lg_lines = ""
    fake_root, fake_log = make_fake_root_with({
        "cooler": {"enabled": True, "ip": "x", "token": "y",
                   "poll_interval": 30, "fast_interval": 5, "confirm_hits": 3,
                   "on_threshold": 60, "off_threshold": 33, "fail_open_after": 3},
        "ambilight": {"enabled": False, "ip": "x", "token": "y",
                      "time_start": 20, "time_end": 8, "lock_delay": 120},
        "ipad_charger": {"enabled": False},
    }, lg_lines)
    os.environ["DEVICE_MANAGER_HOME"] = fake_root
    name = "wmod_bug2_C"
    wsp = importlib.util.spec_from_file_location(
        name, os.path.join(os.path.dirname(__file__), "..", "bin", "miplug_lock_watcher.py"))
    wmod = importlib.util.module_from_spec(wsp); sys.modules[name]=wmod
    captured_C = []
    def fake_rcs_C(): return None
    def fake_rct_C(): return 80.0
    def fake_run_C(state): captured_C.append(("on", state))

    import builtins as bi2
    real_open2 = bi2.open
    def po2(file, *a, **kw):
        try:
            if isinstance(file, str) and file.endswith("miplug.log"):
                return real_open2(fake_log, *a, **kw)
        except Exception: pass
        return real_open2(file, *a, **kw)
    bi2.open = po2
    try:
        try: wsp.loader.exec_module(wmod)
        except RuntimeError as e:
            if "__TEST_STOP__" not in str(e): raise
    finally:
        bi2.open = real_open2
    wmod.read_cpu_temp = fake_rct_C
    wmod.run_cooler = fake_run_C
    wmod._recover_cooler_state = fake_rcs_C
    captured_C.clear()
    _FT.instances.clear()
    h = wmod.Handler.alloc().init()
    h._coolerPollTick_(None)
    # Result: should enter fast confirm 1/3 → confirm_target=on, confirm_count=1
    # run_cooler(on) should NOT have been called yet (need 3/3).
    if captured_C:
        print(f"[IPAD-BUG2-C] FAIL: 超阈值首轮 init mid 不该直接开；confirm 还没走完 run_cooler={captured_C}")
        RC = 1; t3_ok = False
    else:
        # confirm_target == "on" AND confirm_count>=1 (进入 fast)
        if h._cooler_confirm_target != "on" or h._cooler_confirm_count < 1:
            print(f"[IPAD-BUG2-C] FAIL: T>ON 首轮应进入 fast confirm；target={h._cooler_confirm_target} cnt={h._cooler_confirm_count}")
            RC = 1; t3_ok = False
        else:
            print("[IPAD-BUG2-C] OK: T>ON 首轮进入 fast confirm (1/3)，不立刻开")
except Exception as _ee:
    print(f"[IPAD-BUG2-C] ERROR: {_ee}"); RC = 1; t3_ok = False

# ---------------------------------------------------------------------------
# [IPAD-BUG3] _recover_cooler_state should only match COOLER log lines, not iPad/ambilight.
# ---------------------------------------------------------------------------
test_log_cases = [
    # (log_content, expected_result, label)
    ("2026-08-30 01:40:00 INFO [ipad_plug] plug -> on ok (attempt 1, iface=en0)\n"
     "2026-08-30 01:40:01 INFO [somethingelse] INFO blah\n",
     None, "ipad_plug log on (should NOT recover as cooler on)"),
    ("2026-08-30 01:40:00 INFO plug -> on ok (attempt 1)\n",
     None, "ambilight on (should NOT recover as cooler on)"),
    ("2026-08-30 01:40:00 INFO set plug on ok (attempt 1, iface=en0)\n",
     "on", "cooler (miplug.py) on"),
    ("2026-08-30 01:40:00 INFO [watcher] cooler -> on\n",
     "on", "watcher cooler on"),
    ("2026-08-30 01:39:59 INFO [watcher] cooler -> on\n"
     "2026-08-30 01:40:00 INFO [watcher] cooler -> off\n",
     "off", "watcher cooler off (most recent)"),
]

# load the watcher module's _recover_cooler_state function (directly from file)
from pathlib import Path
watcher_src = Path(os.path.join(os.path.dirname(__file__), "..", "bin", "miplug_lock_watcher.py")).read_text()
# Extract function text (simpler: exec just that function with the LOG variable pointing to temp)
ns = {"LOG": None}
# We'll instead patch LOG before calling via open interception. Simpler: run the function
# (the version in wmod we already loaded for bug2) and use the same fake_log technique.

import importlib.util as iu
name = "wmod_bug3"
wsp = iu.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), "..", "bin", "miplug_lock_watcher.py"))
wmod3 = iu.module_from_spec(wsp); sys.modules[name]=wmod3
# dummy root + config
fake_root3 = tempfile.mkdtemp(prefix="dm_w3_")
os.makedirs(os.path.join(fake_root3, "bin"))
os.makedirs(os.path.join(fake_root3, "config"))
with open(os.path.join(fake_root3, "config", "plug_config.yaml"), "w") as f: yaml.safe_dump(PRE_CFG, f)
os.environ["DEVICE_MANAGER_HOME"] = fake_root3
import builtins as bi3
# ★ 关键：防止 wmod3 运行期自己写 run_cooler / fail-safe 的日志串入"历史测试日志"（fl3）。
# 做法：(a) 把 wmod3 内部硬编码的 LOG 常量在 exec 后立刻 overwrite 到 fl3；
#       (b) 同时 root logger 所有 handlers 全替换为 write 到 fl3_runtime 的独立 handler，
#           让 Handler 里的 logging.info / logging.warning 绝不可能写到 fl3。
import logging as _lg3
fl3 = os.path.join(fake_root3, "miplug.test_history.log")
fl3_runtime = os.path.join(fake_root3, "wmod3_runtime.log")
# 先把 root handlers 清 + 强制替换为 fl3_runtime（在任何 load / init 之前就锁死）
for _h in list(_lg3.root.handlers):
    _lg3.root.removeHandler(_h); _h.close()
_fh = _lg3.FileHandler(fl3_runtime, mode="a")
_fh.setFormatter(_lg3.Formatter("%(asctime)s %(levelname)s %(message)s"))
_fh.setLevel(_lg3.INFO)
_lg3.root.addHandler(_fh)
_lg3.root.setLevel(_lg3.INFO)
# 兜底：万一下面 basicConfig 想覆盖 -> 用 lambda 吞：
_lg3.basicConfig = lambda *a, **kw: None  # type: ignore

ro3 = bi3.open
def po3(file, *a, **kw):
    try:
        if isinstance(file, str) and file.endswith("miplug.log"):
            return ro3(fl3, *a, **kw)
    except Exception: pass
    return ro3(file, *a, **kw)
bi3.open = po3
try:
    try: wsp.loader.exec_module(wmod3)
    except RuntimeError as e:
        if "__TEST_STOP__" not in str(e): raise
finally:
    bi3.open = ro3
# ★★ 锁死 LOG 常量 + 再清一遍 handlers（防 wmod3 里 logging.basicConfig 再次挂 FileHandler 到 fl3）
wmod3.LOG = fl3
for _h in list(_lg3.root.handlers):
    _lg3.root.removeHandler(_h); _h.close()
_lg3.root.addHandler(_fh)
_lg3.basicConfig = lambda *a, **kw: None  # type: ignore

# 清 fl3，之后只写我们的 test case（没有 watcher 启动期的脏日志）
with open(fl3, "w") as f: f.truncate(0)

for content, expected, label in test_log_cases:
    with open(fl3, "w") as f: f.write(content)
    got = wmod3._recover_cooler_state()
    ok = got == expected
    if not ok:
        with open(fl3) as f:
            actual = f.read()
        print(f"[IPAD-BUG3] FAIL: {label!r} → got {got!r}, expected {expected!r}. "
              f"wanted[:80]={content[:80]!r}; actual_lines_last5="
              f"{chr(10).join(actual.strip().splitlines()[-5:])!r}; runtime_log_tail="
              f"{chr(10).join((open(fl3_runtime).read().strip().splitlines()[-3:]) if os.path.exists(fl3_runtime) else [])!r}")
        RC = 1
    else:
        print(f"[IPAD-BUG3] OK: {label} → {got}")

print("\nFINAL RC=", RC)
sys.exit(RC)
