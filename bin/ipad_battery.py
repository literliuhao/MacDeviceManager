#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""iPad 无线电量读取模块（pymobiledevice3）

支持两种模式：
  1. 真实模式：通过 pymobiledevice3 无线读取（依赖 tunneld + WiFi 配对）
  2. 模拟模式：配置 mock=true 时，返回 mock_level / mock_charging（用于插座到货前验证逻辑）

作为库使用：
    from ipad_battery import get_ipad_battery
    level, charging = get_ipad_battery(udid, mock_cfg=None)
    # level: int 0-100 或 None；charging: bool 或 None

命令行使用（手动测试）：
    python3 ipad_battery.py            # 读一次，输出 JSON
    python3 ipad_battery.py --loop 5   # 每 5 秒循环打印
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple


def _project_root():
    env = os.environ.get("DEVICE_MANAGER_HOME")
    if env: return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
PROJECT_ROOT = _project_root()
CFG_PATH = PROJECT_ROOT / "config" / "plug_config.yaml"
LOG = "/Users/liuhao/Library/Logs/miplug.log"

log = logging.getLogger("ipad_battery")


def _load_yaml():
    try:
        import yaml
        with open(CFG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.warning("load yaml failed: %s", e)
        return {}


def get_ipad_battery(
    udid: Optional[str] = None,
    mock_cfg: Optional[dict] = None,
    timeout: int = 15,
) -> Tuple[Optional[int], Optional[bool]]:
    """返回 (电量百分比, 是否充电中)。任何异常返回 (None, None)。

    mock_cfg = {"enabled": True, "level": 50, "charging": False}
    开启后不调用 pymobiledevice3，直接返回模拟值（用于插座到货前跑通逻辑）。
    """
    # ---- 模拟模式 ----
    if mock_cfg and mock_cfg.get("enabled"):
        try:
            lvl = int(mock_cfg.get("level", 50))
            chg = bool(mock_cfg.get("charging", False))
            lvl = max(0, min(100, lvl))
            return lvl, chg
        except Exception:
            return None, None

    # ---- 真实模式 ----
    py = sys.executable
    try:
        import importlib.util
        spec = importlib.util.find_spec("pymobiledevice3")
        if spec is None:
            log.warning("pymobiledevice3 not installed — see install guide in ipad/")
            return None, None
    except Exception as e:
        log.warning("import pymobiledevice3 probe failed: %s", e)
        return None, None

    # 构造命令候选序列（pymobiledevice3 v10 起 battery 子命令必须加 single）
    # WiFi 优先级：
    #   1. --tunnel UDID           → tunneld 守护 + 内核隧道（sudo 启动时最快）超时用短时间快速 fallback
    # 优先级（2026-08-24 修复：--tunnel 依赖本机 tunneld，v10+ 需要 sudo 才能创建 TUN 设备，普通用户权限必崩）
    #   1. --userspace --udid UDID  → 纯 Python userspace 隧道，iPadOS 17+ WiFi 无需 sudo/tunneld
    #   2. --udid UDID              → usbmuxd 枚举（USB 或 iOS<17）
    #   3. --tunnel UDID           → 仅当用户手动 sudo 跑 tunneld 时才会成功，放最后兜底（短超时）
    #   4. 无 UDID 的 single 变体  → 自动选第一个设备
    #   (*) 不带 single 的老命令仅作为最后 fallback，且严格过滤 Usage 输出
    base = [py, "-m", "pymobiledevice3"]
    candidates = []
    def _add(args, t=None):
        candidates.append((base + args, t or timeout))
    if udid:
        _add(["diagnostics", "battery", "single", "--userspace", "--udid", str(udid)])
        _add(["diagnostics", "battery", "single", "--udid", str(udid)])
        _add(["diagnostics", "battery", "single", "--tunnel", str(udid)], t=4)   # 移到最后；4s 短超时快速跳过
    _add(["diagnostics", "battery", "single", "--userspace"])
    _add(["diagnostics", "battery", "single"])
    # 老版本 fallback（新版 v10+ 实际会走 Usage，但保留兼容极少数旧安装）
    if udid:
        _add(["diagnostics", "battery", "--udid", str(udid)])
    _add(["diagnostics", "battery"])

    out = ""; err = ""; r = None
    last_rc = None
    tries_digest = []
    for cmd, to in candidates:
        cmd_tag = " ".join(cmd[-4:])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=to)
        except subprocess.TimeoutExpired:
            tries_digest.append(f"[{cmd_tag}] timeout({to}s)")
            log.warning("pymobiledevice3 battery timeout (%ds): %s", to, cmd_tag)
            continue
        except Exception as e:
            tries_digest.append(f"[{cmd_tag}] exec_err:{e}")
            log.warning("battery exec failed: %s cmd=%s", e, cmd_tag)
            continue
        out = (r.stdout or "").strip(); err = (r.stderr or "").strip(); last_rc = r.returncode
        combined = (out + "\n" + err)
        # 严格过滤 Usage/帮助输出（新版 pymobiledevice3 老命令输出此类文本，不应视为有效命中）
        if "Usage:" in combined and ("COMMAND" in combined or "[OPTIONS]" in combined):
            tries_digest.append(f"[{cmd_tag}] rc={last_rc} usage_only(out={len(out)}B,err={len(err)}B)")
            log.info("skip usage-only output for: %s", cmd_tag)
            continue
        # 过滤明确的命令错误提示（例如 No such option: --udid）
        if "No such option" in err or "Error:" in err:
            tries_digest.append(f"[{cmd_tag}] rc={last_rc} err_msg:{(err or out)[:60].replace(chr(10),' ')}")
            log.info("skip error output: %s | cmd=%s", err[:80], cmd_tag)
            continue
        # 金标准命中：合并输出含电量 JSON 关键字（rc 可能非 0 也算）
        if "BatteryPercent" in combined or "CurrentCapacity" in combined:
            tries_digest.append(f"[{cmd_tag}] rc={last_rc} KEYWORD_HIT(out={len(out)}B,err={len(err)}B)")
            break
        # 运行时错误：pymobiledevice3 日志含 ERROR 级别（如 "Device is not connected"）→ 设备层失败，换下一个候选
        if " ERROR " in combined or "CRITICAL" in combined:
            tries_digest.append(f"[{cmd_tag}] rc={last_rc} dev_err:{combined[:60].replace(chr(10),' ')}")
            log.info("device-level error, try next: %s | cmd=%s", combined[:80].replace(chr(10), ' '), cmd_tag)
            continue
        # 兜底命中：rc=0 且 stdout 有实质内容且无错误标记（兼容老版本纯文本输出）
        if last_rc == 0 and out and "No such command" not in err:
            tries_digest.append(f"[{cmd_tag}] rc=0 HIT(out={len(out)}B)")
            break
        # 未命中：记录摘要
        tries_digest.append(f"[{cmd_tag}] rc={last_rc} miss(out={len(out)}B,err={len(err)}B)")
        log.info("candidate not hit: rc=%d out=%dB err=%dB cmd=%s", last_rc, len(out), len(err), cmd_tag)

    # 若最终解析失败，把所有候选的执行摘要打出来（方便排查 watcher 的偶发失败）
    def _finalize_parse_failure():
        if tries_digest:
            log.info("battery all candidates: %s", " | ".join(tries_digest))
        log.info("battery parse failed — stdout=%r stderr=%r", out[:200], err[:200])

    # 合并 stdout + stderr（pymobiledevice3 battery 会把 JSON 打到 stderr 或 stdout 任意一侧）
    combined = (out or "") + "\n" + (err or "")
    # 输出可能是 JSON，也可能是 key: value 文本
    level: Optional[int] = None
    charging: Optional[bool] = None

    # 先试 JSON
    try:
        # 截取第一块 {...}
        if "{" in combined and "}" in combined:
            s = combined.index("{")
            e = combined.rindex("}") + 1
            data = json.loads(combined[s:e])
            for k in ("BatteryPercent", "CurrentCapacity"):
                if k in data and data[k] is not None:
                    try:
                        level = int(float(data[k]))
                        break
                    except Exception:
                        pass
            for k in ("IsCharging", "Charging"):
                if k in data and data[k] is not None:
                    v = data[k]
                    if isinstance(v, bool):
                        charging = v
                    else:
                        charging = str(v).lower() in ("true", "yes", "1")
                    break
            if level is not None:
                return level, charging
    except (json.JSONDecodeError, ValueError, KeyError):
        pass

    # JSON 失败则逐行解析文本
    for line in combined.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, _, v = line.partition(":")
        k = k.strip().strip('"').strip("'")
        v = v.strip().strip('"').strip("'").rstrip(",")
        if level is None and k in ("BatteryPercent", "CurrentCapacity"):
            try:
                level = int(float(v))
            except Exception:
                pass
        if charging is None and k in ("IsCharging", "Charging"):
            charging = v.lower() in ("true", "yes", "1")

    if level is not None:
        level = max(0, min(100, int(level)))
        return level, charging

    # 还是没解析出来
    _finalize_parse_failure()
    return None, None


# ================= 命令行入口 =================
def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [ipad_battery] %(message)s",
    )
    args = sys.argv[1:]
    loop_s = None
    if "--loop" in args:
        i = args.index("--loop")
        try:
            loop_s = int(args[i + 1])
        except Exception:
            loop_s = 5

    # 直接从 YAML 读 ipad_charger 配置（包含 mock + UDID）
    raw = _load_yaml()
    ic = raw.get("ipad_charger", {}) or {}
    mock_cfg = None
    # 嵌套写法：mock.enabled_battery=true 或 mock.enabled=true → 启用模拟
    if isinstance(ic.get("mock"), dict):
        m = ic["mock"]
        if m.get("enabled_battery") or m.get("enabled"):
            mock_cfg = {
                "enabled": True,
                "level": m.get("level", 50),
                "charging": m.get("charging", False),
            }
    # 兼容平铺写法（旧配置格式）
    if mock_cfg is None and (ic.get("mock_enabled") or ic.get("mock_battery_enabled")):
        mock_cfg = {
            "enabled": True,
            "level": ic.get("mock_level", 50),
            "charging": ic.get("mock_charging", False),
        }
    udid = ic.get("udid") or None

    while True:
        t0 = time.time()
        lvl, chg = get_ipad_battery(udid=udid, mock_cfg=mock_cfg)
        print(json.dumps({
            "level": lvl,
            "charging": chg,
            "mock": bool(mock_cfg and mock_cfg.get("enabled")),
            "udid": udid,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }, ensure_ascii=False))
        if loop_s is None:
            return
        time.sleep(loop_s)


if __name__ == "__main__":
    main()
