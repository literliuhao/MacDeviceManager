#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""iPad 充电智能插座控制（小米智能插座3 / cuco.plug.v3）

Usage: ipad_plug.py on|off|status

架构与 miplug.py / ambilight_control.py 一致：
  - 从 plug_config.yaml → ipad_charger 段读取 plug_ip / plug_token / mock 开关
  - 所有 UDP socket 强制绑定 en0（SO_BOUND_IF），避免 launchd/屏保/Parallels 劫持
  - 5 次重试，写入 /Users/liuhao/Library/Logs/miplug.log（统一日志文件，前缀区分）

特殊：智能插座到货前可使用 MOCK 模式
  - 配置 plug_ip/plug_token 为空 OR 显式 mock.enabled=true → 进入 mock
  - mock 下 status 始终返回最近一次 set 的值（进程内静态变量 + 文件缓存）
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Optional

def _project_root():
    env = os.environ.get("DEVICE_MANAGER_HOME")
    if env: return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
PROJECT_ROOT = _project_root()
CONFIG_PATH = PROJECT_ROOT / "config" / "plug_config.yaml"
LOG_PATH = "/Users/liuhao/Library/Logs/miplug.log"
IFACE = "en0"
IPPROTO_IP = 0
IP_BOUND_IF = 25  # macOS <sys/socket.h>

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ipad_plug] %(message)s",
)
log = logging.getLogger("ipad_plug")

# ---------- 状态缓存（mock 模式下记忆上次开关）----------
# 注：不放在 APP_DIR（/Users/liuhao/bin）下，launchd 沙箱下对 bin 目录写入无权限
STATE_CACHE_DIR = Path.home() / ".cache" / "miplug"
STATE_CACHE = STATE_CACHE_DIR / "ipad_plug_state"


def _cache_read() -> Optional[bool]:
    try:
        return bool(int(STATE_CACHE.read_text().strip()))
    except Exception:
        return None


def _cache_write(v: bool):
    try:
        STATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_CACHE.write_text("1" if v else "0")
    except Exception:
        pass


# ---------- 加载配置 ----------
def _load_cfg():
    import yaml
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f) or {}
    ic = raw.get("ipad_charger", {}) or {}
    # mock 判定：显式开启 / IP或TOKEN 为空 → 都算 mock（插座没到）
    mock_explicit = False
    if isinstance(ic.get("mock"), dict):
        mock_explicit = bool(ic["mock"].get("enabled_plug", False))
    mock_by_empty = not (str(ic.get("plug_ip") or "").strip() and str(ic.get("plug_token") or "").strip())
    return {
        "plug_ip": str(ic.get("plug_ip") or "").strip(),
        "plug_token": str(ic.get("plug_token") or "").strip(),
        "mock": bool(mock_explicit or mock_by_empty),
    }


CFG = _load_cfg()

# ---------- en0 绑定（和 miplug.py / ambilight_control.py 相同）----------
try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c"))
    _if_index = _libc.if_nametoindex(IFACE.encode())
except Exception as e:
    log.warning("bind libc/if_nametoindex failed: %s", e)
    _if_index = 0

if _if_index:
    _orig_socket = socket.socket

    class _BoundSocket(_orig_socket):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            try:
                self.setsockopt(IPPROTO_IP, IP_BOUND_IF, _if_index)
            except OSError as e:
                log.warning("bind %s failed: %s", IFACE, e)

    socket.socket = _BoundSocket


# ---------- 真实 / mock 控制 ----------
# cuco.plug.v3（米家智能插座3）官方 MIOT spec：
#   主开关 = SIID=2/PIID=1（bool）；实时功率 = SIID=11/PIID=2（W）；耗电量 = SIID=11/PIID=1
#   SIID=4 是「充电保护」功能（功率低于阈值 N 分钟自动断电），不是继电器，勿操作
# 写入必须用 raw "set_properties" 指令：python-miio 的 set_property_by 在无 mapping 时
# 对 bool 序列化有缺陷（返回 code=0 假成功但继电器不动，实测功率 9W 不变）
MIOT_SW_SIID = 2
MIOT_SW_PIID = 1


def _new_miot_device():
    from miio import MiotDevice
    # 抑制 miio 每次调用打印 "Neither the class nor the parameter defines the mapping"
    # （MiotDevice 未提供 mapping 的常规提示，SIID/PIID 直读写不受影响）
    logging.getLogger("miio").setLevel(logging.ERROR)
    return MiotDevice(ip=CFG["plug_ip"], token=CFG["plug_token"])


def _real_set(on: bool) -> bool:
    d = _new_miot_device()
    action = "on" if on else "off"
    for i in range(5):
        try:
            r = d.send("set_properties", [
                {"siid": MIOT_SW_SIID, "piid": MIOT_SW_PIID, "value": bool(on)}
            ])
            code = r[0].get("code") if r else -1
            if code == 0:
                log.info("plug -> %s ok (attempt %d, iface=%s)", action, i + 1, IFACE)
                _cache_write(on)
                return True
            log.warning("attempt %d returned %s", i + 1, r)
        except Exception as e:
            log.warning("attempt %d error: %s", i + 1, e)
        time.sleep(2)
    log.error("failed to set plug %s", action)
    return False


def _real_get() -> Optional[bool]:
    d = _new_miot_device()
    try:
        r = d.send("get_properties", [
            {"siid": MIOT_SW_SIID, "piid": MIOT_SW_PIID}
        ])
        if r and isinstance(r, list) and len(r) > 0:
            v = r[0].get("value")
            if isinstance(v, bool):
                return v
            if v is not None:
                return bool(v)
    except Exception as e:
        log.warning("status query error: %s", e)
    return None


def set_plug(on: bool) -> bool:
    action = "on" if on else "off"
    if CFG["mock"]:
        log.info("[MOCK] plug -> %s (no real device, cached)", action)
        print(f"[MOCK] plug {action}")
        _cache_write(on)
        return True
    ok = _real_set(on)
    print(f"{'ok' if ok else 'fail'}: plug {action}")
    return ok


def get_status() -> Optional[bool]:
    if CFG["mock"]:
        cached = _cache_read()
        log.info("[MOCK] status -> %s (cached)", ("off" if cached is False else ("on" if cached else "unknown")))
        return cached
    return _real_get()


# ===================== CLI =====================
def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off", "status"):
        print("usage: ipad_plug.py on|off|status")
        sys.exit(2)
    cmd = sys.argv[1]

    if cmd == "status":
        v = get_status()
        if v is None:
            print("unknown")
        else:
            print("on" if v else "off")
        return

    target = cmd == "on"
    ok = set_plug(target)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
