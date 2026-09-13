#!/usr/bin/env python3
"""RED test: macOS 窗口生命周期 —— ⌘W 关窗进程驻留 / ⌘Q 真退出 / Dock 图标重开窗口。

Expected FAIL against CURRENT code (before install_mac_window_lifecycle exists):
  [WQ-1] 源码级：main() 没接 install_mac_window_lifecycle / 没有 setQuitOnLastWindowClosed(False)
  [WQ-2] 运行时：install_mac_window_lifecycle 不存在（AttributeError → 计 FAIL 后提前退出）
GREEN 后：
  [WQ-2] quitOnLastWindowClosed == False
  [WQ-3] ⌘W(=QKeySequence.Close) shortcut 触发 → 窗口隐藏且事件循环 100ms 后仍存活（进程驻留）
  [WQ-4] Dock 图标点击（QApplicationStateChangeEvent→Active 送主窗口）→ 窗口重显
        且 Inactive 事件不会误开窗口
  [WQ-5] ⌘Q(=QKeySequence.Quit) shortcut 触发 → app.exec() 立即返回（真退出）
"""
import os, sys, tempfile, importlib.util, pathlib
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
BIN_DM = os.path.join(HERE, "..", "bin", "device_manager.py")
RC = 0
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# ---------- [WQ-1] 源码级断言：main() 接线 + 关键机制存在 ----------
src = open(BIN_DM).read()
checks_src = [
    ("install_mac_window_lifecycle(app, w)" in src, "main() 调用 install_mac_window_lifecycle(app, w)"),
    ("setQuitOnLastWindowClosed(False)" in src, "setQuitOnLastWindowClosed(False)（⌘W/红点关窗不退出进程）"),
    ("QKeySequence.Close" in src, "⌘W 绑定 QKeySequence.Close"),
    ("QKeySequence.Quit" in src, "⌘Q 绑定 QKeySequence.Quit"),
    ("ApplicationStateChange" in src, "Dock 图标重开监听 ApplicationStateChange"),
]
for ok, label in checks_src:
    if ok:
        print(f"[WQ-1] PASS: {label}")
    else:
        RC += 1
        print(f"[WQ-1] RED-FAIL: 缺 {label}")

# ---------- 运行时（隔离 temp root：不读真 config、不写真 ~/.cache） ----------
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QKeySequence, QShortcut

ROOT = tempfile.mkdtemp(prefix="dm_wq_")
os.makedirs(os.path.join(ROOT, "bin")); os.makedirs(os.path.join(ROOT, "config"))
CFG = {
    "cooler": {"enabled": True, "ip": "1.1.1.1", "token": "x", "poll_interval": 30,
               "fast_interval": 5, "confirm_hits": 3, "on_threshold": 60,
               "off_threshold": 33, "fail_open_after": 3},
    "ambilight": {"enabled": False, "ip": "2.2.2.2", "token": "y", "time_start": 20,
                  "time_end": 8, "lock_delay": 120},
    "ipad_charger": {"enabled": True, "udid": "U1", "plug_ip": "3.3.3.3", "plug_token": "z",
                     "low_battery_threshold": 40, "high_battery_threshold": 90,
                     "check_interval": 180, "fail_close_after": 40, "report_port": 8737,
                     "mock": {"enabled_battery": False, "level": 15, "charging": False,
                              "enabled_plug": False},
                     "sleep_shutdown": True},
}
with open(os.path.join(ROOT, "config", "plug_config.yaml"), "w") as f:
    yaml.safe_dump(CFG, f)
os.environ["DEVICE_MANAGER_HOME"] = ROOT

app = QApplication.instance() or QApplication(sys.argv)
spec = importlib.util.spec_from_file_location("dm_wq", BIN_DM)
dm = importlib.util.module_from_spec(spec)
sys.modules["dm_wq"] = dm
spec.loader.exec_module(dm)

# cache 重定向：绝不碰用户真实 ~/.cache/miplug/window_state.yaml
TMP_CACHE = tempfile.mkdtemp(prefix="dm_wq_cache_")
dm.CACHE_DIR = pathlib.Path(TMP_CACHE)
dm.WINDOW_STATE_PATH = dm.CACHE_DIR / "window_state.yaml"
# 不真正 kickstart 用户 watcher
dm.ManagerWindow._kickstart_service = lambda self: None

# ---------- [WQ-2] install_mac_window_lifecycle 存在 + quitOnLastWindowClosed ----------
if not hasattr(dm, "install_mac_window_lifecycle"):
    RC += 1
    print("[WQ-2] RED-FAIL: install_mac_window_lifecycle 不存在（功能未实现）")
    print(f"\nFINAL RC={RC}")
    sys.exit(RC)

mw = dm.ManagerWindow()
mw.show()
mw.activateWindow()   # offscreen 下把 applicationState 置 Active（与真机 Dock 激活一致）
app.processEvents()
dm.install_mac_window_lifecycle(app, mw)
app.processEvents()

if app.quitOnLastWindowClosed() is False:
    print("[WQ-2] PASS: quitOnLastWindowClosed=False（关窗进程驻留）")
else:
    RC += 1
    print("[WQ-2] FAIL: quitOnLastWindowClosed 仍为 True")

# ---------- [WQ-3] ⌘W shortcut 存在 + 触发后窗口隐藏且事件循环存活 ----------
sc_close = [s for s in mw.findChildren(QShortcut)
            if s.key().matches(QKeySequence(QKeySequence.Close)) == QKeySequence.SequenceMatch.ExactMatch
            and s.context() == Qt.ShortcutContext.ApplicationShortcut]
sc_quit = [s for s in mw.findChildren(QShortcut)
           if s.key().matches(QKeySequence(QKeySequence.Quit)) == QKeySequence.SequenceMatch.ExactMatch
           and s.context() == Qt.ShortcutContext.ApplicationShortcut]
if sc_close:
    print("[WQ-3] PASS: ⌘W=QKeySequence.Close (ApplicationShortcut) 已创建")
else:
    RC += 1
    print("[WQ-3] FAIL: 找不到 ⌘W (Close) 的 ApplicationShortcut")

seq = []
def _close_step():
    if sc_close:
        sc_close[0].activated.emit()   # 模拟按 ⌘W
    seq.append("closed")
def _alive_step():
    seq.append("alive_after_close")    # 100ms 后定时器仍触发 = 事件循环未退出 = 进程驻留
QTimer.singleShot(0, _close_step)
QTimer.singleShot(100, _alive_step)
QTimer.singleShot(250, app.quit)
app.exec()
if "closed" in seq and "alive_after_close" in seq and not mw.isVisible():
    print("[WQ-3] PASS: ⌘W 只关窗口，事件循环仍存活（进程驻留）")
elif "closed" in seq and "alive_after_close" not in seq:
    RC += 1
    print("[WQ-3] FAIL: ⌘W 后事件循环退出了（等于关窗即退出，进程没驻留）")
else:
    RC += 1
    print(f"[WQ-3] FAIL: seq={seq} visible={mw.isVisible()}")

# ---------- [WQ-4] Dock 图标点击（ApplicationStateChange）重开窗口 ----------
# 真机语义：Qt 在应用激活（Dock 图标点击/⌘Tab 切回）时先置 applicationState=Active
#           再向顶层窗口派发 ApplicationStateChange；这里直接模拟派发该事件。
# 防误弹保护（Inactive 不开窗）由源码级断言覆盖（offscreen 无法模拟 Inactive 状态）。
guard_src = "applicationState() == Qt.ApplicationActive" in open(BIN_DM).read()
if not guard_src:
    RC += 1
    print("[WQ-4] FAIL: 源码缺少 applicationState()==Active 防误弹保护")
else:
    print("[WQ-4] PASS(源码): Active 判定保护存在（Inactive 不误弹）")
mw.hide()  # 确保隐藏
app.sendEvent(mw, QEvent(QEvent.ApplicationStateChange))
app.processEvents()
if mw.isVisible():
    print("[WQ-4] PASS: ApplicationStateChange → 窗口重显（点 Dock 图标可重开）")
else:
    RC += 1
    print("[WQ-4] FAIL: ApplicationStateChange 后窗口未重显（Dock 图标点开无效）")

# ---------- [WQ-5] ⌘Q 真退出 ----------
if sc_quit:
    print("[WQ-5] PASS: ⌘Q=QKeySequence.Quit (ApplicationShortcut) 已创建")
else:
    RC += 1
    print("[WQ-5] FAIL: 找不到 ⌘Q (Quit) 的 ApplicationShortcut")
if sc_quit:
    loop_done = []
    def _quit_step():
        sc_quit[0].activated.emit()    # 模拟按 ⌘Q
        QTimer.singleShot(80, lambda: loop_done.append("still_running"))  # 若 quit 失效，80ms 后此定时器会跑
    QTimer.singleShot(0, _quit_step)
    app.exec()                        # 应因 ⌘Q 立即返回
    if loop_done:
        RC += 1
        print("[WQ-5] FAIL: ⌘Q 后事件循环仍在跑（没真退出）")
    else:
        print("[WQ-5] PASS: ⌘Q 触发后 app.exec() 立即返回（真退出）")

try:
    mw.close()
    mw.deleteLater()
except Exception:
    pass
app.processEvents()

print(f"\nFINAL RC={RC}")
sys.exit(RC)
