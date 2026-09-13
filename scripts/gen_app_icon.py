#!/Users/liuhao/.pyenv/versions/3.12.4/bin/python3
"""DeviceManager App 图标生成脚本（浮光原版F风格）。

风格参考：/Applications/浮光.app 高清图标（纯白极简 + 蓝紫对角线渐变 + 右下暖粉橙柔光团）
输出：
  - resources/dm_icon.png   (1024×1024，源文件)
  - resources/DM.iconset/   (iconutil 全尺寸目录)
  - resources/DM.icns       (最终 App 图标)

执行：python3 scripts/gen_app_icon.py
"""
from __future__ import annotations
import sys, math, shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))

from PySide6.QtGui import (
    QPixmap, QPainter, QPainterPath, QColor, QLinearGradient,
    QRadialGradient, QBrush, QPen, QPolygonF, QPainterPathStroker
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtWidgets import QApplication

OUT_PNG     = ROOT / "resources" / "dm_icon.png"
OUT_ICONSET = ROOT / "resources" / "DM.iconset"
OUT_ICNS    = ROOT / "resources" / "DM.icns"
SIZE = 1024


def make_squircle_path(w, h, n=4.6):
    """superellipse (Lamé curve, n≈4.6 ~ macOS Sequoia squircle)"""
    cx, cy = w/2.0, h/2.0
    a, b = w/2.0-1, h/2.0-1
    pts = []
    for i in range(513):
        t = i*2.0*math.pi/512
        c, s = math.cos(t), math.sin(t)
        x = cx + a*(abs(c)**(2.0/n))*(1 if c>=0 else -1)
        y = cy + b*(abs(s)**(2.0/n))*(1 if s>=0 else -1)
        pts.append(QPointF(x,y))
    poly = QPolygonF(pts); p=QPainterPath(); p.addPolygon(poly); p.closeSubpath()
    return p

def _clip(p, path): p.setClipPath(path, Qt.ReplaceClip)


def draw_bg(p, w, h):
    """纯白 + 左上冷光 + 右下暖粉橙柔光团 + 右下淡紫（浮光高清版四层背景）。"""
    p.fillRect(QRectF(0,0,w,h), QColor("#FFFFFF"))

    rg_cool = QRadialGradient(QPointF(w*0.20, h*0.13), w*0.70)
    cool = QColor("#EEF3FF"); cool.setAlpha(180)
    rg_cool.setColorAt(0.0, cool)
    rg_cool.setColorAt(0.4, QColor(255,255,255,60))
    rg_cool.setColorAt(1.0, QColor(255,255,255,0))
    p.fillRect(QRectF(0,0,w,h), rg_cool)

    rg_warm = QRadialGradient(QPointF(w*0.73, h*0.72), w*0.58)
    warm_core = QColor("#FFD7C9"); warm_core.setAlpha(130)
    warm_mid  = QColor("#FFE8DA"); warm_mid.setAlpha(55)
    rg_warm.setColorAt(0.0, warm_core)
    rg_warm.setColorAt(0.35, warm_mid)
    rg_warm.setColorAt(0.7, QColor(255,255,255,10))
    rg_warm.setColorAt(1.0, QColor(255,255,255,0))
    p.fillRect(QRectF(0,0,w,h), rg_warm)

    rg_purple = QRadialGradient(QPointF(w*0.90, h*0.86), w*0.45)
    pur = QColor("#F5EEFF"); pur.setAlpha(130)
    rg_purple.setColorAt(0.0, pur)
    rg_purple.setColorAt(0.5, QColor(255,255,255,30))
    rg_purple.setColorAt(1.0, QColor(255,255,255,0))
    p.fillRect(QRectF(0,0,w,h), rg_purple)


def draw_border(p, sq, w, h):
    """内阴影层 + 顶部高光带 + 极细边缘描线（清晰玻璃感）。"""
    for i in range(10,0,-1):
        sh=i*1.9
        col=QColor(30,40,80,int(9*(11-i)/10))
        pen=QPen(col); pen.setWidthF(1.8); p.setPen(pen); p.setBrush(Qt.NoBrush)
        sc=(min(w,h)/2.0-sh)/(min(w,h)/2.0); cx,cy=w/2.0,h/2.0
        np=QPolygonF()
        for pt in sq.toFillPolygon():
            np.append(QPointF(cx+(pt.x()-cx)*sc, cy+(pt.y()-cy)*sc))
        pp=QPainterPath(); pp.addPolygon(np); pp.closeSubpath(); p.drawPath(pp)
    hg=QLinearGradient(0,0,0,h*0.5)
    t=QColor("#FFFFFF");t.setAlpha(85); m=QColor("#FFFFFF");m.setAlpha(18); b=QColor("#FFFFFF");b.setAlpha(0)
    hg.setColorAt(0.0,t);hg.setColorAt(0.5,m);hg.setColorAt(1.0,b)
    p.setPen(Qt.NoPen);p.setBrush(QBrush(hg))
    p.drawRoundedRect(QRectF(32,32,w-64,h*0.47),240,240)
    ed=QPen(QColor(210,215,230,130));ed.setWidthF(1.0);p.setPen(ed);p.setBrush(Qt.NoBrush);p.drawPath(sq)


def stroke_centerline(center_path, thickness, cap=Qt.RoundCap, join=Qt.RoundJoin):
    """Stroker 均匀宽度描边（保证条带无自交、拐角圆润）。"""
    stroker = QPainterPathStroker()
    stroker.setWidth(thickness)
    stroker.setCapStyle(cap)
    stroker.setJoinStyle(join)
    stroker.setMiterLimit(2.0)
    return stroker.createStroke(center_path)


def build_top_f_centerline(w, h):
    """上F条中心线：左低右高平滑单弧 + 末端上翘（无M谷）。"""
    path = QPainterPath()
    P0  = QPointF(w * 0.270, h * 0.495)
    C1a = QPointF(w * 0.370, h * 0.405)
    C1b = QPointF(w * 0.460, h * 0.340)
    P1  = QPointF(w * 0.540, h * 0.330)
    C2a = QPointF(w * 0.620, h * 0.315)
    C2b = QPointF(w * 0.700, h * 0.240)
    P2  = QPointF(w * 0.760, h * 0.195)
    path.moveTo(P0)
    path.cubicTo(C1a, C1b, P1)
    path.cubicTo(C2a, C2b, P2)
    return path


def build_bottom_f_centerline(w, h):
    """下F条中心线：右上起点 → 右下弯 → 底大弧 → 左下收尖。"""
    path = QPainterPath()
    P0  = QPointF(w * 0.570, h * 0.570)
    C1a = QPointF(w * 0.640, h * 0.610)
    C1b = QPointF(w * 0.665, h * 0.690)
    P1  = QPointF(w * 0.635, h * 0.745)
    C2a = QPointF(w * 0.565, h * 0.850)
    C2b = QPointF(w * 0.420, h * 0.870)
    P2  = QPointF(w * 0.305, h * 0.815)
    C3a = QPointF(w * 0.268, h * 0.795)
    C3b = QPointF(w * 0.248, h * 0.793)
    P3  = QPointF(w * 0.245, h * 0.798)
    path.moveTo(P0)
    path.cubicTo(C1a, C1b, P1)
    path.cubicTo(C2a, C2b, P2)
    path.cubicTo(C3a, C3b, P3)
    return path


def draw_f_glyph(p, w, h):
    """两条F流线：对角线渐变 + SourceAtop叠层斜向高光 + 上亮下暗层次。"""
    T = h * 0.145
    top_band = stroke_centerline(build_top_f_centerline(w,h), T)
    bot_band = stroke_centerline(build_bottom_f_centerline(w,h), T)

    g = QLinearGradient(QPointF(w*0.18, h*0.92), QPointF(w*0.82, h*0.18))
    g.setColorAt(0.0,  QColor("#6A8AFF"))
    g.setColorAt(0.30, QColor("#8FA4FF"))
    g.setColorAt(0.60, QColor("#C2B0FF"))
    g.setColorAt(1.0,  QColor("#E9B6FF"))
    p.setPen(Qt.NoPen); p.setBrush(QBrush(g))
    p.drawPath(top_band); p.drawPath(bot_band)

    prev = p.compositionMode()
    p.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    sf = QLinearGradient(QPointF(w*0.20, h*0.85), QPointF(w*0.80, h*0.20))
    s1=QColor(255,255,255,105); s2=QColor(255,255,255,25); s3=QColor(255,255,255,0)
    sf.setColorAt(0.0,s1); sf.setColorAt(0.55,s2); sf.setColorAt(1.0,s3)
    p.fillRect(QRectF(0,0,w,h), sf)
    sf2 = QLinearGradient(0, h*0.25, 0, h*0.85)
    s2a=QColor(255,255,255,45); s2b=QColor(255,255,255,0); s2c=QColor(10,16,50,30)
    sf2.setColorAt(0.0,s2a); sf2.setColorAt(0.5,s2b); sf2.setColorAt(1.0,s2c)
    p.fillRect(QRectF(0,0,w,h), sf2)
    p.setCompositionMode(prev)


def build_icon():
    c=QPixmap(SIZE,SIZE); c.fill(Qt.transparent)
    p=QPainter(c)
    p.setRenderHint(QPainter.Antialiasing,True)
    p.setRenderHint(QPainter.SmoothPixmapTransform,True)
    sq=make_squircle_path(SIZE,SIZE)
    draw_bg(p,SIZE,SIZE); _clip(p,sq); draw_border(p,sq,SIZE,SIZE)
    draw_f_glyph(p,SIZE,SIZE); p.end()
    return c


def scale(src, n): return src.scaled(n,n,Qt.KeepAspectRatio,Qt.SmoothTransformation)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    print("  → 生成 1024×1024 浮光风格图标...")
    pix = build_icon()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(OUT_PNG), "PNG")
    print(f"  ✓ 源图标: {OUT_PNG} ({pix.width()}×{pix.height()})")

    sizes = [
        ("icon_16x16.png",      16), ("icon_16x16@2x.png",   32),
        ("icon_32x32.png",      32), ("icon_32x32@2x.png",   64),
        ("icon_128x128.png",   128), ("icon_128x128@2x.png", 256),
        ("icon_256x256.png",   256), ("icon_256x256@2x.png", 512),
        ("icon_512x512.png",   512), ("icon_512x512@2x.png",1024),
    ]
    if OUT_ICONSET.exists(): shutil.rmtree(OUT_ICONSET)
    OUT_ICONSET.mkdir(parents=True)
    print(f"  → 生成 {len(sizes)} 个 iconset 尺寸...")
    for name, side in sizes:
        scale(pix, side).save(str(OUT_ICONSET / name), "PNG")
    print(f"  ✓ iconset 目录: {OUT_ICONSET}")

    print("  → iconutil 编译 icns ...")
    subprocess.run(["iconutil","-c","icns",str(OUT_ICONSET),"-o",str(OUT_ICNS)], check=True)
    print(f"  ✓ 编译完成: {OUT_ICNS} ({OUT_ICNS.stat().st_size // 1024} KB)")

    app_res = Path("/Users/liuhao/Applications/DeviceManager.app/Contents/Resources/")
    if app_res.exists():
        shutil.copy(OUT_ICNS, app_res / "DM.icns")
        print(f"  ✓ 已同步到 App bundle: {app_res / 'DM.icns'}")

    print("\n✅ 全部完成：")
    print(f"   · 源 PNG：{OUT_PNG}")
    print(f"   · icns： {OUT_ICNS}")


if __name__ == "__main__":
    main()
