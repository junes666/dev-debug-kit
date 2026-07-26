"""二维码 生成 / 解析 模块（生成离线用 segno；解析可选 opencv）。"""
from __future__ import annotations

import io
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QColor, QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QComboBox, QSpinBox, QLineEdit,
    QLabel, QColorDialog, QFileDialog, QApplication, QSplitter, QScrollArea,
)

from app import widgets

try:
    import segno
    _SEGNO = True
except Exception:  # noqa: BLE001
    _SEGNO = False

try:
    import cv2
    import numpy as np
    _CV2 = True
except Exception:  # noqa: BLE001
    _CV2 = False


class QrTool(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._png_bytes: bytes | None = None
        self._fg = "#000000"
        self._bg = "#ffffff"

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        tabs = QTabWidget()
        tabs.addTab(self._tab_make(), "生成二维码")
        tabs.addTab(self._tab_decode(), "解析二维码")
        root.addWidget(tabs)

    # ------------------------------------------------------------------ #
    #  生成
    # ------------------------------------------------------------------ #
    def _tab_make(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        if not _SEGNO:
            lay.addWidget(widgets.label("二维码生成需要 segno：pip install segno", "hint"))
            lay.addStretch(1)
            return w

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # 左：输入与选项
        left = widgets.Card("内容与样式")
        self.make_in = widgets.CodeEditor(placeholder="输入文本或链接，例如 https://github.com …", wrap=True)
        left.add(widgets.expanding(self.make_in))

        self.ec_combo = QComboBox()
        self.ec_combo.addItems(["L（7%）", "M（15%）", "Q（25%）", "H（30%）"])
        self.ec_combo.setCurrentIndex(1)
        self.scale_spin = QSpinBox(); self.scale_spin.setRange(1, 40); self.scale_spin.setValue(8)
        self.border_spin = QSpinBox(); self.border_spin.setRange(0, 20); self.border_spin.setValue(4)
        left.add(widgets.row(
            widgets.label("纠错：", "label"), self.ec_combo,
            widgets.label("缩放：", "label"), self.scale_spin,
            widgets.label("边距：", "label"), self.border_spin,
        ))
        self.fg_btn = self._color_btn(self._fg, "前景色", self._pick_fg)
        self.bg_btn = self._color_btn(self._bg, "背景色", self._pick_bg)
        left.add(widgets.row(
            widgets.label("前景：", "label"), self.fg_btn,
            widgets.label("背景：", "label"), self.bg_btn,
            None,
            widgets.primary("生成", self._make_qr),
        ))
        split.addWidget(left)

        # 右：预览与导出
        right = widgets.Card("预览")
        self.make_preview = QLabel("点击「生成」后在此显示二维码")
        self.make_preview.setAlignment(Qt.AlignCenter)
        self.make_preview.setProperty("role", "hint")
        self.make_preview.setMinimumSize(280, 280)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.make_preview)
        right.add(widgets.expanding(scroll))
        right.add(widgets.row(
            None,
            widgets.chip("复制图片", self._copy_qr),
            widgets.primary("保存 PNG", self._save_qr),
        ))
        split.addWidget(right)
        split.setSizes([1, 1])
        lay.addWidget(split, 1)
        return w

    def _color_btn(self, color: str, text: str, cb):
        b = widgets.button(text)
        b.setFixedWidth(96)
        self._style_color_btn(b, color)
        b.clicked.connect(cb)
        return b

    @staticmethod
    def _style_color_btn(btn, color: str):
        fg = "#ffffff" if QColor(color).lightnessF() < 0.5 else "#000000"
        btn.setStyleSheet(f"background:{color};color:{fg};border:1px solid #888;border-radius:8px;padding:7px 10px;")
        btn.setProperty("_color", color)

    def _pick_fg(self):
        c = QColorDialog.getColor(QColor(self._fg), self, "选择前景色")
        if c.isValid():
            self._fg = c.name(); self._style_color_btn(self.fg_btn, self._fg)

    def _pick_bg(self):
        c = QColorDialog.getColor(QColor(self._bg), self, "选择背景色")
        if c.isValid():
            self._bg = c.name(); self._style_color_btn(self.bg_btn, self._bg)

    def _make_qr(self):
        text = self.make_in.text()
        if not text:
            widgets.notify(self, "请输入要生成二维码的内容", "warn")
            return
        try:
            level = self.ec_combo.currentText()[0].lower()
            qr = segno.make(text, error=level)
            buf = io.BytesIO()
            qr.save(buf, kind="png", scale=self.scale_spin.value(),
                    border=self.border_spin.value(), dark=self._fg, light=self._bg)
            self._png_bytes = buf.getvalue()
            pix = QPixmap()
            pix.loadFromData(self._png_bytes)
            self.make_preview.setPixmap(pix)
            self.make_preview.setText("")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"生成失败：{e}", "error")

    def _copy_qr(self):
        if not self._png_bytes:
            widgets.notify(self, "请先生成二维码", "warn")
            return
        pix = QPixmap(); pix.loadFromData(self._png_bytes)
        QApplication.clipboard().setPixmap(pix)
        widgets.notify(self, "二维码已复制到剪贴板", "success")

    def _save_qr(self):
        if not self._png_bytes:
            widgets.notify(self, "请先生成二维码", "warn")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存二维码", "qrcode.png", "PNG 图片 (*.png)")
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self._png_bytes)
            widgets.notify(self, f"已保存到 {os.path.basename(path)}", "success")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"保存失败：{e}", "error")

    # ------------------------------------------------------------------ #
    #  解析
    # ------------------------------------------------------------------ #
    def _tab_decode(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        bar = [
            widgets.primary("选择图片", self._decode_pick),
            widgets.chip("从剪贴板粘贴", self._decode_clip),
            None,
            widgets.chip("复制结果", lambda: widgets.copy_text(self, self.decode_out.text())),
        ]
        if not _CV2:
            lay.addWidget(widgets.label(
                "二维码解析需要 opencv：pip install opencv-python-headless numpy（安装后重启即可用）", "hint"))
            for b in bar[:2]:
                b.setEnabled(False)
        lay.addWidget(widgets.row(*bar))

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        c_img = widgets.Card("图片")
        self.decode_preview = QLabel("选择含二维码的图片…")
        self.decode_preview.setAlignment(Qt.AlignCenter)
        self.decode_preview.setProperty("role", "hint")
        self.decode_preview.setMinimumSize(260, 260)
        sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(self.decode_preview)
        c_img.add(widgets.expanding(sc))
        c_out = widgets.Card("识别内容")
        self.decode_out = widgets.CodeEditor(placeholder="识别到的二维码内容显示在这里…", wrap=True)
        self.decode_out.setReadOnly(True)
        c_out.add(widgets.expanding(self.decode_out))
        split.addWidget(c_img)
        split.addWidget(c_out)
        split.setSizes([1, 1])
        lay.addWidget(split, 1)
        return w

    def _decode_pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择二维码图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.webp);;所有文件 (*)")
        if not path:
            return
        img = QImage(path)
        self._show_and_decode(img)

    def _decode_clip(self):
        img = QApplication.clipboard().image()
        if img.isNull():
            widgets.notify(self, "剪贴板中没有图片", "warn")
            return
        self._show_and_decode(img)

    def _show_and_decode(self, img: QImage):
        if img.isNull():
            widgets.notify(self, "图片加载失败", "error")
            return
        self.decode_preview.setPixmap(QPixmap.fromImage(img).scaled(
            420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.decode_preview.setText("")
        if not _CV2:
            return
        try:
            data = self._decode_qr(img)
            if data:
                self.decode_out.set_text(data)
                widgets.notify(self, "识别成功", "success")
            else:
                self.decode_out.set_text("")
                widgets.notify(self, "未识别到二维码", "warn")
        except Exception as e:  # noqa: BLE001
            widgets.notify(self, f"解析失败：{e}", "error")

    @staticmethod
    def _decode_qr(img: QImage) -> str:
        rgba = img.convertToFormat(QImage.Format_RGBA8888)
        w, h = rgba.width(), rgba.height()
        buf = rgba.constBits()
        arr = np.frombuffer(bytes(buf[: w * h * 4]), dtype=np.uint8).reshape(h, w, 4)
        bgr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        det = cv2.QRCodeDetector()
        data, _pts, _ = det.detectAndDecode(bgr)
        if data:
            return data
        # 尝试多码识别
        try:
            ok, decoded, _p, _s = det.detectAndDecodeMulti(bgr)
            if ok and decoded:
                return "\n".join([d for d in decoded if d])
        except Exception:
            pass
        return ""
