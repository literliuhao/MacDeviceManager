#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""氛围灯开关手动控制 (cuco.plug.v3)

Usage: ambilight_control.py on|off|status [--force]

设备: 见 config/plug_config.yaml → ambilight.ip
时间窗口: 20:00-08:00 (on/off 受限; status/--force 不受限)

网络绑定 en0 (SO_BOUND_IF)，保证 launchd/屏保环境下 UDP 不被
Parallels 桥接网卡劫持。
"""
import sys, time, socket, logging, ctypes, ctypes.util, os
from datetime import datetime
from pathlib import Path

def _project_root():
    env = os.environ.get("DEVICE_MANAGER_HOME")
    if env: return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
PROJECT_ROOT = _project_root()
CONFIG_PATH = PROJECT_ROOT / "config" / "plug_config.yaml"

from miio import MiotDevice

# 从统一配置读取 IP / Token
def _load_cfg():
    import yaml
    with open(CONFIG_PATH) as f:
        raw = yaml.safe_load(f)
    a = raw["ambilight"]
    return a["ip"], a["token"], a["time_start"], a["time_end"]

IP, TOKEN, TIME_START, TIME_END = _load_cfg()
IFACE = "en0"
LOG = "/Users/liuhao/Library/Logs/ambilight.log"

IPPROTO_IP = 0
IP_BOUND_IF = 25  # macOS <sys/socket.h>

logging.basicConfig(
    filename=LOG, level=logging.INFO,
    format="%(asctime)s %(levelname)s [control] %(message)s",
)

# --- 强制所有 socket 绑定到 en0 (同 miplug.py) -------------------------------
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_if_index = _libc.if_nametoindex(IFACE.encode())
if _if_index == 0:
    logging.error("interface %s not found", IFACE)
    sys.exit(1)

_orig_socket = socket.socket


class _BoundSocket(_orig_socket):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        try:
            self.setsockopt(IPPROTO_IP, IP_BOUND_IF, _if_index)
        except OSError as e:
            logging.warning("bind %s failed: %s", IFACE, e)


socket.socket = _BoundSocket
# -----------------------------------------------------------------------------


def in_time_window():
    """20:00-08:00 返回 True"""
    h = datetime.now().hour
    return h >= TIME_START or h < TIME_END


def control_plug(on):
    """开/关插座，5 次重试"""
    d = MiotDevice(ip=IP, token=TOKEN)
    action = "on" if on else "off"
    for i in range(5):
        try:
            r = d.set_property_by(2, 1, on)
            code = r[0].get("code") if r else -1
            if code == 0:
                logging.info("plug -> %s ok (attempt %d)", action, i + 1)
                return True
            logging.warning("attempt %d returned %s", i + 1, r)
        except Exception as e:
            logging.warning("attempt %d error: %s", i + 1, e)
        time.sleep(2)
    logging.error("failed to set plug %s", action)
    return False


def get_status():
    """查询当前电源状态"""
    d = MiotDevice(ip=IP, token=TOKEN)
    r = d.get_property_by(2, 1)
    if r and isinstance(r, list):
        return r[0].get("value", None)
    return None


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("on", "off", "status"):
        print("usage: ambilight_control.py on|off|status [--force]")
        sys.exit(2)

    cmd = args[0]
    force = "--force" in args

    if cmd == "status":
        try:
            v = get_status()
            state = "on" if v else "off"
            print(state)
            logging.info("status: %s", state)
        except Exception as e:
            print(f"error: {e}")
            logging.error("status query failed: %s", e)
            sys.exit(1)
        return

    target = cmd == "on"

    # 时间窗口判断 (除非 --force)
    if not force and not in_time_window():
        h = datetime.now().hour
        msg = f"skip: out of time window (20:00-08:00), now {h:02d}:xx"
        logging.info("skip %s (%s)", cmd, msg)
        print(msg)
        return

    ok = control_plug(target)
    print(f"{'ok' if ok else 'fail'}: plug {cmd}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
