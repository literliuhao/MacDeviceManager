#!/usr/bin/env python3
"""RED test for Cooler "never opens" bug chain.

Three expected FAIL items against CURRENT (buggy) watcher code:
  [RC1] startCoolerPolling: 首 tick 进入 fast confirm 后，下一次定时器 interval 应为 FAST(5s)
        因为 L565 _coolerPollTick → L662 reschedule(5) → L566 外部又 reschedule(30) 覆盖 → 实际为 30s
  [RC2] _load_cfg 缺 fast_interval / confirm_hits 时应有默认值 (防止 KeyError)
  [RC3] 5 次温度连续超阈值 (T>60 → target=on) → Handler.cooler_state 应在 CONFIRM_HITS 次后变为 "on"
        (不被 abort，也不依赖 30s 长间隔来完成)
"""
import os, sys, types, importlib.util, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))

RC = 0

# ---------------------------------------------------------------------------
#  Stub Foundation/PyObjC before importing watcher (we never run ObjC runloop)
# ---------------------------------------------------------------------------
_fake_foundation = types.ModuleType("Foundation")
class _FakeTimer:
    instances = []
    def __init__(self, iv, handler, sel, ui, repeats):
        self.interval = iv; self.handler = handler; self.selector = sel
        self.userInfo = ui; self.repeats = repeats; self._valid = True
        _FakeTimer.instances.append(self)
    def invalidate(self): self._valid = False
    @classmethod
    def scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(cls, iv, h, s, ui, r):
        return cls(iv, h, s, ui, r)
_fake_foundation.NSTimer = _FakeTimer

class _FakeNSObject:
    @classmethod
    def alloc(cls): return cls()
    def init(self): return self
_fake_foundation.NSObject = _FakeNSObject
_fake_foundation.NSLog = lambda *a, **k: None

class _FakeNotifCenter:
    @staticmethod
    def defaultCenter(): return _FakeNotifCenter()
    def addObserver_selector_name_object_(self, *a): pass
    def removeObserver_(self, *a, **k): pass
_fake_foundation.NSDistributedNotificationCenter = _FakeNotifCenter

class _FakeRunLoop:
    @staticmethod
    def currentRunLoop(): return _FakeRunLoop()
    def addTimer_forMode_(self, *a, **k): pass
    def runUntilDate_(self, *a, **k):
        # module-level while True loop: raise immediately so exec_module doesn't hang
        raise RuntimeError("__TEST_STOP_NSRUNLOOP__")
_fake_foundation.NSRunLoop = _FakeRunLoop

class _FakeNSDate:
    @staticmethod
    def dateWithTimeIntervalSinceNow_(s): return _FakeNSDate()
_fake_foundation.NSDate = _FakeNSDate
sys.modules.setdefault("Foundation", _fake_foundation)

# PyObjC objc stub
_fake_objc = types.ModuleType("objc")
# builtins.super(klass, instance) returns bound super object we can forward .init to
import builtins as _bt
def _fake_super(klass, instance): return _bt.super(klass, instance)
_fake_objc.super = _fake_super
sys.modules.setdefault("objc", _fake_objc)

# AppKit stub
_fake_appkit = types.ModuleType("AppKit")
class _FakeWS:
    @staticmethod
    def sharedWorkspace(): return _FakeWS()
    def notificationCenter(self): return _FakeNotifCenter()
_fake_appkit.NSWorkspace = _FakeWS
sys.modules.setdefault("AppKit", _fake_appkit)
sys.modules.setdefault("AppKit.NSWorkspace", _fake_appkit)

# PyObjC 风格：让 `NSObject` 的子类继承自普通 object（通过 Handler 的 `class Handler(NSObject)`）
# 因为我们已经把 Foundation.NSObject 设成了普通 Python 类，没问题。

# ---------------------------------------------------------------------------
#  注入 test doubles: 温度 / 插座控制 / logger (避免 import 时写真实日志)
# ---------------------------------------------------------------------------
import logging as _logging
TEST_TEMP_SEQ = []
TEST_RUN_CALLS = []

def _fake_read_cpu_temp():
    if TEST_TEMP_SEQ:
        return float(TEST_TEMP_SEQ.pop(0))
    return None   # 测试不预填 TEMP_SEQ 时，表示"传感器读不到"，走 fail-safe 流程

def _fake_run_cooler(state):
    TEST_RUN_CALLS.append(("cooler", state, __import__("time").monotonic()))

def _fake_run_ipad(state): TEST_RUN_CALLS.append(("ipad", state, None))
def _fake_run_ambilight(state): TEST_RUN_CALLS.append(("ambilight", state, None))
def _fake_battery(): return (None, False)

# import watcher 并替换（先 stub 名字）
watcher_path = os.path.join(os.path.dirname(__file__), "..", "bin", "miplug_lock_watcher.py")

# 先给 PROJECT_ROOT 造一个临时目录 + 合法 config，让模块级 _load_cfg() 不 KeyError
import tempfile, yaml
_FAKEROOT = tempfile.mkdtemp()
os.makedirs(os.path.join(_FAKEROOT, "bin"), exist_ok=True)
os.makedirs(os.path.join(_FAKEROOT, "config"), exist_ok=True)
shim_cfg = {
    "cooler": {"enabled": True, "ip": "1.2.3.4", "token": "x",
               "poll_interval": 30, "on_threshold": 60,
               "off_threshold": 33, "fail_open_after": 3,
               "fast_interval": 5, "confirm_hits": 3},
    "ambilight": {"enabled": False, "ip": "0", "token": "0",
                  "time_start": 20, "time_end": 8, "lock_delay": 120},
    "ipad_charger": {"enabled": False, "udid": "0", "plug_ip": "", "plug_token": "",
                     "low_battery_threshold": 20, "high_battery_threshold": 90,
                     "check_interval": 180, "fail_close_after": 40, "report_port": 8737,
                     "mock": {"enabled_battery": False}, "sleep_shutdown": False},
}
with open(os.path.join(_FAKEROOT, "config", "plug_config.yaml"), "w") as _f:
    yaml.safe_dump(shim_cfg, _f)
os.environ["DEVICE_MANAGER_HOME"] = _FAKEROOT

# Stub modules first, import second
sys.path.insert(0, os.path.dirname(watcher_path))
# silence basicConfig 写真实 log
_old_log_cfg = _logging.basicConfig
_logging.basicConfig = lambda **kw: None

# Pre-patch wmod's module namespace before exec with stubs that stop thread/network activity
class _PreLoader(importlib.util.Loader):
    pass

# Use exec_module but inject stubs FIRST by pre-writing into the module dict before exec
# (easier: override logging.Logger with silence; but we already stubbed basicConfig).
# We'll also preload an import-time hook for thread / http.server / socketserver to prevent
# the battery report server thread from hanging process.
import threading as _threading
_orig_thread_start = _threading.Thread.start
_starts_blocked = True
def _fake_start(self):
    if _starts_blocked: return None   # 测试期间不启动 HTTP listener 子线程
    return _orig_thread_start(self)
_threading.Thread.start = _fake_start

# Also stub http.server HTTPServer.serve_forever
import http.server as _hs
_orig_srv = _hs.HTTPServer.serve_forever
_hs.HTTPServer.serve_forever = lambda self, **kw: None

spec = importlib.util.spec_from_file_location("miplug_lock_watcher", watcher_path)
wmod = importlib.util.module_from_spec(spec)
sys.modules["miplug_lock_watcher"] = wmod

# Stub smctemp calls before exec (watchdog imports them later)
import unittest.mock as um

# exec wmod, but we need module-level read_cpu_temp to be our fake at import-time
# startCoolerPolling → _coolerPollTick_ → read_cpu_temp() is a module-global lookup,
# so we need to make wmod.read_cpu_temp resolve to the fake DURING exec as well.
# We patch the module dict AFTER import defs, BEFORE exec reaches startCoolerPolling().
# The simplest way: exec only up to a specific line using a custom loader wrapper.
# Simpler approach (minimal magic): wrap exec_module with a re-patch immediately after
# Handler class is defined. Since we can't easily break at mid-file, we just patch
# the smctemp binary path instead.

try:
    spec.loader.exec_module(wmod)
except RuntimeError as _e:
    if "__TEST_STOP_NSRUNLOOP__" not in str(_e): raise

# Re-bind globals AFTER exec (module exec defines fresh `def read_cpu_temp` / `run_cooler` names
# which overwrite our pre-assignments). Lookups inside Handler.* use module globals, so
# reassigning on wmod changes behavior for all subsequent calls.
wmod.read_cpu_temp    = _fake_read_cpu_temp
wmod.run_cooler       = _fake_run_cooler
wmod.run_ipad_plug    = _fake_run_ipad
wmod.run_ambilight    = _fake_run_ambilight
wmod.read_ipad_battery = _fake_battery
wmod._recover_cooler_state = lambda: None

# Restore thread/socket fns after import-level startup finished
_threading.Thread.start = _orig_thread_start
_hs.HTTPServer.serve_forever = _orig_srv
_starts_blocked = False

# Restore basicConfig (after import-time basicConfig call happened)
_logging.basicConfig = _old_log_cfg

# monkey-patch name references to testable doubles
wmod.read_cpu_temp    = _fake_read_cpu_temp
wmod.run_cooler       = _fake_run_cooler
wmod.run_ipad_plug    = _fake_run_ipad
wmod.run_ambilight    = _fake_run_ambilight
wmod.read_ipad_battery = _fake_battery

# 把测试前模块级启动产生的 timer/state 清掉
import unittest.mock as um
# 把模块级 handler 先停（invalidate 所有 timer 并重置 confirm 状态）
try:
    if getattr(wmod.handler, "_cooler_poll_timer", None) is not None:
        wmod.handler._cooler_poll_timer.invalidate()
        wmod.handler._cooler_poll_timer = None
except Exception:
    pass
_FakeTimer.instances.clear()
TEST_RUN_CALLS.clear()
TEST_TEMP_SEQ.clear()

# 也把 _recover_cooler_state 固定 return None（否则读 cache 状态乱掉）
wmod._recover_cooler_state = lambda: None

# ---------------------------------------------------------------------------
# [RC1] 首 tick 命中 fast 后，实际 NSTimer interval 应为 FAST(5)，不是 SLOW(30)
# ---------------------------------------------------------------------------
TEMP_SEQ = TEST_TEMP_SEQ
RUN_CALLS = TEST_RUN_CALLS

_FakeTimer.instances.clear()
RUN_CALLS.clear()
TEMP_SEQ[:] = [70.0]   # 超过 ON 阈值 → 进入 fast confirm 1/3

h = wmod.Handler.alloc().init()
# RC1 测试的是"当前 off 态 → 首 tick 超阈值 → 进入 fast confirm → interval=FAST"链路。
# 冷启动 (_cooler_state=None) 时首 tick>T 会走 init mid 直接开，跳过 confirm 阶段，
# 所以显式设成 off 来模拟日常 watcher 已经恢复 state 的场景。
h._cooler_state = "off"
h.startCoolerPolling()

# 检查 interval：旧 bug 是 `_reschedule(SLOW=30)` 在 tick 之后又覆盖了 FAST(5)，
# 修好后 startCoolerPolling 顺序变为：先 SLOW → tick 内命中超阈值时再 SLOW→FAST。
timers = [t for t in _FakeTimer.instances if t._valid and getattr(t, 'handler', None) is h]
if not timers:
    timers = [t for t in _FakeTimer.instances if t._valid]
if not timers:
    print("[RC1] FAIL: no active timer scheduled after startCoolerPolling")
    RC = 1
else:
    active = timers[-1]
    print(f"[RC1] active timer interval = {active.interval}s (want FAST=5s)")
    if abs(active.interval - 5.0) > 1e-6:
        print(f"[RC1] FAIL: startCoolerPolling overrode FAST timer with SLOW interval ({active.interval}s)")
        RC = 1
    else:
        print("[RC1] OK: first-hit fast confirm timer stays at FAST interval")

# ---------------------------------------------------------------------------
# [RC2] load_cfg: 缺 fast_interval / confirm_hits 的 config yaml 不该 KeyError
# ---------------------------------------------------------------------------
import yaml, tempfile
mini_cfg = {
    "cooler": {"enabled": True, "ip": "1.2.3.4", "token": "x",
               "poll_interval": 30, "on_threshold": 60,
               "off_threshold": 33, "fail_open_after": 3},
    # 注意：没 fast_interval，没 confirm_hits
    "ambilight": {"enabled": False, "ip": "0", "token": "0",
                  "time_start": 20, "time_end": 8, "lock_delay": 120},
    "ipad_charger": {"enabled": False, "udid": "0", "plug_ip": "", "plug_token": "",
                     "low_battery_threshold": 20, "high_battery_threshold": 90,
                     "check_interval": 180, "fail_close_after": 40, "report_port": 8737,
                     "mock": {"enabled_battery": False}, "sleep_shutdown": False},
}
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    yaml.safe_dump(mini_cfg, f); tmp = f.name
try:
    old_env = os.environ.get("DEVICE_MANAGER_HOME")
    # 直接 import 后，_load_cfg 是按 PROJECT_ROOT 找 config/plug_config.yaml
    # 我们 monkey-patch _load_cfg 的内部：更简单 — 直接把文件喂给函数
    import unittest.mock as um
    with um.patch.object(wmod.Path, "__init__", lambda self, *a, **kw: None):
        pass  # 改用更直接的 patch open
    # 直接用 yaml.safe_load 调用 load_cfg 同样的 load 流程：复制 _load_cfg 内容手动过一遍
    try:
        with open(tmp) as f:
            c = yaml.safe_load(f)
        cc = c["cooler"]
        got = {
            "POLL":    cc.get("poll_interval", 30),
            "FAST":    cc.get("fast_interval", 5),
            "CONFIRM": cc.get("confirm_hits", 3),
        }
    except KeyError as e:
        got = f"KeyError: {e}"
    # 但这是我们手写的 .get，不能代表 watcher 本身没 bug。
    # 真实验证：用一个全新的 mini yaml 路径 + PROJECT_ROOT 指向临时空根（没 config）
    # 然后再用 Path(CONFIG_PATH) shim（其实 wmod.CONFIG_PATH 已绑；直接再跑 wmod._load_cfg
    # 之前先把 PROJECT_ROOT 改过去，再跑就行）。
    saved_ROOT = wmod.PROJECT_ROOT
    try:
        fake_root2 = tempfile.mkdtemp()
        os.makedirs(os.path.join(fake_root2, "config"), exist_ok=True)
        shim2 = os.path.join(fake_root2, "config", "plug_config.yaml")
        with open(shim2, "w") as f: yaml.safe_dump(mini_cfg, f)
        wmod.PROJECT_ROOT = fake_root2
        wmod.CONFIG_PATH  = wmod.Path(fake_root2) / "config" / "plug_config.yaml"
        try:
            loaded = wmod._load_cfg()
            print(f"[RC2] _load_cfg ok: POLL={loaded['COOLER_POLL_INTERVAL']} FAST={loaded['COOLER_FAST_INTERVAL']} CONFIRM={loaded['COOLER_CONFIRM_HITS']}")
            rc2_ok = (
                loaded["COOLER_FAST_INTERVAL"] == 5 and
                loaded["COOLER_CONFIRM_HITS"] == 3 and
                loaded["COOLER_POLL_INTERVAL"] == 30
            )
            if not rc2_ok:
                print("[RC2] FAIL: defaults not applied; loaded:",
                      {k: loaded[k] for k in ("COOLER_POLL_INTERVAL","COOLER_FAST_INTERVAL","COOLER_CONFIRM_HITS")})
                RC = 1
        except KeyError as e:
            print(f"[RC2] FAIL: _load_cfg KeyError={e} on missing fast_interval/confirm_hits")
            RC = 1
    finally:
        wmod.PROJECT_ROOT = saved_ROOT
finally:
    os.unlink(tmp)

# ---------------------------------------------------------------------------
# [RC3] 连续超阈值 CONFIRM_HITS 次 → 必须调用 run_cooler("on")，状态 = on
# ---------------------------------------------------------------------------
_FakeTimer.instances.clear()
RUN_CALLS.clear()
# confirm=3, 温度序列 = [70 (slow poll hit 1/3), 70, 70, 70, 70]
CONFIRM = wmod.COOLER_CONFIRM_HITS
TEMP_SEQ[:] = [70.0] * (CONFIRM + 2)  # 多给两次备用

h = wmod.Handler.alloc().init()
h._cooler_state = "off"   # 模拟非冷启动（已经由 _recover_cooler_state 恢复成 off）
# 手动跑 step by step，避免 runloop
# 首 tick (slow poll)
h._coolerPollTick_(None)
# 然后按当前激活 timer.interval 再 fire (CONFIRM-1) 次 (fast confirm)
for i in range(CONFIRM + 1):
    active = [t for t in _FakeTimer.instances if t._valid]
    if not active: break
    iv = active[-1].interval
    # 如果 bug 生效 → iv=30 (slow)，那 fast confirm 2 次之间间隔是 30，且因为
    # slow poll 跑了但 target 依然 on，不会 abort；代码仍然继续 confirm，只是慢。
    # 更严格：要求前 CONFIRM 次 interval **都 <= FAST + 1** (不该是 30)
    if i < CONFIRM:
        if iv > wmod.COOLER_FAST_INTERVAL + 0.5:
            print(f"[RC3] step {i}: expected fast interval <= {wmod.COOLER_FAST_INTERVAL+0.5}s, got {iv}s (slow poll leaked into confirm phase)")
            RC = 1
            break
    h._coolerPollTick_(active[-1])
    if h._cooler_state == "on":
        break

called_on = any(c[1] == "on" for c in RUN_CALLS)
print(f"[RC3] final cooler_state={h._cooler_state}, run_cooler calls={RUN_CALLS}, confirm_target_left={h._cooler_confirm_target}")
if h._cooler_state != "on" or not called_on:
    print("[RC3] FAIL: after CONFIRM_HITS consecutive over-threshold temps, cooler not switched on")
    RC = 1
else:
    print("[RC3] OK: fast confirm chain completed, run_cooler(on) fired")

print("\nFINAL RC=", RC)
sys.exit(RC)
