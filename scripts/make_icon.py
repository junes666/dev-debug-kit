"""生成应用图标：渐变圆角底 + </> 开发符号 + 调试光标点。
输出 assets/icon.png (512) 与多尺寸 assets/icon.ico。离屏渲染，无需显示器。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
os.makedirs(ASSETS, exist_ok=True)

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import (
    QImage, QPainter, QLinearGradient, QRadialGradient, QColor, QBrush, QPen,
    QFont, QPainterPath,
)
from PySide6.QtCore import Qt, QRectF, QPointF


def render(size: int = 512) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    s = size
    radius = s * 0.22
    rect = QRectF(0, 0, s, s)

    # 渐变底
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0.0, QColor("#6f9bff"))
    g.setColorAt(0.55, QColor("#4a7bef"))
    g.setColorAt(1.0, QColor("#3358d4"))
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    p.fillPath(path, QBrush(g))

    # 左上柔光高光
    p.save()
    p.setClipPath(path)
    rg = QRadialGradient(QPointF(s * 0.28, s * 0.22), s * 0.7)
    rg.setColorAt(0.0, QColor(255, 255, 255, 70))
    rg.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.fillRect(rect, QBrush(rg))
    # 底部微暗
    dg = QLinearGradient(0, s * 0.5, 0, s)
    dg.setColorAt(0.0, QColor(0, 0, 0, 0))
    dg.setColorAt(1.0, QColor(0, 0, 0, 45))
    p.fillRect(rect, QBrush(dg))
    p.restore()

    # 内描边
    p.setPen(QPen(QColor(255, 255, 255, 40), max(1.0, s * 0.012)))
    p.setBrush(Qt.NoBrush)
    inset = s * 0.012
    ipath = QPainterPath()
    ipath.addRoundedRect(rect.adjusted(inset, inset, -inset, -inset), radius - inset, radius - inset)
    p.drawPath(ipath)

    # </> 开发符号
    f = QFont()
    f.setFamilies(["Segoe UI", "DejaVu Sans", "Arial"])
    f.setBold(True)
    f.setPixelSize(int(s * 0.30))
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    text_rect = QRectF(0, s * 0.12, s, s * 0.62)
    p.drawText(text_rect, Qt.AlignCenter, "</>")

    # 调试光标：底部一段下划线 + 闪烁块（绿色点缀）
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#37d39b"))
    bar_w, bar_h = s * 0.30, s * 0.055
    bx, by = (s - bar_w) / 2, s * 0.72
    p.drawRoundedRect(QRectF(bx, by, bar_w, bar_h), bar_h / 2, bar_h / 2)
    p.setBrush(QColor("#ffffff"))
    cur = s * 0.075
    p.drawRoundedRect(QRectF(bx + bar_w + s * 0.02, by - cur * 0.25, cur, bar_h * 1.5),
                      s * 0.012, s * 0.012)

    p.end()
    return img


def main():
    app = QApplication.instance() or QApplication([])
    big = render(512)
    png_path = os.path.join(ASSETS, "icon.png")
    big.save(png_path)
    print("saved", png_path)

    # 多尺寸 ICO（Pillow）
    try:
        from PIL import Image
        base = Image.open(png_path).convert("RGBA")
        ico_path = os.path.join(ASSETS, "icon.ico")
        base.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                   (64, 64), (128, 128), (256, 256)])
        print("saved", ico_path)
    except Exception as e:
        print("ICO 生成失败（需要 Pillow）:", e, file=sys.stderr)


if __name__ == "__main__":
    main()
