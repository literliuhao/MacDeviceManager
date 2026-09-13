#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""Listen for macOS screen lock/unlock/wake and toggle three Xiaomi devices.

水冷器（见 config → cooler.ip）— 两阶段滞环控制：
  常态轮询 COOLER_POLL_INTERVAL 秒（默认 30s）：
    T > ON_THRESHOLD(60°C)  → 目标态切换为 ON，进入快速确认阶段
    T < OFF_THRESHOLD(33°C) → 目标态切换为 OFF，进入快速确认阶段
    两者之间              → 保持当前状态，继续常态轮询

  快速确认 FAST_INTERVAL 秒（默认 5s）：
    连续 CONFIRM_HITS(3) 次温度都达到阈值 → 真正执行切换，切回常态轮询
    中间任一次未达到阈值               → 清空计数，切回常态轮询
  连续 FAIL_OPEN_AFTER 次读不到温度 → 安全回退为开启（宁费电不过热）
  启动时立即做一次温度决策定初态（冷启动不做确认）。

氛围灯（见 config → ambilight.ip，规则保持不变）：
  仅在 TIME_START:00 - TIME_END:00 时间窗口内动作
    屏幕锁定          → LOCK_DELAY 秒倒计时 → 关灯
    屏幕解锁/系统唤醒 → 取消倒计时 + 开灯
    系统唤醒时若锁屏已超时（NSTimer 睡眠 bug）→ 关灯

iPad 充电（智能插座3）：
  单阶段滞环：每 IPAD_CHECK_INTERVAL 秒读一次 iPad 无线电量
    ≤ LOW_THRESHOLD  → 开插座充电
    ≥ HIGH_THRESHOLD → 关插座停止
    中间区            → 保持当前状态
  注意：iPad 锁屏后 WiFi 休眠、pymobiledevice3 断链是常态（实测成功率 ~6%），
  读不到电量 ≠ 充电异常，按插座状态区分：
    插座关/未知 → 静默等待，不计数
    插座开     → 继续充电，连续 FAIL_CLOSE_AFTER 次无读数才 fail-safe 关插座
  锁屏主通道：iPad 端「快捷指令自动化」电量触界时 HTTP 上报本机 report_port
    （GET /battery?level=N&charging=0|1，与轮询共用同一滞环决策）
  mock 模式：插座未到货时，plug_ip/token 为空即可自动进入模拟（仅日志+缓存）

配置: $DEVICE_MANAGER_HOME/config/plug_config.yaml
重启: launchctl kickstart -k gui/$(id -u)/local.miplug.lockwatcher
"""

import subprocess, logging, os, time, sys, threading, json, signal
from datetime import datetime
from pathlib import Path

PYTHON_BIN = "/Users/liuhao/.pyenv/versions/3.12.4/bin/python3"

def _project_root():
    env = os.environ.get("DEVICE_MANAGER_HOME")
    if env: return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
PROJECT_ROOT = _project_root()
BIN_DIR = PROJECT_ROOT / "bin"
CONFIG_PATH = PROJECT_ROOT / "config" / "plug_config.yaml"

import objc
from Foundation import (
    NSDistributedNotificationCenter, NSObject, NSRunLoop, NSDate, NSTimer,
)
from AppKit import NSWorkspace

# ===================== 从 YAML 读取配置 =====================
def _load_cfg():
    import yaml
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    c = raw["cooler"]
    a = raw["ambilight"]
    ip = raw.get("ipad_charger", {}) or {}
    # mock 子结构 & 平铺写法都兼容
    mock_battery_cfg = None
    if isinstance(ip.get("mock"), dict):
        if ip["mock"].get("enabled") or ip["mock"].get("enabled_battery"):
            mock_battery_cfg = {
                "enabled": True,
                "level": ip["mock"].get("level", 50),
                "charging": ip["mock"].get("charging", False),
            }
    elif ip.get("mock_enabled") or ip.get("mock_battery_enabled"):
        mock_battery_cfg = {
            "enabled": True,
            "level": ip.get("mock_level", 50),
            "charging": ip.get("mock_charging", False),
        }
    return {
        "COOLER_ENABLED": bool(c.get("enabled", True)),
        # poll/fast/confirm: 老配置缺 fast_interval / confirm_hits 时用 AGENTS 默认值
        # (30s / 5s / 3 次)，防止 KeyError 或 confirm=5 把触发时间拉到 90s 以上。
        "COOLER_POLL_INTERVAL":  int(c.get("poll_interval", 30)),
        "COOLER_ON_THRESHOLD":   float(c["on_threshold"]),
        "COOLER_OFF_THRESHOLD":  float(c["off_threshold"]),
        "COOLER_FAIL_OPEN_AFTER": int(c.get("fail_open_after", 3)),
        "COOLER_FAST_INTERVAL":  int(c.get("fast_interval", 5)),
        "COOLER_CONFIRM_HITS":   int(c.get("confirm_hits", 3)),
        "AMBILIGHT_ENABLED": bool(a.get("enabled", True)),
        "AMBILIGHT_TIME_START": a["time_start"],
        "AMBILIGHT_TIME_END":   a["time_end"],
        "AMBILIGHT_LOCK_DELAY": a["lock_delay"],
        "IPAD_ENABLED": bool(ip.get("enabled", False)),
        "IPAD_UDID": ip.get("udid") or None,
        "IPAD_LOW_THRESHOLD": int(ip.get("low_battery_threshold", 20)),
        "IPAD_HIGH_THRESHOLD": int(ip.get("high_battery_threshold", 95)),
        "IPAD_CHECK_INTERVAL": int(ip.get("check_interval", 180)),
        "IPAD_FAIL_CLOSE_AFTER": int(ip.get("fail_close_after", 40)),
        "IPAD_REPORT_PORT": int(ip.get("report_port", 8737)),
        "IPAD_MOCK_BATTERY": mock_battery_cfg,
        "IPAD_SLEEP_SHUTDOWN": bool(ip.get("sleep_shutdown", True)),
    }

CFG = _load_cfg()
COOLER_ENABLED          = CFG["COOLER_ENABLED"]
COOLER_POLL_INTERVAL    = CFG["COOLER_POLL_INTERVAL"]
COOLER_ON_THRESHOLD     = CFG["COOLER_ON_THRESHOLD"]
COOLER_OFF_THRESHOLD    = CFG["COOLER_OFF_THRESHOLD"]
COOLER_FAIL_OPEN_AFTER  = CFG["COOLER_FAIL_OPEN_AFTER"]
COOLER_FAST_INTERVAL    = CFG["COOLER_FAST_INTERVAL"]
COOLER_CONFIRM_HITS     = CFG["COOLER_CONFIRM_HITS"]
AMBILIGHT_ENABLED       = CFG["AMBILIGHT_ENABLED"]
AMBILIGHT_TIME_START    = CFG["AMBILIGHT_TIME_START"]
AMBILIGHT_TIME_END      = CFG["AMBILIGHT_TIME_END"]
AMBILIGHT_LOCK_DELAY    = CFG["AMBILIGHT_LOCK_DELAY"]
IPAD_ENABLED            = CFG["IPAD_ENABLED"]
IPAD_UDID               = CFG["IPAD_UDID"]
IPAD_LOW_THRESHOLD      = CFG["IPAD_LOW_THRESHOLD"]
IPAD_HIGH_THRESHOLD     = CFG["IPAD_HIGH_THRESHOLD"]
# 充电中提前停充裕量（%）：当 charging=True（iPad 明确正在充电）时，level >= HIGH - TRIGGER_MARGIN 就关插座。
# 原因：智能插座物理断开后，iPad BMS 仍会把已接通的涓流/电容继续灌 5~10% 电量（最后直接显示 100%），
# 设置一个提前量抵消这段"充电惯性"。charging=False（已经没在充）时仍按原 HIGH 阈值保持关。
IPAD_HIGH_TRIGGER_MARGIN = int(CFG.get("ipad_high_trigger_margin", 5))
IPAD_CHECK_INTERVAL     = CFG["IPAD_CHECK_INTERVAL"]
IPAD_FAIL_CLOSE_AFTER   = CFG["IPAD_FAIL_CLOSE_AFTER"]
IPAD_REPORT_PORT        = CFG["IPAD_REPORT_PORT"]
IPAD_MOCK_BATTERY       = CFG["IPAD_MOCK_BATTERY"]
IPAD_SLEEP_SHUTDOWN     = CFG["IPAD_SLEEP_SHUTDOWN"]
# 开关冷却期（秒）：两次插座切换之间的最小间隔，防止窄滞环/错误上报造成的震荡
# （实测 93% 时 iPad「充电器已连接」自动化上报写死的 95%，5 秒周期无限开关）
IPAD_SWITCH_COOLDOWN = int(CFG.get("IPAD_SWITCH_COOLDOWN", 300))

# ========== 路径 ==========
LOG      = "/Users/liuhao/Library/Logs/miplug.log"
MIPLUG   = str(BIN_DIR / "miplug.py")
AMBILIGHT_CTRL = str(BIN_DIR / "ambilight_control.py")
IPAD_PLUG = str(BIN_DIR / "ipad_plug.py")
IPAD_BATTERY = str(BIN_DIR / "ipad_battery.py")
SMCTEMP  = str(BIN_DIR / "smctemp")

# ------------- 日志定期截断（>5MB 只保留最后 5000 行，尾部 deque 顺序写回 + os.replace 原子）
def _log_rotate(log_path: str, max_mb: float = 5.0, keep_tail_lines: int = 5000) -> bool:
    """与 GUI 端 log_rotate 实现一致；失败一律吞，不影响 watcher 主业务。"""
    try:
        from collections import deque
        p = Path(log_path)
        if not p.exists() or not p.is_file():
            return False
        if p.stat().st_size <= int(max_mb * 1024 * 1024):
            return False
        keep = max(200, int(keep_tail_lines))
        tail = deque(maxlen=keep)
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    tail.append(line if line.endswith("\n") else line + "\n")
        except OSError:
            return False
        if not tail:
            return False
        tmp = p.with_suffix(p.suffix + ".rotate.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.writelines(tail)
            os.replace(tmp, p)
            return True
        except OSError:
            try: tmp.unlink()
            except OSError: pass
            return False
    except Exception:
        return False

# watcher 启动时先做一次清理（launchd 长期不重启最容易在这里堆日志）
try: _log_rotate(LOG, max_mb=5.0, keep_tail_lines=5000)
except Exception: pass

logging.basicConfig(
    filename=LOG, level=logging.INFO,
    format="%(asctime)s %(levelname)s [watcher] %(message)s",
)

def run_cooler(state):
    logging.info("cooler -> %s", state)
    subprocess.Popen([MIPLUG, state])

def run_ambilight(state):
    logging.info("ambilight -> %s", state)
    subprocess.Popen([AMBILIGHT_CTRL, state])

def run_ipad(state):
    logging.info("ipad -> %s", state)
    subprocess.Popen([PYTHON_BIN, IPAD_PLUG, state])

def read_cpu_temp():
    try:
        out = subprocess.check_output([SMCTEMP, "-c"], timeout=3).decode().strip()
        return float(out)
    except Exception as e:
        logging.warning("smctemp failed: %s", e)
        return None


def read_ipad_battery():
    """通过 ipad_battery.py 读电量，返回 (level, charging) 或 (None, None)。
    使用 import 方式，避免 subprocess 反复创建进程，且 mock 配置直接共享。"""
    try:
        sys.path.insert(0, str(Path(IPAD_BATTERY).parent))
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ipad_battery_mod", IPAD_BATTERY
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.get_ipad_battery(udid=IPAD_UDID, mock_cfg=IPAD_MOCK_BATTERY)
    except Exception as e:
        logging.warning("ipad battery read failed: %s", e)
        return None, None


def in_ambilight_window():
    h = datetime.now().hour
    return h >= AMBILIGHT_TIME_START or h < AMBILIGHT_TIME_END

# ===================== 水冷器辅助函数（模块级，避开 PyObjC selector 检查）=====

def compute_target_state(temp, current):
    """给定温度和当前状态，返回 (target_state, reason)。
    target_state 为 None 表示温度在滞环中间区，不触发切换。

    重要变化（修复 iPad save → kickstart → 冷启动误开水冷）：
      current=None 时不再用 mid=(ON+OFF)/2 做"猜测式初始化"，因为
      save 配置时 CPU 因 IO/fork 会冲到 50°C+（滞环带内），mid 会直接开/关
      导致用户看到"我没碰水冷却器，保存 iPad 后突然开了"。
      current=None 语义改为：状态未知，走 confirm 路径（3 次 FAST 确认后再执行），
      这样冷启动和重启后不会产生不必要的真实硬件动作。
    """
    if temp > COOLER_ON_THRESHOLD:
        return ("on", "T>%.1f°C" % COOLER_ON_THRESHOLD)
    if temp < COOLER_OFF_THRESHOLD:
        return ("off", "T<%.1f°C" % COOLER_OFF_THRESHOLD)
    # OFF <= T <= ON：滞环中间区，当前状态 = last_known_state 保持
    if current is None:
        return (None, "in-band (state unknown: skipped init mid — wait confirm)")
    return (None, "in-band keep %s" % current)

def _reschedule_timer(handler, interval):
    """取消当前轮询定时器，按新 interval 重新启动 repeating 定时器。"""
    if handler._cooler_poll_timer is not None:
        handler._cooler_poll_timer.invalidate()
        handler._cooler_poll_timer = None
    handler._cooler_poll_interval = interval
    handler._cooler_poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        interval, handler, b"_coolerPollTick:", None, True
    )


# ===================== iPad 辅助函数（模块级，避开 PyObjC selector 检查）=======

def compute_ipad_target(level, current, charging=False):
    """给定电量(0-100)和当前插座状态，返回 (target_state, reason)。
    target_state=None 表示在滞环中间区，不切换。

    2026-08-24 停充裕量：charging=True 时按 (HIGH - MARGIN) 作为实际停充阈值，
    抵消智能插座物理断电后 iPad 自身 BMS 继续涓流 5~10% 的"充电惯性"，避免最后冲到 100。
    """
    if level is None:
        return (None, "no-data")
    if level <= IPAD_LOW_THRESHOLD:
        return ("on", "≤%d%%" % IPAD_LOW_THRESHOLD)
    # 关阈值：charging=True 时提前停充（HIGH-MARGIN），charging=False 时仍严格用 HIGH
    off_threshold = IPAD_HIGH_THRESHOLD
    off_note = ""
    if charging:
        off_threshold = max(IPAD_LOW_THRESHOLD + 1, IPAD_HIGH_THRESHOLD - IPAD_HIGH_TRIGGER_MARGIN)
        off_note = " charging-early(-%d%%)" % IPAD_HIGH_TRIGGER_MARGIN
    if level >= off_threshold:
        return ("off", "≥%d%%%s" % (off_threshold, off_note))
    if current is None:
        # 冷启动：未知态不主动开关，等下次命中边界
        return (None, "init mid keep unknown")
    return (None, "in-band keep %s" % current)


def _reschedule_ipad_timer_(handler, interval):
    """取消当前 iPad 轮询定时器，按新 interval 重新启动（selector 用 _ipadPollTick:）。"""
    if handler._ipad_poll_timer is not None:
        handler._ipad_poll_timer.invalidate()
        handler._ipad_poll_timer = None
    handler._ipad_poll_interval = interval
    handler._ipad_poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        interval, handler, b"_ipadPollTick:", None, True
    )


def _log_ipad_event(source, level, charging, action, reason):
    """把 iPad 充电控制事件追加到缓存 JSON（GUI「自动控制动态」卡片读取展示）。
    source: report(iPad上报) / poll(轮询) / failsafe(安全回退)
    action : on(开启充电) / off(停止充电) / keep(保持现状)
    保留最近 30 条，原子写避免 GUI 读到半截文件。"""
    path = os.path.expanduser("~/.cache/miplug/ipad_events.json")
    try:
        events = []
        if os.path.exists(path):
            with open(path) as f:
                events = json.load(f) or []
        events.append({
            "ts": int(time.time()),
            "source": source,
            "level": level,
            "charging": bool(charging) if charging is not None else None,
            "action": action,
            "reason": reason or "",
        })
        events = events[-30:]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(events, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:
        logging.debug("ipad events write failed: %s", e)


_IPAD_PLUG_MOD = None


def _sync_ipad_plug_state(handler):
    """决策前把真实插座状态同步进 watcher 内存（防状态脱节）。

    脱节场景（实测踩过两次）：
      1. GUI/命令行手动开关插座 → watcher 内存态过时
      2. 米家 App / 插座物理按键开关 → 不经过本机任何脚本，缓存文件也过时
    均会导致滞环判定「target==current → keep」而真实插座状态相反，永远不纠正。

    唯一可靠来源：直接 miio 查询设备真实状态（raw get_properties，约 2-4s）。
    查询失败 → 保持现状态不动（下次再试），绝不用过时数据覆盖。
    """
    global _IPAD_PLUG_MOD
    try:
        if _IPAD_PLUG_MOD is None:
            import importlib.util
            spec = importlib.util.spec_from_file_location("ipad_plug_mod", IPAD_PLUG)
            _IPAD_PLUG_MOD = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(_IPAD_PLUG_MOD)
        v = _IPAD_PLUG_MOD._real_get()
        if v is not None:
            new = "on" if v else "off"
            if new != handler._ipad_state:
                logging.info(
                    "ipad: plug state synced from device: %s (watcher had %s)",
                    new, handler._ipad_state,
                )
            handler._ipad_state = new
        else:
            logging.info("ipad: plug state query returned None, keep %s", handler._ipad_state)
    except Exception as e:
        logging.warning("ipad: plug state sync failed: %s", e)


def _ipad_decide_(handler, level, charging, source):
    """电量滞环决策（轮询 tick 与 HTTP 上报共用入口）。模块级避开 PyObjC selector 检查。
    source: poll / report。"""
    with handler._ipad_lock:
        # ---------- 假信号过滤 1：读数连续性校验 ----------
        # 物理事实：电池电量不可能在几秒内跳变几十个百分点。插电瞬间 iOS 电量计
        # 重新校准会把显示值瞬间抬高（实测 24%→95%），iPad「充电器已连接」自动化
        # 此时上报的就是这个校准假值；不校验会导致开/关死循环。
        # 规则：充电中(charging=True)且与上次有效读数跳变 > 25 个百分点 → 丢弃。
        # （慢速的真实充电/放电每分钟最多 1% 左右，25% 阈值有充足余量）
        if charging and handler._ipad_last_level is not None:
            jump = abs(level - handler._ipad_last_level)
            if jump > 25:
                logging.warning(
                    "ipad: [%s] battery=%d%% DROPPED (impossible jump %d%% from last=%d%% — charge calibration artifact)",
                    source, level, jump, handler._ipad_last_level,
                )
                return
        handler._ipad_last_level = level

        # ---------- 假信号过滤 2：插座刚通电 30s 内忽略「充电中」的上报 ----------
        # （双保险：校准假值有时跳变幅度未超 25%，如 82→95，用时间窗兜底）
        if (charging and handler._ipad_state == "on"
                and time.time() - handler._ipad_last_switch_ts < 30):
            logging.info(
                "ipad: [%s] battery=%d%% ignored (charge-calibration window after plug-on, %.0fs)",
                source, level, time.time() - handler._ipad_last_switch_ts,
            )
            return

        # 先同步真实插座状态：手动操作（App/命令行）或 watcher 重启后都会造成内存态与
        # 真实态脱节，不同步会导致「target==current → keep」误判（实测 95% 不停充的根因）
        _sync_ipad_plug_state(handler)

        handler._ipad_fail_cnt = 0

        # ---------- 目标态判定（滞环，含 charging=True 提前停充裕量）----------
        target, reason = compute_ipad_target(level, handler._ipad_state, charging)

        # 冷启动第一次：如果 target 非空，直接执行（不确认，因为电量变化慢）
        # （正常情况 sync 已把 state 初始化为真实值，此分支仅在 sync 失败时兜底）
        if handler._ipad_state is None and target is not None:
            logging.info(
                "ipad: [%s] init battery=%d%% charging=%s → %s (%s)",
                source, level, charging, target, reason,
            )
            run_ipad(target)
            handler._ipad_state = target
            handler._ipad_last_switch_ts = time.time()
            handler._ipad_last_switch_dir = target
            _log_ipad_event(source, level, charging, target, "init " + reason)
            return

        if target is None or target == handler._ipad_state:
            logging.info(
                "ipad: [%s] battery=%d%% charging=%s, keep %s (%s)",
                source, level, charging, handler._ipad_state, reason,
            )
            _log_ipad_event(source, level, charging, "keep", reason)
            return

        # 冷却期防震荡：距上次切换不足 cooldown 秒时拒绝一切新切换（无差别）
        # 实测教训：方向感知版本（反向放行）+ iPad「充电器已连接」自动化每次通电
        # 都上报 95% 假信号（插电校准）→ 形成开/关 5 秒死循环，插一次充电器开关几十次
        since = time.time() - handler._ipad_last_switch_ts
        if since < IPAD_SWITCH_COOLDOWN:
            logging.warning(
                "ipad: [%s] switch to %s REJECTED (cooldown: %ds/%ds since last switch, battery=%d%%)",
                source, target, int(since), IPAD_SWITCH_COOLDOWN, level,
            )
            _log_ipad_event(source, level, charging, "keep",
                            f"冷却期{int(IPAD_SWITCH_COOLDOWN - since)}s内不切换")
            return

        logging.info(
            "ipad: [%s] battery=%d%% charging=%s, %s -> %s (%s, low≤%d%% high≥%d%%)",
            source, level, charging,
            handler._ipad_state, target, reason,
            IPAD_LOW_THRESHOLD, IPAD_HIGH_THRESHOLD,
        )
        run_ipad(target)
        handler._ipad_state = target
        handler._ipad_last_switch_ts = time.time()
        handler._ipad_last_switch_dir = target
        _log_ipad_event(source, level, charging, target, reason)


def _start_battery_report_server(handler):
    """启动 HTTP 上报接收端：iPad 锁屏时 pymobiledevice3 读不到电量（WiFi 休眠断链），
    由 iPad 端「快捷指令自动化」在电量触界时主动上报。

    GET /battery?level=18&charging=0  → 触发与轮询相同的滞环决策
    端口由 plug_config.yaml → ipad_charger.report_port 配置（0 = 禁用）。
    """
    import http.server, urllib.parse
    port = IPAD_REPORT_PORT
    if not port or port <= 0:
        logging.info("ipad: battery report listener disabled (report_port=0)")
        return None

    class _ReportHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                parsed = urllib.parse.urlparse(self.path)
                logging.info("ipad: report request received: %s", self.path)
                if not parsed.path.rstrip("/").endswith("/battery"):
                    self.send_response(404); self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                try:
                    level = int(qs.get("level", ["-1"])[0])
                except ValueError:
                    level = -1
                if not (0 <= level <= 100):
                    self.send_response(400); self.end_headers()
                    self.wfile.write(b"bad level (expect 0-100)")
                    return
                charging = qs.get("charging", ["0"])[0].lower() in ("1", "true", "yes")
                _ipad_decide_(handler, level, charging, "report")
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                logging.warning("ipad: battery report handler error: %s", e)
                try:
                    self.send_response(500); self.end_headers()
                except Exception:
                    pass

        def log_message(self, fmt, *args):
            pass  # 访问日志静默（决策日志由 _ipad_decide_ 输出）

    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", port), _ReportHandler)
    except OSError as e:
        logging.warning("ipad: battery report listener bind failed on port %d: %s", port, e)
        return None
    threading.Thread(target=srv.serve_forever, daemon=True, name="ipad-report-http").start()
    logging.info(
        "ipad: battery report listener started — http://0.0.0.0:%d/battery?level=N&charging=0|1", port
    )
    return srv


def _recover_cooler_state():
    """watcher 重启时从 miplug.log 恢复水冷插座最近状态（取日志里最后一次）。

    ★ 匹配串要够"窄"，否则 iPad/ambilight 的 plug 控制日志会串台：
      允许的 cooler 日志 3 类（每类都是只有水冷才会写的专属签名，含空格前缀避免前缀串）：
       1) "... INFO [watcher] cooler -> on\n"  → Handler.log 里跑 run_cooler 后写
       2) "... INFO cooler -> on\n"            → run_cooler() 函数 log
       3) "... INFO set plug on ok (attempt 1, iface=en0)\n"
                                            → bin/miplug.py 的专属 "set plug %s ok"，
                                               iPad 用 "plug -> %s ok"、ambilight 也用
                                               "plug -> %s ok"，因此不会撞。
    ★ 顺序：正序扫 lines (last-400)，取"最后一条匹配"，不要 reversed 反序扫取第一条
      （之前 reversed 会把最老的 on 当最新 on，导致 off 永远匹配不到）
    """
    last = None
    try:
        with open(LOG, "r", errors="ignore") as f:
            lines = f.readlines()[-400:]
        for line in lines:
            # Case 1/2: watcher 或 run_cooler 直打
            if "[watcher] cooler -> on" in line or line.rstrip("\n").endswith(" cooler -> on"):
                last = "on"; continue
            if "[watcher] cooler -> off" in line or line.rstrip("\n").endswith(" cooler -> off"):
                last = "off"; continue
            # Case 3: miplug.py "set plug {on,off} ok ..."（注意必须有前置空格 + "set plug"，
            #         这样 "[ipad_plug] plug -> on ok" 永远匹配不上）
            stripped = line.rstrip("\n")
            if " set plug on ok " in stripped or stripped.endswith(" set plug on ok"):
                last = "on"; continue
            if " set plug off ok " in stripped or stripped.endswith(" set plug off ok"):
                last = "off"; continue
    except Exception as e:
        logging.warning("cooler state recover failed: %s", e)
    return last


class Handler(NSObject):
    def init(self):
        self = objc.super(Handler, self).init()
        if self is None: return None
        # 水冷器
        self._cooler_poll_timer = None
        self._cooler_poll_interval = COOLER_POLL_INTERVAL
        self._cooler_state = _recover_cooler_state()  # 重启恢复（防冷启动 mid 误判）
        self._cooler_fail_cnt = 0
        self._cooler_confirm_target = None
        self._cooler_confirm_count = 0
        # 氛围灯
        self._ambilight_timer = None
        self._ambilight_lock_time = 0
        # iPad 充电
        self._ipad_poll_timer = None
        self._ipad_poll_interval = IPAD_CHECK_INTERVAL
        self._ipad_state = None          # None=未知, "on"/"off"
        self._ipad_fail_cnt = 0
        self._ipad_last_switch_ts = 0    # 上次插座切换时间戳（冷却期防震荡）
        self._ipad_last_switch_dir = None  # 上次切换方向（"on"/"off"，方向感知冷却）
        self._ipad_last_level = None     # 上次有效电量读数（连续性校验防校准假值）
        self._ipad_lock = threading.Lock()  # HTTP 上报线程与 NSTimer 轮询线程的决策互斥
        return self

    # ---- 氛围灯定时器清理（PyObjC 安全：无下划线 selector，只接受 self）--------------
    def cancelAmb(self):
        if self._ambilight_timer is not None:
            self._ambilight_timer.invalidate()
            self._ambilight_timer = None
            logging.info("ambilight: countdown cancelled")

    # ========================================================================
    #  水冷器 — 两阶段滞环控制
    # ========================================================================
    def startCoolerPolling(self):
        """启动水冷器全天候轮询（只调一次，在程序入口处）。

        注意：_reschedule_timer 必须放在首 tick **之前**做，因为首 tick 里如果命中
        on/off 阈值会进入 fast confirm 并自己再 _reschedule_timer(FAST)。如果顺序写反
        （先 tick 再外部 reschedule(SLOW)），SLOW 的 30s 会覆盖 FAST 的 5s，导致 confirm
        阶段被拉长到 90s+ 且中间 1°C 掉就 abort——表现就是"CPU 明明 60°C+，水冷老不开"。
        """
        if not COOLER_ENABLED:
            logging.info("cooler: disabled by config (cooler.enabled=false) — skip auto polling")
        else:
            _reschedule_timer(self, COOLER_POLL_INTERVAL)
            self._coolerPollTick_(None)
            logging.info(
                "cooler: 2-stage polling started — slow=%ds fast=%ds confirm=%d (ON>%.1f°C OFF<%.1f°C, fail-safe ON after %d misses)",
                COOLER_POLL_INTERVAL, COOLER_FAST_INTERVAL, COOLER_CONFIRM_HITS,
                COOLER_ON_THRESHOLD, COOLER_OFF_THRESHOLD, COOLER_FAIL_OPEN_AFTER,
            )

    def _coolerPollTick_(self, timer):
        if not COOLER_ENABLED:
            return
        temp = read_cpu_temp()

        # ---------- 温度读取失败：安全回退逻辑 ----------
        if temp is None:
            self._cooler_fail_cnt += 1
            if self._cooler_fail_cnt >= COOLER_FAIL_OPEN_AFTER:
                if self._cooler_state != "on":
                    logging.warning(
                        "cooler: temp read failed %d times in a row → fail-safe ON (prev state=%s)",
                        self._cooler_fail_cnt, self._cooler_state,
                    )
                    run_cooler("on")
                    self._cooler_state = "on"
                self._cooler_confirm_target = None
                self._cooler_confirm_count = 0
                if self._cooler_poll_interval != COOLER_POLL_INTERVAL:
                    logging.info("cooler: fail-safe → slow poll %ds", COOLER_POLL_INTERVAL)
                    _reschedule_timer(self, COOLER_POLL_INTERVAL)
                self._cooler_fail_cnt = 0
            else:
                logging.info(
                    "cooler: temp read failed (%d/%d), keeping %s confirm=%s",
                    self._cooler_fail_cnt, COOLER_FAIL_OPEN_AFTER,
                    self._cooler_state, self._cooler_confirm_target,
                )
            return

        self._cooler_fail_cnt = 0

        # ---------- 计算本 tick 应追求的目标态 ----------
        (target, reason) = compute_target_state(temp, self._cooler_state)

        # 冷启动初态决定：仅限 target 是硬阈值命中 (T>ON or T<OFF) 情况，
        # 且有 LAST_KNOWN 状态（从 log 恢复的 _cooler_state）则走 keep，不直接动。
        # ★ 关键修：去掉了之前 "T 在滞环带内也按 mid=46.5°C 二分开关" 的分支，
        #           因为 save 配置 kickstart 后 CPU 刚好 50°C（带内）→ 会把水冷"猜开"，
        #           用户看到的症状就是 "iPad 保存后水冷突然开了"。
        if self._cooler_state is None and target is not None:
            logging.info(
                "cooler: init CPU %.1f°C → confirm-%s-queued (%s) — 3× 快确认后才执行 (防冷启动 mid 误判)",
                temp, target, reason,
            )
            # 不调用 run_cooler；直接进入 FAST confirm 流程（下方）

        # ---------- 快速确认阶段逻辑 ----------
        if self._cooler_confirm_target is not None:
            if target == self._cooler_confirm_target:
                self._cooler_confirm_count += 1
                logging.info(
                    "cooler: fast confirm CPU %.1f°C → %s (%d/%d, reason=%s)",
                    temp, self._cooler_confirm_target,
                    self._cooler_confirm_count, COOLER_CONFIRM_HITS, reason,
                )
                if self._cooler_confirm_count >= COOLER_CONFIRM_HITS:
                    logging.info(
                        "cooler: fast confirm %d/%d OK → state %s -> %s (ON>%.1f OFF<%.1f)",
                        COOLER_CONFIRM_HITS, COOLER_CONFIRM_HITS,
                        self._cooler_state, self._cooler_confirm_target,
                        COOLER_ON_THRESHOLD, COOLER_OFF_THRESHOLD,
                    )
                    run_cooler(self._cooler_confirm_target)
                    self._cooler_state = self._cooler_confirm_target
                    self._cooler_confirm_target = None
                    self._cooler_confirm_count = 0
                    _reschedule_timer(self, COOLER_POLL_INTERVAL)
                    logging.info("cooler: action done → slow poll %ds", COOLER_POLL_INTERVAL)
                return
            else:
                logging.info(
                    "cooler: fast confirm broken CPU %.1f°C target=%s actual=%s → abort, slow poll %ds",
                    temp, self._cooler_confirm_target,
                    target if target is not None else "(in-band)",
                    COOLER_POLL_INTERVAL,
                )
                self._cooler_confirm_target = None
                self._cooler_confirm_count = 0
                _reschedule_timer(self, COOLER_POLL_INTERVAL)
                return

        # ---------- 慢速常态阶段 ----------
        if target is None or target == self._cooler_state:
            logging.info("cooler: CPU %.1f°C, keep %s", temp, self._cooler_state)
            return
        # 进入快速确认阶段
        self._cooler_confirm_target = target
        self._cooler_confirm_count = 1
        logging.info(
            "cooler: slow poll CPU %.1f°C hit %s (%s) → enter fast confirm %ds (%d/%d)",
            temp, target, reason, COOLER_FAST_INTERVAL,
            self._cooler_confirm_count, COOLER_CONFIRM_HITS,
        )
        _reschedule_timer(self, COOLER_FAST_INTERVAL)

    # ========================================================================
    #  iPad 充电 — 单阶段滞环控制
    # ========================================================================
    def startIpadPolling(self):
        """仅当 ipad_charger.enabled=true 时启动。mock 模式仍会生效（测试链路）。"""
        if not IPAD_ENABLED:
            logging.info(
                "ipad: disabled in plug_config.yaml (ipad_charger.enabled=false) — skipped"
            )
            return
        # 立即跑一次，然后启动 repeating 定时器
        self._ipadPollTick_(None)
        _reschedule_ipad_timer_(self, IPAD_CHECK_INTERVAL)
        # HTTP 上报接收端（iPad 快捷指令自动化 → 锁屏时的电量触界通知）
        _start_battery_report_server(self)
        logging.info(
            "ipad: polling started — every %ds (on≤%d%% off≥%d%%, fail-safe OFF after %d misses "
            "while plug ON, report_port=%s, mock_battery=%s, udid=%s)",
            IPAD_CHECK_INTERVAL,
            IPAD_LOW_THRESHOLD, IPAD_HIGH_THRESHOLD,
            IPAD_FAIL_CLOSE_AFTER,
            IPAD_REPORT_PORT or "off",
            bool(IPAD_MOCK_BATTERY and IPAD_MOCK_BATTERY.get("enabled")),
            IPAD_UDID,
        )

    def _ipadPollTick_(self, timer):
        level, charging = read_ipad_battery()

        # ---------- 读不到：宽容处理 ----------
        # iPad 锁屏后 WiFi 休眠、RemoteXPC 断链是常态（实测成功率 ~6%），
        # 读不到 ≠ 充电异常，按插座状态区分对待：
        #   插座关/未知 → 无任何风险，静默等待 iOS 唤醒窗口
        #   插座开     → 继续充电（iPad 自带充满保护），仅当长时间（默认40次≈2小时）
        #                完全无读数才 fail-safe 关插座，防止插座永久开启
        if level is None:
            if self._ipad_state in (None, "off"):
                logging.info(
                    "ipad: battery unreachable (likely locked/asleep), plug=%s — standby",
                    self._ipad_state,
                )
                self._ipad_fail_cnt = 0
                return
            self._ipad_fail_cnt += 1
            if self._ipad_fail_cnt >= IPAD_FAIL_CLOSE_AFTER:
                logging.warning(
                    "ipad: charging but no battery data for %d polls → fail-safe OFF (prev=%s)",
                    self._ipad_fail_cnt, self._ipad_state,
                )
                run_ipad("off")
                self._ipad_state = "off"
                self._ipad_fail_cnt = 0
                _log_ipad_event("failsafe", None, None, "off",
                                "充电中%d次无读数，安全关闭" % IPAD_FAIL_CLOSE_AFTER)
            else:
                logging.info(
                    "ipad: charging but battery unreachable (%d/%d), keep charging",
                    self._ipad_fail_cnt, IPAD_FAIL_CLOSE_AFTER,
                )
            return

        _ipad_decide_(self, level, charging, "poll")

    # ========================================================================
    #  锁屏事件（仅控制氛围灯）
    # ========================================================================
    def onLock_(self, note):
        logging.info("event: screen locked")
        self.cancelAmb()
        if not AMBILIGHT_ENABLED:
            logging.info("ambilight: disabled by config (ambilight.enabled=false) — skip")
            return
        if in_ambilight_window():
            self._ambilight_lock_time = time.time()
            logging.info("ambilight: start %ds countdown to off (window %d:00-%d:00)",
                         AMBILIGHT_LOCK_DELAY, AMBILIGHT_TIME_START, AMBILIGHT_TIME_END)
            self._ambilight_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                AMBILIGHT_LOCK_DELAY, self, b"ambilightTurnOff:", None, False
            )
        else:
            logging.info("ambilight: out of time window (hour=%d), skip", datetime.now().hour)

    def ambilightTurnOff_(self, timer):
        self._ambilight_timer = None
        if not AMBILIGHT_ENABLED:
            return
        if in_ambilight_window():
            logging.info("ambilight: countdown expired -> off")
            run_ambilight("off")
        else:
            logging.info("ambilight: countdown expired but out of window, skip")

    # ========================================================================
    #  解锁事件（仅控制氛围灯）
    # ========================================================================
    def onUnlock_(self, note):
        logging.info("event: screen unlocked")
        self.cancelAmb()
        if not AMBILIGHT_ENABLED:
            logging.info("ambilight: disabled by config — skip")
            return
        if in_ambilight_window():
            logging.info("ambilight: unlocked -> on")
            run_ambilight("on")
        else:
            logging.info("ambilight: out of window (hour=%d), skip", datetime.now().hour)

    # ========================================================================
    #  系统休眠/关机：强制关所有可控插座（用户报的 bug——关机前水冷不关就永远开着，
    #  因为 Mac 关机后再开机 CPU 温度直接 <33℃，滞环不会自动关，所以必须在「进入
    #  低功耗状态前」主动把插座关一遍。关的时候同步内存 state 避免唤醒后误判。）
    # ========================================================================
    def doShutdownAllPlugs_(self, label):
        """统一的休眠/关机断电动作：cooler / ambilight / ipad(sleep_shutdown=true) 全关

        【注意：PyObjC 命名限制】
        这个方法不能带下划线开头/结尾，否则 PyObjC 会把 _ 翻译成 selector 的冒号 :，
        导致 "_shutdown_all_plugs_" → selector="_shutdown:all:plugs:" 需要 3 个 objc 参数，
        直接 BadPrototypeError。用纯驼峰命名避免 ObjC selector 被误翻译。
        """
        # 1) 液冷水冷散热器：永远关（休眠/关机后 CPU 不可能再 >60℃，
        #    关了比开着安全；enabled=false 也不关，避免打扰用户手动模式）
        if COOLER_ENABLED and self._cooler_state != "off":
            logging.info("cooler: %s -> force OFF (safe disconnect, CPU will not poll below 33C)", label)
            run_cooler("off")
            self._cooler_state = "off"
            self._cooler_confirm_target = None
            self._cooler_confirm_count = 0
        # 2) 氛围灯：休眠/关机都要关（不然人走了灯还亮）
        #    直接 run off，无需条件判断：ambilight_control.py 对"已经关"的状态
        #    再调 off 是幂等的（miIO set_properties 不报错），不会浪费电量
        if AMBILIGHT_ENABLED:
            logging.info("ambilight: %s -> force OFF (safe disconnect)", label)
            run_ambilight("off")
            # 取消锁屏倒计时（避免唤醒后还按休眠前的锁时间关灯）
            self.cancelAmb()
            self._ambilight_lock_time = 0
        # 3) iPad 充电：仅在 sleep_shutdown=true 时关（默认 true）
        if IPAD_ENABLED and IPAD_SLEEP_SHUTDOWN and self._ipad_state != "off":
            logging.info("ipad: %s -> force OFF (safe disconnect, sleep_shutdown=true)", label)
            run_ipad("off")
            self._ipad_state = "off"
            self._ipad_fail_cnt = 0

    def onSleep_(self, note):
        logging.info("system going to sleep")
        self.doShutdownAllPlugs_("sleep")

    def onShutdown_(self, note):
        name = (note.name() if callable(getattr(note, "name", None)) else "") or "shutdown"
        logging.info("system shutdown event received: %s", name)
        self.doShutdownAllPlugs_("shutdown")

    # ========================================================================
    #  系统唤醒事件（氛围灯 + iPad 状态重置）
    # ========================================================================
    def onWake_(self, note):
        logging.info("system woke up")
        self.cancelAmb()
        # iPad：清零失败计数，避免 Mac 睡眠期间的失败堆积
        self._ipad_fail_cnt = 0
        if not AMBILIGHT_ENABLED:
            logging.info("ambilight: disabled by config — skip wake control")
            return
        if self._ambilight_lock_time > 0:
            elapsed = time.time() - self._ambilight_lock_time
            if elapsed >= AMBILIGHT_LOCK_DELAY:
                logging.info("ambilight: woke %ds after lock (>= %ds) -> off",
                             int(elapsed), AMBILIGHT_LOCK_DELAY)
                if in_ambilight_window():
                    run_ambilight("off")
                return
            else:
                logging.info("ambilight: woke %ds after lock (< %ds) -> on",
                             int(elapsed), AMBILIGHT_LOCK_DELAY)
        if in_ambilight_window():
            run_ambilight("on")
        else:
            logging.info("ambilight: out of window (hour=%d), skip", datetime.now().hour)


# ===================== Mock 测试入口（不用启动 launchd / ObjC runloop）=====================
def _mock_test_main(rounds=3):
    """--mock-test：不启动 ObjC runloop，直接跑几轮决策链路，便于在设备未到货时验证逻辑。
    
    - 强制 IPAD_ENABLED=True（否则轮询根本不启动）
    - 若用户未配置 mock battery，默认给个 level=50 的假值（plug_config.yaml 的 mock.level 优先）
    - 水冷 / 氛围灯也不跑，只演示 iPad 自动充电决策
    """
    global IPAD_ENABLED, IPAD_MOCK_BATTERY
    IPAD_ENABLED = True
    if not IPAD_MOCK_BATTERY or not IPAD_MOCK_BATTERY.get("enabled"):
        IPAD_MOCK_BATTERY = {"enabled": True, "level": 50, "charging": False}

    # 尝试读取 plug_config.yaml 里 ipad_charger.mock.level（用户可能改了，比如15/98）
    try:
        import yaml as _y
        with open(CONFIG_PATH) as _f:
            _raw = _y.safe_load(_f) or {}
        _mock = (_raw.get("ipad_charger") or {}).get("mock") or {}
        if isinstance(_mock, dict) and _mock.get("level") is not None:
            IPAD_MOCK_BATTERY["level"] = int(_mock["level"])
        if isinstance(_mock, dict) and _mock.get("charging") is not None:
            IPAD_MOCK_BATTERY["charging"] = bool(_mock["charging"])
        logging.info("[MOCK-MAIN] 使用 plug_config.yaml 的 mock 电量: level=%d charging=%s",
                     IPAD_MOCK_BATTERY["level"], IPAD_MOCK_BATTERY["charging"])
    except Exception as e:
        logging.info("[MOCK-MAIN] 读 plug_config.yaml mock 段失败，用默认 level=50 (%s)", e)

    handler = Handler.alloc().init()
    logging.info("[MOCK-MAIN] 开始 %d 轮 iPad 决策（间隔1s，不等真实 %ds 周期）",
                 rounds, IPAD_CHECK_INTERVAL)
    for i in range(rounds):
        logging.info("[MOCK-MAIN] --- round %d/%d (battery level=%d, charging=%s) ---",
                     i+1, rounds, IPAD_MOCK_BATTERY["level"], IPAD_MOCK_BATTERY["charging"])
        try:
            handler._ipadPollTick_(None)
        except Exception:
            logging.exception("[MOCK-MAIN] round %d exception", i+1)
        time.sleep(1)
    logging.info("[MOCK-MAIN] 完成 %d 轮 iPad 自动充电验证", rounds)


if len(sys.argv) >= 2 and sys.argv[1] == "--mock-test":
    _r = 3
    try:
        if len(sys.argv) >= 3: _r = int(sys.argv[2])
    except Exception:
        pass
    _mock_test_main(_r)
    sys.exit(0)


# ===================== 入口 =====================
handler = Handler.alloc().init()

nc = NSDistributedNotificationCenter.defaultCenter()
nc.addObserver_selector_name_object_(handler, b"onLock:",   "com.apple.screenIsLocked",   None)
nc.addObserver_selector_name_object_(handler, b"onUnlock:", "com.apple.screenIsUnlocked", None)

ws_nc = NSWorkspace.sharedWorkspace().notificationCenter()
ws_nc.addObserver_selector_name_object_(
    handler, b"onWake:", "NSWorkspaceDidWakeNotification", None
)
ws_nc.addObserver_selector_name_object_(
    handler, b"onSleep:", "NSWorkspaceWillSleepNotification", None
)
# 关机前通知（macOS 会尝试向前台 NSWorkspace 会话发送；SIGTERM 钩子是兜底）
ws_nc.addObserver_selector_name_object_(
    handler, b"onShutdown:", "NSWorkspaceWillPowerOffNotification", None
)

# —— 最可靠的「关所有插座」钩子：SIGTERM / SIGINT ——
# launchd 在 Mac 关机时会按顺序给 GUI-domain 的 LaunchAgent 发送 SIGTERM（然后 ~5s 后 SIGKILL），
# 这是 100% 能收到的"即将退出"信号，比 NSWorkspaceWillPowerOffNotification 更稳。
# 安装信号处理器：直接让 handler 执行一遍 shutdown_all_plugs_("signal-SIGTERM/SIGINT")。
# 注意：必须在主线程安装（下面 NSRunLoop.run 就在主线程）。
def _term_handler(signum, frame):
    try:
        signame = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        handler.doShutdownAllPlugs_("signal-%s" % signame)
    except Exception as e:
        logging.warning("signal handler cleanup failed: %s", e)
    finally:
        # 正常退出（launchd 要求 SIGTERM 后 0~5 秒内得退出）
        sys.exit(0)

signal.signal(signal.SIGTERM, _term_handler)
signal.signal(signal.SIGINT, _term_handler)

handler.startCoolerPolling()
handler.startIpadPolling()

ipad_status = (
    "enabled every %ds on≤%d%% off≥%d%% fail-off after %d misses, mock=%s, udid=%s"
    % (
        IPAD_CHECK_INTERVAL,
        IPAD_LOW_THRESHOLD, IPAD_HIGH_THRESHOLD, IPAD_FAIL_CLOSE_AFTER,
        bool(IPAD_MOCK_BATTERY and IPAD_MOCK_BATTERY.get("enabled")),
        IPAD_UDID,
    )
) if IPAD_ENABLED else "disabled"

logging.info(
    "watcher started pid=%d | "
    "cooler: slow=%ds fast=%ds confirm=%d on>%.1f°C off<%.1f°C fail-safe after %d misses | "
    "ambilight: window=%d:00-%d:00 delay=%ds | "
    "ipad: %s",
    os.getpid(),
    COOLER_POLL_INTERVAL, COOLER_FAST_INTERVAL, COOLER_CONFIRM_HITS,
    COOLER_ON_THRESHOLD, COOLER_OFF_THRESHOLD, COOLER_FAIL_OPEN_AFTER,
    AMBILIGHT_TIME_START, AMBILIGHT_TIME_END, AMBILIGHT_LOCK_DELAY,
    ipad_status,
)

loop = NSRunLoop.currentRunLoop()
_last_log_rotate_ts = 0.0
_LOG_ROTATE_INTERVAL = 60 * 60  # 每 1 小时检查一次（实际只有 >5MB 才做 I/O）
while True:
    loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(60))
    now = time.time()
    if now - _last_log_rotate_ts > _LOG_ROTATE_INTERVAL:
        _last_log_rotate_ts = now
        try:
            _log_rotate(LOG, max_mb=5.0, keep_tail_lines=5000)
        except Exception:
            pass