#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""智能设备管理界面 — v2（玻璃液态 + 交通灯嵌入标题栏 + 手动开关）

运行：Finder 双击 /Users/liuhao/Applications/设备管理.app
或者：python3 /Users/liuhao/bin/device_manager.py
"""

import sys, os, time, subprocess, threading, logging
from pathlib import Path
from datetime import datetime

def _project_root():
    env = os.environ.get("DEVICE_MANAGER_HOME")
    if env: return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent
PROJECT_ROOT = _project_root()
BIN_DIR = PROJECT_ROOT / "bin"
CONFIG_PATH = PROJECT_ROOT / "config" / "plug_config.yaml"
RESOURCES_DIR = PROJECT_ROOT / "resources"
CFG_PATH = CONFIG_PATH               # 别名兼容原有引用
LOG_PATH = "/Users/liuhao/Library/Logs/miplug.log"
CACHE_DIR = Path.home() / ".cache" / "miplug"
WINDOW_STATE_PATH = CACHE_DIR / "window_state.yaml"
LAUNCH_LABEL = "local.miplug.lockwatcher"
MIPLUG   = str(BIN_DIR / "miplug.py")
AMBILIGHT_CTRL = str(BIN_DIR / "ambilight_control.py")
IPAD_PLUG = str(BIN_DIR / "ipad_plug.py")
ICON_PNG = str(RESOURCES_DIR / "dm_icon.png")

import math
import yaml
from PySide6.QtCore import Qt, QSize, QSizeF, QTimer, Signal, QRect, QRectF, QPoint, QPointF, Property, QEvent, QObject, QTime
from PySide6.QtGui import (
    QIcon, QColor, QPainter, QPainterPath, QBrush, QPen, QFont, QLinearGradient,
    QRadialGradient, QPixmap, QGuiApplication, QCursor, QFontMetrics, QPainter as _Q, QBrush as _B,
    QShortcut, QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QDoubleSpinBox, QTimeEdit,
    QFrame, QStackedWidget, QScrollArea, QMessageBox,
    QSizePolicy, QTextEdit, QCheckBox, QLineEdit,
    QToolButton, QSizeGrip, QDialog, QDialogButtonBox,
)

THEMES = {
    "dark": {
        "bg_1": "#0A0A0F",
        "bg_2": "#14141C",
        "sidebar": "#0D0D14",
        "card":   "rgba(22, 22, 32, 140)",
        "card_border": "rgba(255,255,255,10)",
        "titlebar": "rgba(18, 18, 28, 10)",
        "text":   "#E8E8F0",
        "text_dim": "#8A8AA0",
        "text_weak": "#58586A",
        "accent": "#7A82FF",
        "accent_2": "#C75CFF",
        "accent_soft": "rgba(107,115,255,32)",
        "success": "#3B82F6",
        "warn":    "#FBBF24",
        "danger":  "#F87171",
        "shadow":  "rgba(0,0,0,80)",
        "input_bg": "rgba(255,255,255,4)",
        "input_border": "rgba(255,255,255,8)",
        "sep": "rgba(255,255,255,4)",
        "cooler_c1": "#3B82F6", "cooler_c2": "#1D4ED8",
        "amb_c1":    "#F59E0B", "amb_c2":    "#B45309",
        "ipad_c1":   "#A855F7", "ipad_c2":   "#7E22CE",
    },
    "light": {
        "bg_1": "#F3F4F8",
        "bg_2": "#E9ECF4",
        "sidebar": "#EAEBF1",
        "card":   "rgba(255, 255, 255, 210)",
        "card_border": "rgba(0,0,0,8)",
        "titlebar": "rgba(250, 250, 252, 60)",
        "text":   "#1C1C22",
        "text_dim": "#4A4A58",
        "text_weak": "#88889A",
        "accent": "#5A64FF",
        "accent_2": "#B03DFF",
        "accent_soft": "rgba(90,100,255,22)",
        "success": "#2563EB",
        "warn":    "#D97706",
        "danger":  "#DC2626",
        "shadow":  "rgba(30,30,60,45)",
        "input_bg": "rgba(0,0,0,3)",
        "input_border": "rgba(0,0,0,12)",
        "sep": "rgba(0,0,0,8)",
        "cooler_c1": "#2563EB", "cooler_c2": "#1E40AF",
        "amb_c1":    "#D97706", "amb_c2":    "#92400E",
        "ipad_c1":   "#9333EA", "ipad_c2":   "#6B21A8",
    },
}

# ===================== iOS 26 Liquid Glass 圆角规范 =====================
# 调研结论（WWDC25 Liquid Glass / macOS Tahoe）：
#   · 窗口（带工具栏）：大圆角 ≈26px，且为"连续曲率"（continuous corner / squircle），
#     不是普通圆弧 —— 圆弧在 >16px 时曲率突变肉眼可见
#   · 大卡片（液态玻璃）：26~36px；按钮/小组件：胶囊（半径=高度一半）；
#     输入框/标签：12px；嵌套遵循同心原则（内圆角 ≈ 外圆角 − 内边距）
#   · 玻璃边缘光：边框亮度约 10~20%（比传统毛玻璃亮，模拟边缘折射）
def squircle_path(rect: QRectF, radius: float, n: float = 4.4) -> QPainterPath:
    """连续曲率圆角矩形（Apple continuous corner 的超椭圆近似）。

    圆角段走超椭圆 |x/r|^n + |y/r|^n = 1（n≈4.4），曲率从直边到圆角平滑过渡，
    肉眼上与 iOS 26 的贝塞尔肩+圆弧实现几乎一致，比方角 border-radius 更"液态"。
    """
    r = float(min(radius, rect.width() / 2.0, rect.height() / 2.0))
    if r <= 0.5:
        p = QPainterPath(); p.addRect(rect); return p
    l, t = rect.left(), rect.top()
    rt, b = rect.right(), rect.bottom()
    K = 16  # 每个角的弧线采样数（静态烘焙一次，不心疼）

    def corner(cx, cy, ex, ey):
        # 从 (cx+ex*r, cy) 沿超椭圆弧到 (cx, cy+ey*r)
        pts = []
        for i in range(K + 1):
            a = (math.pi / 2.0) * i / K
            dx = ex * r * (math.cos(a) ** (2.0 / n))
            dy = ey * r * (math.sin(a) ** (2.0 / n))
            pts.append(QPointF(cx + dx, cy + dy))
        return pts

    path = QPainterPath()
    tl = corner(l + r, t + r, -1, -1)          # 左边 → 顶边
    tr = corner(rt - r, t + r, 1, -1)[::-1]    # 顶边 → 右边
    br = corner(rt - r, b - r, 1, 1)           # 右边 → 底边
    bl = corner(l + r, b - r, -1, 1)[::-1]     # 底边 → 左边
    path.moveTo(tl[0])
    for pt in tl[1:] + tr + br + bl:
        path.lineTo(pt)
    path.closeSubpath()
    return path


# ===================== 背景：液态渐变 + 漂浮光斑（全静态，零 CPU） =====================
class GlassBackground(QWidget):
    """静态背景：渐变 + 4 个光斑一次性烘焙进 QPixmap，之后每次 paint 只做一次 blit。

    不再有任何 QTimer —— 界面静止时 CPU 占用为 0。
    iOS 26 样式：窗口本体为 26px 连续曲率圆角矩形，四周留 BLEED 出血位烘软阴影，
    窗口外（出血区）透明 → 配合主窗口 WA_TranslucentBackground 呈圆角浮窗。
    """
    BLEED = 16      # 阴影出血（窗口体与控件边缘之间的空隙）
    WIN_R = 26      # 窗口圆角（macOS Tahoe 带工具栏窗口规格）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._theme = "dark"
        self._cache_pix = None          # 静态背景（渐变+光斑）缓存（resize/theme 变才重建）
        self._cache_key = (-1, -1, "")  # (w, h, theme) 缓存键
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # 关键：不注册任何定时器。光斑位置固定（t=0.6），视觉上是"凝固的液态玻璃"，
        # 但运行期不再有任何周期性重绘。

    def use_theme(self, name):
        self._theme = name
        self._invalidate_cache()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._invalidate_cache()

    def _invalidate_cache(self):
        self._cache_pix = None

    def _build_static_cache(self, w, h):
        """渐变背景 + 光斑 + 窗口阴影一次性画进 QPixmap；paintEvent 只 blit。"""
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing)
        c = THEMES[self._theme]

        # --- iOS 26：窗口体（内缩 BLEED）+ 连续曲率圆角 + 烘焙软阴影 ---
        bl = self.BLEED
        body = QRectF(bl, bl, w - bl * 2, h - bl * 2)
        win_path = squircle_path(body, self.WIN_R)
        # (0) 烘焙阴影：多圈外扩 squircle，alpha 由内向外衰减（静态缓存，零运行期开销）
        steps = 14
        dark_win = (self._theme == "dark")
        base_a = 62 if dark_win else 46
        for i in range(steps, 0, -1):
            grow = 1 + i * (bl - 2) / steps
            a = int(base_a * ((steps - i + 1) / steps) ** 1.9)
            sh = squircle_path(body.adjusted(-grow, -grow, grow, grow), self.WIN_R + grow)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 10, 26, a) if dark_win else QColor(30, 34, 60, a))
            p.drawPath(sh)
        # (1) 渐变 + 光斑全部 clip 进圆角窗口内
        p.save()
        p.setClipPath(win_path, Qt.ReplaceClip)
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0.0, QColor(c["bg_1"]))
        g.setColorAt(1.0, QColor(c["bg_2"]))
        p.fillRect(0, 0, w, h, g)

        # 4 个光斑（固定位置 t=0.6，不再随时间漂移 → 零重绘）
        t = 0.6
        dark = (self._theme == "dark")
        max_side = max(w, h)
        orbs = (
            (0.18 + 0.09 * math.sin(t * 1.2), 0.20 + 0.07 * math.cos(t * 1.0),
             c["accent"],    0.22),
            (0.82 + 0.08 * math.cos(t * 0.9), 0.18 + 0.09 * math.sin(t * 0.7),
             c["accent_2"],  0.18),
            (0.48 + 0.10 * math.sin(t * 0.6), 0.92 + 0.06 * math.cos(t * 0.8),
             c["accent"],    0.14),
            (0.75 + 0.05 * math.cos(t * 1.4), 0.70 + 0.05 * math.sin(t * 1.1),
             c["cooler_c1"], 0.10),
        )
        alpha = 72 if dark else 45
        for cx, cy, col, sc in orbs:
            radius = int(max_side * sc)
            cx_i = int(cx * w); cy_i = int(cy * h)
            rg = QRadialGradient(cx_i, cy_i, radius)
            cc = QColor(col); cc.setAlpha(alpha)
            rg.setColorAt(0.0, cc)
            cc0 = QColor(col); cc0.setAlpha(0)
            rg.setColorAt(1.0, cc0)
            x = max(0, cx_i - radius); y = max(0, cy_i - radius)
            p.fillRect(x, y, radius * 2 + 2, radius * 2 + 2, rg)
        p.restore()
        # (2) 玻璃边缘光：窗口圆角描一圈极细高光（iOS 26 Liquid Glass 边缘折射感）
        edge = QPen(QColor(255, 255, 255, 26 if dark else 90))
        edge.setWidthF(1.0)
        p.setPen(edge); p.setBrush(Qt.NoBrush)
        p.drawPath(win_path)
        p.end()
        self._cache_pix = pix
        self._cache_key = (w, h, self._theme)

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        if self._cache_pix is None or self._cache_key != (w, h, self._theme):
            self._build_static_cache(w, h)
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache_pix)
        p.end()


# ===================== 玻璃卡片 =====================
class GlassCard(QFrame):
    """玻璃卡片：阴影 + 卡底 + 边框 + 顶部高光全部烘焙进一张缓存 QPixmap。

    不再使用 QGraphicsDropShadowEffect —— 那玩意每次子控件重绘（如输入框光标闪烁）
    都会触发整卡离屏渲染 + 高斯模糊，是空闲 CPU 的主要消耗源之一。
    现在运行期 paintEvent 只有一次 drawPixmap，恒定零重计算。
    """
    # 阴影出血量（上/左/右/下），子内容通过 contentsMargins 内缩
    BLEND_MAP = {True: (18, 14, 18, 30), False: (8, 6, 8, 14)}   # intense: (l,t,r,b)

    def __init__(self, parent=None, theme="dark", intense=False, borderless=False):
        super().__init__(parent)
        self._theme = theme
        self._intense = intense
        self._borderless = borderless
        self._cache_pix = None              # 整卡静态渲染缓存（w/h/theme 不变直接 blit）
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # 出血边距：给烘焙阴影留空间，同时把子控件挤进卡片实体区域
        bl, bt, br, bb = self.BLEND_MAP[bool(intense)]
        self.setContentsMargins(bl, bt, br, bb)

    def set_theme(self, theme):
        self._theme = theme
        self._cache_pix = None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._cache_pix = None   # 尺寸变化 → 缓存失效

    def _render_cache(self, w, h):
        """阴影 + 卡底 + 边框 + 高光 一次性渲染进 QPixmap。"""
        c = THEMES[self._theme]
        bl, bt, br, bb = self.BLEND_MAP[bool(self._intense)]
        card_r = QRectF(bl, bt, w - bl - br, h - bt - bb)   # 卡片实体区域
        # iOS 26：主容器 26px / 内层卡片 18px，均为连续曲率（squircle）
        radius = 26 if self._intense else 18

        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix); p.setRenderHint(QPainter.Antialiasing)

        # --- (1) 烘焙软阴影：多圈外扩圆角矩形，alpha 由内向外衰减（模拟高斯模糊）---
        steps = 12
        base_alpha = 46 if self._intense else 26
        for i in range(steps, 0, -1):
            grow = 1 + int(i * (bl - 1) / steps)
            a = int(base_alpha * ((steps - i + 1) / steps) ** 1.8)
            rect = card_r.adjusted(-grow, -grow, grow, grow + (1 if self._intense else 0))
            path = QPainterPath()
            path.addRoundedRect(rect, radius + grow, radius + grow)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, a))
            p.drawPath(path)

        # --- (2) 卡片主体填充（连续曲率圆角） ---
        card_path = squircle_path(card_r, radius)
        card_c = QColor(c["card"])
        if self._intense:
            card_c.setAlpha(200 if self._theme == "dark" else 235)
        p.setPen(Qt.NoPen); p.setBrush(card_c)
        p.drawPath(card_path)

        # --- (3) 边框 ---
        if not self._borderless:
            pen = QPen(QColor(c["card_border"]))
            pen.setWidthF(0.6)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            p.drawPath(card_path)

        # --- (4) 顶部高光渐变 ---
        max_h = max(120, int(card_r.height() * 0.35))
        grd = QLinearGradient(0, card_r.top(), 0, card_r.top() + max_h)
        top_c = QColor("#ffffff"); top_c.setAlpha(18 if self._theme == "dark" else 50)
        grd.setColorAt(0.0, top_c)
        grd.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(grd)
        p.drawPath(card_path)

        p.end()
        return pix

    def paintEvent(self, e):
        w, h = self.width(), self.height()
        if w <= 1 or h <= 1:
            return
        if self._cache_pix is None or self._cache_pix.size() != QSize(w, h):
            self._cache_pix = self._render_cache(w, h)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(0, 0, self._cache_pix)
        p.end()


# ===================== 渐变胶囊按钮 =====================
class GlassButton(QPushButton):
    def __init__(self, text="", theme="dark", kind="default", parent=None, grad=None, compact=False):
        super().__init__(text, parent)
        self._theme = theme; self._kind = kind; self._grad = grad
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(34 if compact else 40)
        self.setStyleSheet(self._css())

    def set_theme(self, theme):
        self._theme = theme; self.setStyleSheet(self._css())

    def _css(self):
        c = THEMES[self._theme]
        if self._kind == "primary":
            if self._grad:
                g1, g2 = self._grad
            else:
                g1, g2 = c["accent"], c["accent_2"]
            bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {g1}, stop:1 {g2})"
            tc, bd = "#ffffff", "transparent"
        elif self._kind == "on":
            g1, g2 = c["success"], "#1D4ED8"
            bg = f"qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {g1}, stop:1 {g2})"
            tc, bd = "#ffffff", "transparent"
        elif self._kind == "off":
            bg = c["input_bg"]
            tc, bd = c["text_dim"], c["input_border"]
        elif self._kind == "danger":
            bg = c["danger"]; tc, bd = "#ffffff", "transparent"
        elif self._kind == "ghost":
            bg = "transparent"; tc, bd = c["text"], "transparent"
        else:
            bg = c["input_bg"]; tc = c["text"]; bd = c["input_border"]
        pad_h = "18px" if self.height() >= 40 else "14px"
        # iOS 26：按钮胶囊化 —— 圆角 = 高度一半（40px→20px，34px→17px）
        r_px = 20 if self.minimumHeight() >= 40 else 17
        return f"""
        QPushButton {{
            background: {bg}; color: {tc}; border: 1px solid {bd};
            border-radius: {r_px}px; padding: 0 {pad_h};
            font-size: 12.5px; font-weight: 650; letter-spacing: 0.15px;
        }}
        QPushButton:hover {{ border: 1px solid {c['accent']}; }}
        QPushButton:pressed {{ padding-top: 1px; padding-left: 1px; }}
        QPushButton:disabled {{ opacity: 0.4; }}
        """


# ===================== 样式辅助 =====================
def make_label(text, theme, weak=False, weight=600, size=12):
    lb = QLabel(text)
    c = THEMES[theme]
    col = c["text_weak"] if weak else c["text_dim"]
    lb.setStyleSheet(f"color: {col}; font-size:{size}px; font-weight:{weight}; letter-spacing:0.2px;")
    return lb


_CB_CHECKED_IMG = None


def _checkbox_checked_img():
    """生成 QCheckBox 选中态的白色对勾 PNG（QSS image 属性需要文件路径，惰性生成一次）。"""
    global _CB_CHECKED_IMG
    if _CB_CHECKED_IMG is None:
        import os as _os
        d = _os.path.expanduser("~/.cache/miplug")
        p = _os.path.join(d, "cb_checked.png")
        try:
            if not _os.path.exists(p):
                _os.makedirs(d, exist_ok=True)
                pm = QPixmap(18, 18)
                pm.fill(Qt.transparent)
                pt = QPainter(pm)
                pt.setRenderHint(QPainter.Antialiasing)
                pen = QPen(QColor(255, 255, 255, 240))
                pen.setWidthF(2.4)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                pt.setPen(pen)
                pt.drawLine(QPointF(4.5, 9.5), QPointF(8.0, 13.0))
                pt.drawLine(QPointF(8.0, 13.0), QPointF(13.5, 5.0))
                pt.end()
                pm.save(p, "PNG")
            _CB_CHECKED_IMG = p
        except Exception:
            _CB_CHECKED_IMG = ""
    return _CB_CHECKED_IMG


def input_css(theme):
    c = THEMES[theme]
    cb_img = _checkbox_checked_img()
    cb_img_css = f"image: url({cb_img});" if cb_img else ""
    return f"""
    QDoubleSpinBox, QSpinBox, QTimeEdit, QLineEdit {{
        background: {c['input_bg']}; color: {c['text']};
        border: 0.7px solid {c['input_border']}; border-radius: 12px;
        padding: 6px 12px; font-size: 13px; min-height: 22px;
    }}
    QDoubleSpinBox:focus, QSpinBox:focus, QTimeEdit:focus, QLineEdit:focus {{
        border: 0.9px solid {c['accent']};
        background: rgba(122,130,255,22);
    }}
    QDoubleSpinBox::up-button, QSpinBox::up-button,
    QDoubleSpinBox::down-button, QSpinBox::down-button {{ width: 0; height: 0; }}
    QCheckBox {{ color: {c['text']}; font-size: 12.5px; padding: 4px 0; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 6px;
        border: 0.7px solid {c['input_border']}; background: {c['input_bg']};
    }}
    QCheckBox::indicator:hover {{
        border: 0.9px solid {c['accent']};
    }}
    QCheckBox::indicator:checked {{
        background: {c['accent']}; border: 0.9px solid {c['accent']};
        {cb_img_css}
    }}
    QCheckBox:checked {{ color: {c['text']}; font-weight: 650; }}
    QToolTip {{
        background: {c['bg_2']}; color: {c['text']};
        border: 1px solid {c['card_border']}; padding: 4px 8px; border-radius: 10px;
    }}
    """


# ===================== 渐变圆点图标 + 图标色 =====================
def device_colors(key, theme):
    c = THEMES[theme]
    return {
        "cooler": (c["cooler_c1"], c["cooler_c2"]),
        "ambilight": (c["amb_c1"], c["amb_c2"]),
        "ipad": (c["ipad_c1"], c["ipad_c2"]),
    }[key]


class IconDot(QLabel):
    """带渐变色圆形 + 图标字符的小图标"""
    def __init__(self, glyph, col1, col2, theme, size=46, parent=None):
        super().__init__(parent)
        self._g = glyph; self._c1 = col1; self._c2 = col2; self._theme = theme; self._s = size
        self.setFixedSize(size, size); self.setAlignment(Qt.AlignCenter)

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        s = self._s
        # 外发光
        rg = QRadialGradient(s//2, s//2, s//2)
        cc = QColor(self._c1); cc.setAlpha(40)
        rg.setColorAt(0.0, cc); rg.setColorAt(1.0, QColor(0,0,0,0))
        p.fillRect(0,0,s,s,rg)
        # 圆
        path = QPainterPath()
        path.addEllipse(3,3,s-6,s-6)
        g = QLinearGradient(0, 0, s, s)
        g.setColorAt(0.0, QColor(self._c1)); g.setColorAt(1.0, QColor(self._c2))
        p.fillPath(path, g)
        # 边框
        pen = QPen(QColor(255,255,255,30)); pen.setWidthF(0.7)
        p.setPen(pen); p.drawPath(path)
        # 字
        p.setPen(QPen(QColor(255,255,255,240)))
        f = QFont("SF Pro Display", int(s * 0.37))
        f.setWeight(QFont.Black)
        p.setFont(f)
        p.drawText(QRect(0, 0, s, s), Qt.AlignCenter, self._g)
        p.end()


# ===================== 手动开关大按钮 =====================
class SwitchBar(QWidget):
    """ON / OFF 两个大胶囊 + 当前状态回显 + 立即操作"""
    clicked_on = Signal(); clicked_off = Signal()

    def __init__(self, theme, parent=None, grad=("#7A82FF","#C75CFF"), label="水冷器开关"):
        super().__init__(parent)
        self._theme = theme
        self._grad = grad
        self._label = label
        lay = QHBoxLayout(self); lay.setContentsMargins(0,0,0,0); lay.setSpacing(10)
        # 说明
        col = QVBoxLayout(); col.setSpacing(1); col.setContentsMargins(4,0,0,0)
        tl = QLabel(label)
        tl.setStyleSheet(f"color:{THEMES[theme]['text']};font-size:13px;font-weight:700;")
        self.state = QLabel("状态：检查中…")
        self.state.setStyleSheet(f"color:{THEMES[theme]['text_dim']};font-size:11px;letter-spacing:0.2px;")
        col.addWidget(tl); col.addWidget(self.state)
        lay.addLayout(col, 1)
        # ON / OFF
        self.btn_off = GlassButton("关闭", theme, "off", compact=True)
        self.btn_off.setFixedWidth(84)
        self.btn_off.clicked.connect(self.clicked_off.emit)
        self.btn_on = GlassButton("开启", theme, "on", compact=True)
        self.btn_on.setFixedWidth(84)
        self.btn_on.clicked.connect(self.clicked_on.emit)
        lay.addWidget(self.btn_off); lay.addWidget(self.btn_on)

    def set_state(self, state):
        """state: 'on' | 'off' | None(未知)"""
        c = THEMES[self._theme]
        if state == "on":
            self.state.setText("状态：已开启")
            self.state.setStyleSheet(f"color:{c['success']};font-size:11px;font-weight:700;letter-spacing:0.2px;")
            self.btn_on.setStyleSheet(self.btn_on._css())
        elif state == "off":
            self.state.setText("状态：已关闭")
            self.state.setStyleSheet(f"color:{c['text_dim']};font-size:11px;letter-spacing:0.2px;")
        else:
            self.state.setText("状态：未知")
            self.state.setStyleSheet(f"color:{c['text_weak']};font-size:11px;letter-spacing:0.2px;")

    def set_theme(self, theme):
        self._theme = theme
        self.btn_on.set_theme(theme); self.btn_off.set_theme(theme)
        self.findChild(QLabel, "", Qt.FindDirectChildrenOnly)
        c = THEMES[theme]
        self.state.setStyleSheet(f"color:{c['text_dim']};font-size:11px;letter-spacing:0.2px;")


# ===================== 通用面板 =====================
class DevicePanel(QScrollArea):
    def __init__(self, theme="dark", parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.viewport().setStyleSheet("background: transparent;")
        self.viewport().setAutoFillBackground(False)
        self.setAutoFillBackground(False)
        # Panel 永远不开横向滚动条（文字太长 = 用换行/略写），保持“潮玻璃”无横条视觉。
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # --- 滚动性能优化：macOS 触摸板关键 ---
        # 1. 每像素滚动（不按行）→ 触摸板丝滑
        try:
            from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea
            self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
            # 2. 智能 viewport 更新：只滚出/滚入的矩形重绘，不全屏（Qt 默认 Full 超卡）
            self.setViewportUpdateMode(QAbstractScrollArea.SmartViewportUpdate)
        except Exception:
            pass
        # 3. 滚动条单步步长改大，避免滚轮/触摸板轻扫只动一像素
        vsb = self.verticalScrollBar(); vsb.setSingleStep(14); vsb.setPageStep(140)
        hsb = self.horizontalScrollBar(); hsb.setSingleStep(14); hsb.setPageStep(220)
        # 4. viewport 不开静态内容 — 配合 SmartViewportUpdate 让 Qt 走脏矩形
        self.viewport().setAttribute(Qt.WA_StaticContents, False)
        self.viewport().setAttribute(Qt.WA_NoSystemBackground, True)
        self._theme = theme
        self.container = QWidget(); self.container.setStyleSheet("background: transparent;")
        self.container.setAutoFillBackground(False)
        # 保证 container 横向/纵向都跟随 viewport：widgetResizable 还不够，要显式允许 Expand
        self.container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._vl = QVBoxLayout(self.container)
        self._vl.setContentsMargins(26, 24, 26, 24)
        self._vl.setSpacing(14)
        self.setWidget(self.container)

    def add_section(self, title, desc=None, top_spacing=4):
        wrap = QWidget(); wl = QVBoxLayout(wrap); wl.setContentsMargins(0, top_spacing, 0, 0)
        box = QVBoxLayout(); box.setSpacing(3); box.setContentsMargins(4, 0, 4, 0)
        tl = QLabel(title)
        tl.setWordWrap(True)      # 标题太长自动换行（避免被 ScrollBarAlwaysOff 裁切）
        tl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        c = THEMES[self._theme]
        tl.setStyleSheet(f"color:{c['text']};font-size:14px;font-weight:750;letter-spacing:0.15px;")
        box.addWidget(tl)
        if desc:
            dl = QLabel(desc)
            dl.setWordWrap(True)
            dl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            dl.setStyleSheet(f"color:{c['text_dim']};font-size:11.5px;")
            box.addWidget(dl)
        wl.addLayout(box)
        self._vl.addWidget(wrap)

    def add_card(self, intense=False):
        card = GlassCard(theme=self._theme, intense=intense, borderless=False)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)
        self._vl.addWidget(card)
        return card, lay

    def add_row(self, layout, label, widget, label_w=118):
        row = QHBoxLayout(); row.setSpacing(14); row.setContentsMargins(0,0,0,0)
        lb = make_label(label, self._theme, size=11.5)
        lb.setMinimumWidth(label_w); lb.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        row.addWidget(lb)
        row.addWidget(widget, 1)
        layout.addLayout(row)

    def add_switchbar(self, grad, label, on_cb, off_cb):
        card, lay = self.add_card(intense=False)
        sw = SwitchBar(self._theme, grad=grad, label=label)
        sw.clicked_on.connect(on_cb); sw.clicked_off.connect(off_cb)
        lay.addWidget(sw)
        return sw

    def set_theme(self, theme):
        self._theme = theme
        for cb in self.findChildren(GlassCard): cb.set_theme(theme)
        for btn in self.findChildren(GlassButton): btn.set_theme(theme)
        # PySide6 findChildren 不接受 tuple，逐个类型遍历合并
        input_types = (QDoubleSpinBox, QSpinBox, QTimeEdit, QLineEdit, QCheckBox)
        for T in input_types:
            for inp in self.findChildren(T):
                inp.setStyleSheet(input_css(theme))
        for sw in self.findChildren(SwitchBar): sw.set_theme(theme)
        # 新增：RangeSlider 主题切换（不传 grad 则保留原有 grad，不回退到紫色）
        for rs in self.findChildren(RangeSlider): rs.set_theme(theme)
        # RangeSliderWithRow 由 CoolerPanel/iPadPanel 的子类 set_theme 传对应 grad 覆盖；
        # 这里 findChildren 全扫时如果子类还没覆写，就**不传 grad**，保留构造时传入的 grad
        for rr in self.findChildren(RangeSliderWithRow): rr.set_theme(theme, grad=None)
        # 更新文本标签
        t = THEMES.get(theme, THEMES["dark"])
        for lb in self.findChildren(QLabel):
            ss = lb.styleSheet()
            if not ss: continue
            for k_old, k_new in zip(["text","text_dim","text_weak"],["text","text_dim","text_weak"]):
                if THEMES["dark"][k_old] in ss:
                    ss = ss.replace(THEMES["dark"][k_old], t[k_new])
            lb.setStyleSheet(ss)


# ---------- 水冷器 ----------
class CoolerPanel(DevicePanel):
    def __init__(self, theme, on_save=None, parent=None):
        super().__init__(theme); self._save_cb = on_save; self._build()

    def _build(self):
        c1, c2 = device_colors("cooler", self._theme)
        self.sw = self.add_switchbar((c1, c2), "水冷器开关", self._do_on, self._do_off)

        # 启用规则总开关
        card0, lay0 = self.add_card(intense=False)
        self.enabled = QCheckBox("启用规则：按以下温度参数运行 watcher 自动控制（不勾选则 watcher 不操作水冷器插座）")
        self.enabled.setStyleSheet(input_css(self._theme))
        wrap = QHBoxLayout(); wrap.addWidget(self.enabled); wrap.addStretch(1)
        lay0.addLayout(wrap)

        self.add_section("温度控制参数", "常态慢速轮询，跨越阈值时进入快速确认阶段")
        card, lay = self.add_card(intense=False)
        # 温度改为 QSpinBox（无小数，suffix 用户无法编辑），范围 1-120°C
        self.on_thr = QSpinBox(); self.on_thr.setRange(1,120); self.on_thr.setSuffix(" °C")
        self.off_thr = QSpinBox(); self.off_thr.setRange(1,120); self.off_thr.setSuffix(" °C")
        self.poll = QSpinBox(); self.poll.setRange(5,600); self.poll.setSuffix(" 秒")
        self.fast = QSpinBox(); self.fast.setRange(1,120); self.fast.setSuffix(" 秒")
        self.hits = QSpinBox(); self.hits.setRange(1,20); self.hits.setSuffix(" 次")
        self.fail_open = QSpinBox(); self.fail_open.setRange(1,30); self.fail_open.setSuffix(" 次")
        for w in [self.on_thr, self.off_thr, self.poll, self.fast, self.hits, self.fail_open]:
            w.setStyleSheet(input_css(self._theme))
        # RangeSlider 区间拖动（左=关闭温度 小值，右=开启温度 大值 —— swap=True）
        self.thr_range = RangeSliderWithRow(
            self._theme, "温度阈值区间",
            vmin=1, vmax=120,
            spin_lo=self.on_thr, spin_hi=self.off_thr, unit="°C",
            lo_label="开启温度", hi_label="关闭温度",
            hint_lo="CPU ≥ <v>°C 时开启水冷",
            hint_hi="温降回 <v>°C 以下关闭水冷",
            swap=True,
            grad_key="cooler",
            ticks=[(10, "10"), (30, "30"), (50, "50"), (70, "70"), (90, "90"), (110, "110")],
        )
        lay.addWidget(self.thr_range)
        self.add_row(lay, "常态轮询间隔", self.poll)
        self.add_row(lay, "快速确认间隔", self.fast)
        self.add_row(lay, "连续确认次数", self.hits)
        self.add_row(lay, "安全回退失败阈值", self.fail_open)

        # 保存
        card2, lay2 = self.add_card(intense=False)
        self.hint = QLabel("改完参数后请点右上角「保存并生效」，会立即写入 plug_config.yaml 并 kickstart 守护进程使其生效。")
        self.hint.setWordWrap(True)
        c = THEMES[self._theme]
        self.hint.setStyleSheet(f"color:{c['text_dim']};font-size:11.5px;line-height:1.55;")
        lay2.addWidget(self.hint)
        btns = QHBoxLayout(); btns.addStretch(1)
        self.btn_revert = GlassButton("恢复默认", self._theme, "default", compact=True)
        self.btn_revert.clicked.connect(self.load_defaults)
        btns.addWidget(self.btn_revert)
        lay2.addLayout(btns)
        self._vl.addStretch(1)

    def load_config(self, d):
        # enabled: 配置里缺省时默认 True（保持老版本用户体验）
        self.enabled.setChecked(bool(d.get("enabled", True)))
        # 温度阈值统一转为 int 并裁剪到合法范围（老配置是 float 也兼容）
        self.on_thr.setValue(max(1, min(120, int(float(d.get("on_threshold",60))))))
        self.off_thr.setValue(max(1, min(120, int(float(d.get("off_threshold",33))))))
        self.poll.setValue(int(d.get("poll_interval",30)))
        self.fast.setValue(int(d.get("fast_interval",5)))
        self.hits.setValue(int(d.get("confirm_hits",3)))
        self.fail_open.setValue(int(d.get("fail_open_after",3)))

    def load_defaults(self):
        self.load_config({})

    def dump_config(self):
        on_v = int(self.on_thr.value())
        off_v = int(self.off_thr.value())
        if on_v <= off_v:
            raise ValueError(f"开启温度 ({on_v}°C) 必须大于关闭温度 ({off_v}°C)（否则滞环失效）")
        return {
            "enabled": self.enabled.isChecked(),
            "on_threshold": on_v,
            "off_threshold": off_v,
            "poll_interval": int(self.poll.value()),
            "fast_interval": int(self.fast.value()),
            "confirm_hits": int(self.hits.value()),
            "fail_open_after": int(self.fail_open.value()),
        }

    def set_theme(self, theme):
        super().set_theme(theme)
        # 把水冷的蓝色渐变灌到区间滑块
        if hasattr(self, "thr_range"):
            self.thr_range.set_theme(theme, grad=device_colors("cooler", theme))

    def save(self):
        if self._save_cb: self._save_cb("cooler")

    def _do_on(self):
        if self.parent(): mw = self.window()
        else: mw = None
        if hasattr(mw, "manual_control"): mw.manual_control("cooler", "on")
        else: subprocess.Popen([MIPLUG, "on"])

    def _do_off(self):
        if self.parent(): mw = self.window()
        else: mw = None
        if hasattr(mw, "manual_control"): mw.manual_control("cooler", "off")
        else: subprocess.Popen([MIPLUG, "off"])


# ---------- 氛围灯 ----------
class AmbilightPanel(DevicePanel):
    def __init__(self, theme, on_save=None, parent=None):
        super().__init__(theme); self._save_cb = on_save; self._build()

    def _build(self):
        c1, c2 = device_colors("ambilight", self._theme)
        self.sw = self.add_switchbar((c1, c2), "氛围灯开关", self._do_on, self._do_off)

        # 启用规则总开关
        card0, lay0 = self.add_card(intense=False)
        self.enabled = QCheckBox("启用规则：按以下时间窗口+锁屏倒计时运行 watcher 自动控制（不勾选则 watcher 不操作氛围灯插座）")
        self.enabled.setStyleSheet(input_css(self._theme))
        wrap = QHBoxLayout(); wrap.addWidget(self.enabled); wrap.addStretch(1)
        lay0.addLayout(wrap)

        self.add_section("时间窗口 + 锁屏倒计时",
            "选一段“关灯窗口”：窗口内关氛围灯并开始锁屏倒计时；窗口外自动开灯。点右上角 Chip 或任意时间数字框会弹出「滚轮滑动选择时间」窗口，不用手敲。开始晚于结束表示跨午夜，例如 20:30 → 07:30 = 当晚到次日早上。")
        card, lay = self.add_card()
        # 注意：对外仍然保存“小时整数 (time_start, time_end)”，因为 ambilight watcher 用的是
        # h = datetime.now().hour，只比整点小时；分钟在 UI 支持调但 dump 时对齐到最近整点，
        # 保持 watcher/ambilight_control.py 零改动。
        self.time_start = QTimeEdit()   # 保留变量名给 dump_config / load_config 语义
        self.time_end   = QTimeEdit()
        self.lock_delay = QSpinBox(); self.lock_delay.setRange(0,500); self.lock_delay.setSuffix(" 秒")
        self.lock_delay.setStyleSheet(input_css(self._theme))
        self.time_range = TimeRangeWithRow(
            self._theme, "关灯窗口",
            start_time_edit=self.time_start, end_time_edit=self.time_end,
            start_label="进入关灯窗口", end_label="自动开灯",
            hint_start="≥ <v> 开始关灯（点 Chip / 时间框弹滑动选择器）",
            hint_end="< <v> 仍关灯，≥ <v> 自动开灯（解锁瞬亮）",
            grad_key="ambilight",
        )
        # 让两个 Chip + QTimeEdit 聚焦都弹 TimePickerDialog（滑动选择，不要手敲）
        self.time_range.chip_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.time_range.chip_end.setCursor(Qt.CursorShape.PointingHandCursor)
        self._install_click(self.time_range.chip_start, self.time_range._open_start_picker)
        self._install_click(self.time_range.chip_end,   self.time_range._open_end_picker)
        self._install_focus_in(self.time_start, self.time_range._open_start_picker)
        self._install_focus_in(self.time_end,   self.time_range._open_end_picker)
        lay.addWidget(self.time_range)

        # 锁屏倒计时滑块（0~500s，和 iPad/Cooler 行同风格液态玻璃）
        self.lock_delay_slider = SingleSliderWithRow(
            self._theme, "锁屏关灯倒计时",
            spin=self.lock_delay, unit=" 秒",
            vmin=0, vmax=500,
            label_thumb="倒计时",
            hint="锁屏后 <v> 秒还没解锁，自动关氛围灯（0 秒 = 立刻关，最大 500 秒）",
            grad_key="ambilight",
            ticks=[(0,"0"),(60,"60"),(120,"120"),(240,"240"),(360,"360"),(500,"500")],
        )
        lay.addWidget(self.lock_delay_slider)

        card2, lay2 = self.add_card()
        c = THEMES[self._theme]
        tip = QLabel("改完参数后请点右上角「保存并生效」，会立即写入 plug_config.yaml 并 kickstart 守护进程使其生效。注：保存时时间会自动对齐到最近整点，以匹配 watcher 的整点判定逻辑（对齐结果已在时间行下方用灰字提示）。")
        tip.setWordWrap(True); tip.setStyleSheet(f"color:{c['text_dim']};font-size:11.5px;line-height:1.55;")
        lay2.addWidget(tip)
        btns = QHBoxLayout(); btns.addStretch(1)
        self.btn_revert = GlassButton("恢复默认", self._theme, "default", compact=True)
        self.btn_revert.clicked.connect(self.load_defaults)
        btns.addWidget(self.btn_revert)
        lay2.addLayout(btns)
        self._vl.addStretch(1)

    @staticmethod
    def _install_click(widget, cb):
        """把任意 QWidget 的鼠标点击（Press + 没有移动很远）转为调用 cb()，用于 Chip 触发打开时间选择弹窗。"""
        class _F(QObject):
            def eventFilter(me, obj, ev):
                if ev.type() == QEvent.Type.MouseButtonPress and ev.button() == Qt.MouseButton.LeftButton:
                    me._press_pos = ev.position()
                elif ev.type() == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                    if hasattr(me, "_press_pos"):
                        delta = (ev.position() - me._press_pos).manhattanLength()
                        if delta < 6.0:
                            QTimer.singleShot(0, cb)
                return False
        ef = _F(widget); widget.installEventFilter(ef); widget._click_filter = ef  # 保留引用避免 GC

    @staticmethod
    def _install_focus_in(te: QTimeEdit, cb):
        """QTimeEdit 获得焦点（点击/键盘Tab）也弹滑动选择器；选完立刻清理焦点避免再弹。"""
        class _F(QObject):
            def eventFilter(me, obj, ev):
                if ev.type() == QEvent.Type.FocusIn and ev.reason() in (
                        Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason,
                        Qt.FocusReason.ShortcutFocusReason, Qt.FocusReason.PopupFocusReason):
                    QTimer.singleShot(0, cb)
                    # 选完会写时间，把焦点让给外层避免鼠标点同一个控件再次立即弹
                    QTimer.singleShot(0, lambda: te.clearFocus())
                return False
        ef = _F(te); te.installEventFilter(ef); te._focus_filter = ef

    def load_config(self, d):
        self.enabled.setChecked(bool(d.get("enabled", True)))
        # 兼容旧整数小时 与 字符串 HH:mm 两种配置
        def _to_qt(v, default_h):
            if isinstance(v, (int, float)):
                return QTime(int(v) % 24, 0)
            if isinstance(v, str):
                if ":" in v:
                    hh, mm = v.split(":", 1)
                    try: return QTime(int(hh) % 24, int(mm) % 60)
                    except: return QTime(default_h, 0)
                else:
                    try: return QTime(int(v) % 24, 0)
                    except: return QTime(default_h, 0)
            return QTime(default_h, 0)
        self.time_start.setTime(_to_qt(d.get("time_start"), 20))
        self.time_end  .setTime(_to_qt(d.get("time_end"),    8))
        # 旧配置可能超过新的上限 500：按 UI 规则夹到 500（安全退化：倒计时不会更长到离谱 3600）
        raw_delay = int(d.get("lock_delay", 120))
        self.lock_delay.setValue(max(0, min(500, raw_delay)))

    def load_defaults(self):
        self.load_config({})

    def dump_config(self):
        s_h = self.time_range.start_hour()
        e_h = self.time_range.end_hour()
        # 不允许 start==end 等价于“24 小时一直关灯 / 一直开灯”的歧义
        if s_h == e_h:
            raise ValueError(
                "关灯窗口的开始与结束不能是同一整点，请调整为不同时间（跨午夜请让开始>结束，如 20:00→08:00）"
            )
        return {"enabled": self.enabled.isChecked(),
                "time_start": int(s_h),
                "time_end": int(e_h),
                "lock_delay": int(self.lock_delay.value())}

    def set_theme(self, theme):
        super().set_theme(theme)
        self.enabled.setStyleSheet(input_css(theme))
        self.lock_delay.setStyleSheet(input_css(theme))
        # tip / revert button
        self.btn_revert.set_theme(theme)
        if hasattr(self, "time_range"):
            self.time_range.set_theme(theme, grad=device_colors("ambilight", theme))
        if hasattr(self, "lock_delay_slider"):
            self.lock_delay_slider.set_theme(theme, grad=device_colors("ambilight", theme))

    def save(self):
        if self._save_cb: self._save_cb("ambil")

    def _do_on(self):
        mw = self.window()
        if hasattr(mw, "manual_control"): mw.manual_control("ambilight", "on")
        else: subprocess.Popen([AMBILIGHT_CTRL, "on", "--force"])

    def _do_off(self):
        mw = self.window()
        if hasattr(mw, "manual_control"): mw.manual_control("ambilight", "off")
        else: subprocess.Popen([AMBILIGHT_CTRL, "off", "--force"])


# ---------- iPad 充电 ----------
class iPadPanel(DevicePanel):
    battery_result = Signal(object, object, bool, str)  # level, charging, is_mock, detail_msg

    def __init__(self, theme, on_save=None, parent=None):
        super().__init__(theme); self._save_cb = on_save
        self.battery_result.connect(self._on_battery_result)
        self._build()

    def _build(self):
        c1, c2 = device_colors("ipad", self._theme)
        self.sw = self.add_switchbar((c1, c2), "iPad 充电开关", self._do_on, self._do_off)

        self.add_section("iPad Pro 自动充电管理",
            "低于阈值开启插座充电，高于阈值关闭插座停止；亮屏时走 pymobiledevice3 无线轮询，锁屏时依赖 iPad 快捷指令自动化上报（HTTP 上报端口）。")
        card, lay = self.add_card()
        # 1) enabled 简化为单行文本，不再显示括号里长说明
        self.enabled = QCheckBox("启用规则：按以下充电阈值参数运行自动控制")
        self.enabled.setStyleSheet(input_css(self._theme))
        # 2) Mac休眠也改为单行文本（用户指定文案：逗号连接，不换行）
        self.sleep_shutdown = QCheckBox("Mac休眠时立刻关闭插座，避免Mac睡了iPad冲通宵")
        self.sleep_shutdown.setStyleSheet(input_css(self._theme))
        self.plug_ip = QLineEdit(); self.plug_ip.setPlaceholderText("智能插座 IP，如 192.168.31.xx（留空自动进入 MOCK 模式）")
        self.plug_token = QLineEdit(); self.plug_token.setPlaceholderText("miIO TOKEN 32 位 HEX（留空自动进入 MOCK 模式）")
        self.low = QSpinBox(); self.low.setRange(1,99); self.low.setSuffix(" %")
        self.high = QSpinBox(); self.high.setRange(50,100); self.high.setSuffix(" %")
        self.interval = QSpinBox(); self.interval.setRange(30,3600); self.interval.setSuffix(" 秒")
        self.fail_close = QSpinBox(); self.fail_close.setRange(1,200); self.fail_close.setSuffix(" 次")
        self.report_port = QSpinBox(); self.report_port.setRange(0,65535); self.report_port.setSpecialValueText("禁用")
        for w in [self.plug_ip, self.plug_token]: w.setStyleSheet(input_css(self._theme))
        for w in [self.low, self.high, self.interval, self.fail_close, self.report_port]:
            w.setStyleSheet(input_css(self._theme))
        # 启用规则 + Mac休眠 上下两格布局（各自单行文本）
        vbox = QVBoxLayout(); vbox.setSpacing(8); vbox.setContentsMargins(0, 2, 0, 6)
        vbox.addWidget(self.enabled)
        vbox.addWidget(self.sleep_shutdown)
        lay.addLayout(vbox)
        self.add_row(lay, "插座 IP", self.plug_ip)
        self.add_row(lay, "插座 TOKEN", self.plug_token)
        # RangeSlider 区间拖动（左=开始充电 低电量，右=停止充电 高电量 —— swap=False）
        self.bat_range = RangeSliderWithRow(
            self._theme, "电量阈值区间",
            vmin=1, vmax=100,
            spin_lo=self.low, spin_hi=self.high, unit="%",
            lo_label="开始充电", hi_label="停止充电",
            hint_lo="电量低于 <v>% 自动开启充电",
            hint_hi="充到 <v>% 自动停止，避免过充",
            swap=False,
            grad_key="ipad",
            ticks=[(10, "10"), (30, "30"), (50, "50"), (70, "70"), (90, "90"), (100, "100")],
        )
        lay.addWidget(self.bat_range)
        self.add_row(lay, "电量检查间隔", self.interval)
        self.add_row(lay, "充电中连续读失败关插座", self.fail_close)
        self.add_row(lay, "HTTP 上报端口", self.report_port)

        # ---------- 电量读取诊断卡片 ----------
        self.add_section("电量读取诊断",
            "手动读取一次 iPad 电量，验证 pymobiledevice3 无线链路或 Mock 配置是否正常。")
        bcard, blay = self.add_card()
        c = THEMES[self._theme]
        brow = QHBoxLayout(); brow.setSpacing(14)
        self.btn_read_battery = GlassButton("读取电量", self._theme, "primary", grad=(c1, c2), compact=True)
        self.btn_read_battery.setMinimumWidth(118)
        self.btn_read_battery.clicked.connect(self._on_read_battery)
        brow.addWidget(self.btn_read_battery)
        self.lbl_battery_big = QLabel("-- %")
        self.lbl_battery_big.setStyleSheet(f"color:{c['text']};font-size:30px;font-weight:800;letter-spacing:0.5px;")
        self.lbl_battery_big.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        brow.addWidget(self.lbl_battery_big, 1)
        self.lbl_charge_status = QLabel("--")
        self.lbl_charge_status.setStyleSheet(f"color:{c['text_dim']};font-size:12px;font-weight:600;")
        self.lbl_charge_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        brow.addWidget(self.lbl_charge_status)
        blay.addLayout(brow)
        self.lbl_battery_detail = QLabel("点击上方「读取电量」按钮开始验证")
        self.lbl_battery_detail.setWordWrap(True)
        self.lbl_battery_detail.setStyleSheet(f"color:{c['text_weak']};font-size:11px;line-height:1.55;")
        blay.addWidget(self.lbl_battery_detail)

        # ---------- 自动控制动态卡片（watcher 事件流实时刷新） ----------
        self.add_section("自动控制动态",
            "实时展示 watcher 收到的每次 iPad 上报 / 轮询结果与插座决策（每 2 秒自动刷新，也可手动刷新）。")
        ecard, elay = self.add_card()
        srow = QHBoxLayout(); srow.setSpacing(10)
        self.lbl_events_summary = QLabel("暂无控制记录")
        self.lbl_events_summary.setStyleSheet(f"color:{c['text']};font-size:13px;font-weight:700;")
        srow.addWidget(self.lbl_events_summary, 1)
        self.btn_events_refresh = GlassButton("刷新", self._theme, "default", compact=True)
        self.btn_events_refresh.clicked.connect(self._force_refresh_events)
        srow.addWidget(self.btn_events_refresh)
        elay.addLayout(srow)
        # 事件行卡片容器（每条消息一张小卡片，最多 6 条）
        self._events_rows_host = QWidget()
        self._events_rows_lay = QVBoxLayout(self._events_rows_host)
        self._events_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._events_rows_lay.setSpacing(7)
        self.lbl_events_empty = QLabel("等待 iPad 上报或 Mac 轮询触发…")
        self.lbl_events_empty.setWordWrap(True)
        self.lbl_events_empty.setStyleSheet(f"color:{c['text_weak']};font-size:11.5px;")
        self._events_rows_lay.addWidget(self.lbl_events_empty)
        elay.addWidget(self._events_rows_host)
        self._events_mtime = 0
        self._events_timer = QTimer(self)
        self._events_timer.setInterval(2000)
        self._events_timer.timeout.connect(self._refresh_ipad_events)
        self._events_timer.start()
        self._refresh_ipad_events()

        # ---------- 保存卡片 ----------
        card2, lay2 = self.add_card()
        tip = QLabel("改完参数后请点右上角「保存并生效」，会立即写入 plug_config.yaml 并 kickstart 守护进程；ipad_charger.enabled=true 时立即开始按阈值轮询；插座/UDID 为空时进入 MOCK 模式（仅日志+缓存，便于验证链路）。")
        tip.setWordWrap(True); tip.setStyleSheet(f"color:{c['text_dim']};font-size:11.5px;line-height:1.55;")
        lay2.addWidget(tip)
        btns = QHBoxLayout(); btns.addStretch(1)
        self.btn_revert = GlassButton("恢复默认", self._theme, "default", compact=True)
        self.btn_revert.clicked.connect(self.load_defaults)
        btns.addWidget(self.btn_revert)
        lay2.addLayout(btns)
        self._vl.addStretch(1)

    def load_config(self, d):
        self.enabled.setChecked(bool(d.get("enabled",True)))
        self.sleep_shutdown.setChecked(bool(d.get("sleep_shutdown",True)))
        self.plug_ip.setText(str(d.get("plug_ip","")))
        self.plug_token.setText(str(d.get("plug_token","")))
        self.low.setValue(max(1, min(99, int(d.get("low_battery_threshold",20)))))
        self.high.setValue(max(50, min(100, int(d.get("high_battery_threshold",95)))))
        self.interval.setValue(max(30, min(3600, int(d.get("check_interval",180)))))
        self.fail_close.setValue(max(1, min(200, int(d.get("fail_close_after",40)))))
        self.report_port.setValue(max(0, min(65535, int(d.get("report_port",8737)))))

    def load_defaults(self):
        self.load_config({})

    def dump_config(self):
        lo = int(self.low.value()); hi = int(self.high.value())
        if lo >= hi:
            raise ValueError(f"开始充电电量 ({lo}%) 必须小于停止充电电量 ({hi}%)")
        return {
            "enabled": self.enabled.isChecked(),
            "sleep_shutdown": self.sleep_shutdown.isChecked(),
            "plug_ip": self.plug_ip.text().strip(),
            "plug_token": self.plug_token.text().strip(),
            "low_battery_threshold": lo,
            "high_battery_threshold": hi,
            "check_interval": int(self.interval.value()),
            "fail_close_after": int(self.fail_close.value()),
            "report_port": int(self.report_port.value()),
        }

    def set_theme(self, theme):
        super().set_theme(theme)
        if hasattr(self, "bat_range"):
            self.bat_range.set_theme(theme, grad=device_colors("ipad", theme))

    def save(self):
        if self._save_cb: self._save_cb("ipad")

    def _do_on(self):
        mw = self.window()
        if hasattr(mw, "manual_control"): mw.manual_control("ipad", "on")

    def _do_off(self):
        mw = self.window()
        if hasattr(mw, "manual_control"): mw.manual_control("ipad", "off")

    # ===================== 电量读取诊断 =====================
    def _load_battery_module(self):
        """以 importlib 动态导入 ipad_battery 模块（与 watcher 一致方式），避免启动时硬依赖"""
        try:
            import importlib.util, sys as _s
            if str(BIN_DIR) not in _s.path:
                _s.path.insert(0, str(BIN_DIR))
            spec = importlib.util.spec_from_file_location(
                "ipad_battery_mod", str(BIN_DIR / "ipad_battery.py")
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod, None
        except Exception as e:
            return None, f"导入 ipad_battery 模块失败: {e}"

    def _read_battery_cfg(self):
        """从 plug_config.yaml 读取 ipad_charger 段，提取 UDID 和 mock 配置"""
        try:
            import yaml as _y
            with open(CONFIG_PATH) as f:
                raw = _y.safe_load(f) or {}
            ic = raw.get("ipad_charger", {}) or {}
            udid = ic.get("udid") or None
            mock_cfg = None
            if isinstance(ic.get("mock"), dict):
                m = ic["mock"]
                if m.get("enabled_battery") or m.get("enabled"):
                    mock_cfg = {
                        "enabled": True,
                        "level": m.get("level", 50),
                        "charging": m.get("charging", False),
                    }
            if mock_cfg is None and (ic.get("mock_enabled") or ic.get("mock_battery_enabled")):
                mock_cfg = {
                    "enabled": True,
                    "level": ic.get("mock_level", 50),
                    "charging": ic.get("mock_charging", False),
                }
            return udid, mock_cfg, None
        except Exception as e:
            return None, None, f"读 plug_config.yaml 失败: {e}"

    def _on_read_battery(self):
        self.btn_read_battery.setEnabled(False)
        self.btn_read_battery.setText("读取中...")
        self.lbl_battery_detail.setText("正在读取 iPad 电量（真实模式约 2-5 秒，Mock 模式立等）...")
        threading.Thread(target=self._worker_read_battery, daemon=True).start()

    def _worker_read_battery(self):
        import time as _t, traceback
        t0 = _t.time()
        try:
            # 1. 导入模块
            mod, err = self._load_battery_module()
            if err:
                self.battery_result.emit(None, None, False, err)
                return
            # 2. 读配置
            udid, mock_cfg, err = self._read_battery_cfg()
            if err:
                self.battery_result.emit(None, None, False, err)
                return
            is_mock = bool(mock_cfg and mock_cfg.get("enabled"))
            # 3. 实际读取
            level, charging = mod.get_ipad_battery(udid=udid, mock_cfg=mock_cfg, timeout=15)
            elapsed_ms = int((_t.time() - t0) * 1000)
            detail = (
                f"模式：{'MOCK（模拟电量，未调 pymobiledevice3）' if is_mock else '真实（pymobiledevice3 + tunneld）'} | "
                f"UDID：{udid or '未配置'} | 耗时：{elapsed_ms}ms"
            )
            self.battery_result.emit(level, charging, is_mock, detail)
        except Exception as e:
            tb = traceback.format_exc(limit=2).replace("\n", " | ")
            self.battery_result.emit(None, None, False, f"读取异常: {e} | {tb}")

    def _on_battery_result(self, level, charging, is_mock, detail):
        self.btn_read_battery.setEnabled(True)
        self.btn_read_battery.setText("读取电量")
        c = THEMES[self._theme]
        if level is None:
            self.lbl_battery_big.setText("读取失败")
            self.lbl_battery_big.setStyleSheet(
                f"color:{c['danger']};font-size:22px;font-weight:800;letter-spacing:0.2px;"
            )
            self.lbl_charge_status.setText("")
            self.lbl_charge_status.setStyleSheet(f"color:{c['text_dim']};font-size:12px;font-weight:600;")
        else:
            self.lbl_battery_big.setText(f"{level} %")
            if level <= 20:
                col = c["danger"]
            elif level >= 95:
                col = c["warn"]
            else:
                col = c["success"]
            self.lbl_battery_big.setStyleSheet(
                f"color:{col};font-size:30px;font-weight:800;letter-spacing:0.5px;"
            )
            chg_text = "充电中" if charging else "未充电"
            mock_tag = " · Mock" if is_mock else ""
            self.lbl_charge_status.setText(f"{chg_text}{mock_tag}")
            st_col = c["ipad_c1"] if charging else c["text_dim"]
            self.lbl_charge_status.setStyleSheet(f"color:{st_col};font-size:12px;font-weight:600;")
        self.lbl_battery_detail.setText(detail)

    # ---------- 自动控制动态（watcher 事件缓存） ----------
    _EVENTS_PATH = os.path.expanduser("~/.cache/miplug/ipad_events.json")
    _SRC_CN = {"report": "iPad上报", "poll": "Mac轮询", "failsafe": "安全回退"}
    _ACT_CN = {"on": "开启充电", "off": "停止充电", "keep": "保持现状"}

    def _force_refresh_events(self):
        """手动刷新：跳过 mtime 缓存立即重读重渲染。"""
        self._events_mtime = 0
        self._refresh_ipad_events()

    def _refresh_ipad_events(self):
        """每 2s 读取 watcher 事件缓存并重建行卡片（文件 mtime 未变则跳过）。"""
        try:
            if not os.path.exists(self._EVENTS_PATH):
                return
            mt = os.path.getmtime(self._EVENTS_PATH)
            if mt == self._events_mtime:
                return
            self._events_mtime = mt
            import json as _json
            with open(self._EVENTS_PATH) as f:
                events = _json.load(f) or []

            c = THEMES[self._theme]
            # 清空旧行卡片
            while self._events_rows_lay.count():
                item = self._events_rows_lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()

            if not events:
                self.lbl_events_empty = QLabel("等待 iPad 上报或 Mac 轮询触发…")
                self.lbl_events_empty.setWordWrap(True)
                self.lbl_events_empty.setStyleSheet(f"color:{c['text_weak']};font-size:11.5px;")
                self._events_rows_lay.addWidget(self.lbl_events_empty)
                self.lbl_events_summary.setText("暂无控制记录")
                return

            events = events[-6:][::-1]  # 最近 6 条，最新在前

            # 顶部摘要
            last = events[0]
            act = self._ACT_CN.get(last.get("action"), last.get("action") or "--")
            src = self._SRC_CN.get(last.get("source"), last.get("source") or "--")
            lvl = f"{last['level']}%" if last.get("level") is not None else "无读数"
            self.lbl_events_summary.setText(
                f"最新：{act} · 电量 {lvl} · 来源 {src} · {time.strftime('%H:%M:%S', time.localtime(last['ts']))}"
            )

            # 行卡片：时间 | 来源 | 电量 充电状态 | 动作（右侧着色）
            for ev in events:
                t = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
                src = self._SRC_CN.get(ev.get("source"), ev.get("source") or "--")
                lvl = f"{ev['level']}%" if ev.get("level") is not None else "--"
                chg = "充电中" if ev.get("charging") else "未充电"
                act = ev.get("action") or "keep"
                act_cn = self._ACT_CN.get(act, act)
                # 动作着色：开=蓝紫 / 关=警示橙 / 保持=灰（禁用绿色语义）
                if act == "on":
                    acol = c["ipad_c1"]
                elif act == "off":
                    acol = c["warn"]
                else:
                    acol = c["text_dim"]

                row = QFrame()
                row.setStyleSheet(
                    f"QFrame {{ background:{c['input_bg']}; border:1px solid {c['card_border']};"
                    f" border-radius:12px; }}"
                )
                rl = QHBoxLayout(row)
                rl.setContentsMargins(12, 8, 12, 8)
                rl.setSpacing(10)
                mono = "font-family:'SF Mono','Menlo',monospace;"
                lb_t = QLabel(t)
                lb_t.setStyleSheet(f"color:{c['text_weak']};font-size:11px;{mono}")
                lb_t.setMinimumWidth(62)
                lb_src = QLabel(src)
                lb_src.setStyleSheet(f"color:{c['text']};font-size:11.5px;font-weight:600;")
                lb_src.setMinimumWidth(58)
                lb_lvl = QLabel(f"{lvl} {chg}")
                lb_lvl.setStyleSheet(f"color:{c['text_dim']};font-size:11.5px;{mono}")
                lb_act = QLabel(act_cn)
                lb_act.setStyleSheet(f"color:{acol};font-size:11.5px;font-weight:750;")
                lb_act.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                for w in (lb_t, lb_src, lb_lvl):
                    rl.addWidget(w)
                rl.addStretch(1)
                rl.addWidget(lb_act)
                self._events_rows_lay.addWidget(row)
        except Exception:
            pass  # 事件缓存读取失败静默（下次 tick 重试）

    def set_theme(self, theme):
        super().set_theme(theme)
        # 行卡片是动态重建的，主题切换后强制按新主题色重绘
        self._force_refresh_events()



# ===================== 区间双拇指滑块（阈值区间拖动）=====================
class RangeSlider(QWidget):
    """液体玻璃风格的双拇指区间滑块（美化 v3 — 精致竖条拇指）。

    相对 v2 的单点调整（按用户“圆球不精致/不精准 → 改竖条”反馈）：
      · 拇指从“3D 圆球”改为“细长圆角竖条”，视觉上就是刻度游标（更精准）；
      · 尺寸：宽 4 / 高 28（轨道 14），上下各伸出轨道 7px，hover/drag → 宽 6、轻微发光；
      · 光晕从径向圆 → 沿竖条方向的竖向发光（和竖条形态一致，不再像“两个圆灯泡”）；
      · 其它（凹槽轨道、刻度、区段渐变）沿用 v2。
    """
    valuesChanged = Signal(int, int)

    def __init__(self, vmin, vmax, lo, hi, unit="", theme="dark", grad=None, parent=None,
                 ticks=None):
        super().__init__(parent)
        self._vmin = int(vmin); self._vmax = int(vmax)
        self._lo = int(lo); self._hi = int(hi)
        self._unit = unit
        self._theme = theme
        self._grad = grad
        self._drag = None
        self._drag_off = 0
        self._ticks = ticks
        # v3 竖条：轨道高 14，竖条需要更“高”来形成游标感；最小高度也加 4 给伸出区。
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._hover = None
        self.setValues(self._lo, self._hi, emit=False)

    # -------- 对外 API ---------------------------------------------------
    def set_theme(self, theme, grad=None):
        self._theme = theme
        if grad is not None: self._grad = grad
        self.update()

    def values(self):
        return self._lo, self._hi

    def setValues(self, lo, hi, emit=True):
        lo = int(max(self._vmin, min(self._vmax, lo)))
        hi = int(max(self._vmin, min(self._vmax, hi)))
        if lo > hi: lo, hi = hi, lo
        if hi <= lo:
            if lo + 1 <= self._vmax:
                hi = lo + 1
            elif lo - 1 >= self._vmin:
                lo -= 1
        changed = (self._lo, self._hi) != (lo, hi)
        self._lo, self._hi = lo, hi
        self.update()
        if emit and changed:
            self.valuesChanged.emit(self._lo, self._hi)

    def setLo(self, lo, emit=True):
        self.setValues(lo, self._hi, emit=emit)

    def setHi(self, hi, emit=True):
        self.setValues(self._lo, hi, emit=emit)

    # -------- 几何（v3 竖条）---------------------------------------------
    def _track_rect(self):
        w, h = self.width(), self.height()
        track_h = 14
        margin_x = 28          # 左右给竖条宽度+刻度空间
        # 轨道纵向位置：稍偏上（下方留给刻度文字 24px + 4px 线）
        y = 30
        return QRect(margin_x, y, max(40, w - margin_x * 2), track_h)

    # v3 不再使用 _thumb_r（删除了圆形的 r 概念），换成：
    # - 竖条主体宽度/高度；
    # - hover/drag 时竖条加宽（竖条高度保持不变以保持精准对齐）。
    def _bar_size(self, active=False):
        """Return (w, h) — active 时竖条稍微宽一点点以示交互。"""
        if active:
            return (6, 28)
        return (4, 28)

    def _x_of(self, v):
        t = self._track_rect()
        span = self._vmax - self._vmin
        if span <= 0: return t.left()
        ratio = (v - self._vmin) / span
        return int(round(t.left() + ratio * t.width()))

    def _v_of(self, x):
        t = self._track_rect()
        if t.width() <= 0: return self._vmin
        ratio = max(0.0, min(1.0, (x - t.left()) / t.width()))
        return int(round(self._vmin + ratio * (self._vmax - self._vmin)))

    def _thumb_bar_rect(self, v, active=False):
        """返回以“当前值x”为中心的竖条 QRectF，纵向和轨道中心对齐、上下伸出。"""
        bw, bh = self._bar_size(active)
        cx = self._x_of(v)
        cy = self._track_rect().center().y()
        x = cx - bw / 2.0
        y = cy - bh / 2.0
        return QRectF(x, y, bw, bh)

    def _ticks_resolved(self):
        if self._ticks is not None: return self._ticks
        n = 5
        t = []
        for i in range(n + 1):
            v = int(round(self._vmin + i * (self._vmax - self._vmin) / n))
            t.append((v, f"{v}"))
        return t

    # -------- 绘制 -------------------------------------------------------
    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform, True)
        c = THEMES[self._theme]
        g1, g2 = self._grad or (c["accent"], c["accent_2"])
        t = self._track_rect()

        # --- 1) 轨道底：玻璃凹槽 + 内阴影 ---
        track_path = QPainterPath()
        track_path.addRoundedRect(QRectF(t), t.height() / 2, t.height() / 2)
        bg = QColor(c["input_bg"])
        p.save(); p.setClipPath(track_path)
        groove = QLinearGradient(0, t.top(), 0, t.bottom())
        if self._theme == "dark":
            groove.setColorAt(0.0, QColor(0, 0, 0, 90))
            groove.setColorAt(1.0, QColor(255, 255, 255, 18))
        else:
            groove.setColorAt(0.0, QColor(0, 0, 0, 28))
            groove.setColorAt(1.0, QColor(255, 255, 255, 50))
        p.fillRect(t, bg); p.fillRect(t, groove)
        p.restore()
        pen = QPen(QColor(c["card_border"])); pen.setWidthF(0.8); pen.setCosmetic(True)
        p.setPen(pen); p.drawPath(track_path)

        # --- 2) 选中区段：液态高光渐变 + 嵌入感 ---
        xl = self._x_of(self._lo); xr = self._x_of(self._hi)
        inset = 2
        sel_rect = QRectF(xl, t.top() + inset, max(1.0, xr - xl), t.height() - inset * 2)
        rr = min(sel_rect.height() / 2, t.height() / 2 - 1)
        sel_path = QPainterPath(); sel_path.addRoundedRect(sel_rect, rr, rr)
        sg = QLinearGradient(t.left(), 0, t.right(), 0)
        sg.setColorAt(0.0, QColor(g1)); sg.setColorAt(1.0, QColor(g2))
        p.fillPath(sel_path, sg)
        p.save(); p.setClipPath(sel_path)
        shine = QLinearGradient(0, sel_rect.top(), 0, sel_rect.bottom())
        shine.setColorAt(0.0, QColor(255, 255, 255, 72))
        shine.setColorAt(0.5, QColor(255, 255, 255, 6))
        shine.setColorAt(1.0, QColor(0, 0, 0, 18))
        p.fillRect(sel_rect.toRect(), shine)
        p.restore()

        # --- 3) 底部刻度 + 主标签文字 ---
        ticks = self._ticks_resolved()
        cy = t.bottom()
        fm = QFontMetrics(p.font())
        f = QFont(); f.setPixelSize(10); f.setWeight(QFont.Medium); p.setFont(f)
        dim = QColor(c["text_dim"]); dim.setAlpha(180)
        for i, (v, lab) in enumerate(ticks):
            x = self._x_of(v)
            major = (i == 0 or i == len(ticks) - 1 or (len(ticks) > 3 and i == len(ticks) // 2))
            h = 7 if major else 4
            ln = QPen(dim); ln.setWidthF(1.0 if major else 0.7); p.setPen(ln)
            p.drawLine(x, cy + 4, x, cy + 4 + h)
            if major:
                text = f"{lab}{self._unit if i == 0 or i == len(ticks) - 1 else ''}"
                tw = fm.horizontalAdvance(text)
                tx = max(2, min(self.width() - tw - 2, x - tw // 2))
                p.setPen(dim)
                p.drawText(int(tx), int(cy + 24), text)

        # --- 4) 双拇指（v3 精致竖条游标） ---
        for side, v in (("lo", self._lo), ("hi", self._hi)):
            active = (self._hover == side or self._drag == side)
            br = self._thumb_bar_rect(v, active=active)
            bw, bh = br.width(), br.height()

            # 4a. 柔和竖向光晕（不再是圆形灯泡）：以竖条为中心、上下渐淡的竖向发光
            halo_w = bw * (4.0 if active else 3.0)
            halo_h = bh * 1.35
            hx = br.center().x() - halo_w / 2.0
            hy = br.center().y() - halo_h / 2.0
            halo_rect = QRectF(hx, hy, halo_w, halo_h)
            # 外扩径向：横向衰减强、纵向衰减弱 —— 形成“沿竖条方向的光晕”
            halo = QRadialGradient(br.center(), max(halo_w, halo_h) / 2)
            halo_color_outer = QColor(g1); halo_color_outer.setAlpha(0)
            halo_color_mid = QColor(g2); halo_color_mid.setAlpha(110 if active else 55)
            halo_color_inner = QColor(g1); halo_color_inner.setAlpha(180 if active else 100)
            halo.setColorAt(0.0, halo_color_inner)
            halo.setColorAt(0.35, halo_color_mid)
            halo.setColorAt(1.0, halo_color_outer)
            p.setPen(Qt.NoPen); p.setBrush(QBrush(halo))
            p.drawEllipse(halo_rect)

            # 4b. 下方投影（elevation）
            sh = QLinearGradient(0, br.bottom() - 2, 0, br.bottom() + 6)
            sh.setColorAt(0.0, QColor(0, 0, 0, 70))
            sh.setColorAt(1.0, QColor(0, 0, 0, 0))
            sh_rect = QRectF(br.left() - 1, br.bottom() - 1, bw + 2, 7)
            sh_path = QPainterPath(); sh_path.addRoundedRect(sh_rect, 3, 3)
            p.setBrush(QBrush(sh)); p.drawPath(sh_path)

            # 4c. 竖条主体（两色渐变 + 上下圆角）
            body_path = QPainterPath()
            r_vert = min(bw, bh) / 2.0
            body_path.addRoundedRect(br, r_vert, r_vert)
            bg = QLinearGradient(br.topLeft(), br.bottomLeft())
            bg.setColorAt(0.0, QColor(g1).lighter(118))
            bg.setColorAt(0.55, QColor(g1))
            bg.setColorAt(1.0, QColor(g2).darker(112))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(bg)); p.drawPath(body_path)

            # 4d. 左/中高光（玻璃反光条 —— 精致感核心）
            p.save(); p.setClipPath(body_path)
            hl_w = max(1.0, bw * 0.42)
            hl_rect = QRectF(br.left() + bw * 0.18, br.top() + 2.0, hl_w, br.height() - 4.0)
            hl = QLinearGradient(hl_rect.topLeft(), hl_rect.topRight())
            hl.setColorAt(0.0, QColor(255, 255, 255, 0))
            hl.setColorAt(0.5, QColor(255, 255, 255, 180 if active else 140))
            hl.setColorAt(1.0, QColor(255, 255, 255, 0))
            p.setBrush(QBrush(hl)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(hl_rect, hl_w / 2, hl_w / 2)
            p.restore()

            # 4e. 上下两个“端点小帽”（增加游标读取的精准度，一眼看到顶/底在哪里）
            cap_r = bw * 0.38
            top_pt = QPointF(br.center().x(), br.top() + 1.2)
            bot_pt = QPointF(br.center().x(), br.bottom() - 1.2)
            cap_pen = QPen(QColor(255, 255, 255, 200 if active else 150))
            cap_pen.setWidthF(0.9)
            p.setPen(cap_pen); p.setBrush(QColor(g1).lighter(125))
            p.drawEllipse(top_pt, cap_r, cap_r)
            p.drawEllipse(bot_pt, cap_r, cap_r)

            # 4f. 整体描边（玻璃质感）
            edge = QPen(QColor(255, 255, 255, 150)); edge.setWidthF(0.7)
            p.setPen(edge); p.setBrush(Qt.NoBrush); p.drawPath(body_path)

        p.end()

    # -------- 交互（hit-test 改用竖条矩形）--------------------------------
    def _hit_test(self, pos):
        def bar_rect_for(v, expand_px=10):
            """把竖条区域 x 轴左右各 expand_px，更容易命中。"""
            r = self._thumb_bar_rect(v, active=False)
            return QRectF(r.left() - expand_px, r.top() - 4,
                          r.width() + expand_px * 2, r.height() + 8)

        L = bar_rect_for(self._lo)
        H = bar_rect_for(self._hi)
        pf = QPointF(pos)
        Lc = L.contains(pf); Hc = H.contains(pf)
        if Lc and not Hc: return "lo"
        if Hc and not Lc: return "hi"
        if Lc and Hc:
            dl = (pos.x() - L.center().x()) ** 2
            dh = (pos.x() - H.center().x()) ** 2
            return "lo" if dl <= dh else "hi"
        tr = self._track_rect()
        if QRectF(tr).adjusted(-14, -26, 14, 34).contains(QPointF(pos)):
            return "track"
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton: return
        hit = self._hit_test(e.pos())
        if hit in ("lo", "hi"):
            self._drag = hit
        elif hit == "track":
            v_click = self._v_of(e.x())
            if v_click < self._lo:
                self._drag = "lo"; self._set_drag_val(v_click)
            elif v_click > self._hi:
                self._drag = "hi"; self._set_drag_val(v_click)
            else:
                self._drag = "track"; self._drag_off = v_click - self._lo
        else:
            return
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = self._hit_test(e.pos())
        self.setCursor(Qt.PointingHandCursor if self._hover else Qt.ArrowCursor)
        if self._drag:
            self._set_drag_val(self._v_of(e.x()))
        self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None; self._drag_off = 0; self.update()

    def leaveEvent(self, e):
        self._hover = None; self.update()

    def _set_drag_val(self, v_click):
        if self._drag == "lo":
            new_lo = max(self._vmin, min(v_click, self._hi))
            new_hi = self._hi
            if new_hi - new_lo < 1:
                new_lo = max(self._vmin, new_hi - 1)
            self.setValues(new_lo, new_hi)
        elif self._drag == "hi":
            new_hi = min(self._vmax, max(v_click, self._lo))
            new_lo = self._lo
            if new_hi - new_lo < 1:
                new_hi = min(self._vmax, new_lo + 1)
            self.setValues(new_lo, new_hi)
        elif self._drag == "track":
            new_lo = v_click - self._drag_off
            span = self._hi - self._lo
            new_lo = max(self._vmin, min(self._vmax - span, new_lo))
            self.setValues(new_lo, new_lo + span)

def bind_spin_pair(slider, spin_lo, spin_hi, panel=None):
    """双向绑定 slider (lo,hi) <-> (spin_lo, spin_hi)。

    CoolerPanel 的语义：spin_lo=开启温度(大)，spin_hi=关闭温度(小)。
    因此 slider 的 hi 对应开启温度，slider 的 lo 对应关闭温度。
    iPadPanel 的语义：spin_lo=低电量(小，开始充)，spin_hi=高电量(大，停充)。
    因此 slider 的 lo=spin_lo，slider 的 hi=spin_hi。

    swap=True 表示 Cooler 这种「左 thumb(lo)=关闭温度(小值) / 右 thumb(hi)=开启温度(大值)」模式。
    """
    # 检测是 cooler 还是 ipad：根据外部调用约定用 swap 参数。但为了让这个 helper 统一工作，
    # 我们在外部构造 RangeSliderWidgetRow 时显式指定 swap。这里不做通用绑定。
    pass


class RangeSliderWithRow(QWidget):
    """『一行区间设置』（美化 v2）：
          [ 设备色小圆点 + 左标签 ]　lo Chip ──── hi Chip （行内对齐，液态玻璃卡片式）
          整行轨道（加粗 + 3D 拇指 + 底部刻度）
          两条辅助文案（默认：当前 lo/hi 语义说明，如 “低于 <lo>% 自动开始充电”）。

    改进（解决“看上去不太优美”）：
      1. Chip 升级成「液态玻璃胶囊」：外层内阴影 + 半透明背景 + 左侧设备色色条 + 白/浅文本。
      2. 顶部标题行用了「小圆点色标 + 标题文本」更有 Apple 设置页视觉锚点。
      3. 底部新增一行灰色辅助说明，告诉用户两个数的物理意义（而不是冷冰冰两个数字）。
      4. 全局增加了合理的 padding/spacing，控件和其他卡片项之间不会挤在一起。
    """

    def __init__(self, theme, label, *, vmin, vmax,
                 spin_lo, spin_hi, unit,
                 lo_label, hi_label,
                 hint_lo=None, hint_hi=None,
                 swap=False,
                 grad=None, grad_key=None, parent=None,
                 ticks=None):
        super().__init__(parent)
        self._theme = theme
        self._swap = swap
        self._spin_lo = spin_lo
        self._spin_hi = spin_hi
        self._unit = unit
        self._lo_label = lo_label
        self._hi_label = hi_label
        self._hint_lo = hint_lo  # "低于 <v> 自动……" 之类说明文案，<v> 会被替换
        self._hint_hi = hint_hi
        self._grad_key = grad_key
        if grad is None and grad_key is not None:
            grad = device_colors(grad_key, theme)
        self._grad = grad
        self._ticks = ticks

        self.slider = RangeSlider(
            vmin, vmax,
            lo=spin_lo.value() if not swap else spin_hi.value(),
            hi=spin_hi.value() if not swap else spin_lo.value(),
            unit=unit, theme=theme, grad=grad, ticks=ticks,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 2)
        root.setSpacing(8)

        # —— 1) 顶部：标题色标 + 两端 Chip ——
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(10)

        title_host = QWidget(); title_host.setAttribute(Qt.WA_TranslucentBackground)
        title_wrap = QHBoxLayout(title_host); title_wrap.setSpacing(8); title_wrap.setContentsMargins(0,0,0,0)
        self._dot = QLabel(); self._dot.setFixedSize(10, 10)
        self._dot.setStyleSheet(self._dot_css(grad))
        self.lb_title = make_label(label, theme, weight=700, size=12)
        self.lb_title.setMinimumWidth(110)
        title_wrap.addWidget(self._dot, 0, Qt.AlignVCenter)
        title_wrap.addWidget(self.lb_title, 0, Qt.AlignVCenter)
        title_wrap.addStretch(1)
        top.addWidget(title_host, 0, Qt.AlignVCenter)

        top.addStretch(1)
        self.chip_lo = self._make_chip()
        self.chip_hi = self._make_chip()
        self.lb_lo_val = QLabel(); self.lb_hi_val = QLabel()
        self._fill_chip(self.chip_lo, lo_label, self.lb_lo_val, True)
        self._fill_chip(self.chip_hi, hi_label, self.lb_hi_val, False)
        top.addWidget(self.chip_lo, 0, Qt.AlignVCenter)
        top.addSpacing(6)
        top.addWidget(self.chip_hi, 0, Qt.AlignVCenter)

        root.addLayout(top)
        root.addWidget(self.slider)

        # —— 2) 底部：语义说明辅助行（两条）——
        hint = QHBoxLayout(); hint.setContentsMargins(2, 0, 2, 0); hint.setSpacing(12)
        self.lb_hint_lo = QLabel()
        self.lb_hint_hi = QLabel()
        for lb in (self.lb_hint_lo, self.lb_hint_hi):
            lb.setWordWrap(False)
            lb.setStyleSheet(f"color:{THEMES[theme]['text_weak']};"
                             f"font-size:10.5px;letter-spacing:0.1px;")
        hint.addWidget(self.lb_hint_lo, 1)
        hint.addWidget(self.lb_hint_hi, 1, Qt.AlignRight)
        root.addLayout(hint)

        # 信号互连
        self._guard = False
        self.slider.valuesChanged.connect(self._on_slider)
        self._spin_lo.valueChanged.connect(lambda _: self._on_spin())
        self._spin_hi.valueChanged.connect(lambda _: self._on_spin())
        self._on_spin()

    # -------- 视觉辅助 ---------------------------------------------------
    def _dot_css(self, grad):
        c1, c2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            f"stop:0 {c1}, stop:1 {c2});"
            f"border-radius:5px;"
            f"border: 1px solid rgba(255,255,255,30%);"
        )

    def _chip_css(self, grad):
        c = THEMES[self._theme]
        # 液态玻璃胶囊：半透明 card 背景 + 内阴影感线性渐变 + 描边
        return (
            f"QFrame {{"
            f"  background-color: {c['card']};"
            f"  border-radius: 14px;"
            f"  border: 1px solid {c['card_border']};"
            f"}}"
        )

    def _make_chip(self):
        w = QFrame()
        w.setMinimumHeight(30)
        w.setStyleSheet(self._chip_css(self._grad))
        lay = QHBoxLayout(w); lay.setContentsMargins(4, 3, 12, 3); lay.setSpacing(8)
        # 左侧设备色竖条（小椭圆 accent bar）
        bar = QFrame(); bar.setObjectName("chipAccentBar")
        bar.setFixedWidth(4)
        bar.setStyleSheet(self._bar_css(self._grad))
        lay.addWidget(bar, 0, Qt.AlignVCenter)
        return w

    def _bar_css(self, grad):
        g1, g2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (
            f"background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            f"   stop:0 {g1}, stop:1 {g2});"
            f"border-radius: 2px;"
        )

    def _fill_chip(self, chip, title, val_label, is_left):
        while chip.layout().count() > 1:  # 保留下标 0 的 accent bar
            item = chip.layout().takeAt(chip.layout().count() - 1)
            w = item.widget()
            if w is not None: w.setParent(None)
        c = THEMES[self._theme]
        t = QLabel(title)
        t.setStyleSheet(f"color:{c['text_dim']};font-size:10.5px;font-weight:650;")
        val_label.setStyleSheet(
            f"color:{c['text']};"
            f"font-size:12.5px;font-weight:800;"
            f"letter-spacing:0.2px;"
        )
        # 左 Chip：[accent bar]  标签  值       右 Chip：[accent bar]  值  标签
        if is_left:
            chip.layout().addWidget(t); chip.layout().addWidget(val_label)
        else:
            chip.layout().addWidget(val_label); chip.layout().addWidget(t)
        chip.layout().addStretch(1)

    # -------- 主题 -------------------------------------------------------
    def set_theme(self, theme, grad=None):
        self._theme = theme
        c = THEMES[theme]
        if grad is None and self._grad_key is not None:
            grad = device_colors(self._grad_key, theme)
        self._grad = grad

        # 标题 + 圆点
        self.lb_title.setStyleSheet(
            f"color: {c['text']}; font-size:12px; font-weight:700; letter-spacing:0.2px;")
        self._dot.setStyleSheet(self._dot_css(grad))

        # Chip 重绘（background + accent bar + 字体颜色）
        for chip in (self.chip_lo, self.chip_hi):
            chip.setStyleSheet(self._chip_css(grad))
            bar = chip.findChild(QFrame, "chipAccentBar")
            if bar is not None:
                bar.setStyleSheet(self._bar_css(grad))
        self._fill_chip(self.chip_lo, self._lo_label, self.lb_lo_val, True)
        self._fill_chip(self.chip_hi, self._hi_label, self.lb_hi_val, False)

        # 提示文案
        self.lb_hint_lo.setStyleSheet(
            f"color:{c['text_weak']};font-size:10.5px;letter-spacing:0.1px;")
        self.lb_hint_hi.setStyleSheet(
            f"color:{c['text_weak']};font-size:10.5px;letter-spacing:0.1px;")

        self.slider.set_theme(theme, grad)
        # 更新 hint
        self._refresh_chips()

    # -------- 同步 -------------------------------------------------------
    def _on_slider(self, s_lo, s_hi):
        if self._guard: return
        self._guard = True
        try:
            if not self._swap:
                if self._spin_lo.value() != s_lo: self._spin_lo.setValue(s_lo)
                if self._spin_hi.value() != s_hi: self._spin_hi.setValue(s_hi)
            else:
                if self._spin_lo.value() != s_hi: self._spin_lo.setValue(s_hi)
                if self._spin_hi.value() != s_lo: self._spin_hi.setValue(s_lo)
            self._refresh_chips()
        finally:
            self._guard = False

    def _on_spin(self):
        if self._guard: return
        self._guard = True
        try:
            if not self._swap:
                self.slider.setValues(self._spin_lo.value(), self._spin_hi.value())
            else:
                self.slider.setValues(self._spin_hi.value(), self._spin_lo.value())
            self._refresh_chips()
        finally:
            self._guard = False

    def _refresh_chips(self):
        s_lo, s_hi = self.slider.values()
        if not self._swap:
            lo_v, hi_v = s_lo, s_hi
        else:
            # lo_chip 显示"开启温度"(大值) / hi_chip 显示"关闭温度"(小值)
            lo_v, hi_v = s_hi, s_lo
        self.lb_lo_val.setText(f"{lo_v}{self._unit}")
        self.lb_hi_val.setText(f"{hi_v}{self._unit}")
        c = THEMES[self._theme]
        # 重写 hint
        if self._hint_lo:
            self.lb_hint_lo.setText(self._hint_lo.replace("<v>", str(lo_v)))
        else:
            self.lb_hint_lo.setText("")
        if self._hint_hi:
            self.lb_hint_hi.setText(self._hint_hi.replace("<v>", str(hi_v)))
        else:
            self.lb_hint_hi.setText("")


# ===================== 时间区间控件（开始时间 / 结束时间） =====================
class TimeRangeWithRow(QWidget):
    """『一行时间窗口设置』：设备色圆点色标 + 标题 + 两个液态玻璃 Chip（开始/结束）
    + 居中两个潮玻璃 QTimeEdit（HH:mm）+ 语义说明 hint。

    设计决策：
      · 保持与 RangeSliderWithRow 一致的视觉语言：设备色渐变圆点、左侧色竖条 Chip、
        主题态文本、底部灰色 hint。
      · 对外仍然用“小时整数 (start_h, end_h)”读写（因为 ambilight 的 watcher 和
        ambilight_control.py 都是用 h = datetime.now().hour 做 20/8 整点比较，
        分钟部分在 UI 允许调但 dump 时四舍五入到最近整点，保持 watcher 零改动）。
      · UI 上允许选 HH:mm（满足用户"标准时间格式”的诉求），若选了 20:30 会转成 21，
        同时 Chip 文本仍然显示用户真实选的 HH:mm（让用户看得出差异），hint 里再用
        "对齐整点：≈21:00" 提醒一下。
    """

    def __init__(self, theme, label, *,
                 start_time_edit, end_time_edit,
                 start_label="窗口开始", end_label="窗口结束",
                 hint_start="窗口开始：<v>", hint_end="窗口结束：<v>",
                 grad=None, grad_key=None, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._start = start_time_edit
        self._end = end_time_edit
        # Compact short-forms for the two chips to keep row from stretching
        def _compact(s: str) -> str:
            if s == "进入关灯窗口": return "关灯"
            if s == "自动开灯": return "开灯"
            if s == "窗口开始": return "开始"
            if s == "窗口结束": return "结束"
            # Fallback: ≤4 chars keep, otherwise first 2
            return s if len(s) <= 4 else s[:2]
        self._start_label = _compact(start_label)   # short-form for chip tag
        self._end_label = _compact(end_label)
        self._start_label_full = start_label
        self._end_label_full = end_label
        # hints: short forms used in bottom row, which word-wraps for narrow widths
        def _short_hint(h: str) -> str:
            return (h.replace("进入关灯窗口：关氛围灯 + 开始锁屏倒计时（点 Chip/时间框可弹滑动窗口）",
                              "窗口开始；点右侧 Chip / 时间框可弹出滑动选择器")
                      .replace("仍在关灯窗口，≥ <v> 自动开氛围灯（解锁瞬亮不受限）",
                               "窗口结束；解锁会立即开灯"))
        self._hint_start = _short_hint(hint_start)
        self._hint_end = _short_hint(hint_end)
        self._grad_key = grad_key
        if grad is None and grad_key is not None:
            grad = device_colors(grad_key, theme)
        self._grad = grad

        root = QVBoxLayout(self); root.setContentsMargins(10, 4, 10, 2); root.setSpacing(8)

        # 1) 顶部：色标 + 标题 / 左右 Chip (Chip tag now 2-4 chars short-form)
        top = QHBoxLayout(); top.setContentsMargins(0,0,0,0); top.setSpacing(10)
        host = QWidget(); host.setAttribute(Qt.WA_TranslucentBackground)
        th = QHBoxLayout(host); th.setSpacing(8); th.setContentsMargins(0,0,0,0)
        self._dot = QLabel(); self._dot.setFixedSize(10,10)
        self._dot.setStyleSheet(self._dot_css(grad))
        self.lb_title = QLabel(label)
        self.lb_title.setStyleSheet(
            f"color:{THEMES[theme]['text']};font-size:12px;font-weight:700;letter-spacing:0.2px;")
        self.lb_title.setMinimumWidth(72)
        self.lb_title.setWordWrap(True)
        self.lb_title.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        th.addWidget(self._dot, 0, Qt.AlignVCenter)
        th.addWidget(self.lb_title, 0, Qt.AlignVCenter)
        th.addStretch(1)
        top.addWidget(host, 1, Qt.AlignVCenter)   # left side takes any excess first
        self.chip_start = self._make_chip(grad, narrow=True)
        self.chip_end = self._make_chip(grad, narrow=True)
        self.lb_start_val = QLabel(); self.lb_end_val = QLabel()
        self._fill_chip(self.chip_start, self._start_label, self.lb_start_val, True)
        self._fill_chip(self.chip_end, self._end_label, self.lb_end_val, False)
        top.addWidget(self.chip_start, 0, Qt.AlignVCenter)
        top.addSpacing(6)
        top.addWidget(self.chip_end, 0, Qt.AlignVCenter)
        root.addLayout(top)

        # 2) 中间：两个潮玻璃 QTimeEdit 并排 + 居中 "→" 连接符；QTimeEdit width capped at 90
        row = QHBoxLayout(); row.setContentsMargins(0,0,0,0); row.setSpacing(10)
        for te in (self._start, self._end):
            te.setDisplayFormat("HH:mm")
            te.setWrapping(True)
            te.setCalendarPopup(False)
            te.setMinimumHeight(32)
            te.setMinimumWidth(88); te.setMaximumWidth(96)
            te.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            te.setStyleSheet(input_css(theme))
            te.setButtonSymbols(QSpinBox.NoButtons)
            te.installEventFilter(self._eventFilter_scroll())
        row.addStretch(1)
        row.addWidget(self._start, 0, Qt.AlignVCenter)
        arrow = QLabel("→")
        c = THEMES[theme]
        arrow.setStyleSheet(
            f"color:{c['text_dim']};font-size:15px;font-weight:800;letter-spacing:0.5px;")
        arrow.setAlignment(Qt.AlignCenter)
        row.addWidget(arrow, 0, Qt.AlignVCenter)
        row.addWidget(self._end, 0, Qt.AlignVCenter)
        row.addStretch(1)
        root.addLayout(row)

        # 3) 底部：语义 hint — 两行文字允许换行，不会强迫窗口变宽
        hint = QVBoxLayout(); hint.setContentsMargins(2,0,2,0); hint.setSpacing(2)
        self.lb_hint_lo = QLabel(); self.lb_hint_hi = QLabel()
        for lb in (self.lb_hint_lo, self.lb_hint_hi):
            lb.setWordWrap(True)
            lb.setStyleSheet(f"color:{c['text_weak']};font-size:10.5px;letter-spacing:0.1px;")
        hint.addWidget(self.lb_hint_lo)
        hint.addWidget(self.lb_hint_hi, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(hint)

        # 信号：时间变化 → Chip 值刷新
        self._guard = False
        self._start.timeChanged.connect(lambda _q: self._refresh())
        self._end.timeChanged.connect(lambda _q: self._refresh())
        self._refresh()

    # 滚轮支持：滚动时增/减 当前被 QLineEdit 子控件选中的 section（小时/分钟）
    def _eventFilter_scroll(self):
        _self = self
        class F(QObject):
            def eventFilter(me, obj, ev):
                if ev.type() == QEvent.Type.Wheel and obj in (_self._start, _self._end):
                    step = 1 if ev.angleDelta().y() > 0 else -1
                    sec = obj.currentSection()
                    secs = (QTimeEdit.HourSection, QTimeEdit.MinuteSection)
                    if sec not in secs: sec = QTimeEdit.HourSection
                    t = obj.time()
                    if sec == QTimeEdit.HourSection:
                        t = t.addSecs(step * 3600)
                    else:
                        t = t.addSecs(step * 60)
                    obj.blockSignals(True); obj.setTime(t); obj.blockSignals(False); _self._refresh()
                    return True
                return False
        return F(self)

    # --- helpers ---
    def _dot_css(self, grad):
        c1, c2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {c1},stop:1 {c2});"
                f"border-radius:5px;border:1px solid rgba(255,255,255,30%);")

    def _chip_css(self, grad):
        c = THEMES[self._theme]
        return (f"QFrame {{"
                f"  background-color:{c['card']};"
                f"  border-radius:14px;border:1px solid {c['card_border']};"
                f"}}")

    def _bar_css(self, grad):
        c1, c2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {c1},stop:1 {c2});"
                f"border-radius:2px;")

    def _make_chip(self, grad, narrow=False):
        w = QFrame(); w.setMinimumHeight(30)
        if narrow:
            w.setMaximumWidth(102)  # enough for short tag (2 chars) + HH:mm + bar
        w.setStyleSheet(self._chip_css(grad))
        right = 8 if narrow else 12
        spacing = 6 if narrow else 8
        lay = QHBoxLayout(w); lay.setContentsMargins(4,3,right,3); lay.setSpacing(spacing)
        bar = QFrame(); bar.setObjectName("chipAccentBar"); bar.setFixedWidth(4)
        bar.setStyleSheet(self._bar_css(grad)); lay.addWidget(bar, 0, Qt.AlignVCenter)
        return w

    def _fill_chip(self, chip, title, val_label, is_left):
        while chip.layout().count() > 1:
            it = chip.layout().takeAt(chip.layout().count()-1)
            w = it.widget();
            if w is not None: w.setParent(None)
        c = THEMES[self._theme]
        t = QLabel(title)
        t.setStyleSheet(f"color:{c['text_dim']};font-size:10.5px;font-weight:650;")
        val_label.setStyleSheet(
            f"color:{c['text']};font-size:12.5px;font-weight:800;letter-spacing:0.2px;")
        if is_left:
            chip.layout().addWidget(t); chip.layout().addWidget(val_label)
        else:
            chip.layout().addWidget(val_label); chip.layout().addWidget(t)
        chip.layout().addStretch(1)

    # --- theme / value sync ---
    def set_theme(self, theme, grad=None):
        self._theme = theme
        if grad is None and self._grad_key is not None:
            grad = device_colors(self._grad_key, theme)
        self._grad = grad
        c = THEMES[theme]
        self.lb_title.setStyleSheet(
            f"color:{c['text']};font-size:12px;font-weight:700;letter-spacing:0.2px;")
        self._dot.setStyleSheet(self._dot_css(grad))
        for chip in (self.chip_start, self.chip_end):
            chip.setStyleSheet(self._chip_css(grad))
            bar = chip.findChild(QFrame, "chipAccentBar")
            if bar is not None: bar.setStyleSheet(self._bar_css(grad))
        self._fill_chip(self.chip_start, self._start_label, self.lb_start_val, True)
        self._fill_chip(self.chip_end, self._end_label, self.lb_end_val, False)
        arrow = self.findChild(QLabel, "", Qt.FindDirectChildrenOnly)
        for w in self.findChildren(QLabel):
            if w.text() == "→":
                w.setStyleSheet(f"color:{c['text_dim']};font-size:15px;font-weight:800;letter-spacing:0.5px;")
        for te in (self._start, self._end):
            te.setStyleSheet(input_css(theme))
        for lb in (self.lb_hint_lo, self.lb_hint_hi):
            lb.setStyleSheet(f"color:{c['text_weak']};font-size:10.5px;letter-spacing:0.1px;")
        self._refresh()

    def _refresh(self):
        s = self._start.time().toString("HH:mm")
        e = self._end.time().toString("HH:mm")
        self.lb_start_val.setText(s); self.lb_end_val.setText(e)
        # 转整点用于 watcher
        s_h = self._start.time().hour() + (1 if self._start.time().minute() >= 30 else 0)
        e_h = self._end.time().hour() + (1 if self._end.time().minute() >= 30 else 0)
        s_h = s_h % 24; e_h = e_h % 24
        # 两种模板：原始完整 hint，和我们 init 里压缩过的短式 hint，都支持 <v> + 对齐后缀
        def _replace(base, v_str, h_int):
            out = base.replace("<v>", v_str)
            if h_int is not None and f":{v_str.split(':',1)[1]}" != ":00":
                # 分钟非 0：加一行“对齐到整点≈HH:00”
                out = out + f" （对齐整点 ≈{h_int:02d}:00）"
            return out
        s_text = _replace(self._hint_start, s, s_h)
        e_text = _replace(self._hint_end, e, e_h)
        self.lb_hint_lo.setText(s_text); self.lb_hint_hi.setText(e_text)

    # 对外：把 QTimeEdit 里选的时间转成 watcher 需要的“小时整数”
    def start_hour(self):
        t = self._start.time()
        h = t.hour() + (1 if t.minute() >= 30 else 0)
        return int(h % 24)

    def end_hour(self):
        t = self._end.time()
        h = t.hour() + (1 if t.minute() >= 30 else 0)
        return int(h % 24)

    # ---- 时间弹窗选择器（可测：不依赖模态 exec）----
    def _open_start_picker(self):
        self._open_picker(True)

    def _open_end_picker(self):
        self._open_picker(False)

    def _open_picker(self, is_start):
        title = "选择关灯窗口开始时间" if is_start else "选择自动开灯时间"
        te = self._start if is_start else self._end
        dlg = TimePickerDialog(self._theme, title=title,
                               grad=device_colors(self._grad_key or "ambilight", self._theme))
        dlg.set_time(te.time())
        # test-friendly: accept programmatically via QTimer.singleShot(0, ...)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_picker_result(is_start, dlg.get_time())

    def _apply_picker_result(self, is_start, qtime: QTime):
        te = self._start if is_start else self._end
        te.setTime(qtime)   # will trigger timeChanged → _refresh


# ===================== 单值滑动行（锁屏倒计时 / 任意单阈值） =====================
class SingleSliderWithRow(QWidget):
    """整行液态玻璃风格：色标圆点 + 标题 + Chip（当前值大号）+ 单 thumb 自绘拖动条 + 刻度
    + 底部 hint。与 RangeSliderWithRow 同视觉语言，但只拖一个值（0..vmax 整数）。"""

    def __init__(self, theme, label, *, spin, unit="", vmin=0, vmax=500,
                 label_thumb="当前值", hint="", grad=None, grad_key=None, ticks=None, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._spin = spin
        self._vmin = int(vmin); self._vmax = int(vmax)
        self._unit = unit or ""
        self._hint = hint or ""
        self._ticks = ticks or []
        self._grad_key = grad_key
        if grad is None and grad_key is not None:
            grad = device_colors(grad_key, theme)
        self._grad = grad
        self._value = int(max(self._vmin, min(self._vmax, spin.value())))
        self._drag = None   # "thumb" | None
        self._guard = False
        self.setMinimumHeight(120)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        root = QVBoxLayout(self); root.setContentsMargins(10, 4, 10, 2); root.setSpacing(8)
        # top: dot + title + chip(value)
        top = QHBoxLayout(); top.setContentsMargins(0,0,0,0); top.setSpacing(10)
        host = QWidget(); host.setAttribute(Qt.WA_TranslucentBackground)
        th = QHBoxLayout(host); th.setSpacing(8); th.setContentsMargins(0,0,0,0)
        self._dot = QLabel(); self._dot.setFixedSize(10,10)
        self._dot.setStyleSheet(self._dot_css(grad))
        self.lb_title = QLabel(label)
        self.lb_title.setStyleSheet(
            f"color:{THEMES[theme]['text']};font-size:12px;font-weight:700;letter-spacing:0.2px;")
        self.lb_title.setMinimumWidth(110)
        th.addWidget(self._dot, 0, Qt.AlignVCenter)
        th.addWidget(self.lb_title, 0, Qt.AlignVCenter)
        th.addStretch(1)
        top.addWidget(host, 0, Qt.AlignVCenter); top.addStretch(1)
        self.chip = self._make_chip(grad)
        self.lb_tag = QLabel(label_thumb)
        self.lb_value = QLabel()
        self._fill_chip()
        top.addWidget(self.chip, 0, Qt.AlignVCenter)
        root.addLayout(top)

        # slider area (custom paint + drag): empty widget we draw on
        self.slider = _SliderHost(self)
        root.addWidget(self.slider, 1)

        # bottom hint — 允许换行，避免窗口稍窄时 "最大 500 秒" 被 viewport 右边界切
        hint_row = QHBoxLayout(); hint_row.setContentsMargins(2,0,2,0)
        self.lb_hint = QLabel()
        self.lb_hint.setWordWrap(True)
        self.lb_hint.setStyleSheet(f"color:{THEMES[theme]['text_weak']};font-size:10.5px;letter-spacing:0.1px;")
        hint_row.addWidget(self.lb_hint, 1)
        root.addLayout(hint_row)

        # bidirectional sync
        self._spin.valueChanged.connect(self._on_spin_change)
        self._refresh_labels()

    # --- value api ---
    def value(self): return int(self._value)
    def set_value(self, v, emit=True):
        v = int(max(self._vmin, min(self._vmax, v)))
        if v == self._value: return
        self._value = v
        self._spin.blockSignals(True)
        try: self._spin.setValue(v)
        finally: self._spin.blockSignals(False)
        self._refresh_labels()
        self.slider.update()
        if emit: pass  # no extra signal, spin already updated

    def _on_spin_change(self, new_val):
        self.set_value(new_val)

    def set_theme(self, theme, grad=None):
        self._theme = theme
        if grad is None and self._grad_key is not None:
            grad = device_colors(self._grad_key, theme)
        self._grad = grad
        c = THEMES[theme]
        self.lb_title.setStyleSheet(f"color:{c['text']};font-size:12px;font-weight:700;letter-spacing:0.2px;")
        self._dot.setStyleSheet(self._dot_css(grad))
        self.chip.setStyleSheet(self._chip_css(grad))
        bar = self.chip.findChild(QFrame, "chipAccentBar")
        if bar is not None: bar.setStyleSheet(self._bar_css(grad))
        self._fill_chip()
        self.lb_hint.setStyleSheet(f"color:{c['text_weak']};font-size:10.5px;letter-spacing:0.1px;")
        self._refresh_labels()
        self.slider.update()

    # --- helpers ---
    def _dot_css(self, grad):
        c1, c2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (f"background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {c1},stop:1 {c2});"
                f"border-radius:5px;border:1px solid rgba(255,255,255,30%);")

    def _chip_css(self, grad):
        c = THEMES[self._theme]
        return (f"QFrame {{ background-color:{c['card']};"
                f"  border-radius:14px;border:1px solid {c['card_border']}; }}")

    def _bar_css(self, grad):
        c1, c2 = grad or (THEMES[self._theme]["accent"], THEMES[self._theme]["accent_2"])
        return (f"background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {c1},stop:1 {c2});"
                f"border-radius:2px;")

    def _make_chip(self, grad):
        w = QFrame(); w.setMinimumHeight(30); w.setStyleSheet(self._chip_css(grad))
        lay = QHBoxLayout(w); lay.setContentsMargins(4,3,12,3); lay.setSpacing(8)
        bar = QFrame(); bar.setObjectName("chipAccentBar"); bar.setFixedWidth(4)
        bar.setStyleSheet(self._bar_css(grad)); lay.addWidget(bar, 0, Qt.AlignVCenter)
        return w

    def _fill_chip(self):
        lay = self.chip.layout()
        while lay.count() > 1:
            it = lay.takeAt(lay.count()-1); w = it.widget()
            if w is not None: w.setParent(None)
        c = THEMES[self._theme]
        self.lb_tag.setStyleSheet(f"color:{c['text_dim']};font-size:10.5px;font-weight:650;")
        self.lb_value.setStyleSheet(f"color:{c['text']};font-size:12.5px;font-weight:800;letter-spacing:0.2px;")
        lay.addWidget(self.lb_tag); lay.addWidget(self.lb_value); lay.addStretch(1)

    def _refresh_labels(self):
        v = self._value
        self.lb_value.setText(f"{v}{self._unit}")
        if self._hint:
            self.lb_hint.setText(self._hint.replace("<v>", str(v)))
        else:
            self.lb_hint.setText("")


class _SliderHost(QWidget):
    """Internal paint host for SingleSliderWithRow: draws track, ticks,
    vertical-bar thumb — same v3 精致竖条游标 画法 as RangeSlider (thumb w4/h28, w6/h28 drag)."""

    def __init__(self, parent: SingleSliderWithRow):
        super().__init__(parent)
        self._p = parent
        self.setMinimumHeight(80)     # 轨道高 14 + 刻度文字 24 + 上下伸出竖条空间 ≈ 80
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._hover = False

    # geometry helpers (aligned to RangeSlider v3: track_h 14, y=28, asymmetric margins
    # so the "500"-3char tick label and "0"-1char label don't get clipped by parent right edge)
    def _geom(self):
        w, h = self.width(), self.height()
        # Left margin for digit "0" label (~10-12px wide). Right margin for "500" (~26-30px).
        margin_l = 32
        margin_r = 52
        track_h = 14
        y = 28
        track_rect = QRectF(margin_l, y, max(40, w - margin_l - margin_r), track_h)
        return track_rect, margin_l

    def _val_to_x(self, v):
        tr, _ = self._geom()
        if self._p._vmax == self._p._vmin: return tr.center().x()
        t = (v - self._p._vmin) / (self._p._vmax - self._p._vmin)
        return tr.left() + t * tr.width()

    def _x_to_val(self, x):
        tr, _ = self._geom()
        if tr.width() <= 0: return self._p._vmin
        t = (x - tr.left()) / tr.width(); t = max(0.0, min(1.0, t))
        return int(round(self._p._vmin + t * (self._p._vmax - self._p._vmin)))

    # paint (mirrors RangeSlider paint sections for look-and-feel parity)
    def paintEvent(self, _ev):
        p = QPainter(self); p.setRenderHints(QPainter.RenderHint.Antialiasing |
                                               QPainter.RenderHint.SmoothPixmapTransform, True)
        c = THEMES[self._p._theme]
        g1, g2 = self._p._grad or (c["accent"], c.get("accent_2", c["accent"]))
        tr, _ = self._geom()

        # 1) track outer groove (玻璃凹槽 + 内阴影)
        track_path = QPainterPath(); track_path.addRoundedRect(tr, tr.height()/2, tr.height()/2)
        bg = QColor(c["input_bg"])
        p.save(); p.setClipPath(track_path)
        groove = QLinearGradient(0, tr.top(), 0, tr.bottom())
        if self._p._theme == "dark":
            groove.setColorAt(0.0, QColor(0,0,0,90)); groove.setColorAt(1.0, QColor(255,255,255,18))
        else:
            groove.setColorAt(0.0, QColor(0,0,0,28)); groove.setColorAt(1.0, QColor(255,255,255,50))
        p.fillRect(tr, bg); p.fillRect(tr, groove); p.restore()
        pen = QPen(QColor(c["card_border"])); pen.setWidthF(0.8); pen.setCosmetic(True)
        p.setPen(pen); p.drawPath(track_path)

        # 2) progress fill from left up to thumb (液态高光渐变 + shine)
        vx = self._val_to_x(self._p._value)
        inset = 2
        sel_rect = QRectF(tr.left(), tr.top() + inset, max(1.0, vx - tr.left()), tr.height() - inset * 2)
        if sel_rect.width() > 0:
            rr = min(sel_rect.height() / 2, tr.height() / 2 - 1)
            sel_path = QPainterPath(); sel_path.addRoundedRect(sel_rect, rr, rr)
            sg = QLinearGradient(tr.left(), 0, tr.right(), 0)
            sg.setColorAt(0.0, QColor(g1)); sg.setColorAt(1.0, QColor(g2))
            p.fillPath(sel_path, sg)
            p.save(); p.setClipPath(sel_path)
            shine = QLinearGradient(0, sel_rect.top(), 0, sel_rect.bottom())
            shine.setColorAt(0.0, QColor(255,255,255,72))
            shine.setColorAt(0.5, QColor(255,255,255,6))
            shine.setColorAt(1.0, QColor(0,0,0,18))
            p.fillRect(sel_rect.toRect(), shine); p.restore()

        # 3) ticks + labels (mirrors RangeSlider tick style)
        ticks = list(self._p._ticks)
        if ticks:
            cy = tr.bottom()
            f = QFont(); f.setPixelSize(10); f.setWeight(QFont.Weight.Medium); p.setFont(f)
            fm = QFontMetrics(f)
            dim = QColor(c["text_dim"]); dim.setAlpha(180)
            n = len(ticks)
            for i, (val, lab) in enumerate(ticks):
                x = self._val_to_x(int(val))
                major = (i == 0 or i == n - 1 or (n > 3 and i == n // 2))
                h = 7 if major else 4
                ln = QPen(dim); ln.setWidthF(1.0 if major else 0.7); p.setPen(ln)
                p.drawLine(QPointF(x, cy + 4), QPointF(x, cy + 4 + h))
                if major:
                    tw = fm.horizontalAdvance(str(lab))
                    tx = max(2, min(self.width() - tw - 2, x - tw // 2))
                    p.setPen(dim)
                    p.drawText(int(tx), int(cy + 24), str(lab))

        # 4) v3 精致竖条 thumb (w4/h28 idle, w6/h28 drag — 上下伸出轨道)
        active = (self._p._drag == "thumb" or self._hover)
        bw, bh = (6, 28) if active else (4, 28)
        cx = vx
        cy = tr.center().y()
        br = QRectF(cx - bw/2, cy - bh/2, bw, bh)

        # 4a. vertical halo (soft, along bar)
        halo_w = bw * (4.0 if active else 3.0)
        halo_h = bh * 1.35
        halo_rect = QRectF(br.center().x() - halo_w/2, br.center().y() - halo_h/2, halo_w, halo_h)
        halo = QRadialGradient(br.center(), max(halo_w, halo_h)/2)
        def _with_alpha(color_str_or_color, a):
            base = QColor(color_str_or_color)
            base.setAlpha(int(max(0, min(255, a))))
            return base
        halo.setColorAt(0.0, _with_alpha(g1, 180 if active else 100))
        halo.setColorAt(0.35, _with_alpha(g2, 110 if active else 55))
        halo.setColorAt(1.0, _with_alpha(g1, 0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(halo)); p.drawEllipse(halo_rect)

        # 4b. elevation drop shadow
        sh = QLinearGradient(0, br.bottom() - 2, 0, br.bottom() + 6)
        sh.setColorAt(0.0, QColor(0,0,0,70)); sh.setColorAt(1.0, QColor(0,0,0,0))
        sh_rect = QRectF(br.left() - 1, br.bottom() - 1, bw + 2, 7)
        sh_path = QPainterPath(); sh_path.addRoundedRect(sh_rect, 3, 3)
        p.setBrush(QBrush(sh)); p.setPen(Qt.PenStyle.NoPen); p.drawPath(sh_path)

        # 4c. thumb body (vertical gradient lighter → original → darker)
        r_vert = min(bw, bh) / 2.0
        body_path = QPainterPath(); body_path.addRoundedRect(br, r_vert, r_vert)
        bg = QLinearGradient(br.topLeft(), br.bottomLeft())
        bg.setColorAt(0.0, QColor(g1).lighter(118))
        bg.setColorAt(0.55, QColor(g1))
        bg.setColorAt(1.0, QColor(g2).darker(112))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(bg)); p.drawPath(body_path)

        # 4d. highlight strip (glass)
        p.save(); p.setClipPath(body_path)
        hl_w = max(1.0, bw * 0.42)
        hl_rect = QRectF(br.left() + bw * 0.18, br.top() + 2.0, hl_w, br.height() - 4.0)
        hl = QLinearGradient(hl_rect.topLeft(), hl_rect.topRight())
        hl.setColorAt(0.0, QColor(255,255,255,0))
        hl.setColorAt(0.5, QColor(255,255,255, 180 if active else 140))
        hl.setColorAt(1.0, QColor(255,255,255,0))
        p.setBrush(QBrush(hl)); p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(hl_rect, hl_w/2, hl_w/2)
        p.restore()

        # 4e. top/bottom caps
        cap_r = bw * 0.38
        top_pt = QPointF(br.center().x(), br.top() + 1.2)
        bot_pt = QPointF(br.center().x(), br.bottom() - 1.2)
        cap_pen = QPen(QColor(255,255,255, 200 if active else 150))
        cap_pen.setWidthF(0.9)
        p.setPen(cap_pen); p.setBrush(_with_alpha(g1, 255).lighter(125))
        p.drawEllipse(top_pt, cap_r, cap_r)
        p.drawEllipse(bot_pt, cap_r, cap_r)

        # 4f. white stroke (glass)
        edge = QPen(QColor(255,255,255,150)); edge.setWidthF(0.7)
        p.setPen(edge); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(body_path)
        p.end()

    # interaction (use hit-test on thumb, tap elsewhere = jump)
    def enterEvent(self, ev):
        self._hover = True; super().enterEvent(ev); self.update()
    def leaveEvent(self, ev):
        self._hover = False; self._p._drag = None; super().leaveEvent(ev); self.update()

    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton: return
        vx = self._val_to_x(self._p._value)
        y = ev.position().y()
        # thumb bar hit rect with generous 10px halo
        hit = QRectF(vx - 10, self._geom()[0].center().y() - 14 - 2, 20, 28 + 4)
        if hit.contains(QPointF(ev.position().x(), y)):
            self._p._drag = "thumb"
        else:
            new_v = self._x_to_val(ev.position().x())
            self._p.set_value(new_v)
            self._p._drag = "thumb"
        self.update()

    def mouseMoveEvent(self, ev):
        if self._p._drag == "thumb":
            new_v = self._x_to_val(ev.position().x())
            self._p.set_value(new_v)
        else:
            vx = self._val_to_x(self._p._value)
            hit = QRectF(vx - 10, self._geom()[0].center().y() - 14 - 2, 20, 28 + 4)
            hover = hit.contains(QPointF(ev.position().x(), ev.position().y()))
            if hover != self._hover:
                self._hover = hover; self.update()

    def mouseReleaseEvent(self, ev):
        self._p._drag = None; self.update()


# ===================== 滚轮列表（小时/分钟 竖直滑动选值） =====================
class WheelList(QWidget):
    """iOS 风格竖直"滚轮"：当前值居中放大高亮，上下 2 项灰色预览，
    支持鼠标拖动 / 滚轮 / step(n) 编程步进，循环（0-wrap）。
    对外：val/set_val/step(n)/count"""

    valueChanged = Signal(int)

    def __init__(self, items, theme, parent=None, grad=None, cycle=True):
        super().__init__(parent)
        self._theme = theme
        self._items = list(items)
        self._count = len(self._items)
        self._idx = 0
        self._cycle = cycle
        self._grad = grad
        self.setMinimumSize(92, 180)
        self._drag = None       # (start_y, start_idx_offset_f)
        self._offset = 0.0      # fractional offset in pixels (during drag)
        self.setMouseTracking(True)

    def count(self): return self._count
    def value(self): return self._items[self._idx % self._count]
    def index(self): return self._idx % self._count

    def set_value(self, v):
        try:
            idx = self._items.index(v)
        except ValueError:
            idx = 0
        self.set_index(idx)

    def set_index(self, i, emit=True):
        clamped = (int(i) % self._count) if self._cycle else max(0, min(self._count-1, int(i)))
        if clamped == self._idx % self._count: return
        self._idx = clamped
        self._offset = 0.0
        self.update()
        if emit: self.valueChanged.emit(self.value())

    def step(self, n):
        if self._cycle:
            self.set_index(self._idx + n)
        else:
            self.set_index(self._idx + n)

    # Geometry
    def _row_h(self): return 36.0
    def _visible_rows(self): return 5

    @staticmethod
    def _as_qfont_weight_local(w):
        if isinstance(w, QFont.Weight):
            return w
        try:
            iw = int(w)
        except Exception:
            return QFont.Weight.Normal
        mapping = [(0, QFont.Weight.Thin), (150, QFont.Weight.ExtraLight), (250, QFont.Weight.Light),
                   (350, QFont.Weight.Normal), (450, QFont.Weight.Medium), (550, QFont.Weight.DemiBold),
                   (650, QFont.Weight.Bold), (750, QFont.Weight.ExtraBold), (1000, QFont.Weight.Black)]
        for t, w_ in mapping:
            if iw <= t: return w_
        return QFont.Weight.Black

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        c = THEMES[self._theme]
        grad = self._grad or (c["accent"], c.get("accent_2", c["accent"]))
        rh = self._row_h()
        # center line (selection row) as glass groove
        cx, cy = self.width()/2, self.height()/2
        sel = QRectF(8, cy - rh/2, self.width()-16, rh)
        path = QPainterPath(); path.addRoundedRect(sel, rh/3, rh/3)
        p.fillPath(path, QColor(c["card"]))
        pen = QPen(QColor(c["card_border"])); pen.setWidthF(0.7); p.setPen(pen); p.drawPath(path)
        # top/bottom fade borders (two soft lines)
        pen2 = QPen(QColor(c["text_weak"])); pen2.setWidthF(0.4); p.setPen(pen2)
        p.drawLine(QPointF(sel.left()+6, sel.top()-2), QPointF(sel.right()-6, sel.top()-2))
        p.drawLine(QPointF(sel.left()+6, sel.bottom()+2), QPointF(sel.right()-6, sel.bottom()+2))

        # draw visible rows
        half = self._visible_rows() // 2   # 2
        cur_idx = self.index()
        # fractional center drift: offset pixels negative = moving up (values increase)
        for k in range(-half, half+1):
            target_idx = (cur_idx + k) % self._count
            # y offset due to drag
            y = cy + k * rh + self._offset
            txt = str(self._items[target_idx])
            if k == 0:
                size = 20
                bold = 800
            else:
                scale = 1.0 - 0.22 * abs(k)
                fg = QColor(c["text_weak"])
                fg.setAlphaF(max(0.25, 1.0 - 0.3 * abs(k)))
                bold = 650
                size = int(13 * scale) + 2
            f = QFont("", size); f.setWeight(self._as_qfont_weight_local(bold))
            p.setFont(f)
            fm = QFontMetrics(f)
            bw = fm.horizontalAdvance(txt)
            if k == 0:
                # Selected row: draw with device-color gradient fill using painter clipping trick.
                # Gradient text via QPainterPath.addText is safe, but we guard against
                # fonts that cause PySide string-convert warnings by using fresh font copy.
                try:
                    y_center = y
                    text_path = QPainterPath()
                    # Qt: addText(x, y, font, text) where y = baseline
                    baseline = y_center + fm.ascent()/2
                    text_path.addText(cx - bw/2, baseline, f, txt)
                    lg = QLinearGradient(0.0, sel.top(), 0.0, sel.bottom())
                    lg.setColorAt(0, QColor(grad[0])); lg.setColorAt(1, QColor(grad[1]))
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(lg); p.drawPath(text_path)
                    # Light stroke outline so readability survives background
                    pen_t = QPen(QColor(0,0,0,28)); pen_t.setWidthF(0.3); p.setPen(pen_t); p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawPath(text_path)
                except Exception:
                    # fallback: plain drawText with gradient-colored pen
                    p.setPen(QPen(QColor(grad[0]))); p.setBrush(Qt.BrushStyle.NoBrush)
                    rect = QRectF(cx - bw/2 - 4, y - rh/2, bw + 8, rh)
                    p.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)
            else:
                p.setPen(QPen(fg)); p.setBrush(Qt.BrushStyle.NoBrush)
                rect = QRectF(cx - bw/2 - 4, y - rh/2, bw + 8, rh)
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)

    # --- drag ---
    def mousePressEvent(self, ev):
        if ev.button() != Qt.MouseButton.LeftButton: return
        self._drag = (ev.position().y(), self._offset, self._idx)

    def mouseMoveEvent(self, ev):
        if self._drag is None: return
        y0, off0, idx0 = self._drag
        delta = ev.position().y() - y0
        rh = self._row_h()
        total = off0 + delta
        steps = int(total // rh)
        new_offset = total - steps * rh
        # wrap steps:
        if self._cycle:
            n_idx = idx0 + steps
        else:
            n_idx = max(0, min(self._count-1, idx0 + steps))
            # clamp offset near extremes
            if n_idx == 0 and new_offset < 0: new_offset = 0
            if n_idx == self._count-1 and new_offset > 0: new_offset = 0
        self._idx = n_idx
        self._offset = new_offset
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is None: return
        rh = self._row_h()
        # snap to nearest row
        add = int(round(self._offset / rh))
        self._idx += add
        if self._cycle:
            self._idx %= self._count
        else:
            self._idx = max(0, min(self._count-1, self._idx))
        self._offset = 0.0
        self._drag = None
        self.update()
        self.valueChanged.emit(self.value())

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        n = 1 if d > 0 else -1
        self.step(-n)  # scroll up => values go up; negating convention matches scroll up increases

    def resizeEvent(self, ev):
        super().resizeEvent(ev); self.update()


# ===================== 时间弹窗（Redesign: 无重复标题 + 单玻璃卡嵌双滚轮 + 顶部小预览）=====================
class TimePickerDialog(QDialog):
    """Redesign v2：
    - 系统标题栏已经写了「选择关灯窗口开始时间」，对话框 body 不再重复写同一段大字标题。
    - 顶部一个 20px 紧凑渐变字「当前时间 HH:mm」做 sub title，替代旧版 80px 巨型橙色胶囊。
    - 小时/分钟 两列滚轮统一嵌入一张实心底的潮玻璃卡片（滚轮不再悬浮在半透明背景上）。
    - 两列滚轮的中心凹槽 y 严格对齐（同一张卡片里的两个 wheel 同高）。
    - 宽 360 / 高 520 内塞下：滚轮卡（≈270H）+ sub title（≈28H）+ hint（≈44H）+ btn（≈40H）+ 26 间距。
    """

    def __init__(self, theme, title="选择时间", grad=None, grad_key="ambilight", minute_step=1, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._grad = grad or device_colors(grad_key, theme)
        self._minute_step = max(1, int(minute_step))
        self.setWindowTitle(title)
        c = THEMES[theme]
        # Dialog 本身给个轻微 glass 背景，不要系统默认的亮色块 / 边框色
        self.setStyleSheet(f"TimePickerDialog {{ background:{c['bg_2']}; }}"
                           f"QDialog {{ background:{c['bg_2']}; }}"
                           # 去掉 QPushButton 上 "filter" 的伪属性告警
                           f"QPushButton {{ qproperty-filter: none; }}")
        self.setMinimumSize(340, 480); self.resize(360, 520)

        root = QVBoxLayout(self); root.setContentsMargins(18, 12, 18, 14); root.setSpacing(10)

        # 1) 顶部小 sub title：当前选中 HH:mm（居中渐变字），不要巨型胶囊
        self.lb_header = GradientLabel(theme, self._grad, "20:00",
                                       font_size=20, weight=750, parent=None,
                                       plate_radius=0, halo=False, plate_alpha=0)
        self.lb_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self.lb_header.setMinimumHeight(28)
        root.addWidget(self.lb_header)

        # 2) 一张实心底玻璃卡片，内嵌两个滚轮（hour / min 列）
        self.card = GlassCard(theme=theme, intense=False, borderless=False)
        # 不吸色太深的板：稍亮一点，让滚轮"玻璃凹槽"能显示出对比（lighten card_bg）
        clr = QColor(c["card"])
        clr = clr.lighter(108) if theme == "dark" else clr.lighter(102)
        self.card.setStyleSheet(
            f"#glassCard {{ background: {clr.name(QColor.HexRgb)}; "
            f"border: 0.8px solid {c['card_border']}; border-radius: 16px; }}"
        )
        cv = QVBoxLayout(self.card); cv.setContentsMargins(14, 16, 14, 16); cv.setSpacing(8)
        # 两个 wheel header: 小时 / 分钟 两字 column header
        hd = QHBoxLayout(); hd.setSpacing(8)
        def _col_hd(s):
            lb = QLabel(s); lb.setAlignment(Qt.AlignCenter)
            lb.setStyleSheet(f"color:{c['text_dim']};font-size:10px;letter-spacing:0.4px;font-weight:700;")
            return lb
        hd.addWidget(_col_hd("小时"))
        sep_hd = QLabel(":"); sep_hd.setAlignment(Qt.AlignCenter); sep_hd.setMaximumWidth(16)
        sep_hd.setStyleSheet(f"color:{c['text_dim']};font-size:10px;font-weight:800;")
        hd.addWidget(sep_hd, 0)
        hd.addWidget(_col_hd("分钟"))
        cv.addLayout(hd)

        wheels_row = QHBoxLayout(); wheels_row.setSpacing(6)
        self.hour_wheel = WheelList([f"{h:02d}" for h in range(24)], theme, grad=self._grad)
        self.min_wheel = WheelList([f"{m:02d}" for m in range(0, 60, self._minute_step)],
                                   theme, grad=self._grad)
        self.hour_wheel.valueChanged.connect(self._on_wheel_changed)
        self.min_wheel.valueChanged.connect(self._on_wheel_changed)
        # 两个 wheel 同固定高度，保证凹槽 y 绝对对齐
        self.hour_wheel.setMinimumHeight(188); self.hour_wheel.setMaximumHeight(188)
        self.min_wheel.setMinimumHeight(188); self.min_wheel.setMaximumHeight(188)
        wheels_row.addWidget(self.hour_wheel, 1)
        sep = QLabel(":")
        sep.setStyleSheet(f"color:{c['text_dim']};font-size:28px;font-weight:800;")
        sep.setAlignment(Qt.AlignCenter); sep.setMaximumWidth(18)
        wheels_row.addWidget(sep, 0, Qt.AlignVCenter)
        wheels_row.addWidget(self.min_wheel, 1)
        cv.addLayout(wheels_row)
        root.addWidget(self.card, 0)

        # 3) hint 行（两行以内：简洁说明 + 整点对齐提示）
        self.lb_hint = QLabel(
            "上下拖动滚轮 / 滚动鼠标滚轮均可。\n"
            "写入配置时按 ≥30 分进一，会在原界面 hint 里给出对齐后的整点。"
        )
        self.lb_hint.setWordWrap(True)
        self.lb_hint.setStyleSheet(f"color:{c['text_weak']};font-size:10.5px;line-height:1.55;")
        root.addWidget(self.lb_hint)
        root.addStretch(1)

        # 4) OK / Cancel
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        cancel = bb.button(QDialogButtonBox.StandardButton.Cancel)
        ok = bb.button(QDialogButtonBox.StandardButton.Ok)
        cancel.setText("取消"); ok.setText("确认")
        ok.setStyleSheet(glass_btn_css("primary", theme, self._grad))
        cancel.setStyleSheet(glass_btn_css("default", theme))
        ok.setMinimumHeight(34); cancel.setMinimumHeight(34)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        root.addWidget(bb)

        # 兼容：旧实现暴露了 lb_title（A1 测试没用到但保留接口），给一个空的隐藏占位避免 KeyError
        self.lb_title = QLabel(""); self.lb_title.hide()

    # --- public API ---
    def set_time(self, t: QTime):
        hh = f"{t.hour():02d}"
        mm_val = (t.minute() // self._minute_step) * self._minute_step
        mm = f"{mm_val:02d}"
        self.hour_wheel.set_value(hh)
        self.min_wheel.set_value(mm)
        self._refresh_header()

    def get_time(self) -> QTime:
        h = int(self.hour_wheel.value())
        m = int(self.min_wheel.value())
        return QTime(h % 24, m % 60)

    # --- internal ---
    def _on_wheel_changed(self, *_):
        self._refresh_header()

    def _refresh_header(self):
        t = self.get_time()
        self.lb_header.set_text(t.toString("HH:mm"))

    def set_theme(self, theme, grad=None):
        self._theme = theme
        self._grad = grad or self._grad
        c = THEMES[theme]
        self.setStyleSheet(f"TimePickerDialog {{ background:{c['bg_2']}; }}QDialog {{ background:{c['bg_2']}; }}")
        self.lb_header.set_theme(theme, self._grad)
        self.hour_wheel._theme = theme; self.hour_wheel._grad = self._grad; self.hour_wheel.update()
        self.min_wheel._theme = theme; self.min_wheel._grad = self._grad; self.min_wheel.update()
        self.card.set_theme(theme)
        clr = QColor(c["card"]); clr = clr.lighter(108) if theme == "dark" else clr.lighter(102)
        self.card.setStyleSheet(
            f"#glassCard {{ background: {clr.name(QColor.HexRgb)}; "
            f"border: 0.8px solid {c['card_border']}; border-radius: 16px; }}"
        )
        self.lb_hint.setStyleSheet(f"color:{c['text_weak']};font-size:10.5px;line-height:1.55;")


class GradientLabel(QLabel):
    """设备色渐变填充文字的小预览（默认不再给大胶囊板；仅在指定 plate_radius>0 时画板）。"""

    def __init__(self, theme, grad, text, font_size=22, weight=750, parent=None,
                 plate_radius=16, halo=True, plate_alpha=255):
        super().__init__(text, parent)
        self._theme = theme; self._grad = grad
        self._font_size = font_size; self._weight = weight
        self._txt = text
        self._plate_radius = int(plate_radius or 0)
        self._halo = bool(halo)
        self._plate_alpha = int(max(0, min(255, plate_alpha or 0)))

    def set_text(self, s):
        self._txt = s; self.update()

    def set_theme(self, theme, grad=None):
        self._theme = theme; self._grad = grad or self._grad; self.update()

    @staticmethod
    def _as_qfont_weight(w):
        if isinstance(w, QFont.Weight):
            return w
        try:
            iw = int(w)
        except Exception:
            return QFont.Weight.Normal
        # Map 0..1000 integers to nearest QFont.Weight enumerator
        mapping = [
            (0,   QFont.Weight.Thin),
            (150, QFont.Weight.ExtraLight),
            (250, QFont.Weight.Light),
            (350, QFont.Weight.Normal),
            (450, QFont.Weight.Medium),
            (550, QFont.Weight.DemiBold),
            (650, QFont.Weight.Bold),
            (750, QFont.Weight.ExtraBold),
            (1000, QFont.Weight.Black),
        ]
        for threshold, w_ in mapping:
            if iw <= threshold:
                return w_
        return QFont.Weight.Black

    def paintEvent(self, e):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = self._grad or (THEMES[self._theme]["accent"], THEMES[self._theme].get("accent_2", "#000"))
        f = QFont("", self._font_size)
        f.setWeight(self._as_qfont_weight(self._weight))
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        fm = QFontMetrics(f)
        w = fm.horizontalAdvance(self._txt)
        h = fm.height()
        rect = self.rect()
        cx = rect.width()/2; cy = rect.height()/2
        text_rect = QRectF(cx - w/2 - 4, cy - h/2, w + 8, h)
        c1 = QColor(grad[0]); c2 = QColor(grad[1])
        # Shadow halo (draw only when enabled, and big enough halo radius exists)
        if self._halo:
            shadow_rect = text_rect.adjusted(-14, -10, 14, 10)
            rg = QRadialGradient(shadow_rect.center(), max(shadow_rect.width(), shadow_rect.height())/2)
            rg.setColorAt(0.0, QColor(c1.red(), c1.green(), c1.blue(), 70))
            rg.setColorAt(1.0, QColor(0,0,0,0))
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(rg); p.drawEllipse(shadow_rect)
        # Glassy plate behind text (disabled when plate alpha 0; otherwise use stored plate_radius)
        if self._plate_alpha > 0:
            r = self._plate_radius
            bg_rect = text_rect.adjusted(-10, -4, 10, 4)
            plate_path = QPainterPath()
            plate_path.addRoundedRect(bg_rect,
                                      min(r, bg_rect.height()/2),
                                      min(r, bg_rect.height()/2))
            card = QColor(THEMES[self._theme]["card"]); card.setAlpha(self._plate_alpha)
            border = QColor(THEMES[self._theme]["card_border"]); border.setAlpha(min(self._plate_alpha, 230))
            p.setBrush(QBrush(card)); p.setPen(QPen(border, 0.7)); p.drawPath(plate_path)
        # Draw text with dual-color gradient: two halves
        p.save()
        top_half = QRectF(text_rect.topLeft(), QSize(text_rect.width(), text_rect.height()/2 + 1))
        bot_half = QRectF(QPointF(text_rect.left(), text_rect.top() + text_rect.height()/2),
                          QSizeF(text_rect.width(), text_rect.height()/2 + 1))
        p.setClipRect(top_half); p.setPen(QPen(c1)); p.setFont(f)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._txt)
        p.setClipRect(bot_half); p.setPen(QPen(c2)); p.setFont(f)
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self._txt)
        p.restore()
        # A tiny highlight (1px) on top of the first half
        if self._plate_alpha > 0:
            hl = QRectF(text_rect.left() + 6, text_rect.top() + 2, max(1.0, text_rect.width()*0.4), 1.2)
            p.setClipRect(text_rect)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(255,255,255,70)); p.drawRect(hl)
        p.end()


def glass_btn_css(kind, theme, grad=None):
    """Shared css for regular QPushButtons (not GlassButton subclass) used in dialogs."""
    c = THEMES[theme]
    if kind == "primary":
        g = grad or (c["accent"], c.get("accent_2", c["accent"]))
        return (f"QPushButton {{ "
                f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {g[0]},stop:1 {g[1]});"
                f"color:white;border:0.7px solid rgba(255,255,255,30%);border-radius:12px;"
                f"padding:6px 16px;font-size:12.5px;font-weight:700; }}"
                f"QPushButton:hover {{ filter:brightness(1.05); }}"
                f"QPushButton:pressed {{ filter:brightness(0.93); }}")
    else:
        return (f"QPushButton {{ background:{c['card']}; color:{c['text']};"
                f"border:0.7px solid {c['card_border']};border-radius:12px;"
                f"padding:6px 16px;font-size:12.5px;font-weight:650; }}"
                f"QPushButton:hover {{ border:0.9px solid {c['accent']}; }}")


# ===================== 日志自动截断（防 miplug.log 无限膨胀） =====================
def log_rotate(log_path: str, max_mb: float = 5.0, keep_tail_lines: int = 5000) -> bool:
    """低开销的定期"尾部保留"式清理：当日志 > max_mb 时，只保留最后 keep_tail_lines。
    · 用「大小先查 + deque 顺序写回」两个 pass，不会一次性把几十 MB 日志读进内存
      （keep <= 2万行时内存安全）。
    · 注意：写回是临时文件 + os.replace 原子重命名，launchd 同时 append 的情况下
      会丢掉"重命名前一瞬间"的少量日志（通常 < 1 行），属于可接受的安全退化，
      比起日志无限拖慢 launchd I/O 代价小得多。
    · 空/不存在/权限错误一律 swallow，不影响主业务。
    """
    try:
        from pathlib import Path
        from collections import deque
        p = Path(log_path)
        if not p.exists() or not p.is_file():
            return False
        cur_size = p.stat().st_size
        if cur_size <= int(max_mb * 1024 * 1024):
            return False  # no-op: 不到阈值不做，减少 I/O
        # read tail into deque
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
        # write to tmp then atomic replace
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
        # 安全兜底：任何异常都吞掉 —— 日志清理绝对不能影响主业务
        return False


# ===================== 导航卡 =====================
class NavCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, desc, glyph, theme, parent=None):
        super().__init__(parent)
        self.key = key; self._theme = theme; self._selected = False
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setMinimumHeight(78)
        c1, c2 = device_colors(key, theme)
        lay = QHBoxLayout(self); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(12)
        self.icon = IconDot(glyph, c1, c2, theme, size=44)
        lay.addWidget(self.icon)
        col = QVBoxLayout(); col.setSpacing(2); col.setContentsMargins(0,0,0,0)
        self.tl = QLabel(title)
        self.dl = QLabel(desc); self.dl.setWordWrap(True)
        col.addWidget(self.tl); col.addWidget(self.dl); lay.addLayout(col, 1)
        self.arr = QLabel("›")
        self.arr.setStyleSheet(f"color:{THEMES[theme]['text_weak']};font-size:20px;font-weight:300;")
        lay.addWidget(self.arr)
        self._apply_style()

    def set_theme(self, theme):
        self._theme = theme
        self.icon._c1, self.icon._c2 = device_colors(self.key, theme)
        self.icon.update()
        self._apply_style()

    def _apply_style(self):
        c = THEMES[self._theme]
        bd = c["accent"] if self._selected else c["card_border"]
        bg = c["accent_soft"] if self._selected else c["card"]
        self.tl.setStyleSheet(f"color:{c['text']};font-size:13px;font-weight:750;")
        self.dl.setStyleSheet(f"color:{c['text_dim']};font-size:11px;line-height:1.35;")
        self.arr.setStyleSheet(f"color:{c['accent'] if self._selected else c['text_weak']};font-size:20px;font-weight:300;")
        self.setStyleSheet(f"""
        NavCard {{
            background: {bg};
            border: 0.7px solid {bd};
            border-radius: 18px;
        }}
        NavCard:hover {{ border: 0.8px solid {c['accent']}; }}
        """)

    def set_selected(self, v):
        self._selected = bool(v); self._apply_style()

    def mousePressEvent(self, e):
        super().mousePressEvent(e); self.clicked.emit(self.key)


# ===================== 自定义标题栏（交通灯按钮嵌入）=====================
class TitleBar(QWidget):
    close_req = Signal(); min_req = Signal(); max_req = Signal(); toggle_theme = Signal()

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setFixedHeight(52)
        self.setAttribute(Qt.WA_TranslucentBackground)
        lay = QHBoxLayout(self); lay.setContentsMargins(18, 0, 16, 0)
        # 布局 spacing 统一设为 0，所有元素间距手动精确 addSpacing 控制
        # （之前默认 spacing=12 让图标→标题叠加到 18px，现在图标→标题严格 6px）
        lay.setSpacing(0)

        # macOS 交通灯（系统原生）
        self.close_btn = QToolButton(); self.close_btn.setFixedSize(12, 12)
        self.min_btn = QToolButton(); self.min_btn.setFixedSize(12, 12)
        self.max_btn = QToolButton(); self.max_btn.setFixedSize(12, 12)
        for b in [self.close_btn, self.min_btn, self.max_btn]:
            b.setCursor(Qt.PointingHandCursor)
        self._apply_traffic()
        self.close_btn.clicked.connect(self.close_req.emit)
        self.min_btn.clicked.connect(self.min_req.emit)
        self.max_btn.clicked.connect(self.max_req.emit)
        tl_lay = QHBoxLayout(); tl_lay.setSpacing(8)
        tl_lay.addWidget(self.close_btn); tl_lay.addWidget(self.min_btn); tl_lay.addWidget(self.max_btn)
        lay.addLayout(tl_lay)
        lay.addSpacing(6)

        # 原 LOGO 位置移除，保留标题水平位置不变（原 LOGO 宽 30px + LOGO→标题间距 6px = 36px 占位）
        logo_h = 30
        lay.addSpacing(36)

        # 标题容器：高度严格 == 图标高度；t1 贴顶、t2 贴底
        tt_container = QWidget(); tt_container.setFixedHeight(logo_h)
        tt = QVBoxLayout(tt_container); tt.setSpacing(0); tt.setContentsMargins(0, 0, 0, 0)
        self.t1_title = QLabel("设备管理")
        self.t1_title.setStyleSheet(f"color:{THEMES[theme]['text']};font-size:13px;font-weight:750;")
        self.t1_title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.t2_sub = QLabel("Device Manager")
        self.t2_sub.setStyleSheet(f"color:{THEMES[theme]['text_weak']};font-size:9.5px;letter-spacing:1.3px;")
        self.t2_sub.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        tt.addWidget(self.t1_title, 0, Qt.AlignTop)
        tt.addStretch(1)
        tt.addWidget(self.t2_sub, 0, Qt.AlignBottom)
        lay.addWidget(tt_container, 0, Qt.AlignVCenter)
        lay.addStretch(1)

        # 主题切换按钮（与 stretch 之间留 12px，避免贴到窗口右边缘）
        lay.addSpacing(12)
        self.theme_btn = GlassButton("☾", theme, "ghost", compact=True)
        self.theme_btn.setFixedSize(32, 30)
        self.theme_btn.clicked.connect(self.toggle_theme.emit)
        lay.addWidget(self.theme_btn, 0, Qt.AlignVCenter)

        # 记录偏移用于拖动窗口
        self._drag_pos = None

    def _apply_traffic(self):
        c = THEMES[self._theme]
        self.close_btn.setStyleSheet(f"""
            QToolButton {{ background:#FF5F57; border:0.5px solid rgba(0,0,0,10); border-radius:6px; }}
            QToolButton:hover {{ background:#FF6F67; }}
        """)
        self.min_btn.setStyleSheet(f"""
            QToolButton {{ background:#FEBC2E; border:0.5px solid rgba(0,0,0,10); border-radius:6px; }}
            QToolButton:hover {{ background:#FFCD40; }}
        """)
        self.max_btn.setStyleSheet(f"""
            QToolButton {{ background:#28C840; border:0.5px solid rgba(0,0,0,10); border-radius:6px; }}
            QToolButton:hover {{ background:#38D850; }}
        """)

    def set_theme(self, theme):
        self._theme = theme
        self._apply_traffic()
        self.theme_btn.set_theme(theme)
        # 主题切换时，所有通过 styleSheet 染色的子控件（标题/标签等）统一做色值替换。
        # 与 dark/light 两套 THEMS 的 key 交叉扫描，确保切回来（light→dark）同样生效。
        for lb in self.findChildren(QLabel):
            ss = lb.styleSheet()
            if not ss:
                continue
            for key in ("text", "text_dim", "text_weak"):
                for src_t in THEMES:
                    old = THEMES[src_t][key]
                    new = THEMES[theme][key]
                    if old != new and old in ss:
                        ss = ss.replace(old, new)
            lb.setStyleSheet(ss)

    # 允许窗口拖动（在标题栏区域）
    def mousePressEvent(self, e):
        self._drag_pos = e.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.window().move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


# ===================== 主窗口 =====================
class ManagerWindow(QMainWindow):
    log_signal = Signal(str, str)
    refresh_status_sig = Signal()
    sw_result_sig = Signal(str, object)   # (device, state)；后台线程探测结果回 GUI 线程

    def __init__(self):
        super().__init__()
        self.theme = "dark"
        self.setWindowTitle("设备管理")
        # 默认 1360×860 起，保证三页卡都能一次看完；最小 1100×700
        self.resize(1360, 860)
        self.setMinimumSize(1100, 700)
        self.setWindowFlags(Qt.FramelessWindowHint)  # 无边框：标题栏自制
        # iOS 26 圆角浮窗：窗口透明，圆角窗口体+软阴影由 GlassBackground 烘焙绘制
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.log_signal.connect(self._on_log)
        self.refresh_status_sig.connect(self._refresh_status)
        self.sw_result_sig.connect(self._on_sw_result)

        self.bg = GlassBackground(self); self.setCentralWidget(self.bg)
        self.root = QVBoxLayout(self.bg)
        # iOS 26：边距 > BLEED(16)，让窗口四周留出阴影出血 + 圆角呼吸空间
        self.root.setContentsMargins(24, 20, 24, 24)
        self.root.setSpacing(12)

        # 标题栏（交通灯在这里）
        self.title_bar = TitleBar(self.theme)
        self.title_bar.close_req.connect(self.close)
        self.title_bar.min_req.connect(self.showMinimized)
        self.title_bar.max_req.connect(self._toggle_max)
        self.title_bar.toggle_theme.connect(self.toggle_theme)
        self.root.addWidget(self.title_bar)

        # 主体（左右分栏 + 底部日志）一行
        body_hl = QHBoxLayout(); body_hl.setSpacing(14); body_hl.setContentsMargins(0, 0, 0, 0)
        self.root.addLayout(body_hl, 1)

        # ------- 侧栏 -------
        self.sidebar = GlassCard(theme=self.theme, intense=True)
        self.sidebar.setFixedWidth(316)
        sl = QVBoxLayout(self.sidebar); sl.setContentsMargins(14, 12, 14, 14); sl.setSpacing(10)

        # 导航卡片
        self.nav_cooler = NavCard("cooler", "水冷器控制", "两阶段温度滞环（CPU 温度）", "水", self.theme)
        self.nav_ambil  = NavCard("ambilight", "氛围灯控制", "时间窗口 + 锁屏倒计时", "灯", self.theme)
        self.nav_ipad   = NavCard("ipad", "iPad 充电", "电量阈值自动充电", "⚡", self.theme)
        for nc in [self.nav_cooler, self.nav_ambil, self.nav_ipad]:
            nc.clicked.connect(self._nav_click)
            sl.addWidget(nc)

        sl.addStretch(1)

        # 服务状态
        sc = GlassCard(theme=self.theme, intense=False)
        sl2 = QVBoxLayout(sc); sl2.setContentsMargins(14, 11, 14, 11); sl2.setSpacing(5)
        row1 = QHBoxLayout(); row1.setSpacing(8)
        self.dot = QLabel("●"); self.dot.setStyleSheet(f"color:{THEMES[self.theme]['success']};font-size:11px;")
        s1 = QLabel("守护进程"); s1.setStyleSheet(f"color:{THEMES[self.theme]['text']};font-size:12.5px;font-weight:750;")
        row1.addWidget(self.dot); row1.addWidget(s1); row1.addStretch(1)
        self.status_pid = QLabel("--"); self.status_pid.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.status_pid.setStyleSheet(f"color:{THEMES[self.theme]['text_weak']};font-size:10.5px;")
        row1.addWidget(self.status_pid)
        sl2.addLayout(row1)
        self.status_label = QLabel("检查中...")
        self.status_label.setStyleSheet(f"color:{THEMES[self.theme]['text_weak']};font-size:10.5px;")
        sl2.addWidget(self.status_label)
        sl.addWidget(sc)

        # 查看日志按钮
        self.log_btn = GlassButton("查看系统日志", self.theme, "default", compact=True)
        self.log_btn.setMinimumHeight(34)
        self.log_btn.clicked.connect(self._open_log)
        sl.addWidget(self.log_btn)

        body_hl.addWidget(self.sidebar)

        # ------- 右侧内容 -------
        self.content = GlassCard(theme=self.theme, intense=True)
        cl = QVBoxLayout(self.content); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)

        # 内容头
        self.head_wrap = QWidget()
        hl = QHBoxLayout(self.head_wrap); hl.setContentsMargins(24, 16, 24, 6); hl.setSpacing(10)
        tcol = QVBoxLayout(); tcol.setSpacing(2)
        self.head_title = QLabel("设备")
        self.head_title.setStyleSheet(f"color:{THEMES[self.theme]['text']};font-size:19px;font-weight:800;letter-spacing:0.2px;")
        self.head_sub = QLabel("参数配置")
        self.head_sub.setStyleSheet(f"color:{THEMES[self.theme]['text_dim']};font-size:12px;")
        tcol.addWidget(self.head_title); tcol.addWidget(self.head_sub)
        hl.addLayout(tcol); hl.addStretch(1)
        self.save_btn = GlassButton("保存并生效", self.theme, "primary")
        self.save_btn.clicked.connect(self.save_current)
        hl.addWidget(self.save_btn)
        cl.addWidget(self.head_wrap)

        self.stack = QStackedWidget(); self.stack.setAttribute(Qt.WA_TranslucentBackground)
        self.cooler_panel = CoolerPanel(self.theme, on_save=lambda k: self.save_panel(k))
        self.ambil_panel = AmbilightPanel(self.theme, on_save=lambda k: self.save_panel(k))
        self.ipad_panel = iPadPanel(self.theme, on_save=lambda k: self.save_panel(k))
        self.stack.addWidget(self.cooler_panel)
        self.stack.addWidget(self.ambil_panel)
        self.stack.addWidget(self.ipad_panel)
        cl.addWidget(self.stack, 1)

        body_hl.addWidget(self.content, 1)

        # 加载配置
        self.cfg_data = self._read_yaml()
        self.cooler_panel.load_config(self.cfg_data.get("cooler", {}))
        self.ambil_panel.load_config(self.cfg_data.get("ambilight", {}))
        self.ipad_panel.load_config(self.cfg_data.get("ipad_charger", {}))

        self._nav_click("cooler")
        QTimer.singleShot(200, self._refresh_status)
        QTimer.singleShot(300, self._refresh_switches)
        # 服务状态：30 秒一次（PID 有缓存，日常只是一次 os.kill 系统调用，≈0 开销）
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(30000)
        # 开关状态：30 秒一次 + 切换页卡/手动操作时事件驱动触发。
        # 实际探测在后台 daemon 线程跑（起 python 子进程 + 网络 IO 都不阻塞 GUI），
        # 且只探测当前可见页卡对应的设备，隐藏页卡不浪费资源。
        self._sw_timer = QTimer(self)
        self._sw_timer.timeout.connect(self._refresh_switches)
        self._sw_timer.start(30000)
        self._sw_busy = False           # 防止后台线程重入
        self._watcher_pid = None        # 缓存 watcher PID（避免每次 pgrep fork）
        self._log_mtime = None          # cooler 日志文件 mtime 缓存（未变不重读）

        self.log("info", "界面已启动，配置读取于 " + str(CFG_PATH))
        # 读取上次窗口状态（大小 / 位置 / 是否最大化）
        self._load_window_state()

    # ---- 主题切换 ----
    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        if self.theme not in THEMES:
            # 未实现的主题（比如 light）：回退 dark，避免 KeyError
            self.theme = "dark"
        self.bg.use_theme(self.theme)
        is_dark = self.theme == "dark"
        self.title_bar.theme_btn.setText("☾" if is_dark else "☀")
        self.title_bar.set_theme(self.theme)
        self.sidebar.set_theme(self.theme); self.content.set_theme(self.theme)
        for nc in [self.nav_cooler, self.nav_ambil, self.nav_ipad]: nc.set_theme(self.theme)
        # 注意顺序：先让 DevicePanel（父类）set_theme 把通用输入控件/玻璃卡片等主题刷新，
        #          然后子类 CoolerPanel/iPadPanel.set_theme **最后一步**把对应设备渐变（cooler=蓝色，ipad=紫色）
        #          写入 RangeSlider，保证最终的 slider._grad 一定是设备色系。
        for p in [self.cooler_panel, self.ambil_panel, self.ipad_panel]:
            # 显式按类调用：避免子类 set_theme 内部 super() 再回到 DevicePanel 把 slider 的 grad
            # 通过 findChildren 改成 None（DevicePanel.set_theme 对 RangeSliderWithRow 传 grad=None，
            # 但 RangeSlider.set_theme 收到 None 是保留原 grad，所以顺序其实也没所谓 —— 这里显式独立调用保持清晰）
            p.set_theme(self.theme)
        for b in [self.save_btn, self.log_btn]:
            b.set_theme(self.theme)
        t = THEMES[self.theme]
        self.head_title.setStyleSheet(f"color:{t['text']};font-size:19px;font-weight:800;letter-spacing:0.2px;")
        self.head_sub.setStyleSheet(f"color:{t['text_dim']};font-size:12px;")
        self.dot.setStyleSheet(f"color:{t['success']};font-size:11px;")
        self.status_label.setStyleSheet(f"color:{t['text_weak']};font-size:10.5px;")
        self.status_pid.setStyleSheet(f"color:{t['text_weak']};font-size:10.5px;")

    # ---- 最大化切换 ----
    def _toggle_max(self):
        # 最大化时保留窄出血（10px），圆角窗口体和阴影仍完整可见（悬浮感）
        if self.isMaximized(): self.showNormal(); self.root.setContentsMargins(24, 20, 24, 24)
        else: self.showMaximized(); self.root.setContentsMargins(10, 10, 10, 10)

    # ---- 窗口状态记忆 ----
    def _save_window_state(self):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            maxed = bool(self.isMaximized())
            data = {
                "maximized": maxed,
                "size": [self.width(), self.height()],
                "pos":  [self.x(), self.y()],
            }
            tmp = WINDOW_STATE_PATH.with_suffix(".yaml.tmp")
            with open(tmp, "w") as f:
                yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, WINDOW_STATE_PATH)
        except Exception as e:
            logging.warning("save window state failed: %s", e)

    def _load_window_state(self):
        try:
            if not WINDOW_STATE_PATH.exists():
                return
            with open(WINDOW_STATE_PATH) as f:
                s = yaml.safe_load(f) or {}
            if not isinstance(s, dict):
                return
            min_w, min_h = self.minimumWidth() or 1100, self.minimumHeight() or 700
            # size 恢复（但不小于最小尺寸，避免用户拖到特别小后打不开）
            sz = s.get("size")
            if isinstance(sz, list) and len(sz) == 2:
                w, h = max(min_w, int(sz[0])), max(min_h, int(sz[1]))
                self.resize(w, h)
            # pos 恢复（但确保窗口可见在某一屏内）
            po = s.get("pos")
            if isinstance(po, list) and len(po) == 2:
                x, y = int(po[0]), int(po[1])
                screen = QGuiApplication.primaryScreen()
                if screen is not None:
                    vg = screen.availableVirtualGeometry()
                    # 保证至少 200×200 在可视区内
                    cx, cy = x + self.width() // 2, y + self.height() // 2
                    if vg.left() <= cx <= vg.right() and vg.top() <= cy <= vg.bottom():
                        self.move(x, y)
            # 最大化最后放，因为会覆盖 size/pos
            if s.get("maximized"):
                self.showMaximized()
                self.root.setContentsMargins(0, 0, 0, 0)
        except Exception as e:
            logging.warning("load window state failed: %s", e)

    def closeEvent(self, e):
        self._save_window_state()
        super().closeEvent(e)

    # ---- 导航 ----
    def _nav_click(self, key):
        mapping = {
            "cooler": (0, "水冷器", "两阶段温度滞环控制", True, "保存并生效", device_colors("cooler",self.theme)),
            "ambilight": (1, "氛围灯", "时间窗口 + 锁屏倒计时", True, "保存并生效", device_colors("ambilight",self.theme)),
            "ipad": (2, "iPad 充电管理", "电量阈值自动充电（需启用规则）", False, "保存并生效", device_colors("ipad",self.theme)),
        }
        idx, title, sub, show_kick, btn_text, grad = mapping[key]
        self.stack.setCurrentIndex(idx)
        self.head_title.setText(title); self.head_sub.setText(sub)
        self.save_btn.setText(btn_text)
        # 重写 save_btn 渐变
        self.save_btn._grad = grad
        self.save_btn.setStyleSheet(self.save_btn._css())
        self._current_key = key
        for nc in [self.nav_cooler, self.nav_ambil, self.nav_ipad]:
            nc.set_selected(nc.key == key)
        # 切到新页卡：事件驱动立刻刷新该设备的开关状态（后台线程，不卡 UI）
        QTimer.singleShot(150, self._refresh_switches)

    def save_current(self):
        self.save_panel(self._current_key)

    # ---- YAML / 服务 ----
    def _read_yaml(self):
        with open(CFG_PATH) as f:
            return yaml.safe_load(f) or {}

    def _write_yaml(self, only_key=None):
        """按页保存 YAML。

        关键修复（iPad save → cooler 不该被 GUI 内存值"偷渡"覆写）：
          - only_key 不为空时，只 dump 对应页面回 YAML；其它页直接复用 raw 里的旧字典，
            不再执行 "raw[x] = {**raw[x], **other_panel.dump_config()}"，避免：
              用户从未切换过 CoolerPanel → widget 默认值 (60°C / 33°C / 3次)
              或切换过又没点保存 → widget 上拖动过的值
            全部不会"趁"保存 iPad 时写进 cooler.* 段。
          - only_key=None 保持旧行为（全面板 dump），供"一键全部保存"使用（目前没有 UI 触发）。
        """
        raw = self._read_yaml()
        # 映射：save_panel 传入的 key (cooler/ambil/ipad) → (raw 顶级键, dump 方法)
        panel_map = {
            "cooler": ("cooler",       self.cooler_panel.dump_config),
            "ambil":  ("ambilight",    self.ambil_panel.dump_config),
            "ipad":   ("ipad_charger", self.ipad_panel.dump_config),
        }
        # 别名归一化（兜底：防止将来有人在 panel.save() 里传 raw 全拼，导致和 panel_map 简写键对不上；
        #            也兼容 'ambilight'/'ipad_charger' 这种 YAML 顶级键名直接被外部当 only_key 传进来的情况）
        if only_key is not None:
            only_key = {
                "ambilight":    "ambil",
                "cooler":       "cooler",
                "ipad_charger": "ipad",
                "ipad":         "ipad",
                "ambil":        "ambil",
            }.get(only_key, only_key)
        if only_key is None:
            # 全面板写入（保持旧兜底行为）
            for _k, (_rk, _df) in panel_map.items():
                raw[_rk] = {**(raw.get(_rk) or {}), **_df()}
        else:
            if only_key not in panel_map:
                raise ValueError(f"_write_yaml(only_key={only_key!r}) 未知面板键，合法键={list(panel_map)}")
            _rk, _df = panel_map[only_key]
            raw[_rk] = {**(raw.get(_rk) or {}), **_df()}
            # 重新把其它 panel 的 widget 值从 raw（磁盘旧值）填回一次，
            # 防止下一次用户没切页就 save 别的 panel 时，GUI 里旧的 widget 值（过期）
            # 与 raw 磁盘值不一致。注意：只"回填"未保存的 panel，不会改变用户正在看的 UI
            # （因为用户正在看的是 only_key 对应的 panel，其它页卡没显示，回填不会闪）。
            for _other_key, (_other_rk, _other_dump) in panel_map.items():
                if _other_key == only_key:
                    continue
                _panel_obj = {
                    "cooler": self.cooler_panel,
                    "ambil":  self.ambil_panel,
                    "ipad":   self.ipad_panel,
                }[_other_key]
                _panel_obj.load_config(raw.get(_other_rk, {}) or {})
        tmp = CFG_PATH.with_suffix(".yaml.tmp")
        with open(tmp, "w") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=160)
        os.replace(tmp, CFG_PATH)
        self.cfg_data = raw

    def _kickstart_service(self):
        try:
            uid = os.getuid()
            target = f"gui/{uid}/{LAUNCH_LABEL}"
            self.log("info", f"执行: launchctl kickstart -k {target}")
            r = subprocess.run(["launchctl","kickstart","-k",target],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self.log("success", "服务重启成功（新配置已生效）")
                QTimer.singleShot(800, lambda: self.refresh_status_sig.emit())
            else:
                self.log("warn", f"服务重启 exit={r.returncode}: {r.stderr.strip()}")
        except Exception as e:
            self.log("error", f"服务重启失败: {e}")

    def save_panel(self, key):
        try:
            self.log("info", f"保存 [{key}] 配置...")
            self._write_yaml(only_key=key)
            self.log("success", f"YAML 已写入 {CFG_PATH.name}")
            # 保存成功顺手清理一次日志（>5MB 只保留最后 5k 行），避免 launchd 长期不重启时
            # miplug.log 无限涨拖慢 I/O；清理失败吞掉不影响主流程。
            try: log_rotate(LOG_PATH, max_mb=5.0, keep_tail_lines=5000)
            except Exception: pass
            # 三设备共用同一个 watcher，任何配置变动都 kickstart 使其立即重读 YAML
            if key == "ipad":
                # 从刚写入的配置读一下 enabled 提示用户
                try:
                    import yaml as _y
                    with open(CFG_PATH) as _f:
                        _ic = (_y.safe_load(_f) or {}).get("ipad_charger", {}) or {}
                    _ena = _ic.get("enabled", False)
                    if _ena:
                        self.log("info", "iPad 充电已启用，守护进程重启后开始按阈值轮询")
                    else:
                        self.log("info", "iPad 充电未启用（ipad_charger.enabled=false），守护进程仅预热配置，不启动轮询")
                except Exception:
                    pass
            threading.Thread(target=self._kickstart_service, daemon=True).start()
            # kickstart 后 watcher 换新 PID，清缓存让状态栏立刻重查
            self._watcher_pid = None
            QTimer.singleShot(2500, self.refresh_status_sig.emit)
        except Exception as e:
            self.log("error", f"保存失败: {e}")
            QMessageBox.warning(self, "保存失败", str(e))

    # ---- 手动控制 ----
    def manual_control(self, device, state):
        cmd = None
        if device == "cooler":
            cmd = [MIPLUG, state]
        elif device == "ambilight":
            cmd = [AMBILIGHT_CTRL, state, "--force"]
        elif device == "ipad":
            cmd = [sys.executable, IPAD_PLUG, state]
        try:
            self.log("info", f"手动: {device} -> {state}")
            subprocess.Popen(cmd)
            # 乐观立即显示状态，2.5s 后再刷新（miio 查询含握手约 2-4s，太快会查到旧值）
            self._set_sw(device, state)
            QTimer.singleShot(2500, self._refresh_switches)
        except Exception as e:
            self.log("error", f"控制失败: {e}")

    def _set_sw(self, device, state):
        sw = {"cooler": self.cooler_panel.sw,
              "ambilight": self.ambil_panel.sw,
              "ipad": self.ipad_panel.sw}[device]
        sw.set_state(state)

    # ---- 刷新手动开关状态 ----
    def _refresh_switches(self):
        """只探测当前可见页卡对应的设备；探测在后台线程，绝不阻塞 GUI。"""
        if getattr(self, "_sw_busy", False):
            return   # 上一次探测还没回来，跳过（避免线程堆积）

        key = getattr(self, "_current_key", None)
        if key not in ("cooler", "ambilight", "ipad"):
            return
        self._sw_busy = True
        threading.Thread(target=self._worker_switches, args=(key,), daemon=True).start()

    def _worker_switches(self, device):
        """后台线程：做 subprocess / 文件 IO，结果通过信号回 GUI 线程。"""
        state = None
        try:
            if device == "ambilight":
                r = subprocess.run([AMBILIGHT_CTRL, "status"],
                                   capture_output=True, text=True, timeout=5)
                out = r.stdout or ""
                if "status: on" in out: state = "on"
                elif "status: off" in out: state = "off"
            elif device == "cooler":
                # 看日志最后几百行最近的 cooler on/off；mtime 没变直接沿用缓存
                try:
                    st = os.stat(LOG_PATH)
                    mtime = (st.st_mtime_ns, st.st_size)
                except OSError:
                    mtime = None
                if mtime is not None and mtime == self._log_mtime and hasattr(self, "_log_state_cache"):
                    state = self._log_state_cache
                else:
                    with open(LOG_PATH, "r", errors="ignore") as f:
                        lines = f.readlines()[-300:]
                    last_state = None
                    for line in reversed(lines):
                        if "[watcher] cooler -> on" in line or "set plug on ok" in line:
                            last_state = "on"; break
                        if "[watcher] cooler -> off" in line or "set plug off ok" in line:
                            last_state = "off"; break
                    state = last_state
                    if mtime is not None:
                        self._log_mtime = mtime
                        self._log_state_cache = last_state
            elif device == "ipad":
                # miio 初始化握手需 2-4s，timeout 过短会 TimeoutExpired → 状态被覆盖成「未知」
                r = subprocess.run([sys.executable, IPAD_PLUG, "status"],
                                   capture_output=True, text=True, timeout=12)
                out = (r.stdout or "").strip().lower()
                if out == "on": state = "on"
                elif out == "off": state = "off"
        except Exception:
            state = None
        finally:
            self._sw_busy = False
        self.sw_result_sig.emit(device, state)

    def _on_sw_result(self, device, state):
        sw = {"cooler": self.cooler_panel.sw,
              "ambilight": self.ambil_panel.sw,
              "ipad": self.ipad_panel.sw}.get(device)
        # 探测失败（None）时保留现有显示：刚点击后的乐观状态更可信，
        # 覆盖成「未知」会让用户误以为控制没生效
        if sw is not None and state in ("on", "off"):
            sw.set_state(state)

    # ---- 日志 / 状态 ----
    def log(self, level, msg):
        self.log_signal.emit(level, msg)

    def _on_log(self, level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        pre = {"success": " OK  ", "warn": "WARN ", "error": "ERR  "}.get(level, "INFO ")
        # 不再在 UI 内显示底部操作日志；保留 stdout 输出便于从命令行启动时查看
        print(f"[{ts}] {pre} {msg}", flush=True)
        if hasattr(self, "log_view") and self.log_view is not None:
            cmap = {"success": "#3B82F6", "warn": "#D97706", "error": "#DC2626", "info": ""}
            col = cmap.get(level, "")
            col_html = f"color:{col};" if col else ""
            self.log_view.append(
                f'<span style="color:{THEMES[self.theme]["text_weak"]};">{ts}</span> '
                f'<span style="font-weight:700;{col_html}">{pre}</span> '
                f'<span style="color:{THEMES[self.theme]["text"]};">{msg}</span>'
            )

    def _refresh_status(self):
        """watcher 存活检测。优先用缓存的 PID + os.kill(pid,0)（纯系统调用，0 fork），
        只有缓存 PID 失效时才回退 pgrep 重新找一次。"""
        pid = getattr(self, "_watcher_pid", None)
        if pid is not None:
            try:
                os.kill(pid, 0)   # 不发信号，仅探测存活
            except OSError:
                pid = None        # 进程没了，重新 pgrep
        if pid is None:
            try:
                out = subprocess.check_output(["pgrep", "-f", "miplug_lock_watcher.py"],
                                              text=True, stderr=subprocess.DEVNULL).strip().splitlines()
                for line in out:
                    p = line.split()[0]
                    if p.isdigit():
                        pid = int(p); break
            except Exception:
                pid = None
            self._watcher_pid = pid
        try:
            if pid:
                self.dot.setStyleSheet(f"color:{THEMES[self.theme]['success']};font-size:11px;")
                self.status_label.setText("运行中")
                self.status_pid.setText(f"PID {pid}")
            else:
                raise FileNotFoundError
        except Exception:
            self.dot.setStyleSheet(f"color:{THEMES[self.theme]['danger']};font-size:11px;")
            self.status_label.setText("未运行，保存配置会自动重启服务")
            self.status_pid.setText("--")

    def _open_log(self):
        try:
            subprocess.Popen(["open","-a","Console",LOG_PATH])
            self.log("info", f"已用 Console.app 打开 {LOG_PATH}")
        except Exception as e:
            self.log("error", f"无法打开日志: {e}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.bg.resize(self.size())

    # 允许拖动窗口（整个 bg 区域里，标题栏已能拖，这里补一下点击空白区的拖动在标题栏外也能生效的逻辑不用，保持主流：只有标题栏拖动）


# ===================== macOS 窗口生命周期（⌘W 关窗驻留 / ⌘Q 退出） ======================
class _DockReopener(QObject):
    """⌘W / 红色交通灯关窗后，点击 Dock 图标重新显示主窗口（macOS 惯例）。

    用户点 Dock 图标 → 系统激活应用 → Qt 把 applicationState 置 Active 并向所有
    顶层窗口派发 ApplicationStateChange（兼容旧的 ApplicationActivate）。
    本过滤器装在主窗口 + QApplication 两级，捕获后把窗口重新 show 出来。
    """
    def __init__(self, app, win, parent=None):
        super().__init__(parent)
        self._app = app
        self._win = win

    def _reopen(self):
        if not self._win.isVisible():
            self._win.show()
        self._win.raise_()
        self._win.activateWindow()

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.ApplicationActivate:
            self._reopen()
        elif t == QEvent.ApplicationStateChange and \
                self._app.applicationState() == Qt.ApplicationActive:
            # 只有变 Active（Dock 图标点击 / ⌘Tab 切回）才重开；
            # 变 Inactive（切走别的应用）绝不能误弹窗口
            self._reopen()
        return False


def install_mac_window_lifecycle(app, w):
    """macOS 惯例窗口生命周期（main() 与离线测试共用）：
      - ⌘W（或红色交通灯）：只关当前窗口，进程驻留（watcher、轮询等继续跑）
      - ⌘Q：真正退出应用
      - 关窗后点 Dock 图标：重新打开主窗口
    """
    app.setQuitOnLastWindowClosed(False)
    sc_close = QShortcut(QKeySequence(QKeySequence.Close), w)   # macOS 自动映射 ⌘W
    sc_close.setContext(Qt.ApplicationShortcut)
    sc_close.activated.connect(w.close)
    sc_quit = QShortcut(QKeySequence(QKeySequence.Quit), w)    # macOS 自动映射 ⌘Q
    sc_quit.setContext(Qt.ApplicationShortcut)
    sc_quit.activated.connect(app.quit)
    reopener = _DockReopener(app, w, parent=w)  # parent 到主窗口：防止被 GC 后过滤器失效
    w.installEventFilter(reopener)   # ApplicationStateChange 派发给顶层窗口
    app.installEventFilter(reopener) # ApplicationActivate 派发给 QApplication
    return reopener


# ===================== 入口 =====================
def main():
    # 启动先尝试清理一次 miplug.log（防日志无限涨拖慢 launchd I/O）
    try: log_rotate(LOG_PATH, max_mb=5.0, keep_tail_lines=5000)
    except Exception: pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("DeviceManager")
    app.setOrganizationName("Hermes")
    try:
        font = QFont("SF Pro Text", 13)
        app.setFont(font)
    except Exception:
        pass
    w = ManagerWindow()
    w.show()
    # macOS 惯例：⌘W 只关窗（进程驻留）、⌘Q 真退出、关窗后点 Dock 图标重开窗口
    install_mac_window_lifecycle(app, w)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
