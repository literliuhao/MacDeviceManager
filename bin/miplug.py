#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""Toggle Xiaomi smart plug (cuco.plug.v3) via miIO/miot.
Usage: miplug.py on|off

Forces all UDP sockets to be bound to a specific network interface
(via SO_BOUND_IF on macOS) so it works reliably under launchd/screensaver
context where multiple default routes (Parallels bridges) can hijack traffic.
"""
import sys, time, socket, logging, os, ctypes, ctypes.util
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
    c = raw["cooler"]
    return c["ip"], c["token"]

IP, TOKEN = _load_cfg()
IFACE = "en0"                       # LAN interface
LOG = "/Users/liuhao/Library/Logs/miplug.log"
IPPROTO_IP = 0
IP_BOUND_IF = 25                     # <sys/socket.h> on macOS

logging.basicConfig(
    filename=LOG, level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# --- force every socket in this process onto IFACE ---------------------------
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

def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off"):
        print("usage: miplug.py on|off"); sys.exit(2)
    target = sys.argv[1] == "on"
    d = MiotDevice(ip=IP, token=TOKEN)
    for i in range(5):
        try:
            r = d.set_property_by(2, 1, target)
            code = r[0].get("code") if r else -1
            if code == 0:
                logging.info("set plug %s ok (attempt %d, iface=%s)", sys.argv[1], i + 1, IFACE)
                return
            logging.warning("attempt %d returned %s", i + 1, r)
        except Exception as e:
            logging.warning("attempt %d error: %s", i + 1, e)
        time.sleep(2)
    logging.error("failed to set plug %s", sys.argv[1])
    sys.exit(1)

if __name__ == "__main__":
    main()
