"""共享 UI 控件与工具函数。所有模块复用这里的组件，保证风格统一。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QSize, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtGui import QColor, QFont, QPainter, QTextFormat, QTextOption
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QFrame, QVBoxLayout, QHBoxLayout, QPlainTextEdit,
    QTextEdit, QApplication, QGraphicsOpacityEffect, QSizePolicy,
)

from . import theme

_MODE = "dark"


def set_mode(mode: str):
    global _MODE
    _MODE = mode


def pal() -> dict:
    return theme.palette(_MODE)


def mono_font(size: int = 12) -> QFont:
    f = QFont()
    for fam in ("JetBrains Mono", "Cascadia Code", "Consolas", "Menlo", "DejaVu Sans Mono"):
        f.setFamily(fam)
        break
    f.setStyleHint(QFont.Monospace)
    f.setFamilies(["JetBrains Mono", "Cascadia Code", "Consolas", "Menlo", "DejaVu Sans Mono", "monospace"])
    f.setPointSize(size)
    return f


# --------------------------------------------------------------------------- #
#  按钮工厂
# --------------------------------------------------------------------------- #
def button(text: str, variant: str = "default", on_click=None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if variant != "default":
        b.setProperty("variant", variant)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(on_click)
    return b


def primary(text, on_click=None, tip=""):
    return button(text, "primary", on_click, tip)


def ghost(text, on_click=None, tip=""):
    return button(text, "ghost", on_click, tip)


def danger(text, on_click=None, tip=""):
    return button(text, "danger", on_click, tip)


def chip(text, on_click=None, tip=""):
    return button(text, "chip", on_click, tip)


def label(text: str, role: str = "") -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    return lb


def badge(text: str, kind: str = "muted") -> QLabel:
    lb = QLabel(text)
    lb.setProperty("badge", kind)
    lb.setAlignment(Qt.AlignCenter)
    return lb


def hdivider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFrameShape(QFrame.NoFrame)
    f.setFixedHeight(1)
    return f


def vdivider() -> QFrame:
    f = QFrame()
    f.setObjectName("VDivider")
    f.setFixedWidth(1)
    return f


def row(*widgets, spacing: int = 8, stretch_last: bool = False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for i, x in enumerate(widgets):
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
        else:  # (widget, stretch)
            lay.addWidget(x[0], x[1])
    if stretch_last:
        lay.addStretch(1)
    return w


# --------------------------------------------------------------------------- #
#  Card 卡片容器
# --------------------------------------------------------------------------- #
class Card(QFrame):
    def __init__(self, title: str = "", hint: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(14, 12, 14, 14)
        self._outer.setSpacing(10)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            t = QLabel(title)
            t.setObjectName("CardTitle")
            head.addWidget(t)
            if hint:
                h = QLabel(hint)
                h.setObjectName("CardHint")
                head.addWidget(h)
            head.addStretch(1)
            self.header = head
            self._outer.addLayout(head)
        else:
            self.header = None
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        self._outer.addLayout(self.body, 1)

    def add(self, w):
        if isinstance(w, QWidget):
            self.body.addWidget(w)
        else:
            self.body.addLayout(w)
        return w

    def add_header_widget(self, w):
        """把控件加到标题行右侧（在 stretch 之后）"""
        if self.header is not None:
            self.header.addWidget(w)
        return w


# --------------------------------------------------------------------------- #
#  代码编辑器（带行号）
# --------------------------------------------------------------------------- #
class _LineNumberArea(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, e):
        self.editor.paint_line_numbers(e)


class CodeEditor(QPlainTextEdit):
    """等宽 + 行号 + 软 Tab 的代码编辑器。"""

    def __init__(self, placeholder: str = "", parent=None, wrap: bool = False):
        super().__init__(parent)
        self.setFont(mono_font(12))
        self.setPlaceholderText(placeholder)
        self.setTabStopDistance(28)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth if wrap else QPlainTextEdit.NoWrap)
        self.setWordWrapMode(QTextOption.WrapAnywhere if wrap else QTextOption.NoWrap)
        self._lna = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_width)
        self.updateRequest.connect(self._update_area)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_width()
        self._highlight_line()

    # 行号区宽度
    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 14 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_width(self, *_):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_area(self, rect, dy):
        if dy:
            self._lna.scroll(0, dy)
        else:
            self._lna.update(0, rect.y(), self._lna.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_width()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        self._lna.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event):
        p = pal()
        painter = QPainter(self._lna)
        painter.fillRect(event.rect(), QColor(p["bg1"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        bottom = top + self.blockBoundingRect(block).height()
        cur_line = self.textCursor().blockNumber()
        painter.setFont(self.font())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(QColor(p["accent_hi"] if num == cur_line else p["fg_muted"]))
                painter.drawText(0, int(top), self._lna.width() - 8,
                                 self.fontMetrics().height(), Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + self.blockBoundingRect(block).height()
            num += 1

    def _highlight_line(self):
        p = pal()
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(p["bg3"]))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    def text(self) -> str:
        return self.toPlainText()

    def set_text(self, s: str):
        self.setPlainText(s)


# --------------------------------------------------------------------------- #
#  Toast 通知
# --------------------------------------------------------------------------- #
_COLORS = {"success": "ok", "error": "err", "warn": "warn", "info": "info"}


def notify(anchor: QWidget, message: str, kind: str = "info", ms: int = 2200):
    """在 anchor 所在顶层窗口右下角弹出短暂提示。"""
    win = anchor.window() if anchor else None
    if win is None:
        return
    p = pal()
    color = p[_COLORS.get(kind, "info")]
    toast = QLabel(message, win)
    toast.setObjectName("Toast")
    toast.setStyleSheet(f"#Toast{{background:{color};color:#0e1320;font-weight:600;"
                        f"border-radius:10px;padding:10px 16px;}}")
    toast.setAttribute(Qt.WA_TransparentForMouseEvents)
    toast.adjustSize()
    margin = 24
    x = win.width() - toast.width() - margin
    y = win.height() - toast.height() - margin
    toast.move(x, y + 12)
    toast.show()
    toast.raise_()
    eff = QGraphicsOpacityEffect(toast)
    toast.setGraphicsEffect(eff)

    anim_in = QPropertyAnimation(eff, b"opacity", toast)
    anim_in.setDuration(160)
    anim_in.setStartValue(0.0)
    anim_in.setEndValue(1.0)
    move_in = QPropertyAnimation(toast, b"pos", toast)
    move_in.setDuration(200)
    move_in.setStartValue(QPoint(x, y + 12))
    move_in.setEndValue(QPoint(x, y))
    move_in.setEasingCurve(QEasingCurve.OutCubic)
    anim_in.start()
    move_in.start()
    toast._anims = (anim_in, move_in)  # keep refs

    def fade_out():
        a = QPropertyAnimation(eff, b"opacity", toast)
        a.setDuration(240)
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.finished.connect(toast.deleteLater)
        a.start()
        toast._fade = a

    QTimer.singleShot(ms, fade_out)


# --------------------------------------------------------------------------- #
#  剪贴板
# --------------------------------------------------------------------------- #
def copy_text(anchor: QWidget, text: str, label_txt: str = "已复制"):
    QApplication.clipboard().setText(text or "")
    notify(anchor, label_txt, "success")


def paste_text() -> str:
    return QApplication.clipboard().text()


def expanding(w: QWidget) -> QWidget:
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    return w
